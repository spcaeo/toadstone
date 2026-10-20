"""Tests for the MCP server (src/mcp/).

Drives the real protocol against the real Flask app with a throwaway database,
so no server, no network and no crawl are needed:

    python tests/mcp_tests.py

Two things are being pinned. The first is that every tool answers, with the
shape its description promises - a model cannot work around a tool that
silently returns the wrong key. The second, and the reason this server needs
tests at all, is that the guardrails refuse: an MCP server is a crawler with
its trigger wired to a language model, and the interesting failure is not a
wrong answer, it is two thousand requests to a site that is not yours.

No test may start a crawl. `RecordingClient` below intercepts the one write
that would, so the happy path through `start_crawl` is exercised without
anything being fetched.
"""

import json
import os
import sys
import tempfile

os.environ.setdefault('LOCAL_MODE', 'true')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import crawl_db  # noqa: E402
from src.mcp import config  # noqa: E402
from src.mcp.client import FlaskClient  # noqa: E402
from src.mcp.guardrails import Guardrails  # noqa: E402
from src.mcp.server import (INVALID_PARAMS, INVALID_REQUEST, METHOD_NOT_FOUND,  # noqa: E402
                            PARSE_ERROR, build)
from src.mcp.tools import DATASETS  # noqa: E402
from src.utils.issue_factory import make_issue  # noqa: E402

PASSED, FAILED = [], []

SITE = 'https://site.test'
OTHER_USER = 999_777          # an id the local test session will never have
STUB_CRAWL_ID = 4242          # what RecordingClient pretends to have started


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


# ---------------------------------------------------------------------------
# fixtures: two crawls of one site, and one crawl belonging to somebody else
# ---------------------------------------------------------------------------

HOME = f'{SITE}/'
PRICING = f'{SITE}/pricing'

HEADINGS = [
    {'level': 1, 'text': 'Pricing', 'is_visible': True, 'dom_order': 0},
    {'level': 2, 'text': 'Plans', 'is_visible': True, 'dom_order': 1},
    {'level': 3, 'text': 'Team', 'is_visible': True, 'dom_order': 2},
]


def build_fixture_crawls(user_id):
    """Two crawls of one site: the second fixes one issue and files another."""
    def crawl(session_id):
        return crawl_db.create_crawl(user_id=user_id, session_id=session_id,
                                     base_url=SITE, base_domain='site.test',
                                     config_snapshot={})

    first, second = crawl('m1'), crawl('m2')

    pages = [
        {'url': HOME, 'status_code': 200, 'title': 'Home', 'h1': 'Home',
         'meta_description': 'The home page', 'canonical_url': HOME,
         'word_count': 400, 'main_text': 'home body', 'main_text_hash': 'h1',
         'depth': 0, 'content_type': 'text/html', 'lang': 'en'},
        {'url': PRICING, 'status_code': 200, 'title': 'Pricing', 'h1': 'Pricing',
         'meta_description': '', 'canonical_url': PRICING, 'word_count': 120,
         'main_text': 'pricing body', 'main_text_hash': 'h2', 'depth': 1,
         'content_type': 'text/html', 'lang': 'en', 'headings': HEADINGS,
         'images': [{'src': f'{SITE}/img/a.png', 'alt': ''},
                    {'src': f'{SITE}/img/b.png', 'alt': 'A chart'}],
         'json_ld': [{'@context': 'https://schema.org', '@type': 'Product'}]},
    ]
    links = [
        {'source_url': HOME, 'target_url': PRICING, 'anchor_text': 'See pricing',
         'is_internal': True, 'target_status': 200, 'placement': 'body', 'rel': '',
         'nofollow': False, 'link_target': '', 'in_nav': False, 'in_main': True,
         'in_heading': False},
        {'source_url': PRICING, 'target_url': 'https://partner.test/x',
         'anchor_text': 'partner', 'is_internal': False, 'target_status': 200,
         'placement': 'body', 'rel': 'nofollow', 'nofollow': True,
         'link_target': '_blank', 'in_nav': False, 'in_main': True,
         'in_heading': False},
    ]

    thin = make_issue(PRICING, 'thin_content', 'warning', 'Content', 'Thin Content',
                      'Page has only 120 words', offending_value='120',
                      evidence={'found': '120 words'},
                      how_to_fix='Add substantive copy.')
    missing_desc = make_issue(PRICING, 'missing_meta_description', 'error', 'SEO',
                              'Missing Meta Description', 'No meta description',
                              evidence={'selector': 'head > meta[name=description]'},
                              how_to_fix='Write one.')
    slow = make_issue(HOME, 'slow_response_time', 'warning', 'Performance',
                      'Slow Response Time', 'Took 4000ms', offending_value='4000')

    for crawl_id, issues in ((first, [thin, missing_desc]), (second, [thin, slow])):
        crawl_db.save_url_batch(crawl_id, pages)
        crawl_db.save_links_batch(crawl_id, links)
        crawl_db.save_issues_batch(crawl_id, issues)
        crawl_db.update_crawl_stats(crawl_id, discovered=len(pages),
                                    crawled=len(pages), max_depth=1)
        crawl_db.set_crawl_status(crawl_id, 'completed')

    foreign = crawl_db.create_crawl(user_id=OTHER_USER, session_id='m3',
                                    base_url='https://private.test',
                                    base_domain='private.test', config_snapshot={})
    crawl_db.set_crawl_status(foreign, 'completed')

    return first, second, foreign, {'thin': thin, 'missing_desc': missing_desc,
                                    'slow': slow}


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

