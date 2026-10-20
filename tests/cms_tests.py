"""Deterministic tests for the read-only WordPress connector.

Runs src/core/cms_wordpress.py against tiny fake WordPress REST APIs started
by this script, so every check is pinned down without touching a real site:

    python tests/cms_tests.py

Four fixture sites are served:

  * the "dirty" site - three posts over two pages (so X-WP-TotalPages has to
    be followed) and two pages. Between them: an item whose JSON-LD the live
    page strips, an item with a Yoast SEO title and description beyond the
    limits, an item with a doubled space in its title and 'None' in its SEO
    description, an item written by an unexpected author (resolved through
    /wp/v2/users/<id>, since that item carries no _embedded block), an item
    missing from the sitemap and an item the crawl never reached.
  * the "clean" site - one item that matches its live page in every respect
    and must produce no issues at all.
  * the "plain" site - no /wp-json/ at all, which must cost one request and
    produce no issues.
  * the "broken" site - a wp/v2 root that works and a /posts endpoint that
    500s, which must be collected as an error rather than raised.

Every fixture records the HTTP method of each request, so the last test can
prove the connector never issues anything but GET.
"""
import http.server
import json
import math
import os
import sys
import threading
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from src.core import cms_wordpress  # noqa: E402

# Fixture servers bind here upward. Override if these clash locally.
BASE_PORT = int(os.environ.get('CMS_FIXTURE_PORT', '8941'))

EXPECTED_AUTHOR = 'Jane Doe'

PASSED, FAILED = [], []


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def serve(handler, port):
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', port), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# --------------------------------------------------------------------------
# a fake WordPress REST API
# --------------------------------------------------------------------------

def make_wp_handler(items_by_type, users, methods, broken_types=(),
                    not_wordpress=False):
    """Serve wp/v2 for `items_by_type`, recording (method, path) in `methods`."""

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def _record(self):
            methods.append((self.command, urlparse(self.path).path))
            length = int(self.headers.get('Content-Length') or 0)
            if length:
                self.rfile.read(length)

        def _send(self, code, body=b'', ctype='application/json', extra=None):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            for key, value in (extra or {}).items():
                self.send_header(key, str(value))
            self.end_headers()
            if body and self.command != 'HEAD':
                self.wfile.write(body)

        def _json(self, code, payload, extra=None):
            self._send(code, json.dumps(payload).encode('utf-8'), extra=extra)

        def do_GET(self):
            self._record()
            parsed = urlparse(self.path)
            path, query = parsed.path, parse_qs(parsed.query)

            if not_wordpress:
                return self._send(404, b'{"code":"rest_no_route"}')

            if path.rstrip('/') == '/wp-json/wp/v2':
                return self._json(200, {'namespace': 'wp/v2',
                                        'routes': {'/wp/v2/posts': {}, '/wp/v2/pages': {}}})

            if path.startswith('/wp-json/wp/v2/users/'):
                user_id = path.rsplit('/', 1)[-1]
                user = users.get(user_id)
                if user is None:
                    return self._send(404, b'{"code":"rest_user_invalid_id"}')
                return self._json(200, user)

            if path.startswith('/wp-json/wp/v2/'):
                item_type = path[len('/wp-json/wp/v2/'):].strip('/')
                if item_type in broken_types:
                    return self._send(500, b'{"code":"internal_server_error"}')
                if item_type not in items_by_type:
                    return self._send(404, b'{"code":"rest_no_route"}')

                items = items_by_type[item_type]
                per_page = int((query.get('per_page') or ['100'])[0])
                page = int((query.get('page') or ['1'])[0])
                total_pages = max(1, math.ceil(len(items) / per_page))
                if page > total_pages:
                    return self._send(400, b'{"code":"rest_post_invalid_page_number"}')
                chunk = items[(page - 1) * per_page: page * per_page]
                return self._json(200, chunk, extra={
                    'X-WP-Total': len(items), 'X-WP-TotalPages': total_pages})

            return self._send(404, b'{"code":"rest_no_route"}')

        # Recorded and refused: the connector must never reach these.
        def do_POST(self):
            self._record()
            self._send(405, b'{"code":"rest_no_route"}')

        do_PUT = do_PATCH = do_DELETE = do_HEAD = do_POST

    return Handler


