#!/usr/bin/env python3
"""What a finished crawl concludes, against a real fixture site.

The other half of the fixture suite is about how the crawler behaves
while it runs. This half is about what it decides afterwards: the
regression report, the sitemap audit, selector rules, date problems,
and the one test that matters most here - that every check can actually
fire from a real crawl rather than only from a hand-built page dict.

That test exists because four checks were once reachable only in unit
tests: the crawler never recorded the field they read, so they could
not fire on any real site. Nothing caught it until something looked.

Split from fixture_tests.py when it passed 1,000 lines; the setup is
imported rather than copied so the two cannot drift apart.
"""

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.crawler import WebCrawler  # noqa: E402
from fixture_tests import (BASE_PORT, FAILED, PASSED, crawl, html,  # noqa: E402
                           make_handler, png, result, serve)


def test_crawl_reports_what_changed_since_the_previous_crawl():
    """Two crawls of a site that lost a paragraph and a link, end to end."""
    import tempfile
    from src import crawl_db

    port = BASE_PORT + 17
    kept = 'The first paragraph stays exactly as it was across both crawls. '
    doomed = 'This whole sentence about pricing disappears in the second crawl. '
    routes = {
        '/': html(f'<main><h1>Home</h1><p>{kept * 6}{doomed * 3}</p>'
                  f'<a href="/kept">kept</a><a href="/removed">removed</a></main>'),
        '/kept': html('<h1>Kept</h1><p>' + ('kept page copy ' * 60) + '</p>'),
        '/removed': html('<h1>Target</h1><p>' + ('target page copy ' * 60) + '</p>'),
    }
    srv = serve(make_handler(routes), port)
    original_db = crawl_db.DB_FILE
    handle, path = tempfile.mkstemp(suffix='.db')
    os.close(handle)

    def run_crawl():
        crawler = WebCrawler()
        crawler.config.update({
            'delay': 0.02, 'concurrency': 3, 'max_urls': 50, 'max_depth': 3,
            'crawl_external': False, 'crawl_images': False,
            'discover_sitemaps': False, 'respect_robots': False,
        })
        ok, message = crawler.start_crawl(f'http://127.0.0.1:{port}/', user_id=1, session_id='s')
        assert ok, message
        deadline = time.time() + 60
        while crawler.is_running and time.time() < deadline:
            time.sleep(0.1)
        return crawler

    try:
        crawl_db.DB_FILE = path
        crawl_db.init_crawl_tables()

        first = run_crawl()
        result('integrity: first crawl has nothing to compare with',
               not [i for i in first.issue_detector.get_issues()
                    if i.get('check') in ('content_removed', 'internal_link_removed')])

        # The site loses a paragraph and an internal link
        routes['/'] = html(f'<main><h1>Home</h1><p>{kept * 6}</p>'
                           f'<a href="/kept">kept</a></main>')

        second = run_crawl()
        issues = second.issue_detector.get_issues()
        removed = [i for i in issues if i.get('check') == 'content_removed']
        lost_links = [i for i in issues if i.get('check') == 'internal_link_removed']

        result('integrity: removed text is reported', len(removed) == 1,
               str([(i['url'], i['details'][:70]) for i in removed]))
        result('integrity: the removed sentence is in the evidence',
               removed and 'pricing disappears' in removed[0]['evidence'].get('snippet', ''),
               str(removed[0]['evidence'])[:160] if removed else '')
        result('integrity: the lost internal link is reported',
               len(lost_links) == 1 and any('/removed' in u for u in
                                            lost_links[0]['evidence'].get('related_urls', [])),
               str([(i['url'], i['evidence']) for i in lost_links])[:200])
        result('integrity: the unchanged page is not reported',
               not [i for i in removed if i['url'].endswith('/kept')])

        # Stable ids: the same defect keeps its id, so it can be tracked
        from src.core.crawl_diff import regression_report
        report = regression_report(first.crawl_id, second.crawl_id)
        # An unchanged defect must keep its id between crawls, or every crawl
        # reports everything as new and nothing as fixed
        result('integrity: regression report separates new from still-open',
               report['counts']['still_open'] > 0 and report['counts']['new'] >= 1
               and isinstance(report['counts']['by_severity'], dict),
               str(report['counts']))
    finally:
        srv.shutdown()
        crawl_db.DB_FILE = original_db
        os.unlink(path)