class RecordingClient(FlaskClient):
    """A client that records the crawl-start instead of making it.

    `POST /api/crawls` is the one call in this server that would reach the
    network, so the tests must never let it through. Everything else - the
    guardrails, the argument handling, the response shape - is the real code
    path.
    """

    def __init__(self, app, cookies=None):
        super().__init__(app, cookies)
        self.posts = []

    def post_json(self, path, payload=None, params=None):
        self.posts.append((path, payload))
        if path == '/api/crawls':
            return {'success': True, 'crawl_id': STUB_CRAWL_ID,
                    'status_url': f'/api/crawl_status?crawl_id={STUB_CRAWL_ID}'}
        return super().post_json(path, payload, params)


def settings(**overrides):
    """Settings built from nothing but these overrides: the env must not leak in."""
    base = config.from_env(environ={})
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def server(client=None, **overrides):
    import main

    resolved = settings(**overrides)
    return build(resolved, client or FlaskClient(main.app)), resolved


def call(srv, name, arguments=None):
    """Run one tool through the protocol. Returns (is_error, payload)."""
    response = srv.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                           'params': {'name': name, 'arguments': arguments or {}}})
    body = response['result']
    return body['isError'], json.loads(body['content'][0]['text'])


def ok(srv, name, arguments=None):
    """Run a tool that is expected to succeed, or raise so the test fails loudly."""
    is_error, payload = call(srv, name, arguments)
    if is_error:
        raise AssertionError(f'{name} refused: {payload.get("error")}')
    return payload


# ---------------------------------------------------------------------------
# protocol
# ---------------------------------------------------------------------------

def test_initialize():
    srv, _ = server()
    body = srv.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                       'params': {'protocolVersion': '2025-06-18'}})['result']
    result('initialize: agrees the protocol version',
           body['protocolVersion'] == '2025-06-18', str(body.get('protocolVersion')))
    result('initialize: declares tools and nothing else',
           list(body['capabilities']) == ['tools'], str(body['capabilities']))
    result('initialize: names the server',
           body['serverInfo']['name'] == 'toadstone', str(body['serverInfo']))
    result('initialize: the instructions state the guardrails',
           'read-only' in body['instructions'] and '2000' in body['instructions'],
           body['instructions'][-120:])

    old = srv.handle({'jsonrpc': '2.0', 'id': 2, 'method': 'initialize',
                      'params': {'protocolVersion': '2024-11-05'}})['result']
    result('initialize: an older supported version is honoured, not overridden',
           old['protocolVersion'] == '2024-11-05', str(old['protocolVersion']))

    future = srv.handle({'jsonrpc': '2.0', 'id': 3, 'method': 'initialize',
                         'params': {'protocolVersion': '2099-01-01'}})['result']
    result('initialize: an unknown version falls back to ours',
           future['protocolVersion'] == '2025-06-18', str(future['protocolVersion']))

    result('ping: answers',
           srv.handle({'jsonrpc': '2.0', 'id': 4, 'method': 'ping'})['result'] == {})
    result('notifications: get no reply',
           srv.handle({'jsonrpc': '2.0',
                       'method': 'notifications/initialized'}) is None)