def jsonld_script(payload):
    return ('<script type="application/ld+json">'
            + json.dumps(payload) + '</script>')


def wp_item(item_id, slug, title, origin, item_type='post', **extra):
    """A REST payload item with the core keys WordPress always sends."""
    item = {
        'id': item_id,
        'date_gmt': '2026-01-05T09:00:00',
        'modified_gmt': '2026-02-01T09:00:00',
        'slug': slug,
        'status': 'publish',
        'type': item_type,
        'link': f'{origin}/{slug}/',
        'title': {'rendered': title},
        'content': {'rendered': extra.pop('content', f'<p>Body of {title}.</p>')},
        'excerpt': {'rendered': extra.pop('excerpt', f'<p>{title} in short.</p>')},
        'author': extra.pop('author', 1),
        'meta': extra.pop('meta', {}),
    }
    embedded_author = extra.pop('embedded_author', EXPECTED_AUTHOR)
    if embedded_author:
        item['_embedded'] = {'author': [{'id': item['author'], 'name': embedded_author}]}
    item.update(extra)
    return item


FAQ_JSONLD = {'@context': 'https://schema.org', '@type': 'FAQPage',
              'mainEntity': [{'@type': 'Question', 'name': 'Is it read-only?'}]}

LONG_TITLE = 'A Really Quite Excessively Long SEO Title That Nobody Will Ever Read In Full'
LONG_DESC = ('This SEO description rambles on well past the limit, which is exactly the '
             'sort of thing the front end will truncate in the search results, and it '
             'keeps going for a good while longer than anyone needs it to.')


def dirty_site():
    """(origin, items_by_type, users, methods) for the site full of defects."""
    origin = f'http://127.0.0.1:{BASE_PORT}'
    posts = [
        wp_item(11, 'stripped-schema', 'Stripped Schema', origin,
                content='<p>Intro.</p>' + jsonld_script(FAQ_JSONLD)),
        wp_item(12, 'long-seo', 'Long SEO', origin,
                yoast_head_json={'title': LONG_TITLE, 'description': LONG_DESC}),
        # no _embedded: the author has to be resolved through /wp/v2/users/7
        wp_item(13, 'double-space', 'Double  Space Post', origin,
                author=7, embedded_author=None,
                meta={'_yoast_wpseo_metadesc': 'None'}),
    ]
    pages = [
        wp_item(21, 'contact', 'Contact', origin, item_type='page'),
        wp_item(22, 'hidden-gem', 'Hidden Gem', origin, item_type='page'),
    ]
    users = {'7': {'id': 7, 'name': 'Bob Editor'}}
    return origin, {'posts': posts, 'pages': pages}, users, []


def dirty_pages(origin):
    """The crawl result for the dirty site: /hidden-gem/ was never reached."""
    return [
        {'url': f'{origin}/stripped-schema/', 'status_code': 200,
         'title': 'Stripped Schema', 'json_ld': [], 'schema_org': []},
        {'url': f'{origin}/long-seo/', 'status_code': 200,
         'title': 'Long SEO', 'json_ld': [], 'schema_org': []},
        {'url': f'{origin}/double-space/', 'status_code': 200,
         'title': 'Double  Space Post', 'json_ld': [], 'schema_org': []},
        {'url': f'{origin}/contact/', 'status_code': 200,
         'title': 'Contact', 'json_ld': [], 'schema_org': []},
    ]


def dirty_sitemap(origin):
    """Everything published except /contact/, which is the finding."""
    return [f'{origin}/stripped-schema/', f'{origin}/long-seo/',
            f'{origin}/double-space/', f'{origin}/hidden-gem/']


SERVERS = {}


