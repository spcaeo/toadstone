"""Tests for the reporting API (src/api/reporting.py).

Drives the real Flask app with its test client against a throwaway database,
so no server and no network are needed:

    python tests/api_tests.py

Each check pins a promise the API makes to whoever is fixing the site: the
filters narrow what they asked for, the evidence survives the round trip, the
formats are actually parseable, an accepted issue stops being reported, and the
page-detail endpoint answers for one URL with its headings tree, its links both
ways, its issues and a diff against the previous crawl.
"""

import csv
import io
import json
import os
import sys
import tempfile
from urllib.parse import quote

os.environ.setdefault('LOCAL_MODE', 'true')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import crawl_db  # noqa: E402
from src.utils.issue_factory import make_issue  # noqa: E402

PASSED, FAILED = [], []


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def build_fixture_crawls():
    """Two crawls of one site: the second fixes one issue and adds another."""
    first = crawl_db.create_crawl(user_id=1, session_id='s1', base_url='https://site.test',
                                  base_domain='site.test', config_snapshot={})
    second = crawl_db.create_crawl(user_id=1, session_id='s2', base_url='https://site.test',
                                   base_domain='site.test', config_snapshot={})

    pages = [
        {'url': 'https://site.test/', 'status_code': 200, 'title': 'Home', 'h1': 'Home',
         'meta_description': 'The home page', 'canonical_url': 'https://site.test/',
         'word_count': 400, 'main_text': 'home body', 'main_text_hash': 'h1', 'depth': 0},
        {'url': 'https://site.test/pricing', 'status_code': 200, 'title': 'Pricing', 'h1': 'Pricing',
         'meta_description': '', 'canonical_url': 'https://site.test/pricing',
         'word_count': 120, 'main_text': 'pricing body', 'main_text_hash': 'h2', 'depth': 1},
    ]
    links = [
        {'source_url': 'https://site.test/', 'target_url': 'https://site.test/pricing',
         'anchor_text': 'See pricing', 'is_internal': True, 'target_status': 200,
         'placement': 'body', 'rel': '', 'nofollow': False, 'link_target': '',
         'in_nav': False, 'in_main': True, 'in_heading': False},
        {'source_url': 'https://site.test/pricing', 'target_url': 'https://other.test/x',
         'anchor_text': 'partner', 'is_internal': False, 'target_status': 200,
         'placement': 'body', 'rel': 'nofollow', 'nofollow': True, 'link_target': '_blank',
         'in_nav': False, 'in_main': True, 'in_heading': False},
    ]

    thin = make_issue('https://site.test/pricing', 'thin_content', 'warning', 'Content',
                      'Thin Content', 'Page has only 120 words',
                      offending_value='120', evidence={'found': '120 words'},
                      how_to_fix='Add substantive copy.')
    missing_desc = make_issue('https://site.test/pricing', 'missing_meta_description', 'error',
                              'SEO', 'Missing Meta Description', 'No meta description',
                              evidence={'selector': 'head > meta[name=description]'},
                              how_to_fix='Write one.')
    slow = make_issue('https://site.test/', 'slow_response_time', 'warning', 'Performance',
                      'Slow Response Time', 'Took 4000ms', offending_value='4000')

    for crawl_id, issues, in ((first, [thin, missing_desc]), (second, [thin, slow])):
        crawl_db.save_url_batch(crawl_id, pages)
        crawl_db.save_links_batch(crawl_id, links)
        crawl_db.save_issues_batch(crawl_id, issues)
        crawl_db.set_crawl_status(crawl_id, 'completed')

    crawl_db.save_sitemap_audit(second, [{'url': 'https://site.test/sitemap.xml', 'status': 200,
                                          'entries': 2, 'bytes': 300, 'is_index': False,
                                          'errors': []}],
                                {'in_both': ['https://site.test/'],
                                 'crawled_only': ['https://site.test/pricing'],
                                 'sitemap_only': []})
    return first, second, {'thin': thin, 'missing_desc': missing_desc, 'slow': slow}


DETAIL_URL = 'https://detail.test/guide'
NEW_PAGE_URL = 'https://detail.test/changelog'

