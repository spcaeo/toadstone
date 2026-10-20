"""Sitemap auditing: discovery, fetching and every sitemap check in one place.

The crawler's own SitemapParser (src/core/sitemap_parser.py) exists to feed the
frontier - it throws away everything an audit needs (which file a URL came
from, its <lastmod>, the file's size, the ones that failed). This module fetches
the sitemaps a second time and keeps all of it, then compares the result with
the crawled pages.

Typical use from the crawler, once a crawl has finished:

    from src.core import sitemap_audit

    data = sitemap_audit.fetch_sitemaps(
        session, sitemap_audit.discover_sitemaps(session, base_url))
    issues = sitemap_audit.audit(data, pages, base_url, robots_checker=can_fetch)
    lists = sitemap_audit.coverage(data, pages, base_url)
    files = sitemap_audit.file_summaries(data)

or `audit_site(session, base_url, pages, ...)`, which runs all four.

Nothing here raises on a bad sitemap: an unreachable, non-200, truncated or
unparseable file becomes a record in `errors` and an issue of its own.

GROUPING RULE
    A check that can fire on thousands of URLs would bury every other finding,
    so each per-URL check reports one issue per URL while at most
    `group_threshold` URLs (default 5) are affected, and a single grouped issue
    on `base_url` above that - "12 sitemap URLs return 404" - carrying the
    offending URLs in `evidence.related_urls` (capped at `max_related_urls`).
    A grouped issue uses an empty offending value, so its id stays stable while
    the list of affected URLs shrinks between crawls and regression tracking
    can call it fixed exactly once.

URL MATCHING
    Sitemap entries and crawled pages are matched on `compare_key`: tracking
    parameters stripped, scheme and fragment dropped, host lowercased and
    de-www'd, one trailing slash ignored. A tracking parameter or an http/https
    difference therefore never reads as a coverage gap - those are reported by
    the hygiene checks instead.
"""

import gzip
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

from src.utils.issue_factory import make_issue
from src.utils.url_params import DEFAULT_TRACKING_PARAMS, strip_tracking_params

CATEGORY = 'Sitemap'

DEFAULTS = {
    'timeout': 10,
    # sitemaps.org limits, and the recursion guard
    'max_files': 50,
    'max_urls_per_file': 50000,
    'max_bytes_per_file': 50 * 1024 * 1024,
    # share of entries that may carry no <lastmod> before it is a finding
    'missing_lastmod_ratio': 0.20,
    # per-URL findings above this many URLs collapse into one grouped issue
    'group_threshold': 5,
    'max_related_urls': 50,
    # a date-only lastmod in a +13 timezone is "tomorrow" in UTC; one day of
    # slack keeps that from reading as a future date
    'future_tolerance_seconds': 86400,
}

# W3C datetime, the only format <lastmod> accepts
_LASTMOD_RE = re.compile(
    r'^(?P<date>\d{4}-\d{2}-\d{2})'
    r'(?:[Tt](?P<time>\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?)'
    r'(?P<tz>[Zz]|[+-]\d{2}:\d{2})?)?$'
)

_FILE_EXT_RE = re.compile(r'\.[A-Za-z0-9]{2,5}$')


def _config(overrides=None):
    cfg = dict(DEFAULTS)
    for key, value in (overrides or {}).items():
        if key in cfg and value is not None:
            cfg[key] = value
    return cfg


def _text(response):
    """Body of a response as text, whatever the session hands back."""
    text = getattr(response, 'text', None)
    if isinstance(text, str):
        return text
    return (getattr(response, 'content', b'') or b'').decode('utf-8', 'replace')


