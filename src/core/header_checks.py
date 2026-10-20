"""Response headers, mixed content and pagination, over pages a crawl collected.

Nothing here fetches anything. The headers are the ones the crawl already
recorded (`page['response_headers']`, filled by both the requests path and the
rendered path in `src/crawler.py`), and the markup facts are extracted once
while the page is parsed, by `extract_page_resources` in this module.

    header_issues(pages, links, base_url, config=None) -> [issue, ...]

Three groups of checks:

  security headers  HSTS, CSP, X-Content-Type-Options, X-Frame-Options,
                    Referrer-Policy, Permissions-Policy, Set-Cookie flags and
                    version disclosure. Reported once per page per problem.
  mixed content     http:// subresources on an https page. Scripts, styles,
                    iframes and objects are blocked outright by browsers
                    (`mixed_content_active`); images and media are downgraded
                    with a warning (`mixed_content_passive`). A plain link to
                    an http site is not mixed content and is not reported.
  pagination        rel=next / rel=prev chains: broken targets, one-way links,
                    page 2+ canonicalising to page 1, and pages nothing but
                    the pagination links at.

A page is only judged when the crawl actually recorded headers for it. Without
that guard a crawl loaded from the database - where headers are not stored -
would report every page as missing every header, which is the failure mode
this checklist exists to avoid.

Grouping follows `src/core/technical_checks.py`: up to `group_threshold` (5)
offenders are reported one issue per URL; beyond that they collapse into a
single issue carrying the count and the URLs in `evidence.related_urls`.
"""

import re
from collections import defaultdict
from urllib.parse import urljoin, urlsplit, urlunsplit

from src.utils.issue_factory import make_issue
from src.utils.url_params import DEFAULT_TRACKING_PARAMS, strip_tracking_params

CATEGORY = 'Security'
PAGINATION_CATEGORY = 'Technical'

DEFAULTS = {
    'group_threshold': 5,
    'max_related_urls': 50,
    # Six months. Shorter than this and a browser that has not seen the site
    # recently falls back to http, which is what HSTS exists to prevent; it is
    # also the floor the browser preload list accepts.
    'hsts_min_max_age': 180 * 24 * 60 * 60,
    # How far to walk a rel=prev chain looking for its first page. Long enough
    # for any real paginated set, short enough that a cycle cannot hang us.
    'max_pagination_walk': 100,
    'tracking_params': DEFAULT_TRACKING_PARAMS,
    'skip_checks': (),
}

# Browsers refuse to load these over http on an https page, so the page is
# broken, not merely insecure.
ACTIVE_KINDS = ('script', 'stylesheet', 'iframe', 'object')

# A version number in a Server or X-Powered-By header names the exact build to
# look up in a vulnerability database. "nginx" alone does not, and is ignored.
_VERSION_RE = re.compile(r'(?:/|\s|^)v?\d+\.\d+')

_MAX_AGE_RE = re.compile(r'max-age\s*=\s*"?(\d+)', re.IGNORECASE)

# Set-Cookie headers arrive joined with ", " when the transport collapses
# repeated headers. Split only where a new "name=" begins, so the comma inside
# "Expires=Wed, 21 Oct 2015 07:28:00 GMT" does not start a second cookie.
_COOKIE_SPLIT_RE = re.compile(r',\s*(?=[^\s;=,]+=)')


