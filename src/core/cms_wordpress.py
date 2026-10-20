"""Read-only WordPress REST connector: what the CMS holds vs what the site serves.

A crawl only ever sees the rendered front end. Half of the defects a site owner
actually has to fix live one layer further back: a JSON-LD block the theme
strips, an SEO title the plugin truncates, a custom field still holding the
string 'None', a post published last week that never reached the sitemap. This
module pulls the same items straight from the WordPress REST API and diffs them
against the crawled pages.

    from src.core import cms_wordpress

    report = cms_wordpress.audit_site(session, base_url, pages,
                                      config={'expected_author': 'Jane Doe'},
                                      sitemap_urls=sitemap_urls)
    # {'available': bool, 'items': [...], 'errors': [...], 'issues': [...]}

READ-ONLY IS A HARD RULE
    Every request this module makes goes through `_get`, which calls
    `session.get`. There is no POST, PUT, PATCH or DELETE anywhere in this
    file, and nothing here ever writes to the CMS. Credentials are never sent
    on the connector's own initiative: it works with whatever the public REST
    API exposes, and `config['auth_header']` - if the caller sets one for a
    private site - is passed straight through to the request headers.

    A site that is not WordPress costs exactly one request (`/wp-json/wp/v2/`)
    and produces `available: False` and no issues. Nothing here raises either:
    an unreachable host, a non-200, HTML where JSON was promised or a truncated
    page list all become records in `errors`, beside the items that were read.

GROUPING RULE
    Mirrors src/core/sitemap_audit.py: a per-item check reports one issue per
    item while at most `group_threshold` items (default 5) are affected, and a
    single grouped issue on the site's base URL above that, carrying the
    offending URLs in `evidence.related_urls` (capped at `max_related_urls`).
    A grouped issue uses an empty offending value so its id stays stable while
    the list of affected items changes between crawls.

URL MATCHING
    CMS `link` values are matched to crawled pages and to sitemap entries on
    `sitemap_audit.compare_key`, so a tracking parameter, an http/https
    difference or a trailing slash never reads as "not crawled".
"""

import html as html_module
import json
import re
from collections import defaultdict
from urllib.parse import urlparse

from src.core.sitemap_audit import compare_key
from src.utils.issue_factory import make_issue

try:  # BeautifulSoup is a crawl dependency; the regex fallback keeps this
    from bs4 import BeautifulSoup  # importable in isolation all the same
except ImportError:  # pragma: no cover - exercised only without bs4
    BeautifulSoup = None

CATEGORY = 'CMS'

# Path appended to the site root to reach the REST namespace.
WP_NAMESPACE_PATH = '/wp-json/wp/v2'

DEFAULTS = {
    'timeout': 10,
    # WordPress caps per_page at 100 and refuses anything larger
    'per_page': 100,
    'max_items': 2000,
    'types': ('posts', 'pages'),
    # hard stop on pagination, whatever X-WP-TotalPages claims
    'max_pages_per_type': 200,
    # SEO length limits, in characters
    'seo_title_max': 60,
    'seo_description_max': 160,
    # per-item findings above this many items collapse into one grouped issue
    'group_threshold': 5,
    'max_related_urls': 50,
    # author name (or list of names) every item is expected to carry;
    # None disables the check entirely
    'expected_author': None,
    # 'Authorization: Basic ...', a bare header value, or a dict of headers -
    # passed straight through to the session on every request
    'auth_header': None,
    # values that mean "nobody filled this in"
    'placeholder_values': ('none', 'null', 'undefined', 'tbd', 'n/a', 'na',
                           'todo', 'placeholder', 'lorem ipsum'),
    # a value starting with one of these is a placeholder too
    'placeholder_prefixes': ('lorem ipsum',),
    # fields scanned for placeholder values
    'placeholder_fields': ('title', 'seo.title', 'seo.description', 'excerpt'),
    # fields where an empty value is itself the defect (elsewhere only a
    # whitespace-only value counts, since an unset optional field is normal)
    'required_fields': ('title',),
    # fields scanned for stray whitespace
    'whitespace_fields': ('title', 'seo.title', 'seo.description'),
    # set by audit_site; compare() falls back to the first item's origin
    'base_url': '',
    # iterable of sitemap URLs; the sitemap check is skipped without it
    'sitemap_urls': None,
}

