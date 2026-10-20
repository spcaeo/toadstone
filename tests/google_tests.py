#!/usr/bin/env python3
"""Search Console and GA4: the joins, the thresholds, and failing honestly.

No network and no credentials: every HTTP call goes through an injected
opener returning canned payloads shaped like Google's real ones.

That is also the limit of what these prove. The response shapes here are
written from Google's published API documentation, not captured from a live
account, so the mapping logic is verified and the wire format is not. Until
someone runs this against a real property, treat "works" as "works against
the documented shape".
"""

import io
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import analytics, google_oauth, search_console  # noqa: E402
from src.core.google_auth import Credentials, GoogleAuthError, consent_url  # noqa: E402
from src.utils.issue_factory import EVIDENCE_FIELDS, SEVERITIES  # noqa: E402

PASSED, FAILED = [], []
SITE = 'https://site.test'


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def opener_returning(*payloads):
    """Answers each successive call with the next payload, repeating the last."""
    queue = list(payloads)

    def send(request, timeout=None):
        payload = queue.pop(0) if len(queue) > 1 else queue[0]
        return FakeResponse(json.dumps(payload).encode('utf-8'))
    return send


def failing_opener(code, body=None):
    def send(request, timeout=None):
        raise urllib.error.HTTPError(
            'https://x', code, 'no', {},
            io.BytesIO(json.dumps(body or {}).encode('utf-8')))
    return send


def creds(opener=None):
    return Credentials('id', 'secret', 'refresh', opener=opener or
                       opener_returning({'access_token': 'tok', 'expires_in': 3600}))


def page(path, url=None):
    return {'url': url or f'{SITE}{path}', 'status_code': 200, 'content_type': 'text/html'}


def slugs(issues):
    return sorted(i['check'] for i in issues)


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

def test_credentials_and_token_caching():
    calls = []

    def counting(request, timeout=None):
        calls.append(1)
        return FakeResponse(json.dumps({'access_token': 'tok', 'expires_in': 3600})
                            .encode('utf-8'))

    c = Credentials('id', 'secret', 'refresh', opener=counting)
    result('auth: a configured credential reports itself ready', c.configured, 'ok')
    result('auth: the first call fetches a token', c.access_token(now=0) == 'tok', 'ok')
    c.access_token(now=10)
    c.access_token(now=20)
    result('auth: a cached token is reused rather than refetched',
           len(calls) == 1, f'{len(calls)} token requests')
    c.access_token(now=4000)
    result('auth: an expired token is refreshed', len(calls) == 2, f'{len(calls)} requests')


def test_incomplete_or_rejected_credentials():
    for missing in (('', 's', 'r'), ('i', '', 'r'), ('i', 's', '')):
        c = Credentials(*missing)
        result(f'auth: {missing} is not configured', not c.configured, 'ok')
        try:
            c.access_token()
            result('auth: an incomplete credential refuses to be used', False, 'it was used')
        except GoogleAuthError:
            result('auth: an incomplete credential refuses to be used', True)
        break

    revoked = Credentials('id', 'secret', 'refresh',
                          opener=failing_opener(400, {'error': 'invalid_grant'}))
    try:
        revoked.access_token()
        result('auth: a revoked refresh token is reported clearly', False, 'no error')
    except GoogleAuthError as exc:
        result('auth: a revoked refresh token says to re-authorise',
               're-authorise' in str(exc).lower() or 'rejected' in str(exc).lower(), str(exc))


def test_consent_url_asks_for_a_refresh_token():
    url = consent_url('client-id', 'http://localhost/callback', ['search_console'])
    result('auth: the consent URL asks for offline access',
           'access_type=offline' in url, url[:90])
    result('auth: and forces consent, which is what returns a refresh token',
           'prompt=consent' in url, url[:90])
    result('auth: the requested scope is read-only',
           'webmasters.readonly' in url, url[-60:])


# ---------------------------------------------------------------------------
# Search Console
# ---------------------------------------------------------------------------

def gsc_rows(rows):
    return {'rows': [{'keys': keys, 'clicks': c, 'impressions': i, 'ctr': ctr,
                      'position': p} for keys, c, i, ctr, p in rows]}


