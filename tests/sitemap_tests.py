"""Deterministic tests for the sitemap audit against a local fixture site.

Runs src/core/sitemap_audit.py against tiny HTTP servers started by this
script, so every check is pinned down without touching a third-party site:

    python tests/sitemap_tests.py

Two fixture sites are served:

  * the "dirty" site - robots.txt -> sitemap index -> two child sitemaps (one
    gzipped) plus a nested index and a sitemap file that 404s. Between them
    they list a URL that 404s, one that redirects, one that is noindex, one
    that canonicalises elsewhere, one that canonicalises to a different form
    of itself, one blocked by robots.txt, a malformed <lastmod>, a future
    <lastmod>, a missing <lastmod> and a URL duplicated across both children.
  * the "clean" site - no robots.txt (so discovery must fall back to
    /sitemap.xml) and a sitemap that must produce no issues at all.

The crawl result is supplied as page dicts rather than by running a crawl:
the audit's contract is those dicts, and fixing them here keeps the checks
independent of crawler behaviour.
"""
import gzip
import http.server
import os
import sys
import threading
from datetime import datetime, timedelta, timezone
from urllib.robotparser import RobotFileParser

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from src.core import sitemap_audit  # noqa: E402

# Fixture servers bind here upward. Override if these clash locally.
BASE_PORT = int(os.environ.get('SITEMAP_FIXTURE_PORT', '8931'))

NOW = datetime.now(timezone.utc)
PAST = (NOW - timedelta(days=30)).strftime('%Y-%m-%d')
FUTURE = (NOW + timedelta(days=365)).strftime('%Y-%m-%d')

PASSED, FAILED = [], []


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def serve(handler, port):
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def make_handler(routes):
    """Serve `routes`: path -> str (XML/text) or bytes (a .gz body)."""

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_GET(self):
            body = routes.get(self.path)
            if body is None:
                self.send_response(404)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Content-Length', '9')
                self.end_headers()
                self.wfile.write(b'not found')
                return
            if isinstance(body, str):
                ctype = 'text/plain' if self.path.endswith('.txt') else 'application/xml'
                body = body.encode()
            else:
                ctype = 'application/x-gzip'
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_HEAD = do_GET

    return Handler


def urlset(entries):
    """entries: list of (loc, lastmod|None)."""
    rows = []
    for loc, lastmod in entries:
        lastmod_tag = f'<lastmod>{lastmod}</lastmod>' if lastmod else ''
        rows.append(f'<url><loc>{loc}</loc>{lastmod_tag}</url>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + ''.join(rows) + '</urlset>')


def sitemapindex(locs):
    rows = ''.join(f'<sitemap><loc>{loc}</loc></sitemap>' for loc in locs)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + rows + '</sitemapindex>')


DIRTY = f'http://127.0.0.1:{BASE_PORT}'
CLEAN = f'http://127.0.0.1:{BASE_PORT + 1}'

DIRTY_ROBOTS = (
    'User-agent: *\n'
    'Disallow: /private/\n'
    '\n'
    f'Sitemap: {DIRTY}/sitemap_index.xml\n'
    f'Sitemap: {DIRTY}/sitemap-missing.xml\n'
)

DIRTY_ROUTES = {
    '/robots.txt': DIRTY_ROBOTS,
    '/sitemap_index.xml': sitemapindex([
        f'{DIRTY}/sitemap-pages.xml',
        f'{DIRTY}/sitemap-posts.xml.gz',
        f'{DIRTY}/sitemap-nested-index.xml',
    ]),
    '/sitemap-pages.xml': urlset([
        (f'{DIRTY}/ok/', PAST),
        (f'{DIRTY}/gone/', PAST),
        (f'{DIRTY}/noindex/', PAST),
        (f'{DIRTY}/canonical-elsewhere/', PAST),
        (f'{DIRTY}/variant/?x=1', PAST),
        (f'{DIRTY}/redirected/', PAST),
        (f'{DIRTY}/bad-date/', 'last tuesday'),
        (f'{DIRTY}/future/', FUTURE),
        (f'{DIRTY}/private/secret/', PAST),
        (f'{DIRTY}/dupe/', PAST),
        (f'{DIRTY}/no-lastmod/', None),
    ]),
    '/sitemap-posts.xml.gz': gzip.compress(urlset([
        (f'{DIRTY}/post-1/', PAST),
        (f'{DIRTY}/dupe/', PAST),
    ]).encode()),
    '/sitemap-nested-index.xml': sitemapindex([f'{DIRTY}/sitemap-deeper.xml']),
    '/sitemap-deeper.xml': urlset([(f'{DIRTY}/deep/', PAST)]),
    # /sitemap-missing.xml is deliberately absent: it must 404
}