def header_issues(pages, links, base_url, config=None):
    """Every header, mixed-content and pagination finding for one crawl."""
    cfg = dict(DEFAULTS)
    cfg.update(config or {})

    pages = [page for page in (pages or []) if isinstance(page, dict) and page.get('url')]
    links = [link for link in (links or []) if isinstance(link, dict)]
    served = [page for page in pages if _served(page)]
    judgeable = [page for page in served if _headers(page) is not None]

    issues = []

    def emit(check, severity, name, items, group_details, how_to_fix):
        """One issue per offender, or one grouped issue when there are many."""
        if not items or check in cfg['skip_checks']:
            return
        threshold = cfg['group_threshold']
        if len(items) <= threshold:
            for item in items:
                issues.append(make_issue(
                    item['url'], check, severity, item.get('category', CATEGORY), name,
                    item['details'], offending_value=item.get('offending', ''),
                    evidence=item.get('evidence'), how_to_fix=how_to_fix))
            return
        urls = [item['url'] for item in items][:cfg['max_related_urls']]
        issues.append(make_issue(
            base_url or items[0]['url'], check, severity,
            items[0].get('category', CATEGORY), name,
            group_details.format(count=len(items)),
            evidence={'found': str(len(items)), 'related_urls': urls},
            how_to_fix=how_to_fix))

    _security_header_checks(judgeable, cfg, emit)
    _cookie_checks(judgeable, cfg, emit)
    _mixed_content_checks(served, cfg, emit)
    _pagination_checks(pages, served, links, cfg, emit)

    return issues


# ---------------------------------------------------------------------------
# extraction - run once per page while its markup is still parsed
# ---------------------------------------------------------------------------

def extract_page_resources(soup, base_url, result):
    """Insecure subresources, insecure form targets and pagination links.

    Only the http:// subresources are kept. Holding every script, stylesheet
    and image URL for every page would double a large crawl's memory for data
    no check reads, and the secure ones are exactly the ones nothing asks
    about.
    """
    insecure = []
    seen = set()

    def record(kind, raw):
        url = _absolute(raw, base_url)
        if not url or not url.lower().startswith('http://'):
            return
        key = (kind, url)
        if key in seen:
            return
        seen.add(key)
        insecure.append({'url': url, 'kind': kind})

    for tag in soup.find_all('script', src=True):
        record('script', tag.get('src'))
    for tag in soup.find_all('link', href=True):
        rel = [value.lower() for value in _rel_values(tag)]
        if 'stylesheet' in rel:
            record('stylesheet', tag.get('href'))
    for tag in soup.find_all(['iframe', 'frame']):
        record('iframe', tag.get('src'))
    for tag in soup.find_all('embed'):
        record('object', tag.get('src'))
    for tag in soup.find_all('object'):
        record('object', tag.get('data'))
    for tag in soup.find_all('img'):
        record('image', tag.get('src'))
        for candidate in _srcset_urls(tag.get('srcset')):
            record('image', candidate)
    for tag in soup.find_all('input', attrs={'type': 'image'}):
        record('image', tag.get('src'))
    for tag in soup.find_all(['audio', 'video', 'track']):
        record('media', tag.get('src'))
    for tag in soup.find_all('video'):
        record('media', tag.get('poster'))
    for tag in soup.find_all('source'):
        # A <source> inside <picture> is an image; inside <audio>/<video> it is
        # media. Browsers block neither, so the distinction only changes the
        # wording, but the wording is what a reader acts on.
        parent = tag.parent.name if tag.parent is not None else ''
        kind = 'image' if parent == 'picture' else 'media'
        record(kind, tag.get('src'))
        for candidate in _srcset_urls(tag.get('srcset')):
            record(kind, candidate)

    result['insecure_resources'] = insecure

    actions = []
    for form in soup.find_all('form'):
        action = _absolute(form.get('action'), base_url)
        if action and action.lower().startswith('http://') and action not in actions:
            actions.append(action)
    result['insecure_form_actions'] = actions

    result['pagination'] = _extract_pagination(soup, base_url)


def _extract_pagination(soup, base_url):
    """{'next': url, 'prev': url} from rel=next / rel=prev, <link> winning.

    `<a rel="next">` is how most templates mark pagination, so both are read;
    a `<link>` in the head is the more deliberate declaration and wins when a
    page carries both.
    """
    found = {}
    for tag_name in ('a', 'link'):
        for tag in soup.find_all(tag_name, href=True):
            rel = {value.lower() for value in _rel_values(tag)}
            target = _absolute(tag.get('href'), base_url)
            if not target:
                continue
            if 'next' in rel:
                found['next'] = target
            if 'prev' in rel or 'previous' in rel:
                found['prev'] = target
    return found