def test_tools_list():
    srv, _ = server()
    tools = srv.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']
    names = [tool['name'] for tool in tools]
    expected = ['list_crawls', 'crawl_status', 'start_crawl', 'list_issues',
                'issue_detail', 'page_detail', 'regression_diff', 'export_dataset',
                'check_reference', 'accept_issue']
    result('tools/list: every planned tool is there', names == expected, str(names))

    well_formed = all(
        tool['description'] and tool['inputSchema']['type'] == 'object'
        and isinstance(tool['inputSchema']['properties'], dict)
        and all(prop.get('description') for prop in tool['inputSchema']['properties'].values())
        for tool in tools)
    result('tools/list: each tool has a description and a described schema',
           well_formed)

    required_exists = all(
        set(tool['inputSchema']['required']) <= set(tool['inputSchema']['properties'])
        for tool in tools)
    result('tools/list: nothing is required that is not a property', required_exists)

    writes = [t for t in tools if t['name'] in ('start_crawl', 'accept_issue')]
    result('tools/list: on a read-only server the write tools say so',
           all('currently running read-only' in t['description'] for t in writes))

    rw = build(settings(allow_writes=True), FlaskClient(__import__('main').app))
    rw_tools = rw.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})['result']['tools']
    result('tools/list: with writes enabled they do not',
           all('currently running read-only' not in t['description']
               for t in rw_tools if t['name'] in ('start_crawl', 'accept_issue')))


def test_protocol_errors():
    srv, _ = server()

    bad_json = json.loads(srv.handle_text('{not json'))
    result('protocol: unparseable JSON is a parse error',
           bad_json['error']['code'] == PARSE_ERROR, str(bad_json['error']))

    unknown = srv.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'nope/nope'})
    result('protocol: an unknown method is -32601',
           unknown['error']['code'] == METHOD_NOT_FOUND, str(unknown['error']))

    batch = srv.handle([{'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}])
    result('protocol: a batch is refused, not half-answered',
           batch['error']['code'] == INVALID_REQUEST, str(batch['error']))

    scalar = srv.handle('ping')
    result('protocol: a non-object request is refused',
           scalar['error']['code'] == INVALID_REQUEST, str(scalar['error']))

    params = srv.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'ping', 'params': []})
    result('protocol: non-object params are -32602',
           params['error']['code'] == INVALID_PARAMS, str(params['error']))

    nameless = srv.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                           'params': {}})
    result('protocol: tools/call with no tool name is -32602',
           nameless['error']['code'] == INVALID_PARAMS, str(nameless['error']))

    resources = srv.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'resources/list'})
    result('protocol: resources/list says this server is tools-only',
           resources['error']['code'] == METHOD_NOT_FOUND
           and 'tools only' in resources['error']['message'],
           str(resources['error']))

    result('protocol: a response arriving at the server is ignored',
           srv.handle({'jsonrpc': '2.0', 'id': 9, 'result': {}}) is None)


# ---------------------------------------------------------------------------
# read tools
# ---------------------------------------------------------------------------

def test_list_crawls():
    srv, _ = server()
    body = ok(srv, 'list_crawls', {'limit': 10})
    ids = [crawl['id'] for crawl in body['crawls']]
    result('list_crawls: returns this user\'s crawls',
           FIRST in ids and SECOND in ids, str(ids))
    result('list_crawls: another user\'s crawl is not in the list',
           FOREIGN not in ids, str(ids))
    result('list_crawls: rows are trimmed to the useful columns',
           set(body['crawls'][0]) <= {'id', 'base_url', 'base_domain', 'status',
                                      'started_at', 'completed_at', 'urls_crawled',
                                      'urls_discovered', 'max_depth_reached',
                                      'can_resume'},
           str(sorted(body['crawls'][0])))
    result('list_crawls: a sitemap blob is not dragged along',
           'sitemap_coverage' not in body['crawls'][0])

    filtered = ok(srv, 'list_crawls', {'limit': 10, 'status': 'running'})
    result('list_crawls: the status filter reaches the API',
           filtered['count'] == 0, str(filtered['count']))


def test_crawl_status():
    srv, _ = server()
    body = ok(srv, 'crawl_status', {'crawl_id': SECOND})
    result('crawl_status: reports the crawl and its state',
           body['crawl_id'] == SECOND and body['status'] == 'completed',
           str(body.get('status')))
    result('crawl_status: carries the progress fields a poller needs',
           body['pages_done'] == 2 and body['progress'] == 100.0
           and 'eta_seconds' in body, str(body))


def test_list_issues():
    srv, _ = server()
    body = ok(srv, 'list_issues', {'crawl_id': SECOND})
    result('list_issues: returns this crawl\'s issues', body['total'] == 2,
           str(body['total']))
    result('list_issues: tallies by severity',
           body['by_severity'] == {'warning': 2}, str(body['by_severity']))
    result('list_issues: tallies by check',
           set(body['by_check']) == {'thin_content', 'slow_response_time'},
           str(body['by_check']))

    first = body['issues'][0]
    result('list_issues: the stable id survives', len(first['id']) == 40, first['id'])
    result('list_issues: the evidence block survives',
           any(issue.get('evidence') for issue in body['issues']),
           str([i.get('evidence') for i in body['issues']]))
    result('list_issues: how_to_fix survives',
           any(issue.get('how_to_fix') for issue in body['issues']))