OLD_TEXT = ('The guide explains how the widget works. '
            'This paragraph will be deleted in the next crawl. '
            'Contact us for more help.')
NEW_TEXT = ('The guide explains how the widget works. '
            'Contact us for more help. '
            'A fresh sentence about pricing appeared.')

# One heading outline, stored the way SEOExtractor.extract_headings stores it:
# flat, in document order, so the endpoint has something real to nest.
DETAIL_HEADINGS = [
    {'level': 1, 'text': 'Guide', 'is_visible': True, 'dom_order': 0},
    {'level': 2, 'text': 'Getting started', 'is_visible': True, 'dom_order': 1},
    {'level': 3, 'text': 'Install the widget', 'is_visible': True, 'dom_order': 2},
    {'level': 3, 'text': 'Configure it', 'is_visible': False, 'dom_order': 3},
    {'level': 2, 'text': 'Troubleshooting', 'is_visible': True, 'dom_order': 4},
]


def build_detail_crawls():
    """Two crawls of a second site, built for the page-detail endpoint.

    Between them one page is rewritten (new title, a deleted paragraph, a new
    one), loses an internal link and gains another, has one issue fixed and one
    filed against it - and a second page appears for the first time. That is
    every branch of `GET /api/crawls/<id>/page` in one fixture.
    """
    old = crawl_db.create_crawl(user_id=1, session_id='d1', base_url='https://detail.test',
                                base_domain='detail.test', config_snapshot={})
    new = crawl_db.create_crawl(user_id=1, session_id='d2', base_url='https://detail.test',
                                base_domain='detail.test', config_snapshot={})

    shared = {
        'url': DETAIL_URL, 'status_code': 200, 'depth': 1, 'is_internal': True,
        'canonical_url': DETAIL_URL, 'lang': 'en', 'content_type': 'text/html',
        'h1': 'Guide', 'h2': ['Getting started', 'Troubleshooting'],
        'h3': ['Install the widget', 'Configure it'],
        'headings': DETAIL_HEADINGS,
        'heading_text_issues': [{'dom_order': 4, 'level': 2, 'problem': 'trailing_whitespace'}],
        'json_ld': [{'@context': 'https://schema.org', '@type': 'Article',
                     'headline': 'Widget guide'}],
        'images': [{'src': 'https://detail.test/img/hero.png', 'alt': ''},
                   {'src': 'https://detail.test/img/step.png', 'alt': 'Step one'}],
        'hreflang': [{'lang': 'en', 'href': DETAIL_URL},
                     {'lang': 'de', 'href': 'https://detail.test/de/guide'}],
        'redirects': [{'from': 'https://detail.test/old-guide', 'to': DETAIL_URL, 'status': 301}],
        'cache_control': 'max-age=3600', 'last_modified': 'Mon, 01 Sep 2025 10:00:00 GMT',
        'etag': 'W/"abc123"', 'content_length': 48211, 'ttfb': 0.182,
        'response_time': 0.441, 'size': 48211,
    }
    before_page = dict(shared, title='Old Guide Title',
                       meta_description='The old description',
                       word_count=500, main_text=OLD_TEXT)
    after_page = dict(shared, title='New Guide Title',
                      meta_description='The old description',
                      word_count=460, main_text=NEW_TEXT)

    def link(source, target, anchor, internal=True, **extra):
        row = {'source_url': source, 'target_url': target, 'anchor_text': anchor,
               'is_internal': internal, 'target_status': 200, 'placement': 'body',
               'rel': '', 'nofollow': False, 'link_target': '',
               'in_nav': False, 'in_main': True, 'in_heading': False}
        row.update(extra)
        return row

    inbound = [
        link('https://detail.test/', DETAIL_URL, 'Read the guide',
             placement='nav', in_nav=True, in_main=False),
        link('https://detail.test/pricing', DETAIL_URL, 'the guide'),
    ]
    outbound_common = [
        link(DETAIL_URL, 'https://detail.test/pricing', 'our pricing'),
        link(DETAIL_URL, 'https://external.test/docs', 'vendor docs', internal=False,
             rel='nofollow noopener', nofollow=True, link_target='_blank'),
    ]
    before_links = inbound + outbound_common + [
        link(DETAIL_URL, 'https://detail.test/legacy', 'legacy notes'),
    ]
    after_links = inbound + outbound_common + [
        link(DETAIL_URL, NEW_PAGE_URL, 'the changelog'),
    ]

    missing_alt = make_issue(DETAIL_URL, 'images_missing_alt', 'error', 'Images',
                             'Image Missing Alt Text', 'hero.png has no alt attribute',
                             offending_value='https://detail.test/img/hero.png',
                             evidence={'selector': 'img[src$="hero.png"]'},
                             how_to_fix='Describe the image in an alt attribute.')
    long_title = make_issue(DETAIL_URL, 'title_too_long', 'warning', 'SEO',
                            'Title Too Long', 'The title is 71 characters',
                            offending_value='71', evidence={'found': '71 characters'},
                            how_to_fix='Trim the title to about 60 characters.')
    duplicate_h1 = make_issue(DETAIL_URL, 'duplicate_h1', 'warning', 'Content',
                              'Duplicate H1', 'Two h1 elements on the page',
                              offending_value='2', how_to_fix='Keep one h1 per page.')
    # Filed against a different URL, so it must not reach the guide's detail.
    elsewhere = make_issue('https://detail.test/pricing', 'thin_content', 'warning', 'Content',
                           'Thin Content', 'Page has only 80 words', offending_value='80')
    changelog_issue = make_issue(NEW_PAGE_URL, 'missing_meta_description', 'error', 'SEO',
                                 'Missing Meta Description', 'No meta description',
                                 how_to_fix='Write one.')

    crawl_db.save_url_batch(old, [before_page])
    crawl_db.save_links_batch(old, before_links)
    crawl_db.save_issues_batch(old, [missing_alt, long_title, elsewhere])
    crawl_db.set_crawl_status(old, 'completed')

    crawl_db.save_url_batch(new, [after_page, {
        'url': NEW_PAGE_URL, 'status_code': 200, 'title': 'Changelog', 'h1': 'Changelog',
        'word_count': 90, 'main_text': 'Every release is listed here.', 'depth': 2}])
    crawl_db.save_links_batch(new, after_links)
    crawl_db.save_issues_batch(new, [long_title, duplicate_h1, elsewhere, changelog_issue])
    crawl_db.set_crawl_status(new, 'completed')

    return old, new, {'missing_alt': missing_alt, 'long_title': long_title,
                      'duplicate_h1': duplicate_h1, 'changelog': changelog_issue}