# ---------------------------------------------------------------------------
# security response headers
# ---------------------------------------------------------------------------

def _security_header_checks(pages, cfg, emit):
    no_hsts, short_hsts, no_csp, csp_inline, csp_eval = [], [], [], [], []
    no_nosniff, no_frame_options, no_referrer, bad_referrer = [], [], [], []
    no_permissions, disclosed = [], []

    for page in pages:
        headers = _headers(page)
        url = _final_url(page)
        https = urlsplit(url).scheme == 'https'

        csp = _first_value(headers.get('content-security-policy'))
        directives = _csp_directives(csp)

        if https:
            hsts = _first_value(headers.get('strict-transport-security'))
            if not hsts:
                no_hsts.append(_item(
                    url, 'strict-transport-security',
                    'This https page is served without a Strict-Transport-Security header.',
                    {'expected': 'Strict-Transport-Security: max-age=31536000; includeSubDomains'}))
            else:
                max_age = _max_age(hsts)
                if max_age is None:
                    short_hsts.append(_item(
                        url, hsts[:80],
                        'The Strict-Transport-Security header names no max-age, so browsers '
                        'ignore it.',
                        {'found': hsts[:200], 'expected': 'max-age=31536000'}))
                elif max_age < cfg['hsts_min_max_age']:
                    short_hsts.append(_item(
                        url, str(max_age),
                        f'Strict-Transport-Security lasts {max_age // 86400} day(s), less than '
                        f'the {cfg["hsts_min_max_age"] // 86400} days browsers expect.',
                        {'found': f'max-age={max_age}',
                         'expected': f'max-age={cfg["hsts_min_max_age"]} or more'}))

        if not csp:
            report_only = _first_value(headers.get('content-security-policy-report-only'))
            note = (' A report-only policy is set, but it blocks nothing.'
                    if report_only else '')
            no_csp.append(_item(
                url, 'content-security-policy',
                f'This page is served without a Content-Security-Policy header.{note}',
                {'expected': 'Content-Security-Policy with at least a default-src'}))
        else:
            sources = directives.get('script-src') or directives.get('default-src') or []
            # A nonce, a hash or strict-dynamic makes 'unsafe-inline' inert in
            # every browser that implements CSP2, so a policy carrying both is
            # not actually permissive and reporting it would be a false alarm.
            neutralised = any(
                source.startswith(('nonce-', 'sha256-', 'sha384-', 'sha512-'))
                or source == 'strict-dynamic'
                for source in sources)
            if 'unsafe-inline' in sources and not neutralised:
                csp_inline.append(_item(
                    url, 'unsafe-inline',
                    "The Content-Security-Policy allows 'unsafe-inline' scripts, which is most "
                    'of what the policy was meant to stop.',
                    {'found': csp[:200], 'expected': "a nonce or hash instead of 'unsafe-inline'"}))
            if 'unsafe-eval' in sources:
                csp_eval.append(_item(
                    url, 'unsafe-eval',
                    "The Content-Security-Policy allows 'unsafe-eval', so any injected string "
                    'can be executed as code.',
                    {'found': csp[:200], 'expected': "a policy without 'unsafe-eval'"}))

        nosniff = _first_value(headers.get('x-content-type-options')).lower()
        if 'nosniff' not in nosniff:
            no_nosniff.append(_item(
                url, 'x-content-type-options',
                'This page is served without X-Content-Type-Options: nosniff, so browsers may '
                'guess a response type the server did not declare.',
                {'found': nosniff or '(absent)', 'expected': 'nosniff'}))

        # frame-ancestors is the modern form of X-Frame-Options and overrides
        # it, so a page with a correct CSP is not also missing XFO.
        if 'frame-ancestors' not in directives and not _first_value(headers.get('x-frame-options')):
            no_frame_options.append(_item(
                url, 'x-frame-options',
                'Nothing stops this page being framed: no X-Frame-Options header and no CSP '
                'frame-ancestors directive.',
                {'expected': "X-Frame-Options: SAMEORIGIN, or CSP frame-ancestors 'self'"}))

        referrer = _referrer_policy(page, headers)
        if not referrer:
            no_referrer.append(_item(
                url, 'referrer-policy',
                'This page sets no Referrer-Policy, so what gets sent to other sites is left '
                "to the browser's default.",
                {'expected': 'Referrer-Policy: strict-origin-when-cross-origin'}))
        elif 'unsafe-url' in [token.strip().lower() for token in referrer.split(',')]:
            bad_referrer.append(_item(
                url, referrer[:80],
                'The Referrer-Policy is unsafe-url, which leaks the full URL of every page to '
                'every site it links to.',
                {'found': referrer[:200], 'expected': 'strict-origin-when-cross-origin'}))

        if not _first_value(headers.get('permissions-policy')) \
                and not _first_value(headers.get('feature-policy')):
            no_permissions.append(_item(
                url, 'permissions-policy',
                'This page sets no Permissions-Policy, so embedded content inherits access to '
                'the camera, microphone and geolocation.',
                {'expected': 'Permissions-Policy: camera=(), microphone=(), geolocation=()'}))

        versions = [f'{name}: {value}'
                    for name, value in (('Server', _first_value(headers.get('server'))),
                                        ('X-Powered-By', _first_value(headers.get('x-powered-by'))))
                    if value and _VERSION_RE.search(value)]
        if versions:
            disclosed.append(_item(
                url, ' '.join(versions)[:120],
                f'The response names its own software version: {"; ".join(versions)}.',
                {'found': '; '.join(versions)[:200],
                 'expected': 'the product name without a version, or no header at all'}))

    emit('missing_hsts', 'warning', 'No HSTS Header', no_hsts,
         '{count} https pages are served without Strict-Transport-Security.',
         'Send Strict-Transport-Security: max-age=31536000; includeSubDomains on https responses.')
    emit('hsts_max_age_too_short', 'info', 'HSTS Expires Too Soon', short_hsts,
         '{count} pages set an HSTS lifetime shorter than six months.',
         'Raise max-age to at least 15552000 seconds (six months).')
    emit('missing_csp', 'info', 'No Content-Security-Policy', no_csp,
         '{count} pages are served without a Content-Security-Policy.',
         'Send a Content-Security-Policy header, starting with a report-only policy if the '
         'site has never had one.')
    emit('csp_unsafe_inline', 'warning', "CSP Allows 'unsafe-inline'", csp_inline,
         "{count} pages allow 'unsafe-inline' scripts in their Content-Security-Policy.",
         "Replace 'unsafe-inline' with a per-response nonce or a hash for each inline script.")
    emit('csp_unsafe_eval', 'warning', "CSP Allows 'unsafe-eval'", csp_eval,
         "{count} pages allow 'unsafe-eval' in their Content-Security-Policy.",
         "Remove 'unsafe-eval' and replace the code that needs it.")
    emit('missing_x_content_type_options', 'warning', 'No X-Content-Type-Options', no_nosniff,
         '{count} pages are served without X-Content-Type-Options: nosniff.',
         'Send X-Content-Type-Options: nosniff on every response.')
    emit('missing_x_frame_options', 'warning', 'Page Can Be Framed', no_frame_options,
         '{count} pages can be framed by any site.',
         "Send X-Frame-Options: SAMEORIGIN, or a CSP frame-ancestors 'self' directive.")
    emit('missing_referrer_policy', 'info', 'No Referrer-Policy', no_referrer,
         '{count} pages set no Referrer-Policy.',
         'Send Referrer-Policy: strict-origin-when-cross-origin.')
    emit('insecure_referrer_policy', 'warning', 'Referrer-Policy Leaks Full URLs', bad_referrer,
         '{count} pages set Referrer-Policy: unsafe-url.',
         'Replace unsafe-url with strict-origin-when-cross-origin.')
    emit('missing_permissions_policy', 'info', 'No Permissions-Policy', no_permissions,
         '{count} pages set no Permissions-Policy.',
         'Send a Permissions-Policy header denying the browser features the page does not use.')
    emit('server_version_disclosed', 'info', 'Server Version Disclosed', disclosed,
         '{count} pages name their server software version in a response header.',
         'Drop the version from the Server header and remove X-Powered-By entirely.')