# severity, UI name, how_to_fix, grouped-details template
CHECKS = {
    'cms_structured_data_stripped': (
        'error', 'Structured Data Stripped By The Front End',
        'Render the JSON-LD block from the CMS content in the page template, '
        'or remove it from the CMS so the two agree.',
        '{count} CMS items carry JSON-LD that the live pages do not serve.',
    ),
    'cms_seo_title_too_long': (
        'warning', 'CMS SEO Title Too Long',
        'Shorten the SEO title in the CMS to the configured limit.',
        '{count} CMS items have an SEO title beyond the configured limit.',
    ),
    'cms_seo_description_too_long': (
        'warning', 'CMS SEO Description Too Long',
        'Shorten the SEO description in the CMS to the configured limit.',
        '{count} CMS items have an SEO description beyond the configured limit.',
    ),
    'cms_field_placeholder': (
        'error', 'Placeholder Value In A CMS Field',
        'Fill the field in with real content, or clear it so the template '
        'falls back instead of printing the placeholder.',
        '{count} CMS fields still hold a placeholder value.',
    ),
    'cms_stray_whitespace': (
        'warning', 'Stray Whitespace In A CMS Field',
        'Trim the leading, trailing and doubled spaces out of the field.',
        '{count} CMS fields contain leading, trailing or doubled whitespace.',
    ),
    'cms_author_unexpected': (
        'warning', 'Unexpected CMS Author',
        'Reassign the item to the expected author, or add the author to the '
        'expected_author setting.',
        '{count} CMS items are attributed to an unexpected author.',
    ),
    'cms_published_not_in_sitemap': (
        'warning', 'Published Item Missing From The Sitemap',
        'Include the published URL in the XML sitemap, or unpublish it.',
        '{count} published CMS items are missing from the sitemap.',
    ),
    'cms_not_crawled': (
        'warning', 'Published Item Never Crawled',
        'Link the published URL from the site so it can be reached, and check '
        'that robots.txt and the crawl scope do not exclude it.',
        '{count} published CMS items were never reached by the crawl.',
    ),
}

# Item keys WordPress core defines. Everything else an item carries - plugin
# blocks, ACF groups, registered meta - is kept verbatim in `raw_fields`.
_CORE_KEYS = frozenset({
    'id', 'date', 'date_gmt', 'guid', 'modified', 'modified_gmt', 'slug',
    'status', 'type', 'link', 'title', 'content', 'excerpt', 'author',
    'featured_media', 'comment_status', 'ping_status', 'sticky', 'template',
    'format', 'categories', 'tags', 'parent', 'menu_order',
    '_links', '_embedded',
})