CLEAN_ROUTES = {
    # no robots.txt on purpose - discovery must fall back to /sitemap.xml
    '/sitemap.xml': urlset([
        (f'{CLEAN}/', PAST),
        (f'{CLEAN}/about/', PAST),
        (f'{CLEAN}/contact/', PAST),
    ]),
}


def page(url, status=200, **fields):
    row = {'url': url, 'status_code': status, 'content_type': 'text/html; charset=utf-8',
           'canonical_url': url, 'robots': 'index, follow', 'main_text_hash': 'h',
           'redirects': [], 'redirected_to': ''}
    row.update(fields)
    return row


DIRTY_PAGES = [
    page(f'{DIRTY}/ok/'),
    page(f'{DIRTY}/gone/', 404, canonical_url=''),
    page(f'{DIRTY}/noindex/', robots='noindex, follow'),
    page(f'{DIRTY}/canonical-elsewhere/', canonical_url=f'{DIRTY}/ok/'),
    page(f'{DIRTY}/variant/?x=1', canonical_url=f'{DIRTY}/variant/'),
    page(f'{DIRTY}/redirected/', redirected_to=f'{DIRTY}/ok/'),
    page(f'{DIRTY}/bad-date/'),
    page(f'{DIRTY}/future/'),
    page(f'{DIRTY}/dupe/'),
    page(f'{DIRTY}/no-lastmod/'),
    page(f'{DIRTY}/post-1/'),
    page(f'{DIRTY}/orphan/'),
    page(f'{DIRTY}/logo.png', content_type='image/png', canonical_url=''),
    # /private/secret/ is never crawled: robots.txt disallows it
]

CLEAN_PAGES = [page(f'{CLEAN}/'), page(f'{CLEAN}/about/'), page(f'{CLEAN}/contact/')]


def robots_checker_for(robots_text, base):
    """A robots_checker with the crawler's contract: True means allowed."""
    parser = RobotFileParser()
    parser.parse(robots_text.splitlines())
    return lambda url: parser.can_fetch('Toadstone', url)


def by_check(issues):
    out = {}
    for issue in issues:
        out.setdefault(issue['check'], []).append(issue)
    return out


_cache = {}


def dirty_audit():
    """Fetch and audit the dirty fixture once, then reuse the result."""
    if 'dirty' not in _cache:
        session = requests.Session()
        sitemaps = sitemap_audit.discover_sitemaps(session, DIRTY)
        data = sitemap_audit.fetch_sitemaps(session, sitemaps)
        issues = sitemap_audit.audit(
            data, DIRTY_PAGES, DIRTY,
            robots_checker=robots_checker_for(DIRTY_ROBOTS, DIRTY))
        _cache['dirty'] = (sitemaps, data, issues)
    return _cache['dirty']


def test_discovery_reads_robots_then_falls_back():
    """robots.txt Sitemap: lines win; a site without robots.txt gets /sitemap.xml."""
    session = requests.Session()
    found = sitemap_audit.discover_sitemaps(session, DIRTY)
    result('discovery: both robots.txt Sitemap: lines are returned',
           found == [f'{DIRTY}/sitemap_index.xml', f'{DIRTY}/sitemap-missing.xml'], str(found))

    fallback = sitemap_audit.discover_sitemaps(session, CLEAN)
    result('discovery: falls back to /sitemap.xml without robots.txt',
           fallback == [f'{CLEAN}/sitemap.xml'], str(fallback))

    result('discovery: a URL with no scheme yields nothing',
           sitemap_audit.discover_sitemaps(session, 'not-a-url') == [])