# ---------------------------------------------------------------------------
# cookies
# ---------------------------------------------------------------------------

def _cookie_checks(pages, cfg, emit):
    no_secure, no_httponly, no_samesite = [], [], []

    for page in pages:
        headers = _headers(page)
        url = _final_url(page)
        https = urlsplit(url).scheme == 'https'

        insecure, scriptable, cross_site = [], [], []
        for raw in _set_cookies(headers):
            name, attrs = _parse_cookie(raw)
            if not name:
                continue
            # A cookie without Secure can only leak over a plain-http request,
            # so judging it on an http page says nothing the page's own scheme
            # has not already said.
            if https and 'secure' not in attrs:
                insecure.append(name)
            if 'httponly' not in attrs:
                scriptable.append(name)
            if 'samesite' not in attrs:
                cross_site.append(name)

        if insecure:
            no_secure.append(_cookie_item(
                url, insecure, 'without the Secure flag',
                'so a browser will send them over plain http', 'Secure'))
        if scriptable:
            no_httponly.append(_cookie_item(
                url, scriptable, 'without HttpOnly',
                'so any script on the page can read them', 'HttpOnly'))
        if cross_site:
            no_samesite.append(_cookie_item(
                url, cross_site, 'without a SameSite attribute',
                'so the browser picks the default for you', 'SameSite=Lax'))

    emit('cookie_without_secure_flag', 'warning', 'Cookie Without the Secure Flag', no_secure,
         '{count} pages set a cookie without the Secure flag.',
         'Add Secure to every cookie the site sets over https.')
    emit('cookie_without_httponly', 'warning', 'Cookie Readable by Script', no_httponly,
         '{count} pages set a cookie without HttpOnly.',
         'Add HttpOnly to every cookie that no script needs to read.')
    emit('cookie_without_samesite', 'info', 'Cookie Without SameSite', no_samesite,
         '{count} pages set a cookie with no SameSite attribute.',
         'Add SameSite=Lax, or SameSite=None; Secure for a cookie that must cross sites.')