def test_crawl_audits_the_sitemap():
    """The crawl checks its own sitemap: a dead entry and a missing page."""
    port = BASE_PORT + 19
    base = f'http://127.0.0.1:{port}'
    sitemap = ('<?xml version="1.0" encoding="UTF-8"?>'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               f'<url><loc>{base}/</loc><lastmod>2026-01-01T00:00:00+00:00</lastmod></url>'
               f'<url><loc>{base}/gone</loc><lastmod>2026-01-01T00:00:00+00:00</lastmod></url>'
               '</urlset>')
    srv = serve(make_handler({
        '/': html('<h1>Home</h1><p>' + ('home copy ' * 60) + '</p>'
                  '<a href="/orphan">orphan</a>'),
        '/orphan': html('<h1>Orphan</h1><p>' + ('orphan copy ' * 60) + '</p>'),
        '/robots.txt': f'User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml',
        '/sitemap.xml': sitemap,
    }), port)
    try:
        # Sitemap discovery on, as in a real crawl: that is how a URL listed
        # only in the sitemap gets fetched and its status becomes known
        crawler = crawl(f'{base}/', discover_sitemaps=True)
        issues = crawler.issue_detector.get_issues()
        checks = {i.get('check') for i in issues}

        result('sitemap: a sitemap URL that 404s is an error',
               'sitemap_url_not_found' in checks,
               str([i['details'][:70] for i in issues if i.get('check') == 'sitemap_url_not_found']))
        result('sitemap: a crawled page missing from the sitemap is reported',
               'sitemap_missing_crawled_url' in checks,
               str([i['details'][:70] for i in issues if i.get('check') == 'sitemap_missing_crawled_url']))
        result('sitemap: entries were read for the freshness checks',
               len(crawler.sitemap_lastmod) == 2, str(crawler.sitemap_lastmod))
        result('sitemap: per-file summaries are kept for the API',
               len(crawler.sitemap_files) == 1 and crawler.sitemap_files[0]['entries'] == 2,
               str(crawler.sitemap_files))
        # The orphan is crawled but unlisted; /gone counts as covered because
        # the crawl did reach it (its 404 is reported as its own issue)
        coverage = crawler.sitemap_coverage
        result('sitemap: coverage lists are kept',
               coverage.get('crawled_only') == [f'{base}/orphan']
               and f'{base}/' in coverage.get('in_both', []),
               str(coverage)[:200])
    finally:
        srv.shutdown()


def test_a_selector_rule_runs_against_a_real_crawl():
    """The feature request's own cta-label example must actually run.

    Selector rules read the markup, which a crawl does not store - so the rule
    loaded, reported "skipped: no page HTML available", and the example in the
    request could never fire. The crawl now keeps the markup while those rules
    pass, and only when such a rule exists.
    """
    import json
    import tempfile
    from src.core import rules_engine

    port = BASE_PORT + 23
    srv = serve(make_handler({
        '/': html('<h1>Home</h1><p>' + ('copy ' * 60) + '</p>'
                  '<a class="btn" href="/contact">Get a Free Quote</a>'),
        '/contact': html('<h1>Contact</h1><p>' + ('copy ' * 60) + '</p>'
                         '<a class="btn" href="/">Book a Discovery Call</a>'),
    }), port)

    rules_dir = tempfile.mkdtemp()
    original_dir = rules_engine.DATA_DIR
    try:
        rules_engine.DATA_DIR = rules_dir
        os.makedirs(os.path.join(rules_dir, 'rules'), exist_ok=True)
        with open(os.path.join(rules_dir, 'rules', '127.0.0.1.json'), 'w') as handle:
            json.dump({'rules': [{
                'id': 'cta-label', 'severity': 'warning',
                'selector': 'a.btn', 'must_equal': 'Book a Discovery Call',
            }]}, handle)

        crawler = crawl(f'http://127.0.0.1:{port}/')
        found = [i for i in crawler.issue_detector.get_issues()
                 if i.get('check') == 'rule:cta-label']

        result('selector rule: the wrong label is reported', len(found) == 1,
               str([(i['url'], i['details'][:70]) for i in found]))
        result('selector rule: it names what it expected and found',
               found and 'Book a Discovery Call' in str(found[0].get('evidence')),
               str(found[0].get('evidence'))[:160] if found else '')
        result('selector rule: the page with the right label is not reported',
               all(not i['url'].endswith('/contact') for i in found),
               str([i['url'] for i in found]))
        result('selector rule: the markup is not stored on the crawl result',
               all('html' not in page for page in crawler.crawl_results),
               str([k for k in (crawler.crawl_results[0] or {}) if k == 'html']))
    finally:
        rules_engine.DATA_DIR = original_dir
        srv.shutdown()