def client():
    import main
    main.app.config['TESTING'] = True
    return main.app.test_client()


def get_json(app, url):
    response = app.get(url)
    return response.status_code, json.loads(response.data or b'{}')


def page_url(crawl_id, url):
    return f'/api/crawls/{crawl_id}/page?url={quote(url, safe="")}'


def test_issues_endpoint():
    app = client()
    status, body = get_json(app, f'/api/crawls/{SECOND}/issues')
    result('issues: returns this crawl only', status == 200 and body['total'] == 2,
           f"status={status} total={body.get('total')}")
    result('issues: evidence survives the round trip',
           any(i['evidence'] for i in body['issues']),
           str([i['evidence'] for i in body['issues']]))
    result('issues: how_to_fix is included',
           any(i['how_to_fix'] for i in body['issues']))
    result('issues: stable ordering puts errors first',
           [i['type'] for i in body['issues']] == sorted(
               [i['type'] for i in body['issues']],
               key=lambda t: {'error': 0, 'warning': 1, 'info': 2}[t]),
           str([i['type'] for i in body['issues']]))

    _, warnings = get_json(app, f'/api/crawls/{SECOND}/issues?severity=warning')
    result('issues: severity filter', {i['type'] for i in warnings['issues']} == {'warning'},
           str([i['type'] for i in warnings['issues']]))

    _, by_check = get_json(app, f'/api/crawls/{SECOND}/issues?check=thin_content')
    result('issues: check filter', [i['check'] for i in by_check['issues']] == ['thin_content'],
           str([i['check'] for i in by_check['issues']]))

    _, by_prefix = get_json(app, f'/api/crawls/{SECOND}/issues?url_prefix=https://site.test/pricing')
    result('issues: url_prefix filter',
           all(i['url'].startswith('https://site.test/pricing') for i in by_prefix['issues'])
           and by_prefix['count'] == 1, str(by_prefix['count']))

    _, page_one = get_json(app, f'/api/crawls/{SECOND}/issues?limit=1&offset=1')
    result('issues: limit and offset paginate a stable order',
           page_one['count'] == 1 and page_one['total'] == 2 and page_one['offset'] == 1,
           str({k: page_one[k] for k in ('count', 'total', 'offset')}))