def start_servers():
    origin, items, users, methods = dirty_site()
    SERVERS['dirty'] = {'origin': origin, 'methods': methods,
                        'srv': serve(make_wp_handler(items, users, methods), BASE_PORT)}

    clean_origin = f'http://127.0.0.1:{BASE_PORT + 1}'
    clean_methods = []
    clean_items = {'posts': [wp_item(
        31, 'sunny-recipes', 'Sunny Recipes', clean_origin,
        content='<p>Body.</p>' + jsonld_script(FAQ_JSONLD),
        yoast_head_json={'title': 'Sunny Recipes For A Short Week',
                         'description': 'A tidy description that stays inside the limit.'},
        meta={'_yoast_wpseo_title': '', '_yoast_wpseo_metadesc': ''})],
        'pages': []}
    SERVERS['clean'] = {'origin': clean_origin, 'methods': clean_methods,
                        'srv': serve(make_wp_handler(clean_items, {}, clean_methods),
                                     BASE_PORT + 1)}

    plain_methods = []
    SERVERS['plain'] = {'origin': f'http://127.0.0.1:{BASE_PORT + 2}',
                        'methods': plain_methods,
                        'srv': serve(make_wp_handler({}, {}, plain_methods,
                                                     not_wordpress=True), BASE_PORT + 2)}

    broken_methods = []
    broken_origin = f'http://127.0.0.1:{BASE_PORT + 3}'
    broken_items = {'posts': [], 'pages': [wp_item(41, 'ok', 'Ok', broken_origin,
                                                   item_type='page')]}
    SERVERS['broken'] = {'origin': broken_origin, 'methods': broken_methods,
                         'srv': serve(make_wp_handler(broken_items, {}, broken_methods,
                                                      broken_types=('posts',)),
                                      BASE_PORT + 3)}


def session():
    return requests.Session()


def dirty_report(per_page=2, **config):
    """audit_site against the dirty site, with the defaults the tests share."""
    site = SERVERS['dirty']
    cfg = {'per_page': per_page, 'expected_author': EXPECTED_AUTHOR}
    cfg.update(config)
    return cms_wordpress.audit_site(
        session(), site['origin'], dirty_pages(site['origin']),
        config=cfg, sitemap_urls=dirty_sitemap(site['origin']))


def checks_for(issues, check):
    return [issue for issue in issues if issue['check'] == check]


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------

def test_discover_finds_wordpress():
    found = cms_wordpress.discover(session(), SERVERS['dirty']['origin'])
    result('discover: wp/v2 namespace is detected',
           found['available'] and found['root'].endswith('/wp-json/wp/v2'),
           str(found))


def test_discover_is_unavailable_and_cheap_on_a_plain_site():
    site = SERVERS['plain']
    site['methods'].clear()
    report = cms_wordpress.audit_site(session(), site['origin'], [])
    result('plain site: available is False',
           report['available'] is False, str(report.get('detail')))
    result('plain site: no issues and no items',
           report['issues'] == [] and report['items'] == [],
           f"{len(report['issues'])} issues, {len(report['items'])} items")
    result('plain site: costs exactly one request',
           len(site['methods']) == 1, str(site['methods']))


def test_discover_never_raises_on_a_dead_host():
    found = cms_wordpress.discover(session(), f'http://127.0.0.1:{BASE_PORT + 9}',
                                   timeout=2)
    result('dead host: available is False instead of an exception',
           found['available'] is False and 'could not be fetched' in found['detail'],
           found['detail'][:80])


def test_fetch_items_follows_pagination():
    site = SERVERS['dirty']
    fetched = cms_wordpress.fetch_items(
        session(), site['origin'] + '/wp-json/wp/v2', per_page=2)
    posts = [item for item in fetched['items'] if item['type'] == 'post']
    pages = [item for item in fetched['items'] if item['type'] == 'page']
    result('pagination: X-WP-TotalPages is followed across both pages',
           len(posts) == 3 and {item['slug'] for item in posts} ==
           {'stripped-schema', 'long-seo', 'double-space'},
           f'{len(posts)} posts: ' + ', '.join(item["slug"] for item in posts))
    result('pagination: both types are read',
           len(pages) == 2 and not fetched['errors'],
           f'{len(pages)} pages, errors={fetched["errors"]}')