def test_content_changed_but_lastmod_did_not():
    """A CMS that edits a page without bumping <lastmod> is the check's point.

    It needs the previous crawl's lastmod values, which were never stored - so
    the check existed, passed its unit test, and could not fire on a real site.
    """
    import tempfile
    from src import crawl_db

    port = BASE_PORT + 22
    base = f'http://127.0.0.1:{port}'
    lastmod = '2026-01-01T00:00:00+00:00'
    routes = {
        '/robots.txt': f'User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml',
        '/sitemap.xml': ('<?xml version="1.0" encoding="UTF-8"?>'
                         '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                         f'<url><loc>{base}/</loc><lastmod>{lastmod}</lastmod></url>'
                         '</urlset>'),
        '/': html('<h1>Home</h1><p>' + ('the original copy ' * 60) + '</p>'),
    }
    srv = serve(make_handler(routes), port)
    original_db = crawl_db.DB_FILE
    handle, path = tempfile.mkstemp(suffix='.db')
    os.close(handle)

    def run_crawl():
        crawler = WebCrawler()
        crawler.config.update({'delay': 0.02, 'concurrency': 2, 'max_urls': 20,
                               'max_depth': 2, 'crawl_external': False,
                               'crawl_images': False, 'discover_sitemaps': False,
                               'respect_robots': False})
        ok, message = crawler.start_crawl(f'{base}/', user_id=1, session_id='s')
        assert ok, message
        deadline = time.time() + 60
        while crawler.is_running and time.time() < deadline:
            time.sleep(0.1)
        return crawler

    try:
        crawl_db.DB_FILE = path
        crawl_db.init_crawl_tables()

        first = run_crawl()
        result('lastmod: the first crawl has nothing to compare with',
               not [i for i in first.issue_detector.get_issues()
                    if i.get('check') == 'sitemap_lastmod_not_bumped'])
        stored = crawl_db.get_crawl_by_id(first.crawl_id) or {}
        result('lastmod: the crawl stored the sitemap\'s lastmod values',
               (stored.get('sitemap_entries') or {}).get(f'{base}/') == lastmod,
               str(stored.get('sitemap_entries'))[:120])

        # The page is edited; the sitemap still claims the same lastmod
        routes['/'] = html('<h1>Home</h1><p>' + ('completely rewritten copy ' * 60) + '</p>')

        second = run_crawl()
        found = [i for i in second.issue_detector.get_issues()
                 if i.get('check') == 'sitemap_lastmod_not_bumped']
        result('lastmod: changed content with an unchanged lastmod is reported',
               len(found) == 1, str([i['details'][:90] for i in found]))
    finally:
        srv.shutdown()
        crawl_db.DB_FILE = original_db
        os.unlink(path)


def test_crawl_reports_date_problems():
    """Date checks run over a crawled page and flag a future modified date."""
    port = BASE_PORT + 18
    future = '2027-06-01T10:00:00+00:00'
    jsonld = ('{"@context":"https://schema.org","@type":"BlogPosting",'
              '"headline":"Post","datePublished":"2026-01-01T00:00:00+00:00",'
              f'"dateModified":"{future}"}}')
    srv = serve(make_handler({
        '/': ('<html><head><title>Post</title>'
              f'<script type="application/ld+json">{jsonld}</script>'
              '</head><body><main><h1>Post</h1><p>' + ('body copy ' * 60) + '</p></main></body></html>'),
    }), port)
    try:
        crawler = crawl(f'http://127.0.0.1:{port}/', max_depth=0)
        issues = crawler.issue_detector.get_issues()
        future_dates = [i for i in issues if i.get('check') == 'future_date']
        result('dates: a future dateModified is an error',
               len(future_dates) == 1 and future_dates[0]['type'] == 'error',
               str([(i['issue'], i['details'][:60]) for i in future_dates]))
        result('dates: a correct page raises no missing-date issue',
               not [i for i in issues if i.get('check') == 'missing_dates'],
               str([i['details'][:60] for i in issues if i.get('check') == 'missing_dates']))
    finally:
        srv.shutdown()


