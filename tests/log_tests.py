#!/usr/bin/env python3
"""Server log analysis: parsing, bot verification, and joining to the crawl.

The claim this module makes is that it can tell what Googlebot actually did.
Two things have to hold for that to be true: the parser must not silently
drop lines it does not understand, and a user agent claiming to be Googlebot
must be checked rather than believed. Both are tested here, with DNS faked.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import log_analysis as la  # noqa: E402
from src.utils.issue_factory import EVIDENCE_FIELDS, SEVERITIES  # noqa: E402

PASSED, FAILED = [], []
BASE = 'https://site.test'

GOOGLEBOT = ('Mozilla/5.0 (compatible; Googlebot/2.1; '
             '+http://www.google.com/bot.html)')
CHROME = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
          '(KHTML, like Gecko) Chrome/141.0 Safari/537.36')


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def combined(path='/', status=200, agent=GOOGLEBOT, ip='66.249.66.1'):
    return (f'{ip} - - [18/Oct/2026:10:00:00 +0000] "GET {path} HTTP/1.1" '
            f'{status} 1234 "-" "{agent}"')


def page(path):
    return {'url': f'{BASE}{path}', 'status_code': 200, 'content_type': 'text/html'}


def slugs(issues):
    return sorted(i['check'] for i in issues)


def fake_dns(reverse_map, forward_map):
    def resolver(kind, value):
        if kind == 'reverse':
            if value not in reverse_map:
                raise OSError('no PTR record')
            return reverse_map[value]
        return forward_map.get(value, [])
    return resolver


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def test_the_three_formats():
    apache = la.parse_lines([combined('/a'), combined('/b', 404)])
    result('parse: the combined format is read',
           len(apache['entries']) == 2 and apache['unparsed'] == 0, str(apache['unparsed']))
    result('parse: path, status and agent all survive',
           apache['entries'][0]['path'] == '/a'
           and apache['entries'][1]['status'] == 404
           and 'Googlebot' in apache['entries'][0]['agent'], str(apache['entries'][0]))

    w3c = la.parse_lines([
        '#Software: Microsoft Internet Information Services 10.0',
        '#Fields: date time c-ip cs-uri-stem cs-uri-query sc-status sc-bytes cs(User-Agent)',
        '2026-10-18 10:00:00 66.249.66.1 /a - 200 1234 Googlebot/2.1',
    ])
    result('parse: the IIS W3C format is read', len(w3c['entries']) == 1, str(w3c))
    result('parse: W3C query strings rejoin the path',
           la.parse_lines([
               '#Fields: date time c-ip cs-uri-stem cs-uri-query sc-status sc-bytes cs(User-Agent)',
               '2026-10-18 10:00:00 1.2.3.4 /a p=1 200 10 Chrome',
           ])['entries'][0]['path'] == '/a?p=1', 'ok')

    cdn = la.parse_lines(['{"uri":"/a","status":200,"user_agent":"Googlebot","client_ip":"66.249.66.1"}'])
    result('parse: a CDN JSON line is read',
           len(cdn['entries']) == 1 and cdn['entries'][0]['path'] == '/a', str(cdn))


def test_unreadable_lines_are_counted_not_guessed():
    """A parser that silently drops 40% of a file is worse than one that refuses."""
    mixed = la.parse_lines([combined('/a'), 'this is not a log line at all',
                            'neither is this', 'nor this'])
    result('parse: unreadable lines are counted',
           mixed['unparsed'] == 3 and mixed['total'] == 4, str(mixed['unparsed']))

    issues = la.log_issues(mixed, [page('/a')], BASE, {'verify_bots': False})
    result('parse: too many unreadable lines is reported, not hidden',
           'log_format_unrecognised' in slugs(issues), str(slugs(issues)))
    result('parse: the finding admits the rest may be incomplete',
           any('incomplete' in i['details'] for i in issues
               if i['check'] == 'log_format_unrecognised'), 'ok')

    clean = la.parse_lines([combined('/a')] * 20)
    result('parse: a readable file raises no format warning',
           'log_format_unrecognised' not in slugs(
               la.log_issues(clean, [page('/a')], BASE, {'verify_bots': False})), 'ok')

    result('parse: empty input does not raise',
           la.parse_lines([])['entries'] == [] and la.parse_lines(None)['entries'] == [], 'ok')


# ---------------------------------------------------------------------------
# bot verification
# ---------------------------------------------------------------------------

def test_a_bot_is_verified_not_believed():
    """Anyone can send Googlebot's user agent, and plenty do."""
    real_ip, fake_ip = '66.249.66.1', '203.0.113.9'
    resolver = fake_dns(
        reverse_map={real_ip: 'crawl-66-249-66-1.googlebot.com',
                     fake_ip: 'someone-elses-vps.example.com'},
        forward_map={'crawl-66-249-66-1.googlebot.com': [real_ip]})

    result('bots: a real Googlebot address verifies',
           la.verify_ip(real_ip, 'googlebot', resolver) is True, 'ok')
    result('bots: an address with the wrong hostname does not',
           la.verify_ip(fake_ip, 'googlebot', resolver) is False, 'ok')

    # The forward confirmation is the point: a PTR record alone proves nothing.
    liar = fake_dns(reverse_map={fake_ip: 'crawl-fake.googlebot.com'},
                    forward_map={'crawl-fake.googlebot.com': ['8.8.8.8']})
    result('bots: a forged PTR record that does not forward-confirm is rejected',
           la.verify_ip(fake_ip, 'googlebot', liar) is False, 'ok')

    def broken(kind, value):
        raise OSError('DNS is down')
    result('bots: a DNS failure is not evidence of a spoof',
           la.verify_ip(real_ip, 'googlebot', broken) is None, 'ok')
    result('bots: a crawler with no published hostnames is not judged',
           la.verify_ip('1.2.3.4', 'gptbot', resolver) is None, 'ok')