def test_fetch_items_stops_at_max_items():
    site = SERVERS['dirty']
    fetched = cms_wordpress.fetch_items(
        session(), site['origin'] + '/wp-json/wp/v2', per_page=2, max_items=2)
    result('max_items: the item list is capped and the stop is recorded',
           len(fetched['items']) == 2
           and any(error['kind'] == 'max_items' for error in fetched['errors']),
           f'{len(fetched["items"])} items, errors={fetched["errors"]}')


def test_normalised_item_carries_every_documented_field():
    site = SERVERS['dirty']
    fetched = cms_wordpress.fetch_items(
        session(), site['origin'] + '/wp-json/wp/v2', types=('posts',), per_page=100)
    item = next(one for one in fetched['items'] if one['slug'] == 'stripped-schema')
    expected = {'id', 'type', 'slug', 'link', 'status', 'title', 'excerpt',
                'content_html', 'content_text', 'modified_gmt', 'date_gmt',
                'author_id', 'author_name', 'seo', 'raw_fields'}
    result('item shape: every documented key is present',
           expected <= set(item), str(sorted(expected - set(item))))
    result('item shape: content_text is the text of content_html',
           item['content_text'].startswith('Intro.')
           and '<p>' not in item['content_text'],
           repr(item['content_text'][:60]))
    result('item shape: raw_fields keeps the custom fields, not the core ones',
           'meta' in item['raw_fields'] and 'title' not in item['raw_fields'],
           str(sorted(item['raw_fields'])))


def test_seo_fields_are_read_from_each_plugin():
    cases = (
        ('Yoast', {'yoast_head_json': {'title': 'Y', 'description': 'yd'}}, 'Y', 'yd'),
        ('Yoast meta', {'meta': {'_yoast_wpseo_title': 'YM',
                                 '_yoast_wpseo_metadesc': 'ymd'}}, 'YM', 'ymd'),
        ('Rank Math', {'rank_math_title': 'R', 'rank_math_description': 'rd'}, 'R', 'rd'),
        ('Rank Math meta', {'meta': {'rank_math_title': 'RM',
                                     'rank_math_description': 'rmd'}}, 'RM', 'rmd'),
        ('AIOSEO', {'_aioseo_title': 'A', '_aioseo_description': 'ad'}, 'A', 'ad'),
        ('AIOSEO meta', {'meta': {'_aioseo_title': 'AM',
                                  '_aioseo_description': 'amd'}}, 'AM', 'amd'),
        ('SEOPress', {'meta': {'_seopress_titles_title': 'S',
                               '_seopress_titles_desc': 'sd'}}, 'S', 'sd'),
    )
    for label, raw, title, description in cases:
        seo = cms_wordpress.seo_fields(raw)
        result(f'seo fields: {label} title and description are read',
               seo['title'] == title and seo['description'] == description, str(seo))

    empty = cms_wordpress.seo_fields({'meta': {'_yoast_wpseo_title': ''},
                                      'rank_math_title': 'fallback'})
    result('seo fields: an empty plugin value falls through to the next one',
           empty['title'] == 'fallback', str(empty))


def test_author_comes_from_embed_or_the_users_endpoint():
    site = SERVERS['dirty']
    site['methods'].clear()
    fetched = cms_wordpress.fetch_items(
        session(), site['origin'] + '/wp-json/wp/v2', types=('posts',), per_page=100)
    by_slug = {item['slug']: item for item in fetched['items']}
    user_calls = [path for _, path in site['methods'] if '/users/' in path]
    result('author: _embedded supplies the name without an extra request',
           by_slug['stripped-schema']['author_name'] == EXPECTED_AUTHOR,
           by_slug['stripped-schema']['author_name'])
    result('author: an un-embedded author is resolved through /wp/v2/users/<id>',
           by_slug['double-space']['author_name'] == 'Bob Editor',
           by_slug['double-space']['author_name'])
    result('author: the users endpoint is asked at most once',
           len(user_calls) == 1, str(user_calls))