def _cookie_item(url, names, phrase, consequence, expected):
    listed = ', '.join(sorted(set(names)))
    count = len(set(names))
    return _item(
        url, listed[:120],
        f'{count} cookie(s) are set {phrase} ({listed}), {consequence}.',
        {'found': listed[:200], 'expected': expected})


# ---------------------------------------------------------------------------
# mixed content
# ---------------------------------------------------------------------------

def _mixed_content_checks(pages, cfg, emit):
    active, passive, forms = [], [], []

    for page in pages:
        url = _final_url(page)
        resources = page.get('insecure_resources')
        if urlsplit(url).scheme == 'https' and isinstance(resources, list):
            blocked = [entry for entry in resources
                       if isinstance(entry, dict) and entry.get('url')
                       and entry.get('kind') in ACTIVE_KINDS]
            downgraded = [entry for entry in resources
                          if isinstance(entry, dict) and entry.get('url')
                          and entry.get('kind') not in ACTIVE_KINDS]
            if blocked:
                active.append(_mixed_item(
                    url, blocked,
                    'loads over http and is blocked outright by the browser'))
            if downgraded:
                passive.append(_mixed_item(
                    url, downgraded,
                    'loads over http, which costs the page its secure padlock'))

        actions = page.get('insecure_form_actions')
        if isinstance(actions, list) and actions:
            unique = sorted({action for action in actions if action})
            forms.append(_item(
                url, ' '.join(unique[:5])[:200],
                f'{len(unique)} form(s) on this page post to an http:// address, so whatever '
                f'is typed in travels in clear text.',
                {'found': unique[0], 'expected': 'an https:// form action',
                 'related_urls': unique[:10]}))

    emit('mixed_content_active', 'error', 'Blocked Mixed Content', active,
         '{count} https pages load scripts, styles or frames over http.',
         'Serve the subresource over https, or load it protocol-relative from an https host.')
    emit('mixed_content_passive', 'warning', 'Insecure Images or Media', passive,
         '{count} https pages load images or media over http.',
         'Serve the image or media file over https.')
    emit('insecure_form_action', 'error', 'Form Posts Over http', forms,
         '{count} pages carry a form that posts to an http:// address.',
         'Point the form action at the https:// address of the same endpoint.')


