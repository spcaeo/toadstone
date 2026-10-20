#!/usr/bin/env python3
"""Core Web Vitals: the thresholds, the bands, and when to say nothing.

The data these read was already being fetched and stored; nothing judged it,
so a four-second Largest Contentful Paint produced no finding at all. These
tests pin both halves: that a slow page is reported, and that a fast one - or
one nobody has measured - is not.

No framework, no network: the field-data tests inject a fake opener.
"""

import io
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import pagespeed_checks as ps  # noqa: E402
from src.utils.issue_factory import EVIDENCE_FIELDS, SEVERITIES  # noqa: E402

PASSED, FAILED = [], []
URL = 'https://site.test/a'


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def entry(url=URL, mobile=None, desktop=None):
    return {'url': url, 'mobile': mobile or {}, 'desktop': desktop or {}}


def lab(score=95, **metrics):
    return {'performance_score': score, 'metrics': metrics}


def slugs(issues):
    return sorted(i['check'] for i in issues)


def test_good_vitals_say_nothing():
    """The half that matters: a fast page must produce no findings."""
    fast = [entry(mobile=lab(95, largest_contentful_paint=2.0,
                             cumulative_layout_shift=0.05, first_input_delay=120.0,
                             first_contentful_paint=1.2),
                  desktop=lab(98))]
    result('quiet: a page inside every threshold reports nothing',
           ps.vitals_issues(fast) == [], str(slugs(ps.vitals_issues(fast))))

    result('quiet: exactly at the good boundary is still good',
           ps.vitals_issues([entry(mobile=lab(90, largest_contentful_paint=2.5,
                                              cumulative_layout_shift=0.1,
                                              first_input_delay=200.0))]) == [], 'ok')

    for empty in ([], None, [{}], [{'url': URL}], [{'url': URL, 'mobile': 'nonsense'}]):
        result(f'quiet: {str(empty)[:24]} produces nothing',
               ps.vitals_issues(empty) == [], str(ps.vitals_issues(empty)))


def test_core_vitals_bands():
    poor = ps.vitals_issues([entry(mobile=lab(40, largest_contentful_paint=5.2,
                                              cumulative_layout_shift=0.4,
                                              first_input_delay=700.0))])
    result('bands: a poor LCP, CLS and INP each report',
           {'lcp_poor', 'cls_poor', 'inp_poor'} <= set(slugs(poor)), str(slugs(poor)))
    result('bands: a poor core vital is an error',
           all(i['type'] == 'error' for i in poor if i['check'].endswith('_poor')
               and not i['check'].startswith('performance')),
           str([(i['check'], i['type']) for i in poor]))

    middling = ps.vitals_issues([entry(mobile=lab(80, largest_contentful_paint=3.2,
                                                  cumulative_layout_shift=0.18,
                                                  first_input_delay=300.0))])
    result('bands: the middle band is a warning, not an error',
           all(i['type'] == 'warning' for i in middling), str([(i['check'], i['type']) for i in middling]))
    result('bands: the middle band has its own slug',
           'lcp_needs_improvement' in slugs(middling), str(slugs(middling)))


def test_diagnostics_only_speak_up_when_bad():
    """FCP and friends explain a bad vital; they are not vitals themselves."""
    middling = ps.vitals_issues([entry(mobile=lab(90, first_contentful_paint=2.4,
                                                  speed_index=4.0))])
    result('diagnostics: a middling diagnostic stays quiet',
           middling == [], str(slugs(middling)))

    bad = ps.vitals_issues([entry(mobile=lab(90, first_contentful_paint=4.5))])
    result('diagnostics: a genuinely bad diagnostic reports',
           'first_contentful_paint_poor' in slugs(bad), str(slugs(bad)))
    result('diagnostics: but never as an error',
           all(i['type'] != 'error' for i in bad), str([(i['check'], i['type']) for i in bad]))