def test_fetch_walks_index_and_gzip():
    """One level of index following, .gz decoded, sizes and counts recorded."""
    _, data, _ = dirty_audit()
    files = {record['url']: record for record in data['files']}

    result('fetch: the index and all three children were fetched',
           f'{DIRTY}/sitemap_index.xml' in files
           and f'{DIRTY}/sitemap-pages.xml' in files
           and f'{DIRTY}/sitemap-posts.xml.gz' in files, str(sorted(files)))

    index = files[f'{DIRTY}/sitemap_index.xml']
    result('fetch: the index is flagged is_index with 3 children',
           index['is_index'] and index['entries'] == 3, str(index))

    pages = files[f'{DIRTY}/sitemap-pages.xml']
    result('fetch: a URL sitemap carries its entry count and byte size',
           pages['is_index'] is False and pages['entries'] == 11 and pages['bytes'] > 500,
           str(pages))

    posts = files.get(f'{DIRTY}/sitemap-posts.xml.gz', {})
    compressed = len(DIRTY_ROUTES['/sitemap-posts.xml.gz'])
    result('fetch: a .gz child is decompressed and its uncompressed size recorded',
           posts.get('entries') == 2 and posts.get('bytes', 0) > compressed,
           f"entries={posts.get('entries')} bytes={posts.get('bytes')} gz={compressed}")

    result('fetch: gzipped entries land in the entry map',
           f'{DIRTY}/post-1/' in data['entries']
           and data['entries'][f'{DIRTY}/post-1/']['sitemap'].endswith('.gz'),
           str(data['entries'].get(f'{DIRTY}/post-1/')))

    result('fetch: a URL in two children is recorded as a duplicate',
           len(data['duplicates'].get(f'{DIRTY}/dupe/', [])) == 2,
           str(data['duplicates']))

    result('fetch: deeper nesting is reported, not recursed into',
           any(e['kind'] == 'nested_index' for e in data['errors'])
           and f'{DIRTY}/deep/' not in data['entries'], str(data['errors']))

    result('fetch: a 404 sitemap file is an error, not an exception',
           any(e['kind'] == 'http_error' and e['url'].endswith('sitemap-missing.xml')
               for e in data['errors']), str(data['errors']))

    result('fetch: every entry knows which file listed it',
           all(meta['sitemap'] for meta in data['entries'].values()))


def test_file_cap_stops_the_walk():
    """max_files bounds the number of files fetched and says so."""
    session = requests.Session()
    data = sitemap_audit.fetch_sitemaps(
        session, [f'{DIRTY}/sitemap_index.xml'], config={'max_files': 2})
    result('cap: no more than max_files sitemap files are fetched',
           len(data['files']) == 2, str(len(data['files'])))
    result('cap: hitting the cap is recorded as a file_limit error',
           any(e['kind'] == 'file_limit' for e in data['errors']), str(data['errors']))


def test_uncrawlable_sitemap_urls():
    """404, 3xx, noindex, canonicalised-away and robots-blocked all fire."""
    _, _, issues = dirty_audit()
    found = by_check(issues)

    expected = {
        'sitemap_url_not_found': (f'{DIRTY}/gone/', 'error'),
        'sitemap_url_redirects': (f'{DIRTY}/redirected/', 'warning'),
        'sitemap_url_noindex': (f'{DIRTY}/noindex/', 'error'),
        'sitemap_url_canonicalised_away': (f'{DIRTY}/canonical-elsewhere/', 'error'),
        'sitemap_canonical_mismatch': (f'{DIRTY}/variant/?x=1', 'warning'),
        'sitemap_url_blocked_by_robots': (f'{DIRTY}/private/secret/', 'error'),
    }
    for check, (url, severity) in expected.items():
        hits = found.get(check, [])
        result(f'uncrawlable: {check} fires on the right URL with severity {severity}',
               len(hits) == 1 and hits[0]['url'] == url and hits[0]['type'] == severity,
               str([(h['url'], h['type']) for h in hits]))

    result('uncrawlable: the canonical target travels in the evidence',
           found['sitemap_url_canonicalised_away'][0]['evidence'].get('found') == f'{DIRTY}/ok/',
           str(found['sitemap_url_canonicalised_away'][0]['evidence']))

    result('uncrawlable: a sitemap URL the crawl never reached is info, not error',
           [i['url'] for i in found.get('sitemap_url_not_crawled', [])] == [f'{DIRTY}/private/secret/']
           and found['sitemap_url_not_crawled'][0]['type'] == 'info',
           str([(i['url'], i['type']) for i in found.get('sitemap_url_not_crawled', [])]))

    result('uncrawlable: a healthy sitemap URL produces nothing',
           not any(i['url'] == f'{DIRTY}/ok/' for i in issues),
           str([i['check'] for i in issues if i['url'] == f'{DIRTY}/ok/']))


def test_robots_check_is_skipped_without_a_checker():
    """No robots_checker means no robots findings, not a guess."""
    _, data, _ = dirty_audit()
    issues = sitemap_audit.audit(data, DIRTY_PAGES, DIRTY)
    result('robots: the blocked check is silent when no checker is supplied',
           not any(i['check'] == 'sitemap_url_blocked_by_robots' for i in issues))