def test_search_console_performance_is_read():
    payload = gsc_rows([([f'{SITE}/a'], 10, 1000, 0.01, 8.2)])
    shared = opener_returning({'access_token': 'tok', 'expires_in': 3600}, payload)
    got = search_console.fetch_pages(creds(opener=shared), SITE, opener=shared)
    result('gsc: per-page performance is read',
           got[f'{SITE}/a']['impressions'] == 1000, str(got))


def test_search_console_findings():
    pages = [page('/a'), page('/quiet')]
    performance = {
        f'{SITE}/a': {'clicks': 2, 'impressions': 900, 'ctr': 0.0022, 'position': 12.0},
        f'{SITE}/unlinked': {'clicks': 50, 'impressions': 400, 'ctr': 0.125, 'position': 4.0},
    }
    found = search_console.issues(pages, performance, base_url=SITE)
    result('gsc: a page Google knows and the crawl does not is reported',
           'gsc_page_not_in_crawl' in slugs(found), str(slugs(found)))
    result('gsc: a crawled page with no impressions is reported',
           'gsc_no_impressions' in slugs(found), str(slugs(found)))
    result('gsc: shown often and clicked rarely is reported',
           'gsc_low_ctr' in slugs(found), str(slugs(found)))

    # The quiet halves.
    healthy = {f'{SITE}/a': {'clicks': 300, 'impressions': 1000, 'ctr': 0.3,
                             'position': 2.0}}
    quiet = search_console.issues([page('/a')], healthy, base_url=SITE)
    result('gsc: a page performing well reports nothing', quiet == [], str(slugs(quiet)))

    thin = {f'{SITE}/a': {'clicks': 0, 'impressions': 5, 'ctr': 0.0, 'position': 40.0}}
    result('gsc: a low CTR on five impressions is noise, not a finding',
           'gsc_low_ctr' not in slugs(search_console.issues([page('/a')], thin, base_url=SITE)),
           'ok')


def test_cannibalisation_needs_two_real_pages():
    queries = [
        {'query': 'link indexing', 'page': f'{SITE}/a', 'clicks': 5,
         'impressions': 200, 'position': 6.0},
        {'query': 'link indexing', 'page': f'{SITE}/b', 'clicks': 3,
         'impressions': 180, 'position': 9.0},
    ]
    found = search_console.issues([page('/a'), page('/b')], {}, queries=queries, base_url=SITE)
    result('gsc: two pages shown for one query is reported',
           'gsc_query_cannibalisation' in slugs(found), str(slugs(found)))

    one = [{'query': 'link indexing', 'page': f'{SITE}/a', 'clicks': 5,
            'impressions': 200, 'position': 6.0}]
    result('gsc: one page owning a query is correct, not a finding',
           'gsc_query_cannibalisation' not in slugs(
               search_console.issues([page('/a')], {}, queries=one, base_url=SITE)), 'ok')

    tiny = [dict(row, impressions=3) for row in queries]
    result('gsc: two pages sharing three impressions is not cannibalisation',
           'gsc_query_cannibalisation' not in slugs(
               search_console.issues([page('/a'), page('/b')], {}, queries=tiny,
                                     base_url=SITE)), 'ok')


def test_inspection_findings():
    inspections = {
        f'{SITE}/a': {'verdict': 'FAIL', 'coverage': 'Crawled - currently not indexed'},
        f'{SITE}/b': {'verdict': 'PASS', 'coverage': 'Submitted and indexed',
                      'canonical': f'{SITE}/c', 'user_canonical': f'{SITE}/b'},
    }
    found = search_console.issues([page('/a'), page('/b')], {f'{SITE}/a': {}, f'{SITE}/b': {}},
                                  inspections, base_url=SITE)
    result('gsc: a page Google has not indexed is an error',
           'gsc_not_indexed' in slugs(found), str(slugs(found)))
    result('gsc: the reason Google gave is carried through',
           any('currently not indexed' in i['details'] for i in found), 'ok')
    result('gsc: an indexed page is not reported as unindexed',
           len([i for i in found if i['check'] == 'gsc_not_indexed']) == 1, str(slugs(found)))
    result('gsc: Google overriding the canonical is reported',
           'gsc_canonical_overridden' in slugs(found), str(slugs(found)))