def _mixed_item(url, entries, consequence):
    urls = sorted({entry['url'] for entry in entries})
    kinds = ', '.join(sorted({entry.get('kind') or 'resource' for entry in entries}))
    return _item(
        url, ' '.join(urls[:5])[:200],
        f'{len(urls)} subresource(s) on this https page ({kinds}) {consequence}.',
        {'found': urls[0], 'expected': 'https:// for every subresource',
         'related_urls': urls[:10]})


# ---------------------------------------------------------------------------
# pagination
# ---------------------------------------------------------------------------

def _pagination_checks(pages, sources, links, cfg, emit):
    broken, no_return, to_page_one, orphaned = [], [], [], []

    by_key = {}
    next_of, prev_of = {}, {}
    for page in pages:
        key = _key(page['url'], cfg)
        by_key.setdefault(key, page)
        pagination = page.get('pagination')
        if not isinstance(pagination, dict):
            continue
        if pagination.get('next'):
            next_of[key] = _key(pagination['next'], cfg)
        if pagination.get('prev'):
            prev_of[key] = _key(pagination['prev'], cfg)

    # Every URL the crawl saw, crawled or merely linked. A rel=next pointing
    # outside this set is a URL that exists nowhere on the site; one inside it
    # that was never crawled was only held back by a depth or count limit, and
    # calling that broken would flag healthy pagination on every capped crawl.
    known, inbound, any_internal_link = set(by_key), defaultdict(int), False
    for link in links:
        target = link.get('target_url')
        if not target:
            continue
        known.add(_key(target, cfg))
        if link.get('is_internal'):
            any_internal_link = True
            inbound[_key(target, cfg)] += 1
    link_status = _link_statuses(links, cfg)

    for page in sources:
        key = _key(page['url'], cfg)
        pagination = page.get('pagination')
        if not isinstance(pagination, dict) or not pagination:
            continue
        url = page['url']

        target_url = pagination.get('next')
        if target_url:
            target_key = _key(target_url, cfg)
            status = _target_status(target_key, by_key, link_status)
            if status is not None and (status >= 400 or status == 0):
                broken.append(_item(
                    url, target_url,
                    f'rel="next" points at {target_url}, which answers {status}.',
                    {'found': str(status), 'expected': '200',
                     'related_urls': [target_url]}, PAGINATION_CATEGORY))
            elif target_key not in known:
                broken.append(_item(
                    url, target_url,
                    f'rel="next" points at {target_url}, which the crawl never found anywhere '
                    f'on the site.',
                    {'found': target_url, 'expected': 'a URL that exists',
                     'related_urls': [target_url]}, PAGINATION_CATEGORY))

            # Only the crawled target can be asked what it points back at.
            target = by_key.get(target_key)
            if target is not None and prev_of.get(target_key) != key:
                declared = (target.get('pagination') or {}).get('prev') or ''
                no_return.append(_item(
                    target['url'], url,
                    f'{url} says this is the next page, but this page does not name it as '
                    f'rel="prev".',
                    {'found': declared or '(no rel="prev")', 'expected': url,
                     'related_urls': [url]}, PAGINATION_CATEGORY))

        # A first page has no rel="prev", and that is correct, so only a page
        # that declares one is asked about its canonical.
        canonical = (page.get('canonical_url') or '').strip()
        if prev_of.get(key) and canonical:
            canonical_key = _key(canonical, cfg)
            head_key = _chain_head(key, prev_of, cfg['max_pagination_walk'])
            if canonical_key != key and canonical_key == head_key:
                to_page_one.append(_item(
                    url, canonical,
                    f'This is page 2 or later of a set, but it canonicalises to the first page '
                    f'({canonical}), so its own content is never indexed.',
                    {'found': canonical, 'expected': url, 'related_urls': [canonical]},
                    PAGINATION_CATEGORY))

        if any_internal_link and not inbound.get(key):
            orphaned.append(_item(
                url, 'no inbound links',
                'Nothing links to this paginated page: it can only be reached by following '
                'rel="next" or rel="prev".',
                {'found': '0 internal links', 'expected': 'at least one internal link'},
                PAGINATION_CATEGORY))

    emit('pagination_broken_chain', 'warning', 'Broken Pagination Chain', broken,
         '{count} pages point rel="next" at a URL that does not work.',
         'Point rel="next" at the next page that exists, or drop it from the last page.')
    emit('pagination_no_return_link', 'info', 'Pagination Does Not Link Back', no_return,
         '{count} paginated pages do not name the page that points at them as rel="prev".',
         'Add rel="prev" pointing back at the page that names this one as rel="next".')
    emit('pagination_canonical_to_page_one', 'warning', 'Paginated Page Canonicalises to Page 1',
         to_page_one, '{count} paginated pages canonicalise to the first page of their set.',
         'Make each paginated page canonical to itself; Google reads a canonical to page 1 as '
         'a mistake.')
    emit('pagination_orphaned_page', 'info', 'Paginated Page Has No Inbound Links', orphaned,
         '{count} paginated pages are reachable only through the pagination links.',
         'Link the page from a listing, a sitemap or navigation so it does not depend on the '
         'pagination chain.')