def test_lastmod_checks():
    """Missing, malformed and future <lastmod> are three separate findings."""
    _, _, issues = dirty_audit()
    found = by_check(issues)

    result('lastmod: a missing lastmod is reported as info',
           [i['url'] for i in found.get('sitemap_lastmod_missing', [])] == [f'{DIRTY}/no-lastmod/']
           and found['sitemap_lastmod_missing'][0]['type'] == 'info',
           str([(i['url'], i['type']) for i in found.get('sitemap_lastmod_missing', [])]))

    malformed = found.get('sitemap_lastmod_malformed', [])
    result('lastmod: a malformed lastmod is a warning carrying the bad value',
           len(malformed) == 1 and malformed[0]['url'] == f'{DIRTY}/bad-date/'
           and malformed[0]['type'] == 'warning'
           and malformed[0]['evidence'].get('found') == 'last tuesday',
           str([(i['url'], i['evidence']) for i in malformed]))

    future = found.get('sitemap_lastmod_future', [])
    result('lastmod: a future lastmod is a warning on that URL',
           len(future) == 1 and future[0]['url'] == f'{DIRTY}/future/'
           and future[0]['type'] == 'warning',
           str([(i['url'], i['type']) for i in future]))

    result('lastmod: a valid date produces neither',
           not any(i['url'] == f'{DIRTY}/post-1/' for i in issues))


def test_lastmod_parsing():
    """The W3C datetime rules, without a server."""
    cases = [
        ('2026-09-22', ''),
        ('2026-09-22T10:30:00Z', ''),
        ('2026-09-22T10:30:00+05:30', ''),
        ('2026-09-22T10:30:00.123-08:00', ''),
        ('2026-09-22T10:30:00', 'no_timezone'),
        ('22/09/2026', 'malformed'),
        ('2026-13-45', 'malformed'),
        ('2026-09-22T99:30:00Z', 'malformed'),
        ('', 'missing'),
        (None, 'missing'),
    ]
    bad = [(raw, sitemap_audit.parse_lastmod(raw)[1]) for raw, want in cases
           if sitemap_audit.parse_lastmod(raw)[1] != want]
    result('parse_lastmod: every W3C form is classified correctly', not bad, str(bad))

    moment, _ = sitemap_audit.parse_lastmod('2026-09-22T10:30:00+05:30')
    result('parse_lastmod: the timezone offset is applied',
           moment.utcoffset() == timedelta(hours=5, minutes=30), str(moment))


def test_hygiene_checks():
    """Duplicates, size limits, mixed protocols/hosts, slashes, lastmod coverage."""
    _, _, issues = dirty_audit()
    found = by_check(issues)

    duplicate = found.get('sitemap_duplicate_urls', [])
    result('hygiene: a URL in two child sitemaps is a warning listing both files',
           len(duplicate) == 1 and duplicate[0]['url'] == f'{DIRTY}/dupe/'
           and duplicate[0]['type'] == 'warning'
           and len(duplicate[0]['evidence'].get('related_urls', [])) == 2,
           str([(i['url'], i['evidence']) for i in duplicate]))

    result('hygiene: an unreadable sitemap file is an error',
           any(i['check'] == 'sitemap_file_error' and i['type'] == 'error'
               and i['url'].endswith('sitemap-missing.xml') for i in issues),
           str([i['url'] for i in found.get('sitemap_file_error', [])]))

    result('hygiene: a nested sitemap index is a warning',
           any(i['check'] == 'sitemap_nested_index' and i['type'] == 'warning' for i in issues))

    # Size, protocol, host, slash and lastmod-coverage limits, on synthetic data
    data = {
        'files': [{'url': 'https://s/big.xml', 'parent': None, 'status': 200,
                   'entries': 60000, 'bytes': 60 * 1024 * 1024, 'is_index': False,
                   'error': '', 'error_kind': ''}],
        'entries': {
            'https://s/a/': {'lastmod': PAST, 'sitemap': 'https://s/big.xml'},
            'http://s/b/': {'lastmod': None, 'sitemap': 'https://s/big.xml'},
            'https://www.s/c': {'lastmod': None, 'sitemap': 'https://s/big.xml'},
        },
        'errors': [], 'duplicates': {},
    }
    checks = {i['check'] for i in sitemap_audit.audit(data, [], 'https://s/')}
    for check in ('sitemap_too_many_urls', 'sitemap_too_large', 'sitemap_mixed_protocols',
                  'sitemap_mixed_hosts', 'sitemap_inconsistent_trailing_slash',
                  'sitemap_lastmod_coverage_low'):
        result(f'hygiene: {check} fires on a sitemap that breaks that limit',
               check in checks, str(sorted(checks)))

    relaxed = {i['check'] for i in sitemap_audit.audit(
        data, [], 'https://s/', config={'missing_lastmod_ratio': 0.9})}
    result('hygiene: the lastmod ratio threshold is configurable',
           'sitemap_lastmod_coverage_low' not in relaxed, str(sorted(relaxed)))

    empty = {i['check'] for i in sitemap_audit.audit(
        {'files': [], 'entries': {}, 'errors': [], 'duplicates': {}}, [], 'https://s/')}
    result('hygiene: a site with no readable sitemap is reported once',
           empty == {'sitemap_missing'}, str(sorted(empty)))