def test_a_google_failure_is_not_a_clean_bill_of_health():
    """The failure mode that matters: an integration that is down must not pass."""
    broken = search_console.collect(
        creds(opener=failing_opener(403)), SITE, [page('/a')],
        opener=failing_opener(403))
    result('gsc: a 403 returns an error rather than an empty pass',
           broken['error'] and broken['issues'] == [], str(broken['error']))
    result('gsc: and the error says what is actually wrong',
           'access' in broken['error'].lower() or '403' in broken['error'],
           broken['error'])


# ---------------------------------------------------------------------------
# GA4
# ---------------------------------------------------------------------------

def ga_rows(rows):
    return {'rows': [{'dimensionValues': [{'value': path}],
                      'metricValues': [{'value': str(s)}, {'value': str(e)},
                                       {'value': str(b)}, {'value': '30'}]}
                     for path, s, e, b in rows]}


def test_analytics_is_read_and_joined():
    payload = ga_rows([('/a', 500, 400, 0.2), ('/unlinked', 90, 70, 0.3)])
    shared = opener_returning({'access_token': 'tok', 'expires_in': 3600}, payload)
    traffic = analytics.fetch_pages(creds(opener=shared), '123456', opener=shared,
                                    origin=SITE)
    result('ga: sessions per page are read',
           traffic[f'{SITE}/a']['sessions'] == 500, str(traffic)[:100])

    found = analytics.issues([page('/a'), page('/quiet')], traffic, SITE)
    result('ga: a page with traffic the crawl did not find is reported',
           'ga_page_not_in_crawl' in slugs(found), str(slugs(found)))
    result('ga: a crawled page nobody visited is reported',
           'ga_no_sessions' in slugs(found), str(slugs(found)))


def test_bounce_thresholds():
    busy = {f'{SITE}/a': {'sessions': 500, 'engaged_sessions': 40, 'bounce_rate': 0.92,
                          'average_duration': 4.0}}
    result('ga: a high bounce on real traffic is reported',
           'ga_high_bounce' in slugs(analytics.issues([page('/a')], busy, SITE)), 'ok')

    quiet = {f'{SITE}/a': {'sessions': 4, 'engaged_sessions': 0, 'bounce_rate': 1.0,
                           'average_duration': 1.0}}
    result('ga: a high bounce on four sessions is noise, not a finding',
           'ga_high_bounce' not in slugs(analytics.issues([page('/a')], quiet, SITE)), 'ok')

    good = {f'{SITE}/a': {'sessions': 500, 'engaged_sessions': 450, 'bounce_rate': 0.2,
                          'average_duration': 95.0}}
    result('ga: a page people stay on reports nothing',
           analytics.issues([page('/a')], good, SITE) == [], 'ok')


def test_shape_and_resilience():
    issues = search_console.issues(
        [page('/a')], {f'{SITE}/a': {'clicks': 1, 'impressions': 900, 'ctr': 0.001,
                                     'position': 20.0}}, base_url=SITE)
    issues += analytics.issues([page('/a')], {f'{SITE}/a': {
        'sessions': 500, 'engaged_sessions': 10, 'bounce_rate': 0.95,
        'average_duration': 2.0}}, SITE)
    result('shape: severities are real', all(i['type'] in SEVERITIES for i in issues), 'ok')
    result('shape: only known evidence fields are used',
           all(set(i['evidence']) <= set(EVIDENCE_FIELDS) for i in issues),
           str([set(i['evidence']) for i in issues]))
    result('shape: every issue carries a fix', all(i['how_to_fix'] for i in issues), 'ok')

    for junk in (None, [], [{}], [{'url': None}]):
        try:
            search_console.issues(junk, None, None, None, SITE)
            analytics.issues(junk, None, SITE)
        except Exception as exc:
            result('junk: odd input does not raise', False, f'{type(exc).__name__}: {exc}')
            return
    result('junk: odd input does not raise', True)