def compare_key(url, tracking_params=DEFAULT_TRACKING_PARAMS):
    """Identity used when matching a sitemap entry to a crawled page.

    Scheme, fragment, 'www.', a single trailing slash and tracking parameters
    are all ignored; meaningful query parameters are kept.
    """
    if not url:
        return ''
    parsed = urlparse(strip_tracking_params(str(url).strip(), tracking_params))
    host = (parsed.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    path = parsed.path or '/'
    if len(path) > 1:
        path = path.rstrip('/') or '/'
    key = host + path
    if parsed.query:
        key += '?' + parsed.query
    return key


def _variant_key(url):
    """compare_key without the query string: same page, different address."""
    parsed = urlparse(strip_tracking_params(str(url or '').strip()))
    host = (parsed.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    path = parsed.path or '/'
    return host + (path.rstrip('/') or '/')


def _host_of(url):
    host = (urlparse(str(url or '')).hostname or '').lower()
    return host[4:] if host.startswith('www.') else host


def parse_lastmod(value):
    """Parse a <lastmod> value.

    Returns `(datetime | None, problem)` where problem is one of '' (valid),
    'missing', 'malformed' or 'no_timezone' (a time without a TZD, which
    sitemaps.org does not allow; the value is still parsed, as UTC).
    """
    if value is None or not str(value).strip():
        return None, 'missing'

    raw = str(value).strip()
    match = _LASTMOD_RE.match(raw)
    if not match:
        return None, 'malformed'

    try:
        moment = datetime.strptime(match.group('date'), '%Y-%m-%d')
    except ValueError:
        return None, 'malformed'

    time_part = match.group('time')
    if not time_part:
        return moment.replace(tzinfo=timezone.utc), ''

    bits = time_part.split(':')
    seconds, micro = 0, 0
    if len(bits) > 2:
        whole, _, frac = bits[2].partition('.')
        seconds = int(whole)
        micro = int((frac + '000000')[:6]) if frac else 0
    try:
        moment = moment.replace(hour=int(bits[0]), minute=int(bits[1]),
                                second=seconds, microsecond=micro)
    except ValueError:
        return None, 'malformed'

    tzd = match.group('tz')
    if not tzd:
        return moment.replace(tzinfo=timezone.utc), 'no_timezone'
    if tzd in ('Z', 'z'):
        return moment.replace(tzinfo=timezone.utc), ''
    offset = timedelta(hours=int(tzd[1:3]), minutes=int(tzd[4:6]))
    return moment.replace(tzinfo=timezone(offset if tzd[0] == '+' else -offset)), ''


# --------------------------------------------------------------------------
# discovery and fetching
# --------------------------------------------------------------------------

def discover_sitemaps(session, base_url, timeout=None, config=None):
    """Sitemap URLs declared in robots.txt, or /sitemap.xml as a fallback.

    `session` is a requests.Session (or anything with a compatible `get`), so
    the caller owns the timeout, proxy and user agent. Never raises: a missing
    or unreadable robots.txt simply yields the fallback.
    """
    cfg = _config(config)
    timeout = timeout or cfg['timeout']

    parsed = urlparse(str(base_url or ''))
    if not parsed.scheme or not parsed.netloc:
        return []
    root = f'{parsed.scheme}://{parsed.netloc}'

    robots_text = ''
    try:
        response = session.get(f'{root}/robots.txt', timeout=timeout)
        if int(getattr(response, 'status_code', 0) or 0) == 200:
            robots_text = _text(response)
    except Exception:
        robots_text = ''

    found = []
    for line in robots_text.splitlines():
        line = line.strip()
        if not line.lower().startswith('sitemap:'):
            continue
        value = line.split(':', 1)[1].strip()
        if not value:
            continue
        url = urljoin(root + '/', value)
        if url not in found:
            found.append(url)

    if not found:
        found.append(f'{root}/sitemap.xml')
    return found[:cfg['max_files']]


def fetch_sitemaps(session, sitemap_urls, timeout=None, config=None):
    """Fetch every sitemap file, following index files exactly one level.

    Returns::

        {'files':   [{'url', 'parent', 'status', 'entries', 'bytes',
                      'is_index', 'error', 'error_kind'}, ...],
         'entries': {url: {'lastmod': str|None, 'sitemap': url}},
         'errors':  [{'url', 'parent', 'kind', 'detail'}, ...],
         'duplicates': {url: [sitemap url, ...]}}

    `bytes` is the uncompressed size (.gz files are decompressed first).
    `duplicates` records every URL listed by more than one file, or twice by
    one file - `entries` alone cannot, being keyed by URL. Nothing raises: a
    file that cannot be fetched, is not 200, or does not parse lands in
    `errors` and in its own `files` record with `error` set.
    """
    cfg = _config(config)
    timeout = timeout or cfg['timeout']

    data = {'files': [], 'entries': {}, 'errors': [], 'duplicates': {}}
    queue = [(url, None, 0) for url in dict.fromkeys(sitemap_urls or [])]
    seen = set()

    while queue:
        url, parent, depth = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)

        if len(data['files']) >= cfg['max_files']:
            data['errors'].append({
                'url': url, 'parent': parent, 'kind': 'file_limit',
                'detail': (f"stopped after {cfg['max_files']} sitemap files; "
                           f'{len(queue) + 1} more were not fetched'),
            })
            break

        record, locs, children = _fetch_one(session, url, parent, timeout)
        data['files'].append(record)

        if record['error']:
            data['errors'].append({'url': url, 'parent': parent,
                                   'kind': record['error_kind'],
                                   'detail': record['error']})
            continue

        if record['is_index']:
            if depth >= 1:
                data['errors'].append({
                    'url': url, 'parent': parent, 'kind': 'nested_index',
                    'detail': ('sitemap index nested more than one level deep; '
                               f'its {len(children)} child sitemaps were not fetched'),
                })
                continue
            for child in children:
                queue.append((child, url, depth + 1))
            continue

        for loc, lastmod in locs:
            known = data['entries'].get(loc)
            if known is None:
                data['entries'][loc] = {'lastmod': lastmod, 'sitemap': url}
                continue
            seen_in = data['duplicates'].setdefault(loc, [known['sitemap']])
            seen_in.append(url)
            if lastmod and not known['lastmod']:
                known['lastmod'] = lastmod

    return data


def _fetch_one(session, url, parent, timeout):
    """Fetch and parse one sitemap file. Returns (record, locs, children)."""
    record = {'url': url, 'parent': parent, 'status': 0, 'entries': 0,
              'bytes': 0, 'is_index': False, 'error': '', 'error_kind': ''}

    try:
        response = session.get(url, timeout=timeout)
    except Exception as exc:
        record['error'] = f'{type(exc).__name__}: {exc}'
        record['error_kind'] = 'fetch_failed'
        return record, [], []

    record['status'] = int(getattr(response, 'status_code', 0) or 0)
    content = getattr(response, 'content', b'') or b''

    if record['status'] != 200:
        record['error'] = f'HTTP {record["status"]}'
        record['error_kind'] = 'http_error'
        return record, [], []

    # A .gz sitemap arrives compressed even though requests transparently
    # decodes Content-Encoding, so trust the gzip magic bytes.
    if content[:2] == b'\x1f\x8b':
        try:
            content = gzip.decompress(content)
        except Exception as exc:
            record['error'] = f'gzip error: {exc}'
            record['error_kind'] = 'parse_error'
            return record, [], []
    record['bytes'] = len(content)

    try:
        root = ET.fromstring(content)
    except Exception as exc:
        record['error'] = f'XML parse error: {exc}'
        record['error_kind'] = 'parse_error'
        return record, [], []

    for elem in root.iter():
        if isinstance(elem.tag, str) and '}' in elem.tag:
            elem.tag = elem.tag.split('}', 1)[1]

    nested = root.findall('.//sitemap')
    if nested or root.tag == 'sitemapindex':
        record['is_index'] = True
        children = []
        for entry in nested:
            loc = entry.find('loc')
            if loc is not None and (loc.text or '').strip():
                children.append(urljoin(url, loc.text.strip()))
        record['entries'] = len(children)
        return record, [], children

    locs = []
    for entry in root.findall('.//url'):
        loc = entry.find('loc')
        if loc is None or not (loc.text or '').strip():
            continue
        lastmod = entry.find('lastmod')
        value = (lastmod.text or '').strip() if lastmod is not None else ''
        locs.append((urljoin(url, loc.text.strip()), value or None))
    record['entries'] = len(locs)
    return record, locs, []


def file_summaries(sitemap_data):
    """Per-file summary the API can return as-is."""
    errors_by_url = defaultdict(list)
    for error in (sitemap_data or {}).get('errors', []) or []:
        if error.get('url'):
            errors_by_url[error['url']].append(error.get('detail', error.get('kind', '')))

    summaries = []
    for record in (sitemap_data or {}).get('files', []) or []:
        errors = list(errors_by_url.get(record.get('url'), []))
        if record.get('error') and record['error'] not in errors:
            errors.append(record['error'])
        summaries.append({
            'url': record.get('url', ''),
            'parent': record.get('parent'),
            'status': record.get('status', 0),
            'entries': record.get('entries', 0),
            'bytes': record.get('bytes', 0),
            'is_index': bool(record.get('is_index')),
            'errors': errors,
        })
    return summaries


# --------------------------------------------------------------------------
# page helpers
# --------------------------------------------------------------------------

def _is_html(page):
    content_type = (page.get('content_type') or '').lower()
    return 'html' in content_type or not content_type


def _is_noindex(page):
    return 'noindex' in (page.get('robots') or '').lower()


def _redirect_target(page):
    """Where a crawled page redirected to, or '' when it did not."""
    target = (page.get('redirected_to') or '').strip()
    if target and compare_key(target) != compare_key(page.get('url', '')):
        return target
    chain = page.get('redirects') or []
    if isinstance(chain, (list, tuple)) and chain:
        last = chain[-1]
        if isinstance(last, dict):
            last = last.get('to') or last.get('url') or ''
        if last and compare_key(last) != compare_key(page.get('url', '')):
            return str(last)
    status = int(page.get('status_code') or 0)
    if 300 <= status < 400:
        return page.get('location') or 'a redirect target'
    return ''


def _canonical_problem(url, page):
    """('', ...) | ('variant', canonical) | ('elsewhere', canonical)."""
    canonical = (page.get('canonical_url') or '').strip()
    if not canonical:
        return '', ''
    if strip_tracking_params(canonical) == strip_tracking_params(url):
        return '', canonical
    if compare_key(canonical) == compare_key(url) or _variant_key(canonical) == _variant_key(url):
        return 'variant', canonical
    return 'elsewhere', canonical


def _index_pages(pages):
    """compare_key -> page, preferring a 200 response when a key repeats."""
    by_key = {}
    for page in pages or []:
        key = compare_key(page.get('url', ''))
        if not key:
            continue
        known = by_key.get(key)
        if known is None:
            by_key[key] = page
        elif int(known.get('status_code') or 0) != 200 and int(page.get('status_code') or 0) == 200:
            by_key[key] = page
    return by_key


def _robots_blocked(robots_checker, url):
    if robots_checker is None:
        return False
    try:
        allowed = robots_checker(url)
    except Exception:
        return False
    return allowed is not None and not allowed


# --------------------------------------------------------------------------
# the audit
# --------------------------------------------------------------------------

def _emit(issues, base_url, check, severity, name, items, group_details,
          how_to_fix, cfg):
    """One issue per URL while the list is short, one grouped issue above it.

    `items` are dicts with 'url', 'details' and optionally 'offending' and
    'evidence'. `group_details` is a format string taking {count}.
    """
    if not items:
        return
    items = sorted(items, key=lambda item: item['url'])

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

    evidence = {'found': str(len(items)),
                'related_urls': [item['url'] for item in items][:cfg['max_related_urls']]}
    issues.append(make_issue(
        url=base_url, check=check, severity=severity, category=CATEGORY,
        issue=name, details=group_details.format(count=len(items)),
        offending_value='', evidence=evidence, how_to_fix=how_to_fix,
    ))


def audit(sitemap_data, pages, base_url, robots_checker=None, config=None,
          previous_entries=None, now=None):
    """Every sitemap check, as a list of issues from `make_issue`.

    `sitemap_data` comes from `fetch_sitemaps`, `pages` is the crawl result
    (dicts with url, status_code, canonical_url, robots, content_type,
    main_text_hash, redirects, redirected_to).

    `robots_checker(url)` must return True when robots.txt allows the URL (the
    contract of urllib.robotparser.RobotFileParser.can_fetch); None counts as
    allowed, and the check is skipped entirely when no checker is given.

    `previous_entries` optionally enables the "CMS never bumped lastmod" check:
    {url: {'lastmod': str, 'main_text_hash': str}} from the previous crawl.

    `config` overrides any key of DEFAULTS. `now` is injectable for tests.
    """
    cfg = _config(config)
    now = now or datetime.now(timezone.utc)
    future_cutoff = now + timedelta(seconds=cfg['future_tolerance_seconds'])

    data = sitemap_data or {}
    entries = data.get('entries', {}) or {}
    files = data.get('files', []) or []
    errors = data.get('errors', []) or []
    duplicates = data.get('duplicates', {}) or {}

    issues = []
    pages_by_key = _index_pages(pages)

    _audit_files(issues, files, errors, base_url, entries, cfg)
    _audit_hygiene(issues, entries, duplicates, base_url, cfg)
    _audit_entries(issues, entries, pages_by_key, base_url, robots_checker,
                   future_cutoff, cfg)
    _audit_lastmod_bumps(issues, entries, pages_by_key, previous_entries, base_url, cfg, now)
    _audit_coverage_gaps(issues, entries, pages, base_url, cfg)
    return issues


def _audit_files(issues, files, errors, base_url, entries, cfg):
    """Sitemap files themselves: unreadable, nested, oversized, absent."""
    readable = [record for record in files if not record.get('error')]

    if not files or (not readable and not entries):
        issues.append(make_issue(
            url=base_url, check='sitemap_missing', severity='warning',
            category=CATEGORY, issue='No Readable Sitemap',
            details=('No sitemap could be read for this site: robots.txt declares '
                     'none and /sitemap.xml did not return a usable file.'),
            how_to_fix='Publish an XML sitemap and declare it with a Sitemap: line in robots.txt.',
        ))
    elif readable and not entries:
        issues.append(make_issue(
            url=base_url, check='sitemap_empty', severity='warning',
            category=CATEGORY, issue='Sitemap Contains No URLs',
            details=f'{len(readable)} sitemap file(s) parsed but none listed a single <url> entry.',
            how_to_fix='Regenerate the sitemap so it lists the site\'s indexable URLs.',
        ))

    broken = [error for error in errors
              if error.get('kind') in ('http_error', 'fetch_failed', 'parse_error')]
    _emit(issues, base_url, 'sitemap_file_error', 'error', 'Sitemap File Error',
          [{'url': error['url'],
            'offending': error.get('kind', ''),
            'details': f"Sitemap file could not be read ({error.get('detail', '')}).",
            'evidence': {'found': error.get('detail', ''),
                         'related_urls': [error['parent']] if error.get('parent') else []}}
           for error in broken],
          '{count} sitemap files could not be read (non-200, unreachable or invalid XML).',
          'Serve every sitemap file listed in robots.txt and in the index with HTTP 200 and valid XML.',
          cfg)

    for error in errors:
        if error.get('kind') == 'nested_index':
            issues.append(make_issue(
                url=error['url'], check='sitemap_nested_index', severity='warning',
                category=CATEGORY, issue='Sitemap Index Nested Too Deep',
                details=('A sitemap index lists another sitemap index; search engines '
                         'only follow one level, so these URLs may never be read.'),
                offending_value=error['url'],
                evidence={'found': error.get('detail', ''),
                          'related_urls': [error['parent']] if error.get('parent') else []},
                how_to_fix='Flatten the sitemap tree: an index must point directly at URL sitemaps.',
            ))
        elif error.get('kind') == 'file_limit':
            issues.append(make_issue(
                url=base_url, check='sitemap_file_limit', severity='warning',
                category=CATEGORY, issue='Too Many Sitemap Files',
                details=f"Sitemap audit stopped early: {error.get('detail', '')}.",
                evidence={'found': error.get('detail', '')},
                how_to_fix='Reduce the number of sitemap files, or raise max_files for this site.',
            ))

    for record in files:
        if record.get('error'):
            continue
        if record.get('entries', 0) > cfg['max_urls_per_file']:
            issues.append(make_issue(
                url=record['url'], check='sitemap_too_many_urls', severity='warning',
                category=CATEGORY, issue='Sitemap Over 50,000 URLs',
                details=(f"This sitemap lists {record['entries']:,} URLs "
                         f"(limit: {cfg['max_urls_per_file']:,})."),
                evidence={'found': str(record['entries']),
                          'expected': f"<= {cfg['max_urls_per_file']}"},
                how_to_fix='Split the sitemap into several files and list them in a sitemap index.',
            ))
        if record.get('bytes', 0) > cfg['max_bytes_per_file']:
            issues.append(make_issue(
                url=record['url'], check='sitemap_too_large', severity='warning',
                category=CATEGORY, issue='Sitemap Over 50 MB',
                details=(f"This sitemap is {record['bytes'] / 1048576:.1f} MB uncompressed "
                         f"(limit: {cfg['max_bytes_per_file'] / 1048576:.0f} MB)."),
                evidence={'found': str(record['bytes']),
                          'expected': f"<= {cfg['max_bytes_per_file']} bytes"},
                how_to_fix='Split the sitemap into several smaller files listed in a sitemap index.',
            ))


def _audit_hygiene(issues, entries, duplicates, base_url, cfg):
    """Consistency across the whole set of listed URLs."""
    if not entries:
        return

    _emit(issues, base_url, 'sitemap_duplicate_urls', 'warning', 'Duplicate URL in Sitemaps',
          [{'url': url,
            'details': f'This URL is listed {len(where)} times across {len(set(where))} sitemap file(s).',
            'offending': url,
            'evidence': {'related_urls': sorted(set(where))}}
           for url, where in duplicates.items()],
          '{count} URLs are listed more than once across the sitemap files.',
          'List each URL in exactly one sitemap file, once.',
          cfg)

    schemes = {urlparse(url).scheme.lower() for url in entries}
    schemes.discard('')
    if len(schemes) > 1:
        http_urls = sorted(url for url in entries if urlparse(url).scheme.lower() == 'http')
        issues.append(make_issue(
            url=base_url, check='sitemap_mixed_protocols', severity='warning',
            category=CATEGORY, issue='Mixed http/https in Sitemap',
            details=(f"The sitemap lists URLs on both {' and '.join(sorted(schemes))}; "
                     'only the canonical protocol belongs in it.'),
            evidence={'found': ', '.join(sorted(schemes)),
                      'expected': 'https', 'related_urls': http_urls[:cfg['max_related_urls']]},
            how_to_fix='Regenerate the sitemap using the canonical protocol for every URL.',
        ))

    hosts = {(urlparse(url).hostname or '').lower() for url in entries}
    hosts.discard('')
    if len(hosts) > 1:
        bare = {host[4:] if host.startswith('www.') else host for host in hosts}
        kind = ('www and non-www variants of the same host' if len(bare) < len(hosts)
                else 'several different hosts')
        issues.append(make_issue(
            url=base_url, check='sitemap_mixed_hosts', severity='warning',
            category=CATEGORY, issue='Mixed Hostnames in Sitemap',
            details=f"The sitemap lists {kind}: {', '.join(sorted(hosts))}.",
            evidence={'found': ', '.join(sorted(hosts)),
                      'expected': _host_of(base_url) or 'one canonical host'},
            how_to_fix='List every URL on the canonical hostname only.',
        ))

    with_slash, without_slash = [], []
    for url in entries:
        path = urlparse(url).path or '/'
        if path == '/' or _FILE_EXT_RE.search(path.rstrip('/').rsplit('/', 1)[-1]):
            continue
        (with_slash if path.endswith('/') else without_slash).append(url)
    if with_slash and without_slash:
        minority = with_slash if len(with_slash) < len(without_slash) else without_slash
        issues.append(make_issue(
            url=base_url, check='sitemap_inconsistent_trailing_slash', severity='warning',
            category=CATEGORY, issue='Inconsistent Trailing Slashes',
            details=(f'{len(with_slash)} sitemap URLs end in a slash and '
                     f'{len(without_slash)} do not; one of the two forms redirects.'),
            evidence={'found': f'{len(with_slash)} with / {len(without_slash)} without',
                      'related_urls': sorted(minority)[:cfg['max_related_urls']]},
            how_to_fix='Pick one trailing-slash convention and list only canonical URLs in that form.',
        ))

    missing = sum(1 for meta in entries.values() if not (meta.get('lastmod') or '').strip())
    ratio = missing / len(entries)
    if ratio > cfg['missing_lastmod_ratio']:
        issues.append(make_issue(
            url=base_url, check='sitemap_lastmod_coverage_low', severity='warning',
            category=CATEGORY, issue='Most Sitemap Entries Have No lastmod',
            details=(f'{missing} of {len(entries)} sitemap entries ({ratio:.0%}) carry no '
                     f"<lastmod> (threshold: {cfg['missing_lastmod_ratio']:.0%})."),
            evidence={'found': f'{ratio:.0%}',
                      'expected': f"<= {cfg['missing_lastmod_ratio']:.0%}"},
            how_to_fix='Emit an accurate <lastmod> for every URL from the CMS\'s modified date.',
        ))


def _audit_entries(issues, entries, pages_by_key, base_url, robots_checker,
                   future_cutoff, cfg):
    """Per-URL checks over everything the sitemaps list."""
    buckets = defaultdict(list)

    for url, meta in entries.items():
        page = pages_by_key.get(compare_key(url))

        if _robots_blocked(robots_checker, url):
            buckets['blocked'].append({
                'url': url, 'offending': url,
                'details': 'This URL is listed in the sitemap but robots.txt disallows crawling it.',
                'evidence': {'related_urls': [meta.get('sitemap', '')]},
            })

        if page is None:
            buckets['not_crawled'].append({
                'url': url, 'offending': url,
                'details': 'Listed in the sitemap but never reached by the crawl.',
                'evidence': {'related_urls': [meta.get('sitemap', '')]},
            })
        else:
            status = int(page.get('status_code') or 0)
            redirect_to = _redirect_target(page)
            if 400 <= status < 500:
                buckets['not_found'].append({
                    'url': url, 'offending': str(status),
                    'details': f'The sitemap lists this URL but it returns HTTP {status}.',
                    'evidence': {'found': str(status), 'expected': '200',
                                 'related_urls': [meta.get('sitemap', '')]},
                })
            elif status >= 500:
                buckets['server_error'].append({
                    'url': url, 'offending': str(status),
                    'details': f'The sitemap lists this URL but it returns HTTP {status}.',
                    'evidence': {'found': str(status), 'expected': '200',
                                 'related_urls': [meta.get('sitemap', '')]},
                })
            elif redirect_to:
                buckets['redirect'].append({
                    'url': url, 'offending': redirect_to,
                    'details': f'The sitemap lists this URL but it redirects to {redirect_to}.',
                    'evidence': {'found': redirect_to, 'expected': url,
                                 'related_urls': [redirect_to]},
                })
            else:
                if _is_noindex(page):
                    buckets['noindex'].append({
                        'url': url, 'offending': page.get('robots', 'noindex'),
                        'details': ('The sitemap lists this URL but its meta robots tag says '
                                    'noindex, so the two instructions contradict each other.'),
                        'evidence': {'found': page.get('robots', ''), 'expected': 'index',
                                     'related_urls': [meta.get('sitemap', '')]},
                    })
                problem, canonical = _canonical_problem(url, page)
                if problem == 'elsewhere':
                    buckets['canonicalised'].append({
                        'url': url, 'offending': canonical,
                        'details': (f'The sitemap lists this URL but the page canonicalises to '
                                    f'{canonical}, so it will not be indexed under this address.'),
                        'evidence': {'found': canonical, 'expected': url,
                                     'related_urls': [canonical]},
                    })
                elif problem == 'variant':
                    buckets['canonical_mismatch'].append({
                        'url': url, 'offending': canonical,
                        'details': (f'The sitemap lists {url} while the page canonicalises to '
                                    f'{canonical} - the same page under a different address.'),
                        'evidence': {'found': canonical, 'expected': url,
                                     'related_urls': [canonical]},
                    })

        raw = meta.get('lastmod')
        moment, problem = parse_lastmod(raw)
        if problem == 'missing':
            buckets['lastmod_missing'].append({
                'url': url, 'offending': '',
                'details': 'This sitemap entry has no <lastmod>.',
                'evidence': {'related_urls': [meta.get('sitemap', '')]},
            })
        elif problem in ('malformed', 'no_timezone'):
            reason = ('is not a W3C datetime' if problem == 'malformed'
                      else 'gives a time with no timezone offset')
            buckets['lastmod_malformed'].append({
                'url': url, 'offending': str(raw),
                'details': f'<lastmod> "{raw}" {reason}.',
                'evidence': {'found': str(raw),
                             'expected': '2026-09-22 or 2026-09-22T10:30:00+00:00',
                             'snippet': f'<lastmod>{raw}</lastmod>'},
            })
        if moment is not None and moment > future_cutoff:
            buckets['lastmod_future'].append({
                'url': url, 'offending': str(raw),
                'details': f'<lastmod> "{raw}" is in the future.',
                'evidence': {'found': str(raw), 'expected': f'<= {future_cutoff.date()}',
                             'snippet': f'<lastmod>{raw}</lastmod>'},
            })

    specs = (
        ('not_found', 'sitemap_url_not_found', 'error', 'Sitemap URL Returns 4xx',
         '{count} sitemap URLs return a 4xx status.',
         'Remove dead URLs from the sitemap, or restore the pages they point at.'),
        ('server_error', 'sitemap_url_server_error', 'error', 'Sitemap URL Returns 5xx',
         '{count} sitemap URLs return a 5xx status.',
         'Fix the server errors, or remove those URLs from the sitemap.'),
        ('redirect', 'sitemap_url_redirects', 'warning', 'Sitemap URL Redirects',
         '{count} sitemap URLs redirect instead of serving a page.',
         'List the final destination URL in the sitemap instead of the redirecting one.'),
        ('noindex', 'sitemap_url_noindex', 'error', 'Sitemap URL Is noindex',
         '{count} sitemap URLs are marked noindex.',
         'Either remove the noindex tag or drop the URL from the sitemap.'),
        ('canonicalised', 'sitemap_url_canonicalised_away', 'error',
         'Sitemap URL Canonicalises Elsewhere',
         '{count} sitemap URLs canonicalise to a different page.',
         'List the canonical URL in the sitemap instead of this one.'),
        ('canonical_mismatch', 'sitemap_canonical_mismatch', 'warning',
         'Sitemap/Canonical Mismatch',
         '{count} sitemap URLs differ from their canonical form (query string or trailing slash).',
         'Emit the exact canonical URL in the sitemap, character for character.'),
        ('blocked', 'sitemap_url_blocked_by_robots', 'error', 'Sitemap URL Blocked by robots.txt',
         '{count} sitemap URLs are disallowed by robots.txt.',
         'Allow these URLs in robots.txt, or remove them from the sitemap.'),
        ('not_crawled', 'sitemap_url_not_crawled', 'info', 'Sitemap URL Not Crawled',
         '{count} sitemap URLs were not reached by this crawl.',
         'Check that these URLs are linked from the site, or raise the crawl limit.'),
        ('lastmod_missing', 'sitemap_lastmod_missing', 'info', 'Missing lastmod',
         '{count} sitemap entries carry no <lastmod>.',
         'Emit <lastmod> from the CMS modified date for every entry.'),
        ('lastmod_malformed', 'sitemap_lastmod_malformed', 'warning', 'Malformed lastmod',
         '{count} sitemap entries carry a <lastmod> that is not a valid W3C datetime.',
         'Write <lastmod> as YYYY-MM-DD or a full W3C datetime with a timezone offset.'),
        ('lastmod_future', 'sitemap_lastmod_future', 'warning', 'lastmod in the Future',
         '{count} sitemap entries carry a <lastmod> in the future.',
         'Emit the real modified date; a future date makes the signal worthless.'),
    )
    for bucket, check, severity, name, group_details, how_to_fix in specs:
        _emit(issues, base_url, check, severity, name, buckets[bucket],
              group_details, how_to_fix, cfg)


def _audit_lastmod_bumps(issues, entries, pages_by_key, previous_entries, base_url, cfg,
                         now=None):
    """lastmod unchanged while the page body changed (a CMS not bumping it).

    A day-only lastmod (YYYY-MM-DD) that already names the crawl day cannot
    move any further, so an edit made that same day is not stale. The window
    is one day back, because the site writes its day in its own timezone.
    """
    if not previous_entries:
        return
    now = now or datetime.now(timezone.utc)
    earliest_current_day = (now - timedelta(days=1)).date().isoformat()

    previous_by_key = {}
    for url, meta in previous_entries.items():
        key = compare_key(url)
        if key:
            previous_by_key[key] = meta or {}

    stale = []
    for url, meta in entries.items():
        key = compare_key(url)
        before = previous_by_key.get(key)
        page = pages_by_key.get(key)
        if not before or not page:
            continue
        old_hash = (before.get('main_text_hash') or '').strip()
        new_hash = (page.get('main_text_hash') or '').strip()
        old_lastmod = (before.get('lastmod') or '').strip()
        new_lastmod = (meta.get('lastmod') or '').strip()
        if not old_hash or not new_hash or old_hash == new_hash:
            continue
        if not new_lastmod or new_lastmod != old_lastmod:
            continue
        if len(new_lastmod) == 10 and new_lastmod >= earliest_current_day:
            continue
        stale.append({
            'url': url, 'offending': new_lastmod,
            'details': (f'The page content changed since the last crawl but <lastmod> is '
                        f'still "{new_lastmod}".'),
            'evidence': {'found': new_lastmod, 'expected': 'a date on or after the edit',
                         'snippet': f'<lastmod>{new_lastmod}</lastmod>'},
        })

    _emit(issues, base_url, 'sitemap_lastmod_not_bumped', 'warning', 'lastmod Not Updated',
          stale, '{count} pages changed since the last crawl without their <lastmod> moving.',
          'Make the CMS write the real modified date into the sitemap when content is edited.',
          cfg)


def _audit_coverage_gaps(issues, entries, pages, base_url, cfg):
    """Indexable pages the crawl found that no sitemap lists."""
    if not entries:
        return

    listed = {compare_key(url) for url in entries}
    base_host = _host_of(base_url)
    missing = []

    for page in pages or []:
        url = (page.get('url') or '').strip()
        key = compare_key(url)
        if not key or key in listed:
            continue
        if base_host and _host_of(url) != base_host:
            continue
        if int(page.get('status_code') or 0) != 200 or not _is_html(page):
            continue
        if _is_noindex(page) or _redirect_target(page):
            continue
        if _canonical_problem(url, page)[0] == 'elsewhere':
            continue
        missing.append({
            'url': url, 'offending': '',
            'details': 'This indexable page was crawled but no sitemap lists it.',
            'evidence': {'expected': 'listed in the sitemap'},
        })

    _emit(issues, base_url, 'sitemap_missing_crawled_url', 'info', 'Crawled Page Missing From Sitemap',
          missing, '{count} indexable crawled pages are missing from the sitemap.',
          'Add these URLs to the sitemap, or mark them noindex if they should not be indexed.',
          cfg)


# --------------------------------------------------------------------------
# coverage lists
# --------------------------------------------------------------------------

def coverage(sitemap_data, pages, base_url=None):
    """Three downloadable lists for the UI's coverage view.

    Returns {'in_both': [...], 'crawled_only': [...], 'sitemap_only': [...]},
    each sorted. Crawled HTML pages of any status count as crawled (a listed
    URL that 404s really is "in both"); non-HTML rows (images, PDFs, CSS) are
    left out. With `base_url`, off-host pages are left out too.
    """
    entries = (sitemap_data or {}).get('entries', {}) or {}
    base_host = _host_of(base_url) if base_url else ''

    crawled = {}
    for page in pages or []:
        url = (page.get('url') or '').strip()
        key = compare_key(url)
        if not key or not _is_html(page):
            continue
        if base_host and _host_of(url) != base_host:
            continue
        crawled.setdefault(key, url)

    listed = {}
    for url in entries:
        key = compare_key(url)
        if key:
            listed.setdefault(key, url)

    return {
        'in_both': sorted(url for key, url in crawled.items() if key in listed),
        'crawled_only': sorted(url for key, url in crawled.items() if key not in listed),
        'sitemap_only': sorted(url for key, url in listed.items() if key not in crawled),
    }


def audit_site(session, base_url, pages, robots_checker=None, config=None,
               previous_entries=None, sitemap_urls=None):
    """Discover, fetch and audit in one call - what the crawler runs.

    Returns {'sitemaps': [...], 'data': {...}, 'files': [...],
             'issues': [...], 'coverage': {...}}.
    """
    urls = sitemap_urls or discover_sitemaps(session, base_url, config=config)
    data = fetch_sitemaps(session, urls, config=config)
    return {
        'sitemaps': urls,
        'data': data,
        'files': file_summaries(data),
        'issues': audit(data, pages, base_url, robots_checker=robots_checker,
                        config=config, previous_entries=previous_entries),
        'coverage': coverage(data, pages, base_url),
    }