def test_crawled_pages_missing_from_the_sitemap():
    """Only indexable internal HTML pages count as a coverage gap."""
    _, _, issues = dirty_audit()
    missing = by_check(issues).get('sitemap_missing_crawled_url', [])
    result('coverage gap: an indexable crawled page absent from the sitemap is info',
           [i['url'] for i in missing] == [f'{DIRTY}/orphan/']
           and missing[0]['type'] == 'info',
           str([(i['url'], i['type']) for i in missing]))
    result('coverage gap: images, 404s, noindex and redirects are not coverage gaps',
           not any(i['url'] == f'{DIRTY}/logo.png' for i in issues))


def test_grouping_rule():
    """<= 5 offenders stay per-URL; above that they collapse into one issue."""
    def build(count):
        entries = {f'https://s/p{n}/': {'lastmod': PAST, 'sitemap': 'https://s/sitemap.xml'}
                   for n in range(count)}
        pages = [page(url, 404, canonical_url='') for url in entries]
        data = {'files': [{'url': 'https://s/sitemap.xml', 'parent': None, 'status': 200,
                           'entries': count, 'bytes': 100, 'is_index': False,
                           'error': '', 'error_kind': ''}],
                'entries': entries, 'errors': [], 'duplicates': {}}
        return [i for i in sitemap_audit.audit(data, pages, 'https://s/')
                if i['check'] == 'sitemap_url_not_found']

    few = build(5)
    result('grouping: 5 offenders stay as 5 per-URL issues',
           len(few) == 5 and all(i['url'].startswith('https://s/p') for i in few),
           str(len(few)))

    many = build(8)
    result('grouping: 8 offenders become one grouped issue on the base URL',
           len(many) == 1 and many[0]['url'] == 'https://s/'
           and '8' in many[0]['details'], str([(i['url'], i['details']) for i in many]))
    result('grouping: the grouped issue carries every offending URL as evidence',
           len(many[0]['evidence'].get('related_urls', [])) == 8
           and many[0]['evidence'].get('found') == '8', str(many[0]['evidence']))
    result('grouping: the grouped issue id does not move when the list shrinks',
           build(8)[0]['id'] == build(9)[0]['id'])
    result('grouping: the threshold is configurable',
           len([i for i in sitemap_audit.audit(
               {'files': [], 'entries': {f'https://s/p{n}/': {'lastmod': PAST, 'sitemap': 's'}
                                         for n in range(8)},
                'errors': [], 'duplicates': {}},
               [page(f'https://s/p{n}/', 404, canonical_url='') for n in range(8)],
               'https://s/', config={'group_threshold': 20})
               if i['check'] == 'sitemap_url_not_found']) == 8)