def test_field_names_match_googles_published_schema():
    """The wire format, checked against Google rather than against memory.

    Credentials are needed to prove a live call works. They are not needed to
    prove the field names are right: Google publishes a machine-readable
    Discovery document for both APIs, and these are the names it gives. The
    list below was taken from:

      https://searchconsole.googleapis.com/$discovery/rest?version=v1
      https://analyticsdata.googleapis.com/$discovery/rest?version=v1beta

    Checking this found two real mistakes - the Search Console host was the
    legacy alias, and `verdict` was treated as a boolean when it is an enum
    of five values.
    """
    # ApiDataRow, from the Search Console Discovery document.
    row_fields = {'clicks', 'ctr', 'impressions', 'keys', 'position'}
    parsed = search_console.fetch_pages(
        creds(opener=(shared := opener_returning(
            {'access_token': 't', 'expires_in': 3600},
            gsc_rows([([f'{SITE}/a'], 1, 2, 0.5, 3.0)])))),
        SITE, opener=shared)
    read = set(parsed[f'{SITE}/a'])
    result('schema: every ApiDataRow field Google documents is read',
           read <= row_fields and read == {'clicks', 'impressions', 'ctr', 'position'},
           str(read))

    result('schema: the Search Console host is the documented one, not the alias',
           'searchconsole.googleapis.com' in search_console.SEARCH_ANALYTICS,
           search_console.SEARCH_ANALYTICS)
    result('schema: the URL Inspection path matches the Discovery document',
           search_console.INSPECT
           == 'https://searchconsole.googleapis.com/v1/urlInspection/index:inspect',
           search_console.INSPECT)
    result('schema: the GA4 runReport path matches the Discovery document',
           analytics.RUN_REPORT.startswith('https://analyticsdata.googleapis.com/v1beta/properties/')
           and analytics.RUN_REPORT.endswith(':runReport'), analytics.RUN_REPORT)

    # IndexStatusInspectionResult.verdict is an enum, not a pass/fail flag.
    for state, expected in (('FAIL', 'error'), ('PARTIAL', 'warning'),
                            ('NEUTRAL', 'warning')):
        found = search_console.issues(
            [page('/a')], {f'{SITE}/a': {}},
            {f'{SITE}/a': {'verdict': state, 'coverage': 'x'}}, base_url=SITE)
        hit = [i for i in found if i['check'] == 'gsc_not_indexed']
        result(f'schema: verdict {state} is reported at {expected}',
               hit and hit[0]['type'] == expected, str([(i['check'], i['type']) for i in found]))

    for quiet in ('PASS', 'VERDICT_UNSPECIFIED', ''):
        found = search_console.issues(
            [page('/a')], {f'{SITE}/a': {}},
            {f'{SITE}/a': {'verdict': quiet}}, base_url=SITE)
        result(f'schema: verdict {quiet or "(empty)"} reports nothing',
               not [i for i in found if i['check'] == 'gsc_not_indexed'],
               str([i['check'] for i in found]))


def test_credentials_from_a_gcloud_login():
    """Most people with gcloud already have somewhere to put these.

    Asking them to create a second OAuth client and paste three secrets into
    a form is more steps, more copies of a long-lived credential, and
    nothing to revoke cleanly afterwards.
    """
    import json
    import tempfile

    good = {'type': 'authorized_user', 'client_id': 'i', 'client_secret': 's',
            'refresh_token': 'r'}
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as handle:
        json.dump(good, handle)
        path = handle.name
    from src.core.google_auth import from_adc
    result('adc: an authorized_user file becomes credentials',
           (from_adc(path) or Credentials('', '', '')).configured, 'ok')

    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as handle:
        json.dump({'type': 'service_account', 'client_email': 'x'}, handle)
        sa = handle.name
    result('adc: a service account is refused, since it cannot reach Search Console',
           from_adc(sa) is None, 'ok')
    result('adc: a missing file is None rather than an error',
           from_adc('/nonexistent/adc.json') is None, 'ok')

    from src.core import google_auth
    explicit = google_auth.from_config(
        {'google_client_id': 'a', 'google_client_secret': 'b',
         'google_refresh_token': 'c'})
    result('adc: explicit settings are never overridden by whoever gcloud is',
           explicit.client_id == 'a', explicit.client_id)
    result('adc: and the fallback can be switched off',
           google_auth.from_config({'google_use_adc': False}) is None, 'ok')