def test_spoofing_is_reported():
    real_ip, fake_ip = '66.249.66.1', '203.0.113.9'
    resolver = fake_dns(
        reverse_map={real_ip: 'crawl-66-249-66-1.googlebot.com',
                     fake_ip: 'vps.example.com'},
        forward_map={'crawl-66-249-66-1.googlebot.com': [real_ip]})
    parsed = la.parse_lines([combined('/a', ip=real_ip), combined('/b', ip=fake_ip)])
    issues = la.log_issues(parsed, [page('/a'), page('/b')], BASE, resolver=resolver)
    result('bots: traffic falsely claiming to be a crawler is reported',
           'bot_spoofing' in slugs(issues), str(slugs(issues)))

    honest = la.parse_lines([combined('/a', ip=real_ip)])
    result('bots: a log with only real crawler traffic raises no spoof finding',
           'bot_spoofing' not in slugs(
               la.log_issues(honest, [page('/a')], BASE, resolver=resolver)), 'ok')


def test_bot_names():
    for agent, expected in (
        (GOOGLEBOT, 'Googlebot'),
        ('Mozilla/5.0 (compatible; bingbot/2.0)', 'Bingbot'),
        ('Mozilla/5.0 GPTBot/1.1', 'GPTBot'),
        ('Mozilla/5.0 (compatible; ClaudeBot/1.0)', 'ClaudeBot'),
        ('PerplexityBot/1.0', 'PerplexityBot'),
        (CHROME, ''),
    ):
        result(f'bots: {expected or "a browser"} is identified correctly',
               la.bot_name(agent) == expected, f'{la.bot_name(agent)!r}')


# ---------------------------------------------------------------------------
# joining to the crawl
# ---------------------------------------------------------------------------

def test_what_the_logs_know_that_the_crawl_does_not():
    resolver = fake_dns(reverse_map={'66.249.66.1': 'x.googlebot.com'},
                        forward_map={'x.googlebot.com': ['66.249.66.1']})

    # A page Google asks for that nothing on the site links to.
    parsed = la.parse_lines([combined('/a'), combined('/old-campaign-page')] * 2)
    issues = la.log_issues(parsed, [page('/a')], BASE, resolver=resolver)
    result('join: a page crawlers request but the crawl never found is reported',
           'requested_but_not_crawled' in slugs(issues), str(slugs(issues)))
    found = [i for i in issues if i['check'] == 'requested_but_not_crawled'][0]
    result('join: the URL is named so it can be looked at',
           any('/old-campaign-page' in u for u in found['evidence']['related_urls']),
           str(found['evidence']['related_urls']))

    # A page we found that no crawler has ever asked for.
    quiet = la.log_issues(la.parse_lines([combined('/a')] * 3),
                          [page('/a'), page('/forgotten')], BASE, resolver=resolver)
    result('join: a page no crawler has requested is reported',
           'never_requested_by_search' in slugs(quiet), str(slugs(quiet)))

    # Budget spent on things that never return a 200.
    waste = la.parse_lines([combined('/gone', 404)] * 12 + [combined('/a')] * 3)
    spent = la.log_issues(waste, [page('/a')], BASE, resolver=resolver)
    result('join: budget spent on errors is reported',
           'crawl_budget_wasted' in slugs(spent), str(slugs(spent)))
    result('join: a URL hit only once or twice is not called waste',
           'crawl_budget_wasted' not in slugs(la.log_issues(
               la.parse_lines([combined('/gone', 404)] * 2 + [combined('/a')]),
               [page('/a')], BASE, resolver=resolver)), 'ok')

    # The quiet half: logs and crawl agreeing produces nothing.
    agreed = la.log_issues(la.parse_lines([combined('/a')] * 5),
                           [page('/a')], BASE, resolver=resolver)
    result('join: a log that agrees with the crawl is silent', agreed == [], str(slugs(agreed)))