def test_lastmod_not_bumped_needs_the_previous_crawl():
    """Content changed, <lastmod> did not - only when a previous crawl is given."""
    entries = {'https://s/a/': {'lastmod': PAST, 'sitemap': 'https://s/sitemap.xml'}}
    data = {'files': [{'url': 'https://s/sitemap.xml', 'parent': None, 'status': 200,
                       'entries': 1, 'bytes': 100, 'is_index': False,
                       'error': '', 'error_kind': ''}],
            'entries': entries, 'errors': [], 'duplicates': {}}
    pages = [page('https://s/a/', main_text_hash='new')]
    previous = {'https://s/a/': {'lastmod': PAST, 'main_text_hash': 'old'}}

    checks = {i['check'] for i in sitemap_audit.audit(data, pages, 'https://s/',
                                                      previous_entries=previous)}
    result('lastmod bump: an unchanged lastmod over changed content is reported',
           'sitemap_lastmod_not_bumped' in checks, str(sorted(checks)))

    unchanged = {i['check'] for i in sitemap_audit.audit(
        data, [page('https://s/a/', main_text_hash='old')], 'https://s/',
        previous_entries=previous)}
    result('lastmod bump: unchanged content is not reported',
           'sitemap_lastmod_not_bumped' not in unchanged, str(sorted(unchanged)))

    result('lastmod bump: the check is silent without a previous crawl',
           'sitemap_lastmod_not_bumped' not in
           {i['check'] for i in sitemap_audit.audit(data, pages, 'https://s/')})

    # An edit made on the day the day-only lastmod already names cannot be
    # bumped any further, so it is not stale.
    today = datetime.now(timezone.utc).date().isoformat()
    same_day = dict(data, entries={'https://s/a/': {'lastmod': today,
                                                    'sitemap': 'https://s/sitemap.xml'}})
    result('lastmod bump: a same-day edit under a same-day lastmod is not reported',
           'sitemap_lastmod_not_bumped' not in
           {i['check'] for i in sitemap_audit.audit(
               same_day, pages, 'https://s/',
               previous_entries={'https://s/a/': {'lastmod': today, 'main_text_hash': 'old'}})})


def test_coverage_lists():
    """The three lists the UI downloads."""
    _, data, _ = dirty_audit()
    lists = sitemap_audit.coverage(data, DIRTY_PAGES, DIRTY)

    result('coverage: a crawled URL that is listed lands in in_both',
           f'{DIRTY}/ok/' in lists['in_both'] and f'{DIRTY}/gone/' in lists['in_both'],
           str(lists['in_both']))
    result('coverage: a crawled URL nobody lists lands in crawled_only',
           lists['crawled_only'] == [f'{DIRTY}/orphan/'], str(lists['crawled_only']))
    result('coverage: a listed URL never crawled lands in sitemap_only',
           lists['sitemap_only'] == [f'{DIRTY}/private/secret/'], str(lists['sitemap_only']))
    result('coverage: non-HTML crawl rows are left out of every list',
           not any(f'{DIRTY}/logo.png' in bucket for bucket in lists.values()))
    result('coverage: the three lists never overlap',
           not (set(lists['in_both']) & set(lists['crawled_only']))
           and not (set(lists['sitemap_only']) & set(lists['in_both'])))


def test_file_summaries():
    """The per-file rows the API returns."""
    _, data, _ = dirty_audit()
    rows = {row['url']: row for row in sitemap_audit.file_summaries(data)}

    pages = rows.get(f'{DIRTY}/sitemap-pages.xml', {})
    result('summaries: a healthy file reports status, entries, bytes and no errors',
           pages.get('status') == 200 and pages.get('entries') == 11
           and pages.get('bytes', 0) > 0 and pages.get('errors') == []
           and pages.get('is_index') is False, str(pages))

    missing = rows.get(f'{DIRTY}/sitemap-missing.xml', {})
    result('summaries: a 404 file reports its status and the error text',
           missing.get('status') == 404 and missing.get('errors'), str(missing))

    result('summaries: the index row is flagged and names no parent',
           rows[f'{DIRTY}/sitemap_index.xml']['is_index'] is True
           and rows[f'{DIRTY}/sitemap_index.xml']['parent'] is None)
    result('summaries: a child row names the index that listed it',
           rows[f'{DIRTY}/sitemap-pages.xml']['parent'] == f'{DIRTY}/sitemap_index.xml')


def test_broken_input_never_raises():
    """A dead host, garbage XML and a missing page dict are all survivable."""
    class FakeResponse:
        def __init__(self, status, content):
            self.status_code, self.content = status, content
            self.headers = {}

    class FakeSession:
        def get(self, url, timeout=None):
            if 'boom' in url:
                raise OSError('connection refused')
            if 'garbage' in url:
                return FakeResponse(200, b'<<< not xml at all')
            if 'truncated' in url:
                return FakeResponse(200, b'\x1f\x8b\x08 not really gzip')
            return FakeResponse(500, b'')

    data = sitemap_audit.fetch_sitemaps(
        FakeSession(), ['https://s/boom.xml', 'https://s/garbage.xml',
                        'https://s/truncated.xml.gz', 'https://s/five-hundred.xml'])
    kinds = {e['kind'] for e in data['errors']}
    result('robustness: every failure mode is recorded instead of raised',
           kinds == {'fetch_failed', 'parse_error', 'http_error'}, str(data['errors']))
    result('robustness: a failed fetch still produces a file row',
           len(data['files']) == 4 and data['entries'] == {}, str(data['files']))

    issues = sitemap_audit.audit(data, [], 'https://s/')
    result('robustness: auditing nothing but failures yields file errors and no crash',
           {i['check'] for i in issues} == {'sitemap_missing', 'sitemap_file_error'},
           str(sorted({i['check'] for i in issues})))
    result('robustness: audit tolerates an empty sitemap_data',
           sitemap_audit.audit({}, [], 'https://s/')[0]['check'] == 'sitemap_missing')
    result('robustness: coverage tolerates empty input',
           sitemap_audit.coverage({}, []) ==
           {'in_both': [], 'crawled_only': [], 'sitemap_only': []})