def test_list_issues_filters():
    srv, _ = server()

    errors = ok(srv, 'list_issues', {'crawl_id': FIRST, 'severity': 'error'})
    result('list_issues: severity narrows',
           errors['total'] == 1 and errors['issues'][0]['check'] == 'missing_meta_description',
           str(errors['total']))

    both = ok(srv, 'list_issues', {'crawl_id': FIRST, 'severity': 'error,warning'})
    result('list_issues: a comma-separated severity widens again',
           both['total'] == 2, str(both['total']))

    by_check = ok(srv, 'list_issues', {'crawl_id': FIRST, 'check': 'thin_content'})
    result('list_issues: check narrows', by_check['total'] == 1, str(by_check['total']))

    prefix = ok(srv, 'list_issues', {'crawl_id': FIRST, 'url_prefix': f'{SITE}/pricing'})
    result('list_issues: url_prefix narrows', prefix['total'] == 2, str(prefix['total']))

    nothing = ok(srv, 'list_issues', {'crawl_id': FIRST, 'url_prefix': 'https://elsewhere/'})
    result('list_issues: a prefix that matches nothing returns nothing, not everything',
           nothing['total'] == 0, str(nothing['total']))

    limited = ok(srv, 'list_issues', {'crawl_id': FIRST, 'limit': 1})
    result('list_issues: limit caps the rows but not the total',
           limited['count'] == 1 and limited['total'] == 2,
           f"count={limited['count']} total={limited['total']}")

    since = ok(srv, 'list_issues', {'crawl_id': SECOND, 'since_crawl': FIRST})
    result('list_issues: since_crawl keeps only what is new',
           {i['check'] for i in since['issues']} == {'slow_response_time'},
           str([i['check'] for i in since['issues']]))

    is_error, payload = call(srv, 'list_issues', {'crawl_id': FIRST,
                                                  'severity': 'critical'})
    result('list_issues: an invented severity is refused with the real ones',
           is_error and 'error, warning, info' in payload['error'],
           str(payload.get('error')))


def test_issue_detail():
    srv, _ = server()
    thin_id = ISSUES['thin']['id']
    body = ok(srv, 'issue_detail', {'crawl_id': FIRST, 'issue_id': thin_id})
    result('issue_detail: finds the issue by its stable id',
           body['issue']['id'] == thin_id and body['issue']['check'] == 'thin_content',
           str(body['issue'].get('check')))
    result('issue_detail: attaches the page it sits on',
           body['page']['url'] == PRICING, str((body.get('page') or {}).get('url')))
    result('issue_detail: says how many issues that page has',
           body['page_issue_count'] == 2, str(body.get('page_issue_count')))

    is_error, payload = call(srv, 'issue_detail', {'crawl_id': FIRST,
                                                   'issue_id': 'f' * 40})
    result('issue_detail: an id that is not in this crawl explains why',
           is_error and 'scoped to a crawl' in payload['error'],
           str(payload.get('error')))


def test_page_detail():
    srv, _ = server()
    body = ok(srv, 'page_detail', {'crawl_id': SECOND, 'url': PRICING})
    page = body['page']
    result('page_detail: returns the stored row',
           page['url'] == PRICING and page['status_code'] == 200, str(page.get('url')))
    result('page_detail: the headings are a tree, not a flat list',
           page['headings'][0]['text'] == 'Pricing'
           and page['headings'][0]['children'][0]['text'] == 'Plans',
           str(page['headings'])[:120])
    result('page_detail: images are counted, not inlined',
           page['image_count'] == 2 and page['images_missing_alt'] == 1,
           f"{page['image_count']}/{page['images_missing_alt']}")
    result('page_detail: schema types are named',
           page['schema_types'] == ['Product'], str(page['schema_types']))
    result('page_detail: the page body is not dragged into the context',
           'main_text' not in page, str(sorted(page))[:160])
    result('page_detail: link counts come back',
           body['link_counts']['inbound'] == 1 and body['link_counts']['outbound'] == 1,
           str(body['link_counts']))
    result('page_detail: issues come with it', body['issue_count'] == 1,
           str(body['issue_count']))
    result('page_detail: the diff against the previous crawl is summarised',
           body['diff']['baseline_crawl'] == FIRST
           and body['diff']['issues_fixed']['count'] == 1,
           str(body['diff'])[:160])

    trimmed = ok(srv, 'page_detail', {'crawl_id': SECOND, 'url': PRICING,
                                      'include_links': False})
    result('page_detail: link rows can be left out', 'links' not in trimmed)

    is_error, payload = call(srv, 'page_detail', {'crawl_id': SECOND,
                                                  'url': f'{SITE}/nope'})
    result('page_detail: a URL that was not crawled is a tool error, not a crash',
           is_error and 'not found' in payload['error'].lower(),
           str(payload.get('error')))