def test_structured_data_stripped_is_reported():
    report = dirty_report()
    found = checks_for(report['issues'], 'cms_structured_data_stripped')
    ok = (len(found) == 1
          and found[0]['url'].endswith('/stripped-schema/')
          and found[0]['type'] == 'error'
          and 'faqpage' in found[0]['evidence'].get('snippet', '').lower())
    result('cms_structured_data_stripped: fires on the item the front end strips',
           ok, str([(issue['url'], issue['type']) for issue in found]))


def test_structured_data_present_on_the_live_page_is_not_reported():
    site = SERVERS['dirty']
    pages = dirty_pages(site['origin'])
    for page in pages:
        if page['url'].endswith('/stripped-schema/'):
            page['json_ld'] = [FAQ_JSONLD]
    report = cms_wordpress.audit_site(
        session(), site['origin'], pages,
        config={'per_page': 100, 'expected_author': EXPECTED_AUTHOR},
        sitemap_urls=dirty_sitemap(site['origin']))
    result('cms_structured_data_stripped: silent when the live page serves the block',
           not checks_for(report['issues'], 'cms_structured_data_stripped'),
           str(checks_for(report['issues'], 'cms_structured_data_stripped')))


def test_seo_lengths_are_reported():
    report = dirty_report()
    titles = checks_for(report['issues'], 'cms_seo_title_too_long')
    descriptions = checks_for(report['issues'], 'cms_seo_description_too_long')
    result('cms_seo_title_too_long: fires on the over-long Yoast title',
           len(titles) == 1 and titles[0]['url'].endswith('/long-seo/')
           and titles[0]['evidence']['expected'] == '60 characters or fewer',
           str([issue['url'] for issue in titles]))
    result('cms_seo_description_too_long: fires on the over-long Yoast description',
           len(descriptions) == 1 and descriptions[0]['url'].endswith('/long-seo/'),
           str([issue['url'] for issue in descriptions]))


def test_seo_limits_are_configurable():
    report = dirty_report(seo_title_max=200, seo_description_max=500)
    result('seo limits: raising the limits silences both checks',
           not checks_for(report['issues'], 'cms_seo_title_too_long')
           and not checks_for(report['issues'], 'cms_seo_description_too_long'),
           str([issue['check'] for issue in report['issues']]))


def test_placeholder_field_is_reported():
    report = dirty_report()
    found = checks_for(report['issues'], 'cms_field_placeholder')
    ok = (len(found) == 1 and found[0]['url'].endswith('/double-space/')
          and found[0]['evidence'].get('selector') == 'seo.description'
          and found[0]['type'] == 'error')
    result("cms_field_placeholder: fires on the 'None' SEO description",
           ok, str([(issue['url'], issue['evidence'].get('selector')) for issue in found]))


def test_placeholder_covers_every_documented_value():
    origin = 'https://example.com'
    values = ('None', 'null', 'undefined', 'TBD', 'Lorem ipsum dolor sit amet', '   ')
    items = [{'status': 'publish', 'link': f'{origin}/p{index}/', 'title': 'T',
              'excerpt': '', 'content_html': '', 'author_name': '',
              'seo': {'title': '', 'description': value}, 'raw_fields': {}}
             for index, value in enumerate(values)]
    issues = cms_wordpress.compare(items, [{'url': item['link'], 'status_code': 200}
                                           for item in items],
                                   config={'base_url': origin, 'group_threshold': 99})
    found = checks_for(issues, 'cms_field_placeholder')
    result('cms_field_placeholder: every documented placeholder value fires',
           len(found) == len(values), f'{len(found)} of {len(values)}')

    clean = [{'status': 'publish', 'link': f'{origin}/ok/', 'title': 'Real Title',
              'excerpt': '', 'content_html': '', 'author_name': '',
              'seo': {'title': '', 'description': ''}, 'raw_fields': {}}]
    quiet = cms_wordpress.compare(clean, [{'url': clean[0]['link'], 'status_code': 200}],
                                  config={'base_url': origin})
    result('cms_field_placeholder: an unset optional field is not a placeholder',
           not checks_for(quiet, 'cms_field_placeholder'), str(quiet))