def _chain_head(key, prev_of, limit):
    """Walk rel="prev" back to the first page of a set."""
    seen = {key}
    current = key
    for _ in range(limit):
        previous = prev_of.get(current)
        if not previous or previous in seen:
            return current
        seen.add(previous)
        current = previous
    return current


def _link_statuses(links, cfg):
    """{url key: status} for every link target the crawl got an answer for."""
    statuses = {}
    for link in links:
        target, status = link.get('target_url'), link.get('target_status')
        if target and isinstance(status, int):
            statuses[_key(target, cfg)] = status
    return statuses


def _target_status(key, by_key, link_status):
    page = by_key.get(key)
    if page is not None and isinstance(page.get('status_code'), int):
        return page['status_code']
    return link_status.get(key)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _item(url, offending, details, evidence=None, category=CATEGORY):
    return {'url': url, 'offending': str(offending or ''), 'details': details,
            'evidence': evidence or {}, 'category': category}


def _key(url, cfg):
    """Compare URLs the way a browser would: no tracking noise, one slash."""
    if not url:
        return ''
    cleaned = strip_tracking_params(url.strip(), cfg.get('tracking_params')
                                    or DEFAULT_TRACKING_PARAMS)
    parts = urlsplit(cleaned)
    host = parts.netloc.lower()
    if host.startswith('www.'):
        host = host[4:]
    path = parts.path or '/'
    if len(path) > 1 and path.endswith('/'):
        path = path[:-1]
    return urlunsplit(('', host, path, parts.query, ''))