def test_summary_and_shape():
    resolver = fake_dns(reverse_map={'66.249.66.1': 'x.googlebot.com'},
                        forward_map={'x.googlebot.com': ['66.249.66.1']})
    parsed = la.parse_lines([combined('/a'), combined('/b', 404),
                             combined('/c', agent=CHROME, ip='1.2.3.4')])
    got = la.summary(parsed, [page('/a')], resolver=resolver)
    result('summary: requests are counted', got['requests'] == 3, str(got['requests']))
    result('summary: traffic is split by crawler',
           got['by_bot'].get('Googlebot') == 2 and got['by_bot'].get('Other') == 1,
           str(got['by_bot']))
    result('summary: statuses are counted',
           got['by_status'] == {200: 2, 404: 1}, str(got['by_status']))

    issues = la.log_issues(la.parse_lines([combined('/gone', 404)] * 12), [page('/a')],
                           BASE, resolver=resolver)
    result('shape: severities are real', all(i['type'] in SEVERITIES for i in issues), 'ok')
    result('shape: only known evidence fields are used',
           all(set(i['evidence']) <= set(EVIDENCE_FIELDS) for i in issues),
           str([set(i['evidence']) for i in issues]))
    result('shape: every issue carries a fix', all(i['how_to_fix'] for i in issues), 'ok')

    for junk in (None, {}, {'entries': None}, {'entries': [{}]}):
        try:
            la.log_issues(junk, None, BASE, {'verify_bots': False})
        except Exception as exc:
            result('junk: odd input does not raise', False, f'{type(exc).__name__}: {exc}')
            return
    result('junk: odd input does not raise', True)



def test_the_route_that_makes_any_of_this_reachable():
    """Until this route existed, nothing called the log analyser at all.

    Four log formats, reverse-then-forward DNS bot verification, thirty-six
    passing tests — and `src/core/log_analysis` was imported by its own test
    file and by nothing else. No route, no crawl hook, no UI. A capability
    the project claimed and could not reach.
    """
    import io as _io
    from flask import Flask
    from src.api.logs import create_blueprint, MAX_BYTES

    pages = [{'url': 'https://site.test/a', 'status_code': 200},
             {'url': 'https://site.test/never-requested', 'status_code': 200}]
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(lambda fn: fn, lambda crawl_id: pages))
    client = app.test_client()

    log = (b'66.249.66.1 - - [18/Oct/2026:10:00:00 +0000] "GET /a HTTP/1.1" '
           b'200 120 "-" "Mozilla/5.0 (compatible; Googlebot/2.1; '
           b'+http://www.google.com/bot.html)"\n'
           b'66.249.66.1 - - [18/Oct/2026:10:00:02 +0000] "GET /gone HTTP/1.1" '
           b'404 10 "-" "Mozilla/5.0 (compatible; Googlebot/2.1; '
           b'+http://www.google.com/bot.html)"\n')

    response = client.post('/api/logs/analyse', data={
        'log': (_io.BytesIO(log), 'access.log'), 'crawl_id': '1'},
        content_type='multipart/form-data')
    body = response.get_json()
    result('a log can be uploaded and analysed', response.status_code == 200
           and body.get('success'), str(response.status_code))
    result('every line was understood', body.get('requests_understood') == 2
           and body.get('lines_unparsed') == 0, str(body))
    result('it was compared against the crawl', body.get('pages_compared') == 2)
    result('and it produced findings', bool(body.get('issues')),
           str(len(body.get('issues') or [])))

    slugs = {issue.get('check') for issue in body.get('issues') or []}
    result('a page search engines never asked for is reported',
           'never_requested_by_search' in slugs, str(sorted(slugs)))

    # the refusals
    empty = client.post('/api/logs/analyse',
                        data={'log': (_io.BytesIO(b''), 'empty.log')},
                        content_type='multipart/form-data')
    result('an empty file is refused with a reason', empty.status_code == 400
           and 'empty' in (empty.get_json().get('error') or '').lower())

    missing = client.post('/api/logs/analyse', data={},
                          content_type='multipart/form-data')
    result('no file at all is refused', missing.status_code == 400)

    huge = client.post('/api/logs/analyse',
                       data={'log': (_io.BytesIO(b'x' * (MAX_BYTES + 10)), 'big.log')},
                       content_type='multipart/form-data')
    result('an oversized log is refused rather than swallowed',
           huge.status_code == 413, str(huge.status_code))

    # the formats endpoint exists so an upload form can describe itself
    formats = client.get('/api/logs/formats').get_json()
    result('the readable formats are published',
           len(formats.get('formats') or []) >= 3, str(formats)[:120])

    # a log with no crawl still parses; it simply has nothing to compare to
    alone = client.post('/api/logs/analyse',
                        data={'log': (_io.BytesIO(log), 'access.log')},
                        content_type='multipart/form-data').get_json()
    result('a log with no crawl is still read',
           alone.get('requests_understood') == 2 and alone.get('pages_compared') == 0,
           str(alone)[:120])



