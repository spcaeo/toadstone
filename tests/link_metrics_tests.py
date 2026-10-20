#!/usr/bin/env python3
"""Third-party authority metrics: Moz, Majestic and Ahrefs.

Each vendor needs a paid subscription, so no test here makes a real call.
What *was* done against the real services, without a key, is confirm the
endpoints and the failure shapes: Moz answers a POST with 401, Ahrefs answers
401, and Majestic answers **HTTP 200 with the error in the body**. That last
one is in here verbatim, because trusting Majestic's status code meant a
wrong API key came back as "no metrics found" - an integration that could not
run, looking exactly like a clean result.

The findings matter more than the numbers. A Domain Authority on its own is
trivia; a page with twenty linking domains buried four clicks deep is a
morning's work with an obvious payoff. The tests are written around that.
"""

import io
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import link_metrics  # noqa: E402
from src.core.link_metrics import MetricsError  # noqa: E402

PASSED, FAILED = [], []


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def raises(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except MetricsError as exc:
        return str(exc)
    return None


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def opener_returning(*payloads):
    queue = list(payloads)
    seen = []

    def send(request, timeout=None):
        seen.append(request)
        payload = queue.pop(0) if len(queue) > 1 else queue[0]
        return FakeResponse(json.dumps(payload).encode('utf-8'))
    send.seen = seen
    return send


def failing_opener(code, body=b''):
    def send(request, timeout=None):
        raise urllib.error.HTTPError('https://x', code, 'no', {}, io.BytesIO(body))
    return send


# Captured verbatim from api.majestic.com with a deliberately wrong key.
REAL_MAJESTIC_REFUSAL = {
    'Code': 'FailedRequestViaAPI',
    'ErrorMessage': "API Key does not appear to be correct: API_KEY - 'not-a-key'",
    'FullError': '',
}


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

def test_nothing_runs_without_credentials():
    result('no provider is configured by default',
           link_metrics.configured({}) == [])
    result('and collect does nothing, quietly',
           link_metrics.collect([{'url': 'x'}], {}) ==
           {'metrics': {}, 'provider': '', 'error': ''})
    result('describe says why rather than staying silent',
           'paid subscription' in link_metrics.describe({}))


def test_each_provider_is_recognised_only_when_complete():
    result('Moz needs both halves of its credential',
           link_metrics.configured({'moz_access_id': 'a'}) == []
           and link_metrics.configured({'moz_secret_key': 'b'}) == [])
    result('and is recognised with both',
           link_metrics.configured({'moz_access_id': 'a', 'moz_secret_key': 'b'})
           == ['moz'])
    result('Majestic needs its key',
           link_metrics.configured({'majestic_api_key': 'k'}) == ['majestic'])
    result('Ahrefs needs its token',
           link_metrics.configured({'ahrefs_api_token': 't'}) == ['ahrefs'])
    result('whitespace is not a credential',
           link_metrics.configured({'majestic_api_key': '   '}) == [])


def test_describe_names_where_the_urls_go():
    text = link_metrics.describe({'majestic_api_key': 'k'})
    result('a configured provider is named before anything is sent',
           'majestic' in text and 'sent' in text, text)


# ---------------------------------------------------------------------------
# the vendors
# ---------------------------------------------------------------------------

def test_moz_results_are_mapped():
    opener = opener_returning({'results': [
        {'page': 'https://site.test/a', 'page_authority': 41,
         'domain_authority': 55, 'external_pages_to_page': 12,
         'root_domains_to_page': 7, 'spam_score': 2}]})
    found = link_metrics.moz_metrics(['https://site.test/a'], 'id', 'key', opener)
    row = found.get('https://site.test/a') or {}
    result('a Moz row is mapped onto its URL', bool(row), str(found))
    result('the vendor-specific names become shared ones',
           row.get('linking_domains') == 7 and row.get('external_links') == 12,
           str(row))
    result('and the provider is recorded', row.get('provider') == 'moz')


def test_moz_batches_rather_than_calling_per_url():
    """Vendors rate-limit per request, so this is the difference between one
    call and four hundred."""
    opener = opener_returning({'results': []})
    urls = [f'https://site.test/{n}' for n in range(link_metrics.MOZ_BATCH * 2 + 1)]
    link_metrics.moz_metrics(urls, 'id', 'key', opener)
    result('URLs are batched, not sent one at a time',
           len(opener.seen) == 3, f'{len(opener.seen)} requests for {len(urls)} URLs')


def test_majestic_results_are_mapped():
    opener = opener_returning({'Code': 'OK', 'DataTables': {'Results': {'Data': [
        {'Item': 'https://site.test/a', 'TrustFlow': 22, 'CitationFlow': 30,
         'ExtBackLinks': 140, 'RefDomains': 9}]}}})
    found = link_metrics.majestic_metrics(['https://site.test/a'], 'key', opener)
    row = found.get('https://site.test/a') or {}
    result('a Majestic row is mapped', row.get('trust_flow') == 22, str(row))
    result('its link counts use the shared names',
           row.get('linking_domains') == 9 and row.get('external_links') == 140)


def test_majestics_200_that_means_no(  ):
    """The real find: Majestic refuses with HTTP 200 and the reason in the body."""
    message = raises(link_metrics.majestic_metrics, ['https://site.test/a'],
                     'not-a-key', opener_returning(REAL_MAJESTIC_REFUSAL))
    result('a refusal served as HTTP 200 is still a refusal',
           message is not None, 'it returned empty metrics instead')
    result('and the message repeats what Majestic said',
           message is not None and 'API Key does not appear to be correct' in message,
           repr(message))
    result('and names the thing to fix',
           message is not None and 'Majestic API key' in message, repr(message))


def test_ahrefs_results_are_mapped():
    opener = opener_returning({'metrics': {
        'domain_rating': 61, 'url_rating': 30, 'backlinks': 400,
        'refdomains': 44, 'org_traffic': 1200}})
    found = link_metrics.ahrefs_metrics('https://site.test/a', 'token', opener)
    row = found.get('https://site.test/a') or {}
    result('an Ahrefs row is mapped', row.get('domain_rating') == 61, str(row))
    result('its link counts use the shared names',
           row.get('linking_domains') == 44 and row.get('external_links') == 400)
    result('an empty reply yields nothing rather than a row of Nones',
           link_metrics.ahrefs_metrics('u', 't', opener_returning({})) == {})


def test_vendor_errors_say_what_to_do():
    for code, expected in ((401, 'rejected these credentials'),
                           (403, 'rejected these credentials'),
                           (429, 'rate-limiting'),
                           (402, 'out of quota')):
        message = raises(link_metrics.moz_metrics, ['u'], 'i', 'k',
                         failing_opener(code))
        if message is None or expected not in message:
            result('each vendor error names its own fix', False,
                   f'{code}: {message!r}')
            return
    result('each vendor error names its own fix', True)

    message = raises(link_metrics.moz_metrics, ['u'], 'i', 'k', failing_opener(500))
    result('an unexpected status is still a sentence',
           message is not None and '500' in message, repr(message))


# ---------------------------------------------------------------------------
# collect
# ---------------------------------------------------------------------------

def test_collect_only_looks_up_pages_that_exist():
    opener = opener_returning({'results': []})
    link_metrics.collect(
        [{'url': 'https://site.test/ok', 'status_code': 200},
         {'url': 'https://site.test/gone', 'status_code': 404},
         {'url': 'https://site.test/dead', 'status_code': 0}],
        {'moz_access_id': 'a', 'moz_secret_key': 'b'}, opener)
    sent = json.loads(opener.seen[0].data.decode('utf-8'))['targets']
    result('a 404 is not looked up', 'https://site.test/gone' not in sent, str(sent))
    result('nor is a page that never responded',
           'https://site.test/dead' not in sent, str(sent))
    result('the live page is', sent == ['https://site.test/ok'], str(sent))


def test_collect_caps_how_much_it_spends():
    """Looking up 50,000 URLs costs real money nobody agreed to."""
    opener = opener_returning({'results': []})
    pages = [{'url': f'https://site.test/{n}', 'status_code': 200}
             for n in range(500)]
    link_metrics.collect(pages, {'moz_access_id': 'a', 'moz_secret_key': 'b',
                                 'link_metrics_max_urls': 20}, opener)
    sent = json.loads(opener.seen[0].data.decode('utf-8'))['targets']
    result('the configured cap is honoured', len(sent) == 20, str(len(sent)))

    opener = opener_returning({'results': []})
    link_metrics.collect(pages, {'moz_access_id': 'a', 'moz_secret_key': 'b'}, opener)
    total = sum(len(json.loads(r.data.decode('utf-8'))['targets'])
                for r in opener.seen)
    result('and there is a default cap, not "all of them"',
           total == link_metrics.DEFAULT_MAX_URLS, str(total))


def test_a_failure_is_reported_not_swallowed():
    outcome = link_metrics.collect(
        [{'url': 'https://site.test/a', 'status_code': 200}],
        {'moz_access_id': 'a', 'moz_secret_key': 'b'}, failing_opener(401))
    result('a provider failure comes back as an error',
           outcome['error'] and not outcome['metrics'], str(outcome))

    issues = link_metrics.metric_issues([], outcome)
    result('and becomes a visible finding, not silence',
           [i['check'] for i in issues] == ['link_metrics_unavailable'],
           str([i['check'] for i in issues]))
    result('which says no judgement was made either way',
           'either way' in issues[0]['details'], issues[0]['details'])


# ---------------------------------------------------------------------------
# the findings
# ---------------------------------------------------------------------------

def test_a_page_with_external_links_buried_deep_is_reported():
    """The finding that pays for the subscription."""
    pages = [{'url': 'https://site.test/guide', 'depth': 5, 'linked_from': []}]
    outcome = {'metrics': {'https://site.test/guide': {
        'provider': 'moz', 'linking_domains': 20, 'external_links': 90}},
        'provider': 'moz', 'error': ''}
    issues = link_metrics.metric_issues(pages, outcome)
    slugs = [i['check'] for i in issues]
    result('a buried page with real external links is reported',
           'authority_page_buried' in slugs, str(slugs))
    finding = next(i for i in issues if i['check'] == 'authority_page_buried')
    result('and the finding carries both numbers',
           '20' in finding['details'] and '5' in finding['details'],
           finding['details'][:120])


def test_a_shallow_page_with_links_is_not_reported():
    pages = [{'url': 'https://site.test/', 'depth': 0, 'linked_from': []}]
    outcome = {'metrics': {'https://site.test/': {'linking_domains': 20}},
               'provider': 'moz', 'error': ''}
    result('a well-linked page near the homepage is left alone',
           not link_metrics.metric_issues(pages, outcome))


def test_internally_loved_externally_ignored_is_only_an_observation():
    pages = [{'url': 'https://site.test/pricing', 'depth': 1,
              'linked_from': ['a'] * 25}]
    outcome = {'metrics': {'https://site.test/pricing': {
        'linking_domains': 0, 'external_links': 0}}, 'provider': 'moz', 'error': ''}
    issues = link_metrics.metric_issues(pages, outcome)
    finding = next((i for i in issues if i['check'] == 'no_external_interest'), None)
    result('the pattern is reported', finding is not None)
    result('as info, not a fault - a pricing page looks exactly like this',
           finding is not None and finding['type'] == 'info',
           finding and finding['type'])


def test_a_high_spam_score_is_attributed_to_the_vendor():
    pages = [{'url': 'https://site.test/a', 'depth': 1, 'linked_from': []}]
    outcome = {'metrics': {'https://site.test/a': {'spam_score': 55}},
               'provider': 'moz', 'error': ''}
    finding = next(i for i in link_metrics.metric_issues(pages, outcome)
                   if i['check'] == 'high_spam_score')
    result('a high spam score is reported', finding is not None)
    result('and attributed to the provider rather than to Google',
           'not a Google signal' in finding['details'], finding['details'][:140])


def test_metrics_for_pages_the_crawl_never_saw_are_ignored():
    outcome = {'metrics': {'https://other.test/x': {'linking_domains': 50}},
               'provider': 'moz', 'error': ''}
    result('a metric for an uncrawled URL raises nothing',
           link_metrics.metric_issues([{'url': 'https://site.test/a'}], outcome) == [])


def test_missing_numbers_do_not_raise():
    pages = [{'url': 'https://site.test/a', 'depth': None, 'linked_from': None}]
    outcome = {'metrics': {'https://site.test/a': {
        'linking_domains': None, 'external_links': 'n/a', 'spam_score': ''}},
        'provider': 'moz', 'error': ''}
    try:
        link_metrics.metric_issues(pages, outcome)
        result('absent or junk numbers do not raise', True)
    except Exception as exc:
        result('absent or junk numbers do not raise', False,
               f'{type(exc).__name__}: {exc}')


TESTS = (
    test_nothing_runs_without_credentials,
    test_each_provider_is_recognised_only_when_complete,
    test_describe_names_where_the_urls_go,
    test_moz_results_are_mapped,
    test_moz_batches_rather_than_calling_per_url,
    test_majestic_results_are_mapped,
    test_majestics_200_that_means_no,
    test_ahrefs_results_are_mapped,
    test_vendor_errors_say_what_to_do,
    test_collect_only_looks_up_pages_that_exist,
    test_collect_caps_how_much_it_spends,
    test_a_failure_is_reported_not_swallowed,
    test_a_page_with_external_links_buried_deep_is_reported,
    test_a_shallow_page_with_links_is_not_reported,
    test_internally_loved_externally_ignored_is_only_an_observation,
    test_a_high_spam_score_is_attributed_to_the_vendor,
    test_metrics_for_pages_the_crawl_never_saw_are_ignored,
    test_missing_numbers_do_not_raise,
)


def main():
    for test in TESTS:
        print(f'\n{test.__name__}:')
        try:
            test()
        except Exception as exc:
            result(test.__name__, False, f'raised {type(exc).__name__}: {exc}')
    print('\n' + '=' * 64)
    print(f'  {len(PASSED)} passed, {len(FAILED)} failed')
    print('  NOTE: endpoints and failure shapes were checked against the real')
    print('        services without a key. No successful paid call has been made.')
    if FAILED:
        print('  failed: ' + ', '.join(FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