def test_issue_formats():
    app = client()
    csv_response = app.get(f'/api/crawls/{SECOND}/issues?format=csv')
    rows = list(csv.DictReader(io.StringIO(csv_response.data.decode())))
    result('formats: csv parses with a header and one row per issue',
           len(rows) == 2 and 'evidence' in rows[0] and rows[0]['url'].startswith('https://'),
           str(rows[:1]))
    result('formats: csv is offered as a download',
           'attachment' in csv_response.headers.get('Content-Disposition', ''),
           csv_response.headers.get('Content-Disposition', ''))

    nd = app.get(f'/api/crawls/{SECOND}/issues?format=ndjson')
    lines = [json.loads(line) for line in nd.data.decode().splitlines() if line.strip()]
    result('formats: ndjson is one json object per line',
           len(lines) == 2 and all('check' in line for line in lines), str(lines[:1])[:120])


def test_pages_endpoint():
    app = client()
    status, body = get_json(app, f'/api/crawls/{SECOND}/pages?fields=url,status_code,word_count')
    result('pages: field selection returns only what was asked for',
           status == 200 and set(body['pages'][0]) == {'url', 'status_code', 'word_count'},
           str(body['pages'][0]))
    result('pages: sorted by url',
           [p['url'] for p in body['pages']] == sorted(p['url'] for p in body['pages']))

    _, filtered = get_json(app, f'/api/crawls/{SECOND}/pages?url_prefix=https://site.test/pricing')
    result('pages: url_prefix filter', filtered['count'] == 1, str(filtered['count']))


def test_links_endpoint():
    app = client()
    status, body = get_json(app, f'/api/crawls/{SECOND}/links?internal=true')
    result('links: internal filter', status == 200 and body['count'] == 1
           and body['links'][0]['target_url'].endswith('/pricing'), str(body['links']))
    _, by_anchor = get_json(app, f'/api/crawls/{SECOND}/links?anchor=partner')
    result('links: anchor search', by_anchor['count'] == 1
           and by_anchor['links'][0]['nofollow'] in (1, True), str(by_anchor['links']))


def test_sitemaps_endpoint():
    app = client()
    status, body = get_json(app, f'/api/crawls/{SECOND}/sitemaps')
    result('sitemaps: per-file summaries come back',
           status == 200 and body['files'][0]['entries'] == 2, str(body['files']))
    result('sitemaps: coverage lists come back',
           body['coverage']['crawled_only'] == ['https://site.test/pricing'],
           str(body['coverage']))


def test_diff_endpoint():
    app = client()
    status, body = get_json(app, f'/api/crawls/{SECOND}/diff/{FIRST}')
    new_checks = {i['check'] for i in body['new']}
    fixed_checks = {i['check'] for i in body['fixed']}
    open_checks = {i['check'] for i in body['still_open']}
    result('diff: the issue fixed since the first crawl is listed as fixed',
           status == 200 and fixed_checks == {'missing_meta_description'}, str(fixed_checks))
    result('diff: the new issue is listed as new', new_checks == {'slow_response_time'}, str(new_checks))
    result('diff: the unchanged issue is still open', open_checks == {'thin_content'},
           str(open_checks))