def test_findings_can_be_kept_on_the_crawl_but_only_when_asked():
    """A log says things no crawl can, and they belong beside the crawl's
    own issues rather than in a response someone has to keep open.

    Writing to a crawl is not something a read-only look should do by
    itself, though, so keeping is opt-in and off by default.
    """
    import io as _io
    from flask import Flask
    from src.api.logs import create_blueprint

    pages = [{'url': 'https://site.test/a', 'status_code': 200},
             {'url': 'https://site.test/never-requested', 'status_code': 200}]
    saved = []
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(
        lambda fn: fn, lambda crawl_id: pages,
        lambda crawl_id, issues: saved.append((crawl_id, len(issues)))))
    client = app.test_client()

    log = (b'66.249.66.1 - - [18/Oct/2026:10:00:00 +0000] "GET /a HTTP/1.1" '
           b'200 120 "-" "Googlebot/2.1"\n')

    plain = client.post('/api/logs/analyse', data={
        'log': (_io.BytesIO(log), 'access.log'), 'crawl_id': '7'},
        content_type='multipart/form-data').get_json()
    result('by default the findings are returned and not kept',
           plain.get('kept') is False and not saved, str(saved))
    result('and they are still in the response to read',
           bool(plain.get('issues')))

    kept = client.post('/api/logs/analyse', data={
        'log': (_io.BytesIO(log), 'access.log'), 'crawl_id': '7', 'keep': 'true'},
        content_type='multipart/form-data').get_json()
    result('asked to keep them, it does', kept.get('kept') is True)
    result('writing them against the crawl they were compared to',
           saved and saved[0][0] == 7, str(saved))

    # a store that fails must not report success
    broken = Flask(__name__)
    def explode(crawl_id, issues):
        raise RuntimeError('disk full')
    broken.register_blueprint(create_blueprint(
        lambda fn: fn, lambda crawl_id: pages, explode))
    response = broken.test_client().post('/api/logs/analyse', data={
        'log': (_io.BytesIO(log), 'access.log'), 'crawl_id': '7', 'keep': 'true'},
        content_type='multipart/form-data')
    body = response.get_json()
    result('a failed save is an error, not a quiet success',
           response.status_code == 500 and body.get('success') is False,
           str(response.status_code))
    result('and the findings still come back, so the work is not lost',
           bool(body.get('issues')))

    # an install with no store configured still answers
    nostore = Flask(__name__)
    nostore.register_blueprint(create_blueprint(
        lambda fn: fn, lambda crawl_id: pages))
    answer = nostore.test_client().post('/api/logs/analyse', data={
        'log': (_io.BytesIO(log), 'access.log'), 'crawl_id': '7', 'keep': 'true'},
        content_type='multipart/form-data').get_json()
    result('with nowhere to keep them it still reports what it found',
           answer.get('success') and answer.get('kept') is False)


TESTS = (
    test_findings_can_be_kept_on_the_crawl_but_only_when_asked,
    test_the_route_that_makes_any_of_this_reachable,
    test_the_three_formats,
    test_unreadable_lines_are_counted_not_guessed,
    test_a_bot_is_verified_not_believed,
    test_spoofing_is_reported,
    test_bot_names,
    test_what_the_logs_know_that_the_crawl_does_not,
    test_summary_and_shape,
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