def test_score_and_divergence():
    low = ps.vitals_issues([entry(mobile=lab(35), desktop=lab(40))])
    result('score: a low Lighthouse score is reported',
           'performance_score_low' in slugs(low), str(slugs(low)))
    result('score: a healthy score is not',
           'performance_score_low' not in slugs(ps.vitals_issues([entry(mobile=lab(85))])), 'ok')

    split = ps.vitals_issues([entry(mobile=lab(45), desktop=lab(95))])
    result('divergence: fine on desktop and poor on mobile is reported',
           'mobile_desktop_divergence' in slugs(split), str(slugs(split)))
    close = ps.vitals_issues([entry(mobile=lab(80), desktop=lab(92))])
    result('divergence: a small gap is normal and stays quiet',
           'mobile_desktop_divergence' not in slugs(close), str(slugs(close)))


def test_issue_shape():
    issues = ps.vitals_issues([entry(mobile=lab(30, largest_contentful_paint=6.0))])
    found = [i for i in issues if i['check'] == 'lcp_poor'][0]
    result('shape: the measured value and the threshold are both stated',
           '6.00s' in found['details'] and '2.50s' in found['details'], found['details'])
    result('shape: evidence carries found and expected',
           found['evidence'].get('found') == '6.00s'
           and '2.50s' in found['evidence'].get('expected', ''), str(found['evidence']))
    result('shape: only known evidence fields are used',
           all(set(i['evidence']) <= set(EVIDENCE_FIELDS) for i in issues),
           str([set(i['evidence']) for i in issues]))
    result('shape: every severity is a real one',
           all(i['type'] in SEVERITIES for i in issues), str({i['type'] for i in issues}))
    result('shape: the fix names what to actually do',
           'lazy' in found['how_to_fix'] or 'preload' in found['how_to_fix'], found['how_to_fix'])
    result('shape: two runs produce the same id',
           ps.vitals_issues([entry(mobile=lab(30, largest_contentful_paint=6.0))])[0]['id']
           == found['id'], 'stable')


# ---------------------------------------------------------------------------
# field data
# ---------------------------------------------------------------------------

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def opener_for(payload):
    def send(request, timeout=None):
        return FakeResponse(json.dumps(payload).encode('utf-8'))
    return send


def failing_opener(code):
    def send(request, timeout=None):
        raise urllib.error.HTTPError(ps.CRUX_ENDPOINT, code, 'no', {}, None)
    return send


def test_field_data_fetch():
    payload = {'record': {'metrics': {
        'largest_contentful_paint': {'percentiles': {'p75': 4800}},
        'cumulative_layout_shift': {'percentiles': {'p75': 0.02}},
    }}}
    got = ps.fetch_crux(URL, 'a-key', opener=opener_for(payload))
    result('field: the 75th percentile is what comes back',
           got['metrics']['largest_contentful_paint'] == 4800, str(got.get('metrics')))

    result('field: no API key is reported as a reason, not a crash',
           'error' in ps.fetch_crux(URL, '', opener=opener_for(payload)),
           str(ps.fetch_crux(URL, '')))

    thin = ps.fetch_crux(URL, 'a-key', opener=failing_opener(404))
    result('field: too little traffic is explained, not called a failure',
           'not enough real-user data' in thin.get('error', ''), str(thin))

    broken = ps.fetch_crux(URL, 'a-key', opener=failing_opener(500))
    result('field: a server error is reported as one', 'HTTP 500' in broken.get('error', ''),
           str(broken))


def test_field_issues():
    slow = ps.field_issues(URL, {'metrics': {'largest_contentful_paint': 4800,
                                             'cumulative_layout_shift': 0.02}})
    result('field: a poor real-user LCP is an error',
           slugs(slow) == ['field_lcp_poor'] and slow[0]['type'] == 'error', str(slugs(slow)))
    result('field: the finding says these are real users',
           'Real Chrome users' in slow[0]['details'], slow[0]['details'])

    result('field: good real-user numbers say nothing',
           ps.field_issues(URL, {'metrics': {'largest_contentful_paint': 1800,
                                             'cumulative_layout_shift': 0.01}}) == [], 'ok')

    # A page nobody has measured is not a fast page and not a slow one.
    result('field: no data is not a finding about the page',
           ps.field_issues(URL, {'error': 'not enough real-user data for this URL'}) == [],
           'ok')
    result('field: junk input does not raise',
           ps.field_issues(URL, None) == [] and ps.field_issues(URL, {'metrics': {
               'largest_contentful_paint': 'fast'}}) == [], 'ok')