def test_stray_whitespace_is_reported():
    report = dirty_report()
    found = checks_for(report['issues'], 'cms_stray_whitespace')
    ok = (len(found) == 1 and found[0]['url'].endswith('/double-space/')
          and found[0]['evidence'].get('selector') == 'title'
          and found[0]['evidence'].get('expected') == "'Double Space Post'")
    result('cms_stray_whitespace: fires on the doubled space in the title',
           ok, str([(issue['url'], issue['evidence']) for issue in found]))


def test_unexpected_author_is_reported():
    report = dirty_report()
    found = checks_for(report['issues'], 'cms_author_unexpected')
    ok = (len(found) == 1 and found[0]['url'].endswith('/double-space/')
          and found[0]['evidence'].get('found') == 'Bob Editor')
    result('cms_author_unexpected: fires on the item written by someone else',
           ok, str([(issue['url'], issue['evidence']) for issue in found]))

    without = dirty_report(expected_author=None)
    result('cms_author_unexpected: skipped when no expected author is configured',
           not checks_for(without['issues'], 'cms_author_unexpected'),
           str(checks_for(without['issues'], 'cms_author_unexpected')))


def test_published_item_missing_from_the_sitemap_is_reported():
    report = dirty_report()
    found = checks_for(report['issues'], 'cms_published_not_in_sitemap')
    result('cms_published_not_in_sitemap: fires on the page the sitemap omits',
           len(found) == 1 and found[0]['url'].endswith('/contact/'),
           str([issue['url'] for issue in found]))

    site = SERVERS['dirty']
    without = cms_wordpress.audit_site(
        session(), site['origin'], dirty_pages(site['origin']),
        config={'per_page': 100, 'expected_author': EXPECTED_AUTHOR})
    result('cms_published_not_in_sitemap: skipped when no sitemap is supplied',
           not checks_for(without['issues'], 'cms_published_not_in_sitemap'),
           str(checks_for(without['issues'], 'cms_published_not_in_sitemap')))


def test_published_item_never_crawled_is_reported():
    report = dirty_report()
    found = checks_for(report['issues'], 'cms_not_crawled')
    result('cms_not_crawled: fires on the published item the crawl never reached',
           len(found) == 1 and found[0]['url'].endswith('/hidden-gem/'),
           str([issue['url'] for issue in found]))


def test_a_clean_site_produces_nothing():
    site = SERVERS['clean']
    origin = site['origin']
    pages = [{'url': f'{origin}/sunny-recipes/', 'status_code': 200,
              'title': 'Sunny Recipes', 'json_ld': [FAQ_JSONLD], 'schema_org': []}]
    report = cms_wordpress.audit_site(
        session(), origin, pages,
        config={'per_page': 100, 'expected_author': EXPECTED_AUTHOR},
        sitemap_urls=[f'{origin}/sunny-recipes/'])
    result('clean site: the connector is available and reads the item',
           report['available'] and len(report['items']) == 1,
           str(report.get('detail')))
    result('clean site: no issues at all',
           report['issues'] == [],
           str([(issue['check'], issue['url']) for issue in report['issues']]))