def test_regression_diff():
    srv, _ = server()
    body = ok(srv, 'regression_diff', {'baseline_crawl_id': FIRST,
                                       'candidate_crawl_id': SECOND})
    result('regression_diff: names both crawls',
           body['baseline_crawl'] == FIRST and body['candidate_crawl'] == SECOND)
    result('regression_diff: the new issue is the one that is new',
           [i['check'] for i in body['new']['issues']] == ['slow_response_time'],
           str(body['new']))
    result('regression_diff: the fixed issue is the one that went away',
           [i['check'] for i in body['fixed']['issues']] == ['missing_meta_description'],
           str(body['fixed']['issues']))
    result('regression_diff: the unchanged issue is still open',
           [i['check'] for i in body['still_open']['issues']] == ['thin_content'],
           str(body['still_open']['issues']))
    result('regression_diff: warns that the id is host-sensitive',
           'different hosts' in body['note'])

    is_error, payload = call(srv, 'regression_diff', {'baseline_crawl_id': FIRST,
                                                      'candidate_crawl_id': FIRST})
    result('regression_diff: a crawl against itself is refused',
           is_error and 'nothing to compare' in payload['error'],
           str(payload.get('error')))


def test_export_dataset():
    srv, _ = server()
    body = ok(srv, 'export_dataset', {'crawl_id': FIRST, 'dataset': 'issues'})
    result('export_dataset: csv comes back with a header row',
           'check' in body['data'].splitlines()[0], body['data'].splitlines()[0][:80])
    result('export_dataset: and with the rows',
           'thin_content' in body['data'] and 'missing_meta_description' in body['data'])

    rows = ok(srv, 'export_dataset', {'crawl_id': FIRST, 'dataset': 'issues',
                                      'format': 'ndjson'})
    lines = [line for line in rows['data'].splitlines() if line.strip()]
    result('export_dataset: ndjson is one parseable object per line',
           len(lines) == 2 and all(json.loads(line)['check'] for line in lines),
           str(len(lines)))

    capped = ok(srv, 'export_dataset', {'crawl_id': FIRST, 'dataset': 'issues',
                                        'format': 'ndjson', 'limit': 1})
    result('export_dataset: limit trims the rows and says it did',
           len([l for l in capped['data'].splitlines() if l.strip()]) == 1
           and capped['truncated'], str(capped['truncated']))

    pages = ok(srv, 'export_dataset', {'crawl_id': FIRST, 'dataset': 'pages',
                                       'format': 'json'})
    result('export_dataset: a second dataset works too',
           PRICING in pages['data'], pages['data'][:80])

    is_error, payload = call(srv, 'export_dataset', {'crawl_id': FIRST,
                                                     'dataset': 'nonsense'})
    result('export_dataset: an unknown dataset lists the real ones',
           is_error and 'sitemap-coverage' in payload['error'],
           str(payload.get('error'))[:100])

    is_error, payload = call(srv, 'export_dataset', {'crawl_id': FIRST,
                                                     'dataset': 'issues',
                                                     'format': 'xlsx'})
    result('export_dataset: xlsx is refused with the URL that would serve it',
           is_error and '/export/issues?format=xlsx' in payload['error'],
           str(payload.get('error'))[:120])


def test_check_reference():
    srv, _ = server()
    body = ok(srv, 'check_reference')
    slugs = {row['check'] for row in body['checks']}
    result('check_reference: lists what this install has reported',
           {'thin_content', 'missing_meta_description', 'slow_response_time'} <= slugs,
           str(sorted(slugs)))

    one = ok(srv, 'check_reference', {'check': 'thin_content'})
    result('check_reference: one check comes with its fix text',
           one['check']['how_to_fix'] == 'Add substantive copy.',
           str(one['check'].get('how_to_fix')))
    result('check_reference: and its severity',
           one['check']['severity'] == 'warning', str(one['check'].get('severity')))

    is_error, payload = call(srv, 'check_reference', {'check': 'thin_contnet'})
    result('check_reference: a near miss is told what it probably meant',
           is_error and 'thin_content' in payload['error'], str(payload.get('error')))


def test_dataset_catalogue_in_step_with_exports():
    from src.api.exports.datasets import DATASETS as REAL

    result('export_dataset: the tool\'s dataset list matches the export catalogue',
           set(DATASETS) == set(REAL),
           f'tool={sorted(DATASETS)} api={sorted(REAL)}')


# ---------------------------------------------------------------------------
# guardrails: the point of the exercise
# ---------------------------------------------------------------------------