def test_accepting_an_issue():
    app = client()
    _, before = get_json(app, f'/api/crawls/{SECOND}/issues')
    thin_id = ISSUES['thin']['id']

    refused = app.post(f'/api/issues/{thin_id}/accept', json={})
    result('accept: a reason is required', refused.status_code == 400, str(refused.status_code))

    accepted = app.post(f'/api/issues/{thin_id}/accept',
                        json={'reason': 'Short by design, it is a pricing table'})
    result('accept: accepting succeeds', json.loads(accepted.data)['success'])

    _, after = get_json(app, f'/api/crawls/{SECOND}/issues')
    result('accept: an accepted issue drops out of the list',
           after['total'] == before['total'] - 1
           and thin_id not in {i['id'] for i in after['issues']},
           f"before={before['total']} after={after['total']}")

    _, with_accepted = get_json(app, f'/api/crawls/{SECOND}/issues?include_accepted=true')
    shown = {i['id']: i for i in with_accepted['issues']}
    result('accept: it is still visible on request, with its reason',
           shown.get(thin_id, {}).get('status') == 'accepted'
           and 'pricing table' in (shown.get(thin_id, {}).get('accepted_reason') or ''),
           str(shown.get(thin_id, {}).get('accepted_reason')))

    _, listed = get_json(app, '/api/issues/accepted')
    result('accept: it appears in the accepted list',
           any(a['issue_key'] == thin_id for a in listed['accepted']), str(listed['accepted']))

    app.delete(f'/api/issues/{thin_id}/accept')
    _, restored = get_json(app, f'/api/crawls/{SECOND}/issues')
    result('accept: un-accepting brings it back', restored['total'] == before['total'],
           str(restored['total']))


def test_unknown_crawl_is_not_found():
    app = client()
    status, body = get_json(app, '/api/crawls/999999/issues')
    result('unknown crawl: 404 rather than an empty list',
           status == 404 and body.get('success') is False, f'status={status}')


def test_page_detail_returns_the_whole_page():
    app = client()
    status, body = get_json(app, page_url(DETAIL_NEW, DETAIL_URL))
    page = body.get('page', {})
    result('page detail: the row comes back', status == 200 and page.get('title') == 'New Guide Title'
           and page.get('canonical_url') == DETAIL_URL, f"status={status} title={page.get('title')}")

    tree = page.get('headings') or []
    top = tree[0] if tree else {}
    second_level = [child['text'] for child in top.get('children', [])]
    third_level = [grand['text'] for child in top.get('children', [])
                   for grand in child.get('children', [])]
    result('page detail: headings nest by level under the h1',
           top.get('level') == 1 and second_level == ['Getting started', 'Troubleshooting']
           and third_level == ['Install the widget', 'Configure it'],
           f'{second_level} / {third_level}')
    result('page detail: a heading keeps its visibility and dom order',
           top['children'][0]['children'][1]['is_visible'] is False
           and top['children'][0]['children'][1]['dom_order'] == 3,
           str(top['children'][0]['children'][1]))

    result('page detail: schema blocks are parsed json, not strings',
           isinstance(page.get('json_ld'), list) and page['json_ld'][0]['@type'] == 'Article',
           str(page.get('json_ld'))[:120])
    result('page detail: images and hreflang come back',
           len(page.get('images') or []) == 2 and len(page.get('hreflang') or []) == 2,
           f"images={len(page.get('images') or [])} hreflang={len(page.get('hreflang') or [])}")
    http = page.get('http') or {}
    result('page detail: the http metadata is grouped for the response panel',
           http.get('status_code') == 200 and http.get('etag') == 'W/"abc123"'
           and http.get('cache_control') == 'max-age=3600' and http.get('ttfb') == 0.182
           and http.get('redirects')[0]['status'] == 301, str(http)[:160])

    checks = {i['check'] for i in body.get('issues', [])}
    result('page detail: only this URL\'s issues are listed',
           checks == {'title_too_long', 'duplicate_h1'}, str(checks))
    result('page detail: issues keep the standard shape',
           all({'id', 'check', 'type', 'category', 'issue', 'details', 'evidence',
                'how_to_fix', 'status'} <= set(i) for i in body['issues'])
           and any(i['how_to_fix'] for i in body['issues']), str(body['issues'][:1])[:160])