def _served(page):
    """True for a page the site actually serves to a reader.

    A redirect, an error page, a PDF and another site's page are all things
    whose markup and headers say nothing about this site's health.
    """
    status = page.get('status_code')
    if not isinstance(status, int) or not 200 <= status < 300:
        return False
    if page.get('is_internal') is False:
        return False
    content_type = (page.get('content_type') or '').lower()
    return not content_type or 'html' in content_type


def _judgeable(page):
    """True when this page's headers mean something.

    A crawl that never recorded headers - one loaded from the database, where
    they are not stored - must not be read as a site that sends none.
    """
    return _served(page) and _headers(page) is not None


def _headers(page):
    """Lowercased response headers, or None when the crawl recorded none."""
    headers = page.get('response_headers')
    if not isinstance(headers, dict) or not headers:
        return None
    return {str(name).lower(): value for name, value in headers.items()}


def _final_url(page):
    """Where the response actually came from; the headers belong to it."""
    return page.get('redirected_to') or page.get('url') or ''


def _first_value(value):
    """One header string, whether the crawl stored one value or several."""
    if isinstance(value, (list, tuple)):
        return str(value[0]).strip() if value else ''
    return str(value).strip() if value else ''


def _max_age(value):
    match = _MAX_AGE_RE.search(value or '')
    return int(match.group(1)) if match else None


def _csp_directives(value):
    """{directive: [source, ...]}, quotes stripped, everything lowercased."""
    directives = {}
    for part in (value or '').split(';'):
        tokens = part.split()
        if tokens:
            directives[tokens[0].lower()] = [token.strip('\'"').lower() for token in tokens[1:]]
    return directives


def _referrer_policy(page, headers):
    """The header, or the <meta name="referrer"> that does the same job."""
    header = _first_value(headers.get('referrer-policy'))
    if header:
        return header
    meta = page.get('meta_tags')
    if isinstance(meta, dict):
        return str(meta.get('referrer') or '').strip()
    return ''


def _set_cookies(headers):
    """Every Set-Cookie the response sent, however the transport stored them."""
    value = headers.get('set-cookie')
    if isinstance(value, (list, tuple)):
        return [str(cookie) for cookie in value if cookie]
    if isinstance(value, str) and value.strip():
        return [cookie for cookie in _COOKIE_SPLIT_RE.split(value) if cookie.strip()]
    return []


def _parse_cookie(raw):
    """('name', {'secure': True, 'samesite': 'lax', ...}) from one Set-Cookie."""
    parts = [part.strip() for part in str(raw).split(';')]
    name = parts[0].split('=')[0].strip() if parts else ''
    attrs = {}
    for part in parts[1:]:
        if not part:
            continue
        key, separator, value = part.partition('=')
        attrs[key.strip().lower()] = value.strip().lower() if separator else True
    return name, attrs


def _rel_values(tag):
    rel = tag.get('rel') or []
    if isinstance(rel, str):
        rel = rel.split()
    return [str(value) for value in rel]


def _srcset_urls(value):
    """The candidate URLs in a srcset, ignoring the width/density descriptors."""
    value = (value or '').strip()
    # A data: URI may itself contain commas, which would split into nonsense.
    # Nothing insecure can hide in one, so skipping the whole attribute is safe.
    if not value or 'data:' in value.lower():
        return []
    urls = []
    for candidate in value.split(','):
        tokens = candidate.split()
        if tokens:
            urls.append(tokens[0])
    return urls


def _absolute(raw, base_url):
    raw = (raw or '').strip()
    if not raw or raw.startswith(('#', 'data:', 'javascript:', 'blob:', 'about:', 'mailto:')):
        return ''
    try:
        return urljoin(base_url or '', raw)
    except ValueError:
        return ''