def test_offenders_above_five_are_grouped():
    origin = 'https://example.com'
    items = [{'status': 'publish', 'link': f'{origin}/post-{index}/', 'title': 'Title',
              'excerpt': 'x', 'content_html': '', 'author_name': EXPECTED_AUTHOR,
              'seo': {'title': '', 'description': ''}, 'raw_fields': {}}
             for index in range(6)]
    issues = cms_wordpress.compare(items, [], config={'base_url': origin})
    found = checks_for(issues, 'cms_not_crawled')
    ok = (len(found) == 1 and found[0]['url'] == origin
          and found[0]['evidence']['found'] == '6'
          and len(found[0]['evidence']['related_urls']) == 6)
    result('grouping: six offenders collapse into one issue on the base URL',
           ok, str(found))

    five = cms_wordpress.compare(items[:5], [], config={'base_url': origin})
    result('grouping: five offenders stay as five per-URL issues',
           len(checks_for(five, 'cms_not_crawled')) == 5,
           str(len(checks_for(five, 'cms_not_crawled'))))


def test_a_broken_endpoint_is_collected_not_raised():
    site = SERVERS['broken']
    report = cms_wordpress.audit_site(session(), site['origin'], [])
    kinds = [error['kind'] for error in report['errors']]
    result('errors: a 500 on /posts becomes an error record',
           report['available'] and 'http_error' in kinds, str(report['errors']))
    result('errors: the other type is still read',
           len(report['items']) == 1 and report['items'][0]['slug'] == 'ok',
           str([item['slug'] for item in report['items']]))


def test_every_issue_uses_the_issue_factory_shape():
    report = dirty_report()
    fields = {'id', 'url', 'check', 'type', 'category', 'issue', 'details',
              'evidence', 'how_to_fix'}
    bad = [issue for issue in report['issues']
           if not fields <= set(issue) or issue['category'] != 'CMS'
           or issue['type'] not in ('error', 'warning', 'info')
           or not issue['how_to_fix']]
    result('issue shape: every issue comes from make_issue with a CMS category',
           report['issues'] and not bad, str(bad[:1]))

    again = dirty_report()
    result('issue shape: ids are stable across two identical runs',
           sorted(issue['id'] for issue in report['issues'])
           == sorted(issue['id'] for issue in again['issues']),
           f'{len(report["issues"])} ids compared')


def test_the_connector_never_issues_a_non_get_request():
    site = SERVERS['dirty']

    # prove the recorder has teeth before trusting what it did not record
    site['methods'].clear()
    requests.post(site['origin'] + '/wp-json/wp/v2/posts', data=b'x', timeout=5)
    result('read-only: the fixture does record a non-GET request',
           [method for method, _ in site['methods']] == ['POST'], str(site['methods']))

    site['methods'].clear()
    dirty_report()
    methods = sorted({method for method, _ in site['methods']})
    result('read-only: a full audit issues GET and nothing else',
           methods == ['GET'] and len(site['methods']) > 1, str(methods))

    source = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               '..', 'src', 'core', 'cms_wordpress.py'),
                  encoding='utf-8').read().lower()
    banned = [verb for verb in ('.post(', '.put(', '.patch(', '.delete(')
              if verb in source]
    result('read-only: the module source contains no write verb',
           not banned, str(banned))


TESTS = (
    test_discover_finds_wordpress,
    test_discover_is_unavailable_and_cheap_on_a_plain_site,
    test_discover_never_raises_on_a_dead_host,
    test_fetch_items_follows_pagination,
    test_fetch_items_stops_at_max_items,
    test_normalised_item_carries_every_documented_field,
    test_seo_fields_are_read_from_each_plugin,
    test_author_comes_from_embed_or_the_users_endpoint,
    test_structured_data_stripped_is_reported,
    test_structured_data_present_on_the_live_page_is_not_reported,
    test_seo_lengths_are_reported,
    test_seo_limits_are_configurable,
    test_placeholder_field_is_reported,
    test_placeholder_covers_every_documented_value,
    test_stray_whitespace_is_reported,
    test_unexpected_author_is_reported,
    test_published_item_missing_from_the_sitemap_is_reported,
    test_published_item_never_crawled_is_reported,
    test_a_clean_site_produces_nothing,
    test_offenders_above_five_are_grouped,
    test_a_broken_endpoint_is_collected_not_raised,
    test_every_issue_uses_the_issue_factory_shape,
    test_the_connector_never_issues_a_non_get_request,
)


def main():
    start_servers()
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