def test_read_only_refuses_writes():
    import main

    # RecordingClient, not a plain one: if the read-only guard ever regresses,
    # this test must fail rather than quietly crawl site.test.
    client = RecordingClient(main.app)
    srv, _ = server(client)

    for tool, arguments in (('start_crawl', {'url': SITE}),
                            ('accept_issue', {'issue_id': ISSUES['thin']['id'],
                                              'reason': 'by design'})):
        is_error, payload = call(srv, tool, arguments)
        result(f'read-only: {tool} is refused',
               is_error and payload['rule'] == 'read_only', str(payload)[:120])
        result(f'read-only: {tool} is told how to enable writes',
               'TOADSTONE_MCP_ALLOW_WRITES' in payload['error'])

    result('read-only: nothing was written',
           client.posts == [] and crawl_db.get_accepted_issues(USER_ID) == {},
           f'posts={client.posts} accepted={crawl_db.get_accepted_issues(USER_ID)}')


def test_host_allow_list():
    import main

    client = RecordingClient(main.app)
    srv, _ = server(client, allow_writes=True)

    is_error, payload = call(srv, 'start_crawl', {'url': 'https://not-ours.test/'})
    result('host allow-list: an empty list means only hosts already crawled',
           is_error and payload['rule'] == 'host_allow_list', str(payload)[:140])
    result('host allow-list: the refusal names the hosts that are allowed',
           'site.test' in payload['error'], str(payload.get('error'))[:200])
    result('host allow-list: and nothing was started', client.posts == [],
           str(client.posts))

    listed = RecordingClient(main.app)
    srv, _ = server(listed, allow_writes=True, allow_hosts=['example.com'])
    is_error, payload = call(srv, 'start_crawl', {'url': SITE})
    result('host allow-list: an explicit list excludes a crawled host',
           is_error and 'not on this server\'s allow-list' in payload['error'],
           str(payload.get('error'))[:140])

    sub = RecordingClient(main.app)
    srv, _ = server(sub, allow_writes=True, allow_hosts=['site.test'])
    ok(srv, 'start_crawl', {'url': 'https://shop.site.test/'})
    result('host allow-list: a subdomain of an allowed host is allowed',
           sub.posts and sub.posts[0][1]['url'] == 'https://shop.site.test/',
           str(sub.posts))

    guards = Guardrails(settings(allow_hosts=['*']), FlaskClient(main.app))
    result('host allow-list: * opts out of the check entirely',
           guards.check_host('https://anything.example/') == 'https://anything.example/')


def test_url_cap():
    import main

    client = RecordingClient(main.app)
    srv, _ = server(client, allow_writes=True)

    is_error, payload = call(srv, 'start_crawl', {'url': SITE, 'max_urls': 50_000})
    result('URL cap: a crawl above the cap is refused',
           is_error and payload['rule'] == 'max_urls', str(payload)[:140])
    result('URL cap: the refusal names the cap and how to raise it',
           '2000' in payload['error'] and 'TOADSTONE_MCP_MAX_URLS' in payload['error'],
           str(payload.get('error')))
    result('URL cap: a refused crawl is not started anyway', client.posts == [])

    body = ok(srv, 'start_crawl', {'url': SITE})
    result('URL cap: a crawl that does not ask gets the cap',
           body['max_urls'] == 2000 and client.posts[-1][1]['max_urls'] == 2000,
           str(client.posts[-1][1]))

    lower = RecordingClient(main.app)
    srv, _ = server(lower, allow_writes=True, max_urls=50)
    is_error, payload = call(srv, 'start_crawl', {'url': SITE, 'max_urls': 200})
    result('URL cap: a lowered cap is enforced too',
           is_error and 'cap of 50' in payload['error'], str(payload.get('error')))
    result('URL cap: asking for less than the cap is allowed',
           ok(srv, 'start_crawl', {'url': SITE, 'max_urls': 10})['max_urls'] == 10)


def test_bad_crawl_targets():
    import main

    client = RecordingClient(main.app)
    srv, _ = server(client, allow_writes=True, allow_hosts=['*'])

    for url, expected in (('file:///etc/passwd', 'Only http and https'),
                          ('ftp://site.test/', 'Only http and https'),
                          ('https://user:secret@site.test/', 'Credentials in the URL'),
                          ('not a url', 'Only http and https')):
        is_error, payload = call(srv, 'start_crawl', {'url': url})
        result(f'crawl target: {url!r} is refused',
               is_error and expected in payload['error'], str(payload.get('error'))[:120])

    is_error, payload = call(srv, 'start_crawl', {'url': 'https://site.test/',
                                                  'max_depth': 5000})
    result('crawl target: an absurd max_depth is refused',
           is_error and payload['rule'] == 'max_depth', str(payload)[:120])

    result('crawl target: none of those started anything', client.posts == [],
           str(client.posts))