def test_checks_can_actually_fire_from_a_real_crawl():
    """Every field a check reads must be one the crawl actually stores.

    Four checks passed their unit tests while being dead in practice, because
    the crawler never populated the field they read: image loading and bytes,
    the raw JSON-LD a parse error lives in, and the link's authored href.
    """
    port = BASE_PORT + 20
    broken_jsonld = '{"@context": "https://schema.org", "@type": "WebPage",}'
    body = (
        '<script type="application/ld+json">{"@type":"Organization","name":"X"}</script>'
        f'<script type="application/ld+json">{broken_jsonld}</script>'
        '<main><h1>Home</h1>'
        '<img src="/hero.png" loading="lazy" alt="hero">'
        '<img src="/big.png" alt="big">'
        '<a href="/relative-link">relative</a>'
        f'<a href="http://127.0.0.1:{port}/absolute-link">absolute</a>'
        f'<p>{"body copy " * 60}</p></main>')

    heavy = b'\x89PNG\r\n\x1a\n' + b'x' * (400 * 1024)
    srv = serve(make_handler({
        '/': html(body),
        '/relative-link': html('<h1>Relative</h1><p>' + ('copy ' * 60) + '</p>'),
        '/absolute-link': html('<h1>Absolute</h1><p>' + ('copy ' * 60) + '</p>'),
        '/hero.png': png(),
        '/big.png': heavy,
    }), port)
    try:
        crawler = crawl(f'http://127.0.0.1:{port}/', crawl_images=True, max_depth=1)
        home = next(r for r in crawler.crawl_results if r['url'].endswith(':%d/' % port))

        images = {img['src'].rsplit('/', 1)[-1]: img for img in home.get('images', [])}
        result('storage: an image records its loading attribute',
               images.get('hero.png', {}).get('loading') == 'lazy', str(images.get('hero.png')))
        result('storage: an image records its transfer size',
               (images.get('big.png', {}).get('bytes') or 0) > 100000,
               str(images.get('big.png')))

        result('storage: a JSON-LD block that fails to parse is kept',
               len(home.get('json_ld_errors') or []) == 1
               and 'WebPage' in (home['json_ld_errors'][0].get('snippet') or ''),
               str(home.get('json_ld_errors')))
        result('storage: the blocks that do parse are still parsed',
               len(home.get('json_ld') or []) == 1, str(len(home.get('json_ld') or [])))
        result('storage: raw blocks are kept beside them',
               len(home.get('json_ld_raw') or []) == 2, str(len(home.get('json_ld_raw') or [])))

        hrefs = {l.get('anchor_text'): l.get('href') for l in crawler.link_manager.all_links}
        result('storage: a link keeps the href as authored',
               hrefs.get('relative') == '/relative-link'
               and hrefs.get('absolute', '').startswith('http://'),
               str(hrefs))

        # And the checks that read those fields now have something to judge
        from src.core.technical_checks import technical_issues
        found = technical_issues(crawler.crawl_results, crawler.link_manager.all_links,
                                 f'http://127.0.0.1:{port}')
        checks = {issue['check'] for issue in found}
        result('checks: the oversized-image check can fire', 'image_too_heavy' in checks,
               str(sorted(checks)))
        result('checks: the lazy-above-the-fold check can fire',
               'image_lazy_above_fold' in checks, str(sorted(checks)))
        result('checks: the JSON-LD parse check can fire',
               'jsonld_parse_error' in checks, str(sorted(checks)))
    finally:
        srv.shutdown()


def test_every_issue_has_the_same_shape():
    """One issue shape everywhere, whichever check raised it.

    The older per-page checks named themselves in prose and carried no slug,
    no stable id and no docs link - so a regression report could not track
    them and the UI could not group them with the rest.
    """
    port = BASE_PORT + 21
    srv = serve(make_handler({
        '/': '<html><head></head><body><p>tiny</p></body></html>',
        '/second': html('<h1>Second</h1><p>' + ('words ' * 40) + '</p>'),
    }), port)
    try:
        crawler = crawl(f'http://127.0.0.1:{port}/')
        issues = crawler.issue_detector.get_issues()
        result('shape: the crawl reported something to check', len(issues) >= 3, str(len(issues)))

        missing = [i for i in issues if not i.get('check')]
        result('shape: every issue carries a machine-readable check slug', not missing,
               str([i.get('issue') for i in missing][:4]))
        result('shape: every issue carries a stable id',
               all(i.get('id') and len(i['id']) == 40 for i in issues),
               str([i.get('id') for i in issues][:2]))
        result('shape: every issue links to what the check means',
               all(str(i.get('docs_url', '')).startswith('/checks#') for i in issues),
               str({i.get('docs_url') for i in issues}))
        result('shape: slugs are slugs, not prose',
               all(re.fullmatch(r'[a-z0-9_]+', i['check']) for i in issues),
               str(sorted({i['check'] for i in issues})[:6]))

        # The id must survive a detail that changes between crawls
        again = crawl(f'http://127.0.0.1:{port}/')
        first = {i['id'] for i in issues}
        second = {i['id'] for i in again.issue_detector.get_issues()}
        carried = first & second
        result('shape: the same defect keeps its id across crawls',
               len(carried) >= len(first) - 1,
               f'{len(carried)} of {len(first)} ids carried over')
    finally:
        srv.shutdown()


TESTS = (
    test_crawl_reports_what_changed_since_the_previous_crawl,
    test_crawl_audits_the_sitemap,
    test_a_selector_rule_runs_against_a_real_crawl,
    test_content_changed_but_lastmod_did_not,
    test_crawl_reports_date_problems,
    test_checks_can_actually_fire_from_a_real_crawl,
    test_every_issue_has_the_same_shape,
)


def main():
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