def test_page_detail_splits_links_both_ways():
    app = client()
    _, body = get_json(app, page_url(DETAIL_NEW, DETAIL_URL))
    links = body.get('links', {})
    inbound = links.get('inbound', [])
    outbound = links.get('outbound', [])

    result('page detail: inbound links are the ones pointing at this URL',
           {l['source_url'] for l in inbound} == {'https://detail.test/',
                                                  'https://detail.test/pricing'}
           and all(l['target_url'] == DETAIL_URL for l in inbound),
           str([l['source_url'] for l in inbound]))
    result('page detail: outbound links are the ones leaving this URL',
           {l['target_url'] for l in outbound} == {'https://detail.test/pricing',
                                                   'https://external.test/docs',
                                                   NEW_PAGE_URL}
           and all(l['source_url'] == DETAIL_URL for l in outbound),
           str([l['target_url'] for l in outbound]))

    nav = next((l for l in inbound if l['source_url'] == 'https://detail.test/'), {})
    result('page detail: a link carries its anchor, placement and target status',
           nav.get('anchor_text') == 'Read the guide' and nav.get('placement') == 'nav'
           and nav.get('target_status') == 200, str(nav))
    external = next((l for l in outbound if l['target_url'] == 'https://external.test/docs'), {})
    result('page detail: rel and nofollow survive',
           external.get('nofollow') in (1, True) and 'nofollow' in (external.get('rel') or ''),
           str(external))
    result('page detail: the link counts split internal from external',
           links['counts'] == {'inbound': 2, 'outbound': 3,
                               'outbound_internal': 2, 'outbound_external': 1},
           str(links.get('counts')))


def test_page_detail_diff_against_the_previous_crawl():
    app = client()
    _, body = get_json(app, page_url(DETAIL_NEW, DETAIL_URL))
    diff = body.get('diff') or {}

    result('page diff: the previous crawl is found without being named',
           diff.get('baseline_crawl') == DETAIL_OLD and diff.get('introduced') is False,
           f"baseline={diff.get('baseline_crawl')}")
    result('page diff: a rewritten title is reported with both values',
           (diff.get('title_changed') or {}) == {'from': 'Old Guide Title',
                                                 'to': 'New Guide Title'},
           str(diff.get('title_changed')))
    result('page diff: an unchanged field is not reported as a change',
           diff.get('meta_description_changed') is None
           and diff.get('canonical_changed') is None and diff.get('status_changed') is None,
           str([diff.get('meta_description_changed'), diff.get('canonical_changed'),
                diff.get('status_changed')]))
    result('page diff: the word count delta is signed',
           diff.get('word_count_delta') == -40, str(diff.get('word_count_delta')))

    result('page diff: the deleted sentence is named',
           diff.get('text_changed') is True
           and any('will be deleted' in s for s in diff.get('removed_sentences') or []),
           str(diff.get('removed_sentences')))
    result('page diff: the sentence that replaced it is named',
           any('fresh sentence' in s for s in diff.get('added_sentences') or []),
           str(diff.get('added_sentences')))

    removed = diff.get('links_removed') or []
    added = diff.get('links_added') or []
    result('page diff: the lost internal link is reported, with its direction',
           [l['target_url'] for l in removed] == ['https://detail.test/legacy']
           and removed[0]['direction'] == 'outbound'
           and removed[0]['anchor_text'] == 'legacy notes', str(removed))
    result('page diff: the new internal link is reported',
           [l['target_url'] for l in added] == [NEW_PAGE_URL], str(added))

    result('page diff: the fixed issue is the one that went away',
           [i['check'] for i in diff.get('issues_fixed') or []] == ['images_missing_alt'],
           str([i['check'] for i in diff.get('issues_fixed') or []]))
    result('page diff: the new issue is the one that appeared',
           [i['check'] for i in diff.get('issues_new') or []] == ['duplicate_h1'],
           str([i['check'] for i in diff.get('issues_new') or []]))
    result('page diff: the unchanged issue is still open',
           [i['check'] for i in diff.get('issues_still_open') or []] == ['title_too_long'],
           str([i['check'] for i in diff.get('issues_still_open') or []]))


