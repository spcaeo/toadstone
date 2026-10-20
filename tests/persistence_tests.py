#!/usr/bin/env python3
"""What a crawl leaves behind: the database half of the fixture suite.

Storing a page and reading it back, upgrading a database written by an
older version, retention, and the numbers a reloaded crawl reports.

These were in fixture_tests.py until it passed 1,000 lines. They share
its setup, which is imported rather than copied so the two suites cannot
drift apart.

The schema-upgrade test earns its place: a column added without a
migration once produced a crawl that saved zero issues and reported
success.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.crawler import WebCrawler  # noqa: E402
from fixture_tests import (BASE_PORT, FAILED, PASSED, crawl, html,  # noqa: E402
                           make_handler, png, result, serve)
def _chrome(label):
    """Header/nav/footer markup shared by several fixture pages."""
    return (f'<header><h1>Site {label}</h1><a href="/">home</a></header>'
            f'<nav><a href="/a">alpha</a><a href="/b">beta</a></nav>')


def test_page_body_is_stored_with_fingerprints():
    """The crawl keeps main text, its hashes, headings and HTTP metadata."""
    port = BASE_PORT + 15
    article = ('<main><h2>  Padded heading</h2><p>' + ('the shared body sentence ' * 40) +
               '</p><h3>Sub  heading</h3><p>tail</p></main>')
    other_chrome_same_body = f'<html><head><title>Two</title></head><body>{_chrome("Two")}{article}<footer>bye</footer></body></html>'
    base = make_handler({
        '/': f'<html><head><title>One</title></head><body>{_chrome("One")}{article}<footer>hello</footer></body></html>',
        '/a': other_chrome_same_body,
        '/b': html('<h2>Different</h2><p>' + ('a completely different sentence ' * 40) + '</p>'),
    })

    class CachingHandler(base):
        """Serves the caching headers a real site sends."""

        def end_headers(self):
            self.send_header('Cache-Control', 'max-age=120')
            self.send_header('ETag', 'W/"fixture-etag"')
            self.send_header('Last-Modified', 'Mon, 01 Jan 2026 00:00:00 GMT')
            super().end_headers()

    srv = serve(CachingHandler, port)
    try:
        crawler = crawl(f'http://127.0.0.1:{port}/')
        by_url = {r['url']: r for r in crawler.crawl_results}
        home = by_url[f'http://127.0.0.1:{port}/']
        same = by_url[f'http://127.0.0.1:{port}/a']
        other = by_url[f'http://127.0.0.1:{port}/b']

        result('body: main text stored', 'shared body sentence' in home.get('main_text', ''),
               home.get('main_text', '')[:60])
        result('body: chrome is stripped out',
               'alpha' not in home.get('main_text', '') and 'hello' not in home.get('main_text', ''),
               home.get('main_text', '')[:80])
        result('body: same content under different chrome hashes alike',
               home.get('main_text_hash') and home['main_text_hash'] == same.get('main_text_hash'),
               f"{home.get('main_text_hash')} vs {same.get('main_text_hash')}")
        result('body: different content hashes differently',
               home.get('main_text_hash') != other.get('main_text_hash'))
        result('body: word count counts the body only',
               home.get('main_word_count', 0) > 100, str(home.get('main_word_count')))

        from src.utils.text_fingerprint import similarity
        result('body: simhash is comparable across pages',
               similarity(int(home['simhash']), int(same['simhash'])) == 1.0
               and similarity(int(home['simhash']), int(other['simhash'])) < 0.9,
               f"same={similarity(int(home['simhash']), int(same['simhash'])):.2f} "
               f"other={similarity(int(home['simhash']), int(other['simhash'])):.2f}")

        headings = home.get('headings', [])
        result('headings: captured in document order with levels',
               [h['level'] for h in headings] == [1, 2, 3], str([(h['level'], h['text']) for h in headings]))
        result('headings: dom_order is recorded',
               [h['dom_order'] for h in headings] == [0, 1, 2], str([h.get('dom_order') for h in headings]))
        problems = {(p['problem']) for p in home.get('heading_text_issues', [])}
        result('headings: stray whitespace is flagged',
               {'leading_whitespace', 'double_space'} <= problems, str(home.get('heading_text_issues')))

        result('http: response headers kept',
               home.get('cache_control') == 'max-age=120'
               and home.get('etag') == 'W/"fixture-etag"'
               and home.get('last_modified', '').startswith('Mon, 01 Jan 2026')
               and isinstance(home.get('content_length'), int),
               str({k: home.get(k) for k in ('cache_control', 'etag', 'last_modified', 'content_length')}))
        result('http: ttfb recorded', isinstance(home.get('ttfb'), (int, float)), str(home.get('ttfb')))

        link = next(l for l in crawler.link_manager.all_links if l['anchor_text'] == 'alpha')
        result('links: nav placement recorded', link['in_nav'] and not link['in_main'],
               str({k: link[k] for k in ('in_nav', 'in_main', 'in_heading', 'rel', 'nofollow')}))
    finally:
        srv.shutdown()


def test_existing_database_upgrades_to_the_new_columns():
    """A database from an older build must keep saving after an upgrade.

    CREATE TABLE IF NOT EXISTS silently does nothing when the table is already
    there, so every added column needs its own migration; without one the app
    starts fine and then drops every issue it tries to save.
    """
    import sqlite3
    import tempfile
    from src import crawl_db

    original_db = crawl_db.DB_FILE
    handle, path = tempfile.mkstemp(suffix='.db')
    os.close(handle)
    try:
        # A database as an older Toadstone build left it
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT);
            CREATE TABLE crawls (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, session_id TEXT,
                base_url TEXT, base_domain TEXT, status TEXT, config_snapshot TEXT,
                urls_discovered INTEGER DEFAULT 0, urls_crawled INTEGER DEFAULT 0,
                max_depth_reached INTEGER DEFAULT 0,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMP,
                last_saved_at TIMESTAMP, peak_memory_mb REAL, estimated_size_mb REAL,
                can_resume BOOLEAN DEFAULT 1, resume_checkpoint TEXT);
            CREATE TABLE crawled_urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT, crawl_id INTEGER NOT NULL, url TEXT NOT NULL,
                status_code INTEGER, content_type TEXT, size INTEGER, is_internal BOOLEAN,
                depth INTEGER, title TEXT, meta_description TEXT, h1 TEXT, h2 TEXT, h3 TEXT,
                word_count INTEGER, canonical_url TEXT, lang TEXT, charset TEXT, viewport TEXT,
                robots TEXT, meta_tags TEXT, og_tags TEXT, twitter_tags TEXT, json_ld TEXT,
                analytics TEXT, images TEXT, hreflang TEXT, schema_org TEXT, redirects TEXT,
                linked_from TEXT, external_links INTEGER, internal_links INTEGER,
                response_time REAL, javascript_rendered BOOLEAN DEFAULT 0,
                crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE crawl_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT, crawl_id INTEGER NOT NULL,
                source_url TEXT, target_url TEXT, anchor_text TEXT, is_internal BOOLEAN,
                target_domain TEXT, target_status INTEGER, placement TEXT);
            CREATE TABLE crawl_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT, crawl_id INTEGER NOT NULL,
                url TEXT NOT NULL, type TEXT, category TEXT, issue TEXT, details TEXT,
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE crawl_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT, crawl_id INTEGER NOT NULL,
                url TEXT, depth INTEGER, status TEXT DEFAULT 'pending');
        """)
        conn.commit()
        conn.close()

        crawl_db.DB_FILE = path
        crawl_db.init_crawl_tables()  # must migrate, not just skip

        crawl_id = crawl_db.create_crawl(user_id=1, session_id='s', base_url='http://site',
                                         base_domain='site', config_snapshot={})
        saved_page = crawl_db.save_url_batch(crawl_id, [{
            'url': 'http://site/p', 'status_code': 200, 'main_text': 'text',
            'main_text_hash': 'h', 'simhash': '1', 'main_word_count': 1,
            'headings': [], 'heading_text_issues': [], 'cache_control': 'max-age=1',
            'last_modified': '', 'etag': '', 'content_length': 10, 'ttfb': 1.0,
            'processing_time': 2.0,
        }])
        saved_links = crawl_db.save_links_batch(crawl_id, [{
            'source_url': 'http://site/p', 'target_url': 'http://site/q', 'anchor_text': 'q',
            'is_internal': True, 'rel': '', 'nofollow': False, 'link_target': '',
            'in_nav': False, 'in_main': True, 'in_heading': False,
        }])
        saved_issues = crawl_db.save_issues_batch(crawl_id, [{
            'url': 'http://site/p', 'type': 'warning', 'category': 'Content',
            'issue': 'Thin Content', 'details': 'too short', 'id': 'abc123',
            'check': 'thin_content', 'evidence': {'found': '10 words'},
            'how_to_fix': 'Write more.', 'docs_url': '', 'first_seen_crawl': crawl_id,
        }])

        result('migration: pages still save on an upgraded database', saved_page is not False)
        result('migration: links still save', saved_links is not False)
        result('migration: issues still save', saved_issues is not False)
        loaded = crawl_db.load_crawl_issues(crawl_id)
        result('migration: the new issue fields come back',
               len(loaded) == 1 and loaded[0].get('check') == 'thin_content'
               and loaded[0].get('evidence') == {'found': '10 words'},
               str(loaded[:1]))
        page = crawl_db.load_crawled_urls(crawl_id)
        result('migration: the new page fields come back',
               len(page) == 1 and page[0].get('main_text') == 'text'
               and page[0].get('processing_time') == 2.0,
               str({k: page[0].get(k) for k in ('main_text', 'ttfb', 'processing_time')} if page else None))
    finally:
        crawl_db.DB_FILE = original_db
        os.unlink(path)