def test_tracking_params_are_not_a_mismatch():
    """A tracking parameter must not read as a coverage gap or a mismatch."""
    entries = {'https://s/a/?utm_source=nl': {'lastmod': PAST, 'sitemap': 'https://s/sitemap.xml'}}
    data = {'files': [{'url': 'https://s/sitemap.xml', 'parent': None, 'status': 200,
                       'entries': 1, 'bytes': 100, 'is_index': False,
                       'error': '', 'error_kind': ''}],
            'entries': entries, 'errors': [], 'duplicates': {}}
    issues = sitemap_audit.audit(data, [page('https://s/a/')], 'https://s/')
    result('tracking: a utm parameter matches the clean crawled URL',
           not issues, str([(i['check'], i['url']) for i in issues]))

    lists = sitemap_audit.coverage(data, [page('https://s/a/')], 'https://s/')
    result('tracking: the two forms are one entry in the coverage lists',
           lists['in_both'] == ['https://s/a/'] and not lists['sitemap_only'], str(lists))


def test_clean_sitemap_produces_nothing():
    """The whole point: a correct sitemap must be silent."""
    session = requests.Session()
    sitemaps = sitemap_audit.discover_sitemaps(session, CLEAN)
    data = sitemap_audit.fetch_sitemaps(session, sitemaps)
    issues = sitemap_audit.audit(data, CLEAN_PAGES, CLEAN,
                                 robots_checker=lambda url: True)
    result('clean: a correct sitemap produces no issues at all',
           issues == [], str([(i['check'], i['url']) for i in issues]))
    result('clean: its three URLs were still parsed',
           len(data['entries']) == 3 and data['files'][0]['entries'] == 3,
           str(data['files']))
    result('clean: coverage shows full overlap',
           len(sitemap_audit.coverage(data, CLEAN_PAGES, CLEAN)['in_both']) == 3)


def test_issues_use_the_shared_factory_shape():
    """Every issue carries the fields the API, UI and exports expect."""
    _, _, issues = dirty_audit()
    fields = {'id', 'url', 'check', 'type', 'category', 'issue', 'details',
              'evidence', 'how_to_fix', 'docs_url'}
    bad_shape = [i['check'] for i in issues if set(i) != fields]
    result('shape: every issue has exactly the issue_factory fields', not bad_shape,
           str(bad_shape))

    bad_category = [i['check'] for i in issues if i['category'] != 'Sitemap']
    result('shape: every issue is categorised Sitemap', not bad_category, str(bad_category))

    allowed = {'snippet', 'selector', 'line', 'expected', 'found', 'related_urls'}
    bad_evidence = [i['check'] for i in issues if set(i['evidence']) - allowed]
    result('shape: evidence never carries an unknown key', not bad_evidence, str(bad_evidence))

    no_fix = [i['check'] for i in issues if not i['how_to_fix']]
    result('shape: every issue says how to fix it', not no_fix, str(no_fix))

    ids = [i['id'] for i in issues]
    result('shape: issue ids are unique within one audit',
           len(ids) == len(set(ids)), str(len(ids) - len(set(ids))))

    _cache.pop('dirty', None)
    again = dirty_audit()[2]
    result('shape: a second audit of the same site yields the same ids',
           [i['id'] for i in again] == ids)