_JSONLD_SCRIPT_RE = re.compile(
    r'<script[^>]+type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r'<[^>]+>')
# a literal space, tab or non-breaking space, twice over
_DOUBLED_WS_RE = re.compile('[ \\t\\u00a0]{2,}')
_WS_RUN_RE = re.compile(r'\s+')


def _config(overrides=None):
    cfg = dict(DEFAULTS)
    for key, value in (overrides or {}).items():
        if key in cfg and value is not None:
            cfg[key] = value
    return cfg


# --------------------------------------------------------------------------
# the only place a request is made
# --------------------------------------------------------------------------

def _get(session, url, timeout, headers=None, params=None):
    """The one request primitive in this module, and it is always a GET.

    Nothing else in this file calls the session. Grep for `session.` to
    confirm the connector cannot write to a site.
    """
    return session.get(url, timeout=timeout, headers=headers, params=params)


def auth_headers(auth_header):
    """Normalise `config['auth_header']` into a headers dict (or None).

    Accepts a dict, a full header line ('Authorization: Basic abc') or a bare
    value ('Basic abc', which is sent as Authorization).
    """
    if not auth_header:
        return None
    if isinstance(auth_header, dict):
        return {str(k): str(v) for k, v in auth_header.items() if v}
    text = str(auth_header).strip()
    if not text:
        return None
    name, sep, value = text.partition(':')
    if sep and ' ' not in name.strip():
        return {name.strip(): value.strip()}
    return {'Authorization': text}


def _status(response):
    try:
        return int(getattr(response, 'status_code', 0) or 0)
    except (TypeError, ValueError):
        return 0


def _json(response):
    """Parsed JSON body, or None when the body is not JSON."""
    try:
        return response.json()
    except Exception:
        pass
    text = getattr(response, 'text', None)
    if not isinstance(text, str):
        text = (getattr(response, 'content', b'') or b'').decode('utf-8', 'replace')
    try:
        return json.loads(text)
    except Exception:
        return None


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def discover(session, base_url, timeout=10, headers=None):
    """Does this site expose the WordPress REST API?

    Costs exactly one GET of `<origin>/wp-json/wp/v2/`. Returns
    `{'available': bool, 'root': url, 'detail': str}` and never raises.

    Only the site's origin is probed: a WordPress install under a sub-path
    (https://example.com/blog/) is not auto-detected - pass its namespace root
    to `fetch_items` directly instead.
    """
    parsed = urlparse(str(base_url or '').strip())
    if not parsed.scheme or not parsed.netloc:
        return {'available': False, 'root': '',
                'detail': f'not a usable base URL: {base_url!r}'}

    root = f'{parsed.scheme}://{parsed.netloc}{WP_NAMESPACE_PATH}'

    try:
        response = _get(session, root + '/', timeout, headers=headers)
    except Exception as exc:
        return {'available': False, 'root': root,
                'detail': f'{root}/ could not be fetched: {type(exc).__name__}: {exc}'}

    status = _status(response)
    if status != 200:
        return {'available': False, 'root': root,
                'detail': f'{root}/ returned HTTP {status}'}

    payload = _json(response)
    if not isinstance(payload, dict):
        return {'available': False, 'root': root,
                'detail': f'{root}/ returned 200 but the body was not a JSON object'}

    namespace = payload.get('namespace')
    routes = payload.get('routes')
    if namespace == 'wp/v2' or isinstance(routes, dict):
        return {'available': True, 'root': root,
                'detail': f'WordPress REST namespace wp/v2 available at {root}/'}

    return {'available': False, 'root': root,
            'detail': f'{root}/ returned JSON without a wp/v2 namespace or route table'}


# --------------------------------------------------------------------------
# reading items
# --------------------------------------------------------------------------

def fetch_items(session, root, types=('posts', 'pages'), per_page=100,
                max_items=2000, timeout=10, headers=None):
    """Read every item of `types` from a wp/v2 root, normalised.

    Returns `{'items': [...], 'errors': [...]}` and never raises. Pagination
    follows the `X-WP-TotalPages` response header, falling back to "stop on a
    short page" when the header is absent, and stops at `max_items`.

    Each item is::

        {'id', 'type', 'slug', 'link', 'status', 'title', 'excerpt',
         'content_html', 'content_text', 'modified_gmt', 'date_gmt',
         'author_id', 'author_name',
         'seo': {'title': str, 'description': str},
         'raw_fields': {...}}          # every non-core key, e.g. plugin meta

    An error record is `{'url', 'kind', 'detail'}` with `kind` one of
    'request_failed', 'http_error', 'bad_payload', 'max_items', 'page_limit'.
    """
    root = str(root or '').rstrip('/')
    per_page = max(1, min(int(per_page or 100), 100))
    max_items = max(0, int(max_items or 0))
    result = {'items': [], 'errors': []}
    if not root:
        result['errors'].append({'url': '', 'kind': 'bad_payload',
                                 'detail': 'no wp/v2 root given'})
        return result

    author_cache = {}
    max_pages = int(DEFAULTS['max_pages_per_type'])

    for item_type in (types or ()):
        endpoint = f'{root}/{str(item_type).strip("/")}'
        page, total_pages = 1, None

        while True:
            if len(result['items']) >= max_items:
                result['errors'].append({
                    'url': endpoint, 'kind': 'max_items',
                    'detail': f'stopped at max_items={max_items}; more items were not read',
                })
                return result

            if page > max_pages:
                result['errors'].append({
                    'url': endpoint, 'kind': 'page_limit',
                    'detail': f'stopped after {max_pages} pages of {item_type}',
                })
                break

            params = {'per_page': per_page, 'page': page, '_embed': '1'}
            try:
                response = _get(session, endpoint, timeout, headers=headers, params=params)
            except Exception as exc:
                result['errors'].append({
                    'url': endpoint, 'kind': 'request_failed',
                    'detail': f'page {page}: {type(exc).__name__}: {exc}',
                })
                break

            status = _status(response)
            if status == 400 and page > 1:
                break  # rest_post_invalid_page_number: walked past the last page
            if status != 200:
                result['errors'].append({
                    'url': endpoint, 'kind': 'http_error',
                    'detail': f'page {page} returned HTTP {status}',
                })
                break

            payload = _json(response)
            if not isinstance(payload, list):
                result['errors'].append({
                    'url': endpoint, 'kind': 'bad_payload',
                    'detail': f'page {page} did not return a JSON array of items',
                })
                break
            if not payload:
                break

            for raw in payload:
                if not isinstance(raw, dict):
                    continue
                if len(result['items']) >= max_items:
                    break
                result['items'].append(_normalise(
                    session, root, raw, item_type, author_cache, timeout, headers))

            if total_pages is None:
                total_pages = _total_pages(response)
            if total_pages and page >= total_pages:
                break
            if not total_pages and len(payload) < per_page:
                break
            page += 1

    return result


def _total_pages(response):
    headers = getattr(response, 'headers', None) or {}
    try:
        return int(headers.get('X-WP-TotalPages') or 0)
    except (TypeError, ValueError):
        return 0


def _singular(item_type):
    """'posts' -> 'post': only a fallback, items carry their own `type`."""
    text = str(item_type or '')
    return text[:-1] if text.endswith('s') else text


def _rendered(value):
    """'title'/'content'/'excerpt' are {'rendered': str} in the REST payload."""
    if isinstance(value, dict):
        value = value.get('rendered', '')
    return value if isinstance(value, str) else ''


def _normalise(session, root, raw, requested_type, author_cache, timeout, headers):
    """One REST payload item as the flat dict the rest of this module uses."""
    content_html = _rendered(raw.get('content'))
    author_id = raw.get('author')
    try:
        author_id = int(author_id) if author_id is not None else None
    except (TypeError, ValueError):
        author_id = None

    raw_fields = {key: value for key, value in raw.items() if key not in _CORE_KEYS}

    return {
        'id': raw.get('id'),
        'type': raw.get('type') or _singular(requested_type),
        'slug': raw.get('slug') or '',
        'link': raw.get('link') or '',
        'status': raw.get('status') or '',
        'title': _plain_text(_rendered(raw.get('title')), collapse=False),
        'excerpt': _plain_text(_rendered(raw.get('excerpt'))),
        'content_html': content_html,
        'content_text': _plain_text(content_html),
        'modified_gmt': raw.get('modified_gmt') or '',
        'date_gmt': raw.get('date_gmt') or '',
        'author_id': author_id,
        'author_name': _author_name(session, root, raw, author_id, author_cache,
                                    timeout, headers),
        'seo': seo_fields(raw),
        'raw_fields': raw_fields,
    }


def _author_name(session, root, raw, author_id, cache, timeout, headers):
    """Author display name: from `_embedded` when `_embed` was honoured,
    otherwise one cached GET of /wp/v2/users/<id>."""
    embedded = raw.get('_embedded')
    if isinstance(embedded, dict):
        for author in embedded.get('author') or []:
            if isinstance(author, dict) and author.get('name'):
                name = str(author['name'])
                if author_id is not None:
                    cache[author_id] = name
                return name

    if author_id is None:
        return ''
    if author_id in cache:
        return cache[author_id]

    cache[author_id] = ''  # cache the miss too: never ask twice
    try:
        response = _get(session, f'{root}/users/{author_id}', timeout, headers=headers)
    except Exception:
        return ''
    if _status(response) != 200:
        return ''
    payload = _json(response)
    if isinstance(payload, dict) and payload.get('name'):
        cache[author_id] = str(payload['name'])
    return cache[author_id]


# --------------------------------------------------------------------------
# SEO plugin fields
# --------------------------------------------------------------------------
#
# Every plugin stores its title and description somewhere different, and most
# of them expose it twice: once in a rendered block the REST API adds, and once
# as registered post meta. These are the keys read, in order of precedence:
#
#   Yoast SEO      yoast_head_json.title / .description
#                  yoast_head_json.og_title / .og_description  (fallback)
#                  meta._yoast_wpseo_title / meta._yoast_wpseo_metadesc
#   Rank Math      rank_math_title / rank_math_description  (top level)
#                  meta.rank_math_title / meta.rank_math_description
#   AIOSEO         _aioseo_title / _aioseo_description       (top level)
#                  meta._aioseo_title / meta._aioseo_description
#   SEOPress       meta._seopress_titles_title / meta._seopress_titles_desc
#   Generic        meta.seo_title / meta.seo_description
#
# The first key that holds a non-empty string wins; nothing is inferred from
# the post title here (the fallback to it happens in the length check, which
# says which field it used).

_SEO_TITLE_KEYS = (
    ('yoast_head_json', 'title'),
    ('yoast_head_json', 'og_title'),
    ('meta', '_yoast_wpseo_title'),
    ('rank_math_title',),
    ('meta', 'rank_math_title'),
    ('_aioseo_title',),
    ('meta', '_aioseo_title'),
    ('meta', '_seopress_titles_title'),
    ('meta', 'seo_title'),
)

_SEO_DESCRIPTION_KEYS = (
    ('yoast_head_json', 'description'),
    ('yoast_head_json', 'og_description'),
    ('meta', '_yoast_wpseo_metadesc'),
    ('rank_math_description',),
    ('meta', 'rank_math_description'),
    ('_aioseo_description',),
    ('meta', '_aioseo_description'),
    ('meta', '_seopress_titles_desc'),
    ('meta', 'seo_description'),
)


# The `meta` keys above, so the custom-field scan does not report the same
# value twice - once as seo.title/seo.description and once as meta.<key>.
_SEO_META_KEYS = frozenset(
    path[1] for path in _SEO_TITLE_KEYS + _SEO_DESCRIPTION_KEYS
    if len(path) == 2 and path[0] == 'meta')


def _dig(raw, path):
    node = raw
    for key in path:
        if not isinstance(node, dict):
            return ''
        node = node.get(key)
    if isinstance(node, list) and node:
        node = node[0]
    return node if isinstance(node, str) else ''


def seo_fields(raw):
    """SEO title/description from whichever plugin the site uses.

    See the comment above for every key that is read, and in what order.
    """
    title = ''
    for path in _SEO_TITLE_KEYS:
        title = html_module.unescape(_dig(raw, path))
        if title.strip():
            break
        title = ''

    description = ''
    for path in _SEO_DESCRIPTION_KEYS:
        description = html_module.unescape(_dig(raw, path))
        if description.strip():
            break
        description = ''

    return {'title': title, 'description': description}


# --------------------------------------------------------------------------
# HTML helpers
# --------------------------------------------------------------------------

def _plain_text(markup, collapse=True):
    """Visible text of a fragment of CMS HTML.

    `collapse` is False for the title, whose whitespace is the thing the stray
    whitespace check is looking at - collapsing it first would hide every
    doubled space the CMS holds.
    """
    if not markup:
        return ''
    separator = ' ' if collapse else ''
    text = None
    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(markup, 'html.parser')
            for node in soup(['script', 'style']):
                node.decompose()
            text = soup.get_text(separator)  # bs4 has already unescaped entities
        except Exception:
            text = None
    if text is None:
        text = html_module.unescape(_TAG_RE.sub(separator, markup))
    return _WS_RUN_RE.sub(' ', text).strip() if collapse else text


def content_json_ld(content_html):
    """Every JSON-LD block embedded in a CMS item's content, parsed.

    Returns a list of `{'data': parsed, 'raw': text}`; a block that does not
    parse is skipped rather than reported (the live page's own JSON-LD parse
    check owns that finding).
    """
    if not content_html:
        return []

    raw_blocks = []
    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(content_html, 'html.parser')
            for script in soup.find_all('script', attrs={'type': 'application/ld+json'}):
                raw_blocks.append(script.string or script.get_text() or '')
        except Exception:
            raw_blocks = []
    if not raw_blocks:
        raw_blocks = _JSONLD_SCRIPT_RE.findall(content_html)

    blocks = []
    for text in raw_blocks:
        text = html_module.unescape(str(text or '')).strip()
        if not text:
            continue
        try:
            blocks.append({'data': json.loads(text), 'raw': text})
        except (ValueError, TypeError):
            continue
    return blocks


def jsonld_types(node, found=None):
    """Every @type in a JSON-LD value, walking lists and @graph."""
    if found is None:
        found = set()
    if isinstance(node, list):
        for child in node:
            jsonld_types(child, found)
    elif isinstance(node, dict):
        value = node.get('@type')
        if isinstance(value, str):
            found.add(value.strip().lower())
        elif isinstance(value, list):
            for one in value:
                if isinstance(one, str):
                    found.add(one.strip().lower())
        for key in ('@graph', 'mainEntity', 'hasPart', 'itemListElement'):
            if key in node:
                jsonld_types(node[key], found)
    return found


# --------------------------------------------------------------------------
# the comparison
# --------------------------------------------------------------------------

def _index_pages(pages):
    """Crawled pages keyed by `compare_key`, preferring a 200 over anything else."""
    if isinstance(pages, dict):
        pages = list(pages.values())
    by_key = {}
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        key = compare_key(page.get('url') or '')
        if not key:
            continue
        known = by_key.get(key)
        if known is None:
            by_key[key] = page
        elif int(known.get('status_code') or 0) != 200 and int(page.get('status_code') or 0) == 200:
            by_key[key] = page
    return by_key


def _emit(issues, base_url, check, items, cfg):
    """One issue per item while the list is short, one grouped issue above it.

    `items` are dicts with 'url', 'details' and optionally 'offending' and
    'evidence' - the same contract as sitemap_audit's `_emit`.
    """
    if not items:
        return
    severity, name, how_to_fix, group_details = CHECKS[check]
    items = sorted(items, key=lambda item: (item['url'], item.get('offending', '')))

    if len(items) <= cfg['group_threshold']:
        for item in items:
            issues.append(make_issue(
                url=item['url'], check=check, severity=severity,
                category=CATEGORY, issue=name, details=item['details'],
                offending_value=item.get('offending', ''),
                evidence=item.get('evidence') or {},
                how_to_fix=how_to_fix,
            ))
        return

    related = []
    for item in items:
        if item['url'] not in related:
            related.append(item['url'])
    issues.append(make_issue(
        url=base_url, check=check, severity=severity, category=CATEGORY,
        issue=name, details=group_details.format(count=len(items)),
        offending_value='',
        evidence={'found': str(len(items)),
                  'related_urls': related[:cfg['max_related_urls']]},
        how_to_fix=how_to_fix,
    ))


def _origin(url):
    parsed = urlparse(str(url or ''))
    if parsed.scheme and parsed.netloc:
        return f'{parsed.scheme}://{parsed.netloc}'
    return ''


def _is_published(item):
    return (item.get('status') or 'publish') == 'publish'


def _snippet(text, limit=200):
    text = str(text or '')
    return text if len(text) <= limit else text[:limit] + '...'


def compare(items, pages, config=None):
    """Every CMS-vs-live check, as a list of issues from `make_issue`.

    `items` come from `fetch_items`, `pages` is the crawl result (a list of
    page dicts, or a {url: page} mapping). `config` overrides any key of
    DEFAULTS; `config['sitemap_urls']` enables the sitemap check and
    `config['expected_author']` the author check - both are skipped when unset.
    """
    cfg = _config(config)
    items = [item for item in (items or []) if isinstance(item, dict)]
    base_url = cfg['base_url'] or _origin(next((i.get('link') for i in items if i.get('link')), ''))

    pages_by_key = _index_pages(pages)
    sitemap_keys = None
    if cfg['sitemap_urls'] is not None:
        sitemap_keys = {compare_key(url) for url in cfg['sitemap_urls'] if url}

    expected = cfg['expected_author']
    if isinstance(expected, str):
        expected = [expected]
    expected_lower = {str(name).strip().lower() for name in (expected or ()) if str(name).strip()}

    buckets = defaultdict(list)
    for item in items:
        if not _is_published(item):
            continue
        link = item.get('link') or ''
        page = pages_by_key.get(compare_key(link)) if link else None

        _check_lengths(buckets, item, link, cfg)
        _check_placeholders(buckets, item, link, cfg)
        _check_whitespace(buckets, item, link, cfg)
        _check_author(buckets, item, link, expected_lower)
        _check_coverage(buckets, item, link, page, sitemap_keys)
        if page is not None:
            _check_structured_data(buckets, item, link, page)

    issues = []
    for check in CHECKS:
        _emit(issues, base_url, check, buckets.get(check, []), cfg)
    return issues


def _check_structured_data(buckets, item, link, page):
    """JSON-LD in the CMS content that the live page does not serve."""
    blocks = content_json_ld(item.get('content_html'))
    if not blocks:
        return
    if int(page.get('status_code') or 0) not in (0, 200):
        return

    live_types = set()
    for block in page.get('json_ld') or []:
        live_types |= jsonld_types(block)
    for block in page.get('schema_org') or []:
        value = block.get('type') if isinstance(block, dict) else None
        if isinstance(value, str) and value:
            live_types.add(value.rstrip('/').rsplit('/', 1)[-1].strip().lower())

    for block in blocks:
        types = jsonld_types(block['data'])
        if types and (types & live_types):
            continue
        label = ', '.join(sorted(types)) or 'untyped'
        buckets['cms_structured_data_stripped'].append({
            'url': link,
            'offending': label,
            'details': (f'The CMS content contains a JSON-LD block ({label}) that the '
                        f'live page does not serve - the front end strips it.'),
            'evidence': {'snippet': _snippet(block['raw'], 400),
                         'expected': f'JSON-LD of type: {label}',
                         'found': ('no JSON-LD on the live page' if not live_types
                                   else 'live JSON-LD types: ' + ', '.join(sorted(live_types)))},
        })


def _check_lengths(buckets, item, link, cfg):
    """SEO title/description beyond the configured character limits.

    With no SEO plugin value the post title is measured instead, since that is
    what the template will print; the issue says which field it used.
    """
    seo = item.get('seo') or {}

    title = (seo.get('title') or '').strip()
    source = 'SEO title'
    if not title:
        title, source = (item.get('title') or '').strip(), 'post title (no SEO title set)'
    limit = int(cfg['seo_title_max'])
    if title and len(title) > limit:
        buckets['cms_seo_title_too_long'].append({
            'url': link, 'offending': title,
            'details': (f'The {source} is {len(title)} characters, beyond the '
                        f'{limit}-character limit.'),
            'evidence': {'snippet': _snippet(title),
                         'expected': f'{limit} characters or fewer',
                         'found': f'{len(title)} characters'},
        })

    description = (seo.get('description') or '').strip()
    limit = int(cfg['seo_description_max'])
    if description and len(description) > limit:
        buckets['cms_seo_description_too_long'].append({
            'url': link, 'offending': description,
            'details': (f'The SEO description is {len(description)} characters, beyond '
                        f'the {limit}-character limit.'),
            'evidence': {'snippet': _snippet(description),
                         'expected': f'{limit} characters or fewer',
                         'found': f'{len(description)} characters'},
        })


def _field_values(item, cfg):
    """(field name, value) for every field a placeholder may hide in.

    The named fields, plus every string in the item's custom fields - the
    registered `meta` and an `acf` group, both of which land in `raw_fields`.
    The plugin meta keys that already fed `seo` are skipped, so one value is
    never reported twice.
    """
    seo = item.get('seo') or {}
    known = {
        'title': item.get('title') or '',
        'excerpt': item.get('excerpt') or '',
        'seo.title': seo.get('title') or '',
        'seo.description': seo.get('description') or '',
    }
    for name in cfg['placeholder_fields']:
        if name in known:
            yield name, known[name]

    raw_fields = item.get('raw_fields') or {}
    for group in ('meta', 'acf'):
        values = raw_fields.get(group)
        if not isinstance(values, dict):
            continue
        for key, value in sorted(values.items()):
            if group == 'meta' and key in _SEO_META_KEYS:
                continue  # already reported as seo.title / seo.description
            if isinstance(value, str):
                yield f'{group}.{key}', value


def _check_placeholders(buckets, item, link, cfg):
    """Fields still holding 'None', 'TBD', 'Lorem ipsum', or only whitespace.

    An empty value counts only for `required_fields`: elsewhere an unset
    optional field is normal, while a value made of nothing but whitespace is
    always a defect.
    """
    tokens = {str(value).strip().lower() for value in cfg['placeholder_values']}
    prefixes = tuple(str(value).strip().lower() for value in cfg['placeholder_prefixes'])
    required = set(cfg['required_fields'])

    for name, value in _field_values(item, cfg):
        text = str(value)
        stripped = text.strip()
        reason = ''
        if not stripped:
            if not text and name not in required:
                continue
            reason = 'is empty' if not text else 'contains nothing but whitespace'
        elif stripped.lower() in tokens or stripped.lower().startswith(prefixes):
            reason = f'is the placeholder value {stripped!r}'
        if not reason:
            continue
        buckets['cms_field_placeholder'].append({
            'url': link, 'offending': f'{name}={stripped}',
            'details': f'The CMS field {name} {reason}.',
            'evidence': {'selector': name, 'snippet': _snippet(text),
                         'found': repr(text), 'expected': 'real content, or an unset field'},
        })


def _check_whitespace(buckets, item, link, cfg):
    """Leading, trailing or doubled whitespace in a title or SEO field."""
    seo = item.get('seo') or {}
    values = {
        'title': item.get('title') or '',
        'seo.title': seo.get('title') or '',
        'seo.description': seo.get('description') or '',
    }
    for name in cfg['whitespace_fields']:
        text = values.get(name) or ''
        if not text.strip():
            continue
        problems = []
        if text != text.strip():
            problems.append('leading or trailing whitespace')
        if _DOUBLED_WS_RE.search(text.strip()):
            problems.append('a doubled space')
        if not problems:
            continue
        buckets['cms_stray_whitespace'].append({
            'url': link, 'offending': f'{name}={text}',
            'details': f'The CMS field {name} contains ' + ' and '.join(problems) + '.',
            'evidence': {'selector': name, 'snippet': _snippet(text),
                         'found': repr(text),
                         'expected': repr(_WS_RUN_RE.sub(' ', text).strip())},
        })


def _check_author(buckets, item, link, expected_lower):
    """Author differs from the one the site expects."""
    if not expected_lower:
        return
    name = (item.get('author_name') or '').strip()
    if name.lower() in expected_lower:
        return
    buckets['cms_author_unexpected'].append({
        'url': link, 'offending': name,
        'details': (f'The item is attributed to {name or "no author"}, not to '
                    f'the expected author.'),
        'evidence': {'expected': ', '.join(sorted(expected_lower)),
                     'found': name or '(none)'},
    })


def _check_coverage(buckets, item, link, page, sitemap_keys):
    """Published items missing from the sitemap, or never crawled."""
    if not link:
        return
    key = compare_key(link)

    if sitemap_keys is not None and key not in sitemap_keys:
        buckets['cms_published_not_in_sitemap'].append({
            'url': link, 'offending': link,
            'details': 'This item is published in the CMS but is not listed in the sitemap.',
            'evidence': {'found': 'published in the CMS, absent from the sitemap'},
        })

    if page is None:
        buckets['cms_not_crawled'].append({
            'url': link, 'offending': link,
            'details': 'This item is published in the CMS but the crawl never reached it.',
            'evidence': {'found': 'published in the CMS, absent from the crawl'},
        })


# --------------------------------------------------------------------------
# the one call the crawler makes
# --------------------------------------------------------------------------

def audit_site(session, base_url, pages, config=None, sitemap_urls=None):
    """Discover, read and compare in one call - what the crawler runs.

    Returns `{'available': bool, 'items': [...], 'errors': [...],
    'issues': [...]}`, plus 'root' and 'detail' from `discover` for the UI.
    On a site that is not WordPress this costs one request and returns
    `available: False` with no items and no issues.

    `sitemap_urls` enables the "published but not in the sitemap" check; leave
    it None (the default) and that check is skipped entirely.
    """
    cfg = _config(config)
    headers = auth_headers(cfg['auth_header'])
    report = {'available': False, 'items': [], 'errors': [], 'issues': []}

    found = discover(session, base_url, timeout=cfg['timeout'], headers=headers)
    report['root'] = found['root']
    report['detail'] = found['detail']
    if not found['available']:
        return report

    report['available'] = True
    fetched = fetch_items(session, found['root'], types=cfg['types'],
                          per_page=cfg['per_page'], max_items=cfg['max_items'],
                          timeout=cfg['timeout'], headers=headers)
    report['items'] = fetched['items']
    report['errors'] = fetched['errors']

    compare_config = dict(config or {})
    compare_config['base_url'] = cfg['base_url'] or base_url
    if sitemap_urls is not None:
        compare_config['sitemap_urls'] = sitemap_urls
    report['issues'] = compare(fetched['items'], pages, config=compare_config)
    return report