def test_a_loaded_crawl_reports_its_own_numbers():
    """Loading a saved crawl must restore what it found, not zeros.

    The discovered count and depth come from the link manager and the stats
    dict, both of which a load leaves empty - so a loaded crawl showed "0
    discovered, depth 0" beside its own 98 crawled pages.
    """
    import tempfile
    from src import crawl_db

    original_db = crawl_db.DB_FILE
    handle, path = tempfile.mkstemp(suffix='.db')
    os.close(handle)
    try:
        crawl_db.DB_FILE = path
        crawl_db.init_crawl_tables()
        crawl_id = crawl_db.create_crawl(user_id=1, session_id='s', base_url='http://site',
                                         base_domain='site', config_snapshot={})
        pages = [{'url': f'http://site/p{n}', 'status_code': 200, 'depth': n % 3,
                  'main_text': 'body', 'headings': []} for n in range(6)]
        crawl_db.save_url_batch(crawl_id, pages)
        crawl_db.update_crawl_stats(crawl_id, discovered=9, crawled=6, max_depth=2)
        crawl_db.set_crawl_status(crawl_id, 'completed')

        crawler = WebCrawler()
        crawler.load_data(crawl_db.get_crawl_by_id(crawl_id),
                          crawl_db.load_crawled_urls(crawl_id), [], [])
        status = crawler.get_status_light()

        result('loaded crawl: crawled count restored', status['stats']['crawled'] == 6,
               str(status['stats']))
        result('loaded crawl: discovered count restored, not zero',
               status['stats']['discovered'] == 9, str(status['stats']['discovered']))
        result('loaded crawl: depth restored', status['stats']['depth'] == 2,
               str(status['stats']['depth']))
    finally:
        crawl_db.DB_FILE = original_db
        os.unlink(path)