def test_checks_that_nothing_had_covered():
    """Three sitemap checks shipped with no test at all.

    They are the ones a healthy site never triggers - a sitemap that parses
    but lists nothing, an audit that gave up on the number of files, a URL
    answering 5xx - so they sat unverified while their neighbours ran daily.
    """
    # A file that parsed cleanly and listed no URLs at all.
    empty = {i['check'] for i in sitemap_audit.audit(
        {'files': [{'url': 'https://s/sitemap.xml', 'status': 200, 'entries': 0,
                    'bytes': 120, 'is_index': False, 'error': '', 'error_kind': ''}],
         'entries': {}, 'errors': [], 'duplicates': {}}, [], 'https://s/')}
    result('uncovered: a sitemap that parses but lists nothing is reported',
           'sitemap_empty' in empty, str(sorted(empty)))
    result('uncovered: an empty sitemap is not also reported as missing',
           'sitemap_missing' not in empty, str(sorted(empty)))

    # The walk stopping early is worth saying out loud: the audit that
    # follows is then incomplete, and silence would imply otherwise.
    capped = {i['check'] for i in sitemap_audit.audit(
        {'files': [{'url': 'https://s/sitemap.xml', 'status': 200, 'entries': 1,
                    'bytes': 200, 'is_index': True, 'error': '', 'error_kind': ''}],
         'entries': {'https://s/a': {'lastmod': PAST, 'sitemap': 'https://s/sitemap.xml'}},
         'errors': [{'kind': 'file_limit', 'detail': 'stopped after 50 files'}],
         'duplicates': {}},
        [{'url': 'https://s/a', 'status_code': 200, 'content_type': 'text/html'}],
        'https://s/')}
    result('uncovered: giving up on the file count is reported',
           'sitemap_file_limit' in capped, str(sorted(capped)))

    # A listed URL the server cannot serve.
    broken = sitemap_audit.audit(
        {'files': [{'url': 'https://s/sitemap.xml', 'status': 200, 'entries': 2,
                    'bytes': 200, 'is_index': False, 'error': '', 'error_kind': ''}],
         'entries': {'https://s/ok': {'lastmod': PAST, 'sitemap': 'https://s/sitemap.xml'},
                     'https://s/boom': {'lastmod': PAST, 'sitemap': 'https://s/sitemap.xml'}},
         'errors': [], 'duplicates': {}},
        [{'url': 'https://s/ok', 'status_code': 200, 'content_type': 'text/html'},
         {'url': 'https://s/boom', 'status_code': 503, 'content_type': 'text/html'}],
        'https://s/')
    found = [i for i in broken if i['check'] == 'sitemap_url_server_error']
    result('uncovered: a sitemap URL answering 5xx is an error',
           found and found[0]['type'] == 'error', str(sorted({i['check'] for i in broken})))

    # And the quiet halves: a healthy sitemap raises none of the three.
    healthy = {i['check'] for i in sitemap_audit.audit(
        {'files': [{'url': 'https://s/sitemap.xml', 'status': 200, 'entries': 1,
                    'bytes': 200, 'is_index': False, 'error': '', 'error_kind': ''}],
         'entries': {'https://s/ok': {'lastmod': PAST, 'sitemap': 'https://s/sitemap.xml'}},
         'errors': [], 'duplicates': {}},
        [{'url': 'https://s/ok', 'status_code': 200, 'content_type': 'text/html'}],
        'https://s/')}
    result('uncovered: a healthy sitemap raises none of the three',
           not ({'sitemap_empty', 'sitemap_file_limit', 'sitemap_url_server_error'} & healthy),
           str(sorted(healthy)))


TESTS = (
    test_checks_that_nothing_had_covered,
    test_discovery_reads_robots_then_falls_back,
    test_fetch_walks_index_and_gzip,
    test_file_cap_stops_the_walk,
    test_uncrawlable_sitemap_urls,
    test_robots_check_is_skipped_without_a_checker,
    test_lastmod_checks,
    test_lastmod_parsing,
    test_hygiene_checks,
    test_crawled_pages_missing_from_the_sitemap,
    test_grouping_rule,
    test_lastmod_not_bumped_needs_the_previous_crawl,
    test_coverage_lists,
    test_file_summaries,
    test_broken_input_never_raises,
    test_tracking_params_are_not_a_mismatch,
    test_clean_sitemap_produces_nothing,
    test_issues_use_the_shared_factory_shape,
)


def main():
    serve(make_handler(DIRTY_ROUTES), BASE_PORT)
    serve(make_handler(CLEAN_ROUTES), BASE_PORT + 1)

    for test in TESTS:
        print(f'\n{test.__name__}:')
        try:
            test()
        except Exception as exc:  # a raised test is a failed test
            result(test.__name__, False, f'raised {type(exc).__name__}: {exc}')

    print('\n' + '=' * 64)
    print(f'  {len(PASSED)} passed, {len(FAILED)} failed')
    if FAILED:
        print('  failed: ' + ', '.join(FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