def test_a_403_says_which_kind_of_403_it_is():
    """Scope and property access both arrive as 403 and need different fixes.

    Found by making a real call to Search Console with a token that carried
    cloud-platform but not webmasters.readonly: the message sent the reader
    to check property access, which was not the problem.
    """
    import io as _io
    import json
    from src.core.google_auth import call

    def refusing(reason):
        def send(request, timeout=None):
            raise urllib.error.HTTPError(
                'https://x', 403, 'no', {},
                _io.BytesIO(json.dumps({'error': {'message': 'denied', 'details': [
                    {'reason': reason}]}}).encode()))
        return send

    for reason, expect in (('ACCESS_TOKEN_SCOPE_INSUFFICIENT', 'scope'),
                           ('PERMISSION_DENIED', 'access to this property')):
        try:
            call(creds(), 'https://x', opener=refusing(reason))
            result(f'403: {reason} is explained', False, 'no error raised')
        except Exception as exc:
            result(f'403: {reason} points at the right fix', expect in str(exc), str(exc)[:110])



def test_a_disabled_api_is_named_by_google_not_guessed():
    """The message must name the API Google named.

    This said "enable the Search Console API and the Analytics Data API"
    for any SERVICE_DISABLED error. Against a real project that was wrong
    and expensively so: both of those were already on, and the one actually
    missing was the Analytics Admin API, which the message never mentioned.
    Google supplies the service title and an activation link in the error
    details; a guess sends somebody to a console screen that is fine.
    """
    error = {
        'code': 403,
        'status': 'PERMISSION_DENIED',
        'message': ('Google Analytics Admin API has not been used in project '
                    '731126092361 before or it is disabled.'),
        'details': [{
            '@type': 'type.googleapis.com/google.rpc.ErrorInfo',
            'reason': 'SERVICE_DISABLED',
            'domain': 'googleapis.com',
            'metadata': {
                'serviceTitle': 'Google Analytics Admin API',
                'activationUrl': 'https://console.developers.google.com/apis/'
                                 'api/analyticsadmin.googleapis.com/overview',
            },
        }],
    }
    said = google_oauth._explain_api(error, 403)
    result('names the API Google named', 'Analytics Admin API' in said, said)
    result('does not name an API Google did not',
           'Analytics Data API' not in said and 'Search Console API' not in said,
           said)
    result('gives the activation link Google gave',
           'analyticsadmin.googleapis.com' in said, said)

    # A SERVICE_DISABLED error with no usable details still has to say
    # something, and must not invent an API name to fill the gap.
    bare = google_oauth._explain_api(
        {'code': 403, 'status': 'PERMISSION_DENIED',
         'message': 'API has not been used in project 1 before or it is disabled.'},
        403)
    result('falls back without naming a specific API',
           'not switched on' in bare and 'Analytics Data API' not in bare, bare)

TESTS = (
    test_credentials_from_a_gcloud_login,
    test_a_403_says_which_kind_of_403_it_is,
    test_field_names_match_googles_published_schema,
    test_credentials_and_token_caching,
    test_incomplete_or_rejected_credentials,
    test_consent_url_asks_for_a_refresh_token,
    test_search_console_performance_is_read,
    test_search_console_findings,
    test_cannibalisation_needs_two_real_pages,
    test_inspection_findings,
    test_a_google_failure_is_not_a_clean_bill_of_health,
    test_analytics_is_read_and_joined,
    test_bounce_thresholds,
    test_shape_and_resilience,
    test_a_disabled_api_is_named_by_google_not_guessed,
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
    print('  NOTE: field names and endpoints are checked against Google\'s own')
    print('        Discovery documents. What no one has done yet is make a real')
    print('        call with real credentials against a real property.')
    if FAILED:
        print('  failed: ' + ', '.join(FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