def test_retention_prunes_old_crawls():
    """keep_last_crawls deletes older crawls and takes their rows with them."""
    import tempfile
    from src import crawl_db

    original_db = crawl_db.DB_FILE
    handle, path = tempfile.mkstemp(suffix='.db')
    os.close(handle)
    try:
        crawl_db.DB_FILE = path
        crawl_db.init_crawl_tables()
        ids = []
        for n in range(4):
            crawl_id = crawl_db.create_crawl(user_id=1, session_id=f's{n}',
                                             base_url=f'http://site{n}', base_domain=f'site{n}',
                                             config_snapshot={})
            ids.append(crawl_id)
            crawl_db.save_url_batch(crawl_id, [{'url': f'http://site{n}/', 'status_code': 200,
                                                'main_text': 'body', 'headings': []}])
            crawl_db.set_crawl_status(crawl_id, 'completed')

        deleted = crawl_db.prune_old_crawls(1, 2)
        kept = [c['id'] for c in crawl_db.get_user_crawls(1)]
        result('retention: oldest crawls deleted', sorted(deleted) == sorted(ids[:2]),
               f"deleted={deleted} ids={ids}")
        result('retention: newest kept', sorted(kept) == sorted(ids[2:]), str(kept))
        result('retention: their pages went too',
               crawl_db.load_crawled_urls(ids[0]) == [], str(crawl_db.load_crawled_urls(ids[0])))
        result('retention: keep_last 0 keeps everything', crawl_db.prune_old_crawls(1, 0) == [])
    finally:
        crawl_db.DB_FILE = original_db
        os.unlink(path)