def test_start_crawl_happy_path():
    import main

    client = RecordingClient(main.app)
    srv, _ = server(client, allow_writes=True)

    body = ok(srv, 'start_crawl', {'url': SITE, 'render': True, 'max_urls': 100,
                                   'max_depth': 3})
    path, payload = client.posts[-1]
    result('start_crawl: posts to the crawl-creation route', path == '/api/crawls', path)
    result('start_crawl: passes the options through',
           payload == {'url': SITE, 'render': True, 'max_urls': 100, 'max_depth': 3},
           str(payload))
    result('start_crawl: returns the new crawl id',
           body['crawl_id'] == STUB_CRAWL_ID, str(body.get('crawl_id')))
    result('start_crawl: tells the caller what to do next',
           'crawl_status' in body['next'], body.get('next', ''))


def test_accept_issue():
    import main

    srv, _ = server(FlaskClient(main.app), allow_writes=True)
    thin_id = ISSUES['thin']['id']

    body = ok(srv, 'accept_issue', {'issue_id': thin_id,
                                    'reason': 'Short by design, it is a price table'})
    result('accept_issue: the API accepted it', body['accepted'], str(body))

    after = ok(srv, 'list_issues', {'crawl_id': SECOND})
    result('accept_issue: the issue drops out of the open list',
           thin_id not in {issue['id'] for issue in after['issues']},
           str([i['check'] for i in after['issues']]))

    shown = ok(srv, 'list_issues', {'crawl_id': SECOND, 'include_accepted': True})
    muted = next((i for i in shown['issues'] if i['id'] == thin_id), {})
    result('accept_issue: it is still visible on request, with its reason',
           muted.get('status') == 'accepted' and 'price table' in (muted.get('accepted_reason') or ''),
           str(muted.get('accepted_reason')))

    is_error, payload = call(srv, 'accept_issue', {'issue_id': thin_id, 'reason': '   '})
    result('accept_issue: a blank reason is refused',
           is_error and 'reason is required' in payload['error'],
           str(payload.get('error')))

    # Put the fixture back for any test that runs after this one.
    crawl_db.unaccept_issue(thin_id, USER_ID)


def test_another_users_crawl_is_refused():
    srv, _ = server()
    for tool, arguments in (('list_issues', {'crawl_id': FOREIGN}),
                            ('crawl_status', {'crawl_id': FOREIGN}),
                            ('export_dataset', {'crawl_id': FOREIGN,
                                                'dataset': 'issues'})):
        is_error, payload = call(srv, tool, arguments)
        result(f'ownership: {tool} will not read another account\'s crawl',
               is_error and 'nauthorized' in json.dumps(payload),
               str(payload)[:140])


def test_malformed_input_does_not_raise():
    srv, _ = server()

    cases = (
        ('nope', {}, 'Unknown tool'),
        ('list_crawls', {'bogus': 1}, 'does not take bogus'),
        ('list_issues', {'crawl_id': 'abc'}, 'whole number'),
        ('list_issues', {'crawl_id': -3}, 'must be positive'),
        ('list_issues', {'crawl_id': FIRST, 'limit': 'lots'}, 'whole number'),
        ('list_issues', {'crawl_id': FIRST, 'limit': 0}, 'at least 1'),
        ('page_detail', {'crawl_id': FIRST}, 'needs url'),
        ('issue_detail', {'crawl_id': FIRST, 'issue_id': ''}, 'needs issue_id'),
        ('crawl_status', {'crawl_id': 10 ** 9}, 'not found'),
        ('export_dataset', {'crawl_id': FIRST, 'dataset': 'issues',
                            'format': 'parquet'}, 'format must be one of'),
    )
    for tool, arguments, expected in cases:
        try:
            is_error, payload = call(srv, tool, arguments)
        except Exception as error:
            result(f'malformed: {tool} {arguments} is handled', False,
                   f'raised {type(error).__name__}: {error}')
            continue
        text = json.dumps(payload).lower()
        result(f'malformed: {tool} {arguments} is a tool error, not a crash',
               is_error and expected.lower() in text, str(payload)[:140])

    result('malformed: a list where arguments were expected is handled',
           call(srv, 'list_crawls', None)[0] is False)

    weird = srv.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                        'params': {'name': 'list_crawls', 'arguments': 'limit=3'}})
    result('malformed: a string of arguments is a tool error, not a 500',
           weird['result']['isError'] and 'must be an object'
           in weird['result']['content'][0]['text'], str(weird)[:160])


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------