def test_lab_and_field_are_distinguishable():
    """A reader must be able to tell a simulation from a measurement."""
    lab_slugs = set(slugs(ps.vitals_issues([entry(mobile=lab(30, largest_contentful_paint=6.0))])))
    field_slugs = set(slugs(ps.field_issues(URL, {'metrics': {'largest_contentful_paint': 6000}})))
    result('sources: field slugs are prefixed so they never merge with lab ones',
           not (lab_slugs & field_slugs) and all(s.startswith('field_') for s in field_slugs),
           f'{sorted(lab_slugs)} vs {sorted(field_slugs)}')



def test_a_run_where_pagespeed_answered_nothing_says_so():
    """A quota-exhausted run used to read as a clean bill of performance.

    PageSpeed without an API key shares an anonymous quota that is routinely
    exhausted — a real call made while writing this returned 429, "Quota
    exceeded for quota metric 'Queries'". The retry and backoff handled that
    correctly and returned success: False, and then vitals_issues produced
    nothing at all. No metrics, no issues, no notice: exactly what a site
    with excellent Core Web Vitals looks like.

    Every other integration here says when it could not run. This one is the
    worst candidate for staying quiet, because performance is the thing
    someone switches it on to find out about.
    """
    failed = [{'url': 'https://site.test/',
               'mobile': {'success': False, 'error': 'API returned status 429',
                          'strategy': 'mobile'},
               'desktop': {'success': False, 'error': 'API returned status 429',
                           'strategy': 'desktop'}}]
    found = ps.vitals_issues(failed, {})
    result('a wholly failed run produces a finding, not silence',
           [i['check'] for i in found] == ['pagespeed_unavailable'],
           str([i['check'] for i in found]))
    result('and it repeats what the API said',
           '429' in found[0]['details'], found[0]['details'][:90])
    result('and says no judgement was made either way',
           'either way' in found[0]['details'])
    result('as info, because it is about us and not about the site',
           found[0]['type'] == 'info', found[0]['type'])

    # a good run is unaffected
    good = [{'url': 'https://site.test/',
             'mobile': {'success': True, 'performance_score': 31,
                        'metrics': {'largest_contentful_paint': 6.2,
                                    'cumulative_layout_shift': 0.31,
                                    'first_input_delay': 620,
                                    'first_contentful_paint': 4.1},
                        'strategy': 'mobile'},
             'desktop': {'success': False, 'error': 'x', 'strategy': 'desktop'}}]
    slugs = {i['check'] for i in ps.vitals_issues(good, {})}
    result('a run that did answer still reports its metrics',
           'lcp_poor' in slugs and 'pagespeed_unavailable' not in slugs,
           str(sorted(slugs)))

    # one page answering is enough to judge that page
    mixed = good + failed
    slugs = {i['check'] for i in ps.vitals_issues(mixed, {})}
    result('one page answering is not treated as a failed run',
           'pagespeed_unavailable' not in slugs, str(sorted(slugs)))

    result('and asking about nothing reports nothing',
           ps.vitals_issues([], {}) == [])


TESTS = (
    test_a_run_where_pagespeed_answered_nothing_says_so,
    test_good_vitals_say_nothing,
    test_core_vitals_bands,
    test_diagnostics_only_speak_up_when_bad,
    test_score_and_divergence,
    test_issue_shape,
    test_field_data_fetch,
    test_field_issues,
    test_lab_and_field_are_distinguishable,
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