def test_page_detail_diff_for_a_brand_new_page():
    app = client()
    _, body = get_json(app, page_url(DETAIL_NEW, NEW_PAGE_URL))
    diff = body.get('diff') or {}
    result('page diff: a page absent from the baseline says so',
           diff.get('introduced') is True and diff.get('baseline_crawl') == DETAIL_OLD,
           str({k: diff.get(k) for k in ('introduced', 'baseline_crawl')}))
    result('page diff: a new page reports no removals',
           diff.get('removed_sentences') == [] and diff.get('links_removed') == []
           and diff.get('issues_fixed') == [] and diff.get('word_count_delta') is None,
           str({k: diff.get(k) for k in ('removed_sentences', 'links_removed',
                                         'issues_fixed', 'word_count_delta')}))
    result('page diff: every issue on a new page is new',
           [i['check'] for i in diff.get('issues_new') or []] == ['missing_meta_description']
           and diff.get('issues_still_open') == [],
           str([i['check'] for i in diff.get('issues_new') or []]))


def test_page_detail_without_a_baseline():
    app = client()
    status, body = get_json(app, page_url(DETAIL_OLD, DETAIL_URL))
    result('page detail: the first crawl of a site has no diff but still answers',
           status == 200 and body.get('diff') is None and 'earlier' in (body.get('note') or ''),
           f"status={status} note={body.get('note')}")

    status, body = get_json(app, page_url(DETAIL_NEW, DETAIL_URL) + '&baseline=999999')
    result('page detail: a baseline that does not exist falls back to no diff',
           status == 200 and body.get('diff') is None
           and '999999' in (body.get('note') or '') and body.get('page'),
           f"status={status} note={body.get('note')}")

    status, body = get_json(app, page_url(DETAIL_NEW, DETAIL_URL) + f'&baseline={DETAIL_OLD}')
    result('page detail: an explicit baseline is honoured',
           status == 200 and (body.get('diff') or {}).get('baseline_crawl') == DETAIL_OLD,
           str((body.get('diff') or {}).get('baseline_crawl')))


def test_page_detail_not_found():
    app = client()
    status, body = get_json(app, page_url(999999, DETAIL_URL))
    result('page detail: an unknown crawl is a 404',
           status == 404 and body.get('success') is False, f'status={status}')

    status, body = get_json(app, page_url(DETAIL_NEW, 'https://detail.test/nope'))
    result('page detail: an unknown URL is a 404 naming the crawl',
           status == 404 and str(DETAIL_NEW) in (body.get('error') or ''),
           f"status={status} error={body.get('error')}")

    status, body = get_json(app, f'/api/crawls/{DETAIL_NEW}/page')
    result('page detail: a missing url parameter is a 400',
           status == 400 and 'url' in (body.get('error') or ''),
           f"status={status} error={body.get('error')}")


TESTS = (
    test_issues_endpoint,
    test_issue_formats,
    test_pages_endpoint,
    test_links_endpoint,
    test_sitemaps_endpoint,
    test_diff_endpoint,
    test_accepting_an_issue,
    test_unknown_crawl_is_not_found,
    test_page_detail_returns_the_whole_page,
    test_page_detail_splits_links_both_ways,
    test_page_detail_diff_against_the_previous_crawl,
    test_page_detail_diff_for_a_brand_new_page,
    test_page_detail_without_a_baseline,
    test_page_detail_not_found,
)


def main_():
    global FIRST, SECOND, ISSUES, DETAIL_OLD, DETAIL_NEW, DETAIL_ISSUES

    handle, path = tempfile.mkstemp(suffix='.db')
    os.close(handle)
    original = crawl_db.DB_FILE
    try:
        crawl_db.DB_FILE = path
        import main as app_module
        app_module.crawl_db = crawl_db  # the app reads through the same module
        crawl_db.init_crawl_tables()
        FIRST, SECOND, ISSUES = build_fixture_crawls()
        DETAIL_OLD, DETAIL_NEW, DETAIL_ISSUES = build_detail_crawls()

        for test in TESTS:
            print(f'\n{test.__name__}:')
            try:
                test()
            except Exception as exc:  # a raised test is a failed test
                result(test.__name__, False, f'raised {type(exc).__name__}: {exc}')
    finally:
        crawl_db.DB_FILE = original
        os.unlink(path)

    print('\n' + '=' * 64)
    print(f'  {len(PASSED)} passed, {len(FAILED)} failed')
    if FAILED:
        print('  failed: ' + ', '.join(FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main_())