def test_http_transport():
    import main

    http = main.app.test_client()

    info = json.loads(http.get('/api/mcp').data)
    result('http: GET describes the server and its guardrails',
           info['server']['name'] == 'toadstone'
           and info['guardrails']['mode'] == 'read-only', str(info)[:160])

    response = http.post('/api/mcp', json={'jsonrpc': '2.0', 'id': 1,
                                           'method': 'tools/list'})
    body = json.loads(response.data)
    result('http: POST answers a JSON-RPC request',
           response.status_code == 200 and len(body['result']['tools']) == 10,
           f'status={response.status_code}')

    called = http.post('/api/mcp', json={'jsonrpc': '2.0', 'id': 2,
                                         'method': 'tools/call',
                                         'params': {'name': 'list_crawls',
                                                    'arguments': {'limit': 2}}})
    payload = json.loads(json.loads(called.data)['result']['content'][0]['text'])
    result('http: a tool call runs as the signed-in user',
           payload['count'] >= 1 and FOREIGN not in [c['id'] for c in payload['crawls']],
           str(payload)[:140])

    note = http.post('/api/mcp', json={'jsonrpc': '2.0',
                                       'method': 'notifications/initialized'})
    result('http: a notification gets 202 and no body',
           note.status_code == 202 and not note.data, str(note.status_code))

    broken = http.post('/api/mcp', data='{nope', content_type='application/json')
    result('http: unparseable JSON is a JSON-RPC parse error, not a 500',
           broken.status_code == 200
           and json.loads(broken.data)['error']['code'] == PARSE_ERROR,
           f'status={broken.status_code}')


def test_the_old_env_prefix_still_works():
    """The project was renamed, and an agent runtime's config file was not.

    A stdio config block written against LIBRECRAWL_MCP_* is set once and
    never read again. If the rename had silently dropped those names, the
    server would have come up on its defaults - pointing at localhost:5000,
    read-only, 2,000 URLs - and looked like it was working.
    """
    legacy = config.from_env(environ={'LIBRECRAWL_MCP_URL': 'http://old.example:9000',
                                      'LIBRECRAWL_MCP_MAX_URLS': '7',
                                      'LIBRECRAWL_MCP_ALLOW_WRITES': 'true'})
    result('env: the pre-rename prefix is still honoured',
           legacy.base_url == 'http://old.example:9000' and legacy.max_urls == 7
           and legacy.allow_writes is True,
           f'{legacy.base_url} max_urls={legacy.max_urls} writes={legacy.allow_writes}')

    both = config.from_env(environ={'TOADSTONE_MCP_URL': 'http://new.example:9000',
                                    'LIBRECRAWL_MCP_URL': 'http://old.example:9000'})
    result('env: the current name wins when both are set',
           both.base_url == 'http://new.example:9000', both.base_url)

    empty = config.from_env(environ={'TOADSTONE_MCP_URL': ''})
    result('env: an empty value falls through to the default rather than an empty URL',
           empty.base_url == config.DEFAULT_BASE_URL, empty.base_url)


TESTS = (
    test_initialize,
    test_the_old_env_prefix_still_works,
    test_tools_list,
    test_protocol_errors,
    test_list_crawls,
    test_crawl_status,
    test_list_issues,
    test_list_issues_filters,
    test_issue_detail,
    test_page_detail,
    test_regression_diff,
    test_export_dataset,
    test_check_reference,
    test_dataset_catalogue_in_step_with_exports,
    test_read_only_refuses_writes,
    test_host_allow_list,
    test_url_cap,
    test_bad_crawl_targets,
    test_start_crawl_happy_path,
    test_accept_issue,
    test_another_users_crawl_is_refused,
    test_malformed_input_does_not_raise,
    test_http_transport,
)


def main():
    global FIRST, SECOND, FOREIGN, ISSUES, USER_ID

    handle, path = tempfile.mkstemp(suffix='.db')
    os.close(handle)
    original = crawl_db.DB_FILE
    try:
        crawl_db.DB_FILE = path
        import main as app_module
        app_module.crawl_db = crawl_db       # the app reads through the same module
        app_module.app.config['TESTING'] = True

        # main.py mounts this itself now. Mount it here only when it has not,
        # so the suite works against an app that registers it and one that
        # does not - registering twice raises on the blueprint's name.
        if 'mcp' not in app_module.app.blueprints:
            from src.mcp.http_app import create_blueprint
            app_module.app.register_blueprint(
                create_blueprint(app_module.login_required, settings()))

        crawl_db.init_crawl_tables()
        USER_ID = json.loads(
            app_module.app.test_client().get('/api/user/info').data)['user']['id']
        FIRST, SECOND, FOREIGN, ISSUES = build_fixture_crawls(USER_ID)

        for test in TESTS:
            print(f'\n{test.__name__}:')
            try:
                test()
            except Exception as exc:         # a raised test is a failed test
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
    sys.exit(main())