def test_stored_page_data_round_trips_through_the_database():
    """Everything the crawl stores per page must load back unchanged."""
    import tempfile
    from src import crawl_db

    original_db = crawl_db.DB_FILE
    handle, path = tempfile.mkstemp(suffix='.db')
    os.close(handle)
    try:
        crawl_db.DB_FILE = path
        crawl_db.init_crawl_tables()
        crawl_id = crawl_db.create_crawl(user_id=1, session_id='s', base_url='http://site',
                                         base_domain='site', config_snapshot={})
        page = {
            'url': 'http://site/p', 'status_code': 200, 'main_text': 'the body text',
            'main_text_hash': 'abc123', 'simhash': '12345678901234567890',
            'main_word_count': 3,
            'headings': [{'level': 1, 'text': 'Title', 'is_visible': True, 'dom_order': 0}],
            'heading_text_issues': [{'dom_order': 0, 'level': 1, 'problem': 'double_space'}],
            'cache_control': 'max-age=60', 'last_modified': 'Mon, 01 Jan 2026 00:00:00 GMT',
            'etag': 'W/"abc"', 'content_length': 4096, 'ttfb': 12.5,
        }
        crawl_db.save_url_batch(crawl_id, [page])
        crawl_db.save_links_batch(crawl_id, [{
            'source_url': 'http://site/p', 'target_url': 'http://site/q', 'anchor_text': 'q',
            'is_internal': True, 'rel': 'nofollow', 'nofollow': True, 'link_target': '_blank',
            'in_nav': False, 'in_main': True, 'in_heading': False,
        }])

        loaded = crawl_db.load_crawled_urls(crawl_id)[0]
        mismatched = [k for k, v in page.items() if loaded.get(k) != v]
        result('round-trip: every stored page field comes back', not mismatched,
               str({k: (page[k], loaded.get(k)) for k in mismatched}))
        link = crawl_db.load_crawl_links(crawl_id)[0]
        result('round-trip: link context comes back',
               link.get('rel') == 'nofollow' and bool(link.get('nofollow'))
               and link.get('link_target') == '_blank' and bool(link.get('in_main')),
               str({k: link.get(k) for k in ('rel', 'nofollow', 'link_target', 'in_nav', 'in_main')}))
    finally:
        crawl_db.DB_FILE = original_db
        os.unlink(path)


TESTS = (
    test_page_body_is_stored_with_fingerprints,
    test_existing_database_upgrades_to_the_new_columns,
    test_a_loaded_crawl_reports_its_own_numbers,
    test_retention_prunes_old_crawls,
    test_stored_page_data_round_trips_through_the_database,
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
