#!/usr/bin/env python3
"""The per-page detector's error paths, which nothing else covered.

Nine of this module's checks had no test anywhere. They are the ones a live
crawl of a healthy site never triggers - a domain that does not resolve, a
request that times out, an image that 404s - so they sat unverified while the
checks around them were exercised daily.

An untested check is not neutral. Every defect found in this crawler so far
was in a check that *had* run; the ones that had not were simply unmeasured.
So each check here gets two tests: one proving it fires, and one proving it
stays quiet on a healthy page.

No framework and no dependencies, like the other suites here.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.issue_detector import IssueDetector, LEGACY_CHECK_SLUGS, as_standard_issue

PASSED, FAILED = [], []
URL = 'https://site.test/page'


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def page(**overrides):
    """A crawl result with everything a healthy HTML page carries."""
    base = {
        'url': URL,
        'status_code': 200,
        'content_type': 'text/html',
        'error': None,
        'error_type': None,
        'size': 50 * 1024,
        'title': 'A title that is a sensible length for a page',
        'meta_description': ('A meta description of a sensible length, long enough '
                             'to avoid the short warning and short enough to avoid '
                             'the long one entirely.'),
        'h1': 'A single heading',
        'word_count': 800,
        'images': [],
        'broken_images': [],
        'canonical_url': URL,
        'lang': 'en',
        'viewport': 'width=device-width, initial-scale=1',
        'meta_tags': {'viewport': 'width=device-width, initial-scale=1'},
        'og_tags': {'og:title': 'A title'},
        'twitter_tags': {'twitter:card': 'summary'},
        'json_ld': [{'@context': 'https://schema.org', '@type': 'WebPage'}],
        'response_time': 0.2,
        'robots': '',
    }
    base.update(overrides)
    return base


def run(result_dict):
    """Slugs the detector raises for one crawl result."""
    detector = IssueDetector()
    detector.detect_issues(result_dict)
    return sorted(issue['check'] for issue
                  in (as_standard_issue(i) for i in detector.detected_issues))


def test_network_errors_each_have_their_own_check():
    """Five ways a fetch fails, each named rather than lumped together."""
    cases = [
        ('dns_not_found', 'dns_not_found'),
        ('connection_refused', 'connection_refused'),
        ('timeout', 'request_timeout'),
        ('ssl_error', 'ssl_tls_error'),
        ('connection_error', 'connection_error'),
    ]
    for error_type, slug in cases:
        found = run(page(status_code=0, error_type=error_type,
                         error='the fetch failed', content_type=''))
        result(f'network: {error_type} is reported as {slug}', slug in found, str(found))

    # An error the map does not know still gets reported, not swallowed.
    unknown = run(page(status_code=0, error_type='something_new',
                       error='unexpected', content_type=''))
    result('network: an unrecognised failure still reports no_response',
           'no_response' in unknown, str(unknown))

    # A size refusal is deliberate, not a network failure.
    too_big = run(page(status_code=0, error_type='file_too_large', content_type=''))
    result('network: a file we chose not to download is not a failure',
           not any(slug in too_big for _, slug in cases) and 'no_response' not in too_big,
           str(too_big))

    result('network: a page that answered reports no network error',
           not any(slug in run(page()) for _, slug in cases), str(run(page())))


def test_page_size_thresholds():
    """Two bands, and the boundary between them."""
    large = run(page(size=4 * 1024 * 1024))
    result('size: over 3MB is an error', 'large_page_size' in large, str(large))
    result('size: over 3MB is not also reported as moderate',
           'moderate_page_size' not in large, str(large))

    moderate = run(page(size=2 * 1024 * 1024))
    result('size: over 1MB is a warning', 'moderate_page_size' in moderate, str(moderate))
    result('size: over 1MB is not reported as large',
           'large_page_size' not in moderate, str(moderate))

    small = run(page(size=200 * 1024))
    result('size: an ordinary page is quiet',
           'moderate_page_size' not in small and 'large_page_size' not in small, str(small))


def test_broken_images():
    """An image that does not answer, and one that answers with a 404."""
    dead = run(page(broken_images=[{'url': '/gone.png', 'status': 0}]))
    result('images: an image that never answers is reported',
           'broken_image' in dead, str(dead))

    missing = run(page(broken_images=[{'url': '/gone.png', 'status': 404}]))
    result('images: a 404 image carries its status in the slug',
           'broken_image_404' in missing, str(missing))

    server = run(page(broken_images=[{'url': '/boom.png', 'status': 503}]))
    result('images: a 5xx image carries its status too',
           'broken_image_503' in server, str(server))

    fine = run(page(broken_images=[]))
    result('images: a page with working images is quiet',
           not any(c.startswith('broken_image') for c in fine), str(fine))


def test_tracking_parameters_on_internal_links():
    """Campaign parameters belong on inbound links, not internal ones."""
    detector = IssueDetector()
    detector.detect_tracking_link_issues([
        {'source_url': f'{URL}/a', 'target_url': f'{URL}/b?utm_source=newsletter',
         'is_internal': True},
        {'source_url': f'{URL}/c', 'target_url': f'{URL}/b?utm_source=newsletter',
         'is_internal': True},
    ])
    found = [as_standard_issue(i) for i in detector.detected_issues]
    result('tracking: internal links carrying utm are reported',
           any(i['check'] == 'tracking_parameters_on_internal_links' for i in found),
           str([i['check'] for i in found]))
    result('tracking: the whole crawl gets one issue, not one per link',
           len(found) == 1, str(len(found)))

    clean = IssueDetector()
    clean.detect_tracking_link_issues([
        {'source_url': f'{URL}/a', 'target_url': f'{URL}/b', 'is_internal': True},
    ])
    result('tracking: clean internal links are quiet',
           not clean.detected_issues, str(clean.detected_issues))

    external = IssueDetector()
    external.detect_tracking_link_issues([
        {'source_url': f'{URL}/a', 'target_url': 'https://other.test/x?utm_source=ads',
         'is_internal': False},
    ])
    result('tracking: an outbound campaign link is not our problem',
           not external.detected_issues, str(external.detected_issues))

    result('tracking: no links at all does not raise',
           IssueDetector().detect_tracking_link_issues(None) is None)


def test_non_html_is_never_page_audited():
    """A .webp is a file, not a page with a missing H1.

    With image crawling on, image URLs were being audited as web pages and
    producing missing-H1, missing-description and thin-content findings. The
    content type is the guard.
    """
    image = run(page(content_type='image/webp', title='', meta_description='',
                     h1='', word_count=3, json_ld=[], og_tags={}, twitter_tags={},
                     canonical_url='', lang='', meta_tags={}))
    result('content type: an image produces no page issues at all',
           image == [], str(image))

    pdf = run(page(content_type='application/pdf', title='', h1='', word_count=0))
    result('content type: a PDF produces no page issues either', pdf == [], str(pdf))

    html = run(page(content_type='text/html', title='', h1='', word_count=10,
                    meta_description=''))
    result('content type: a real HTML page is still audited',
           'missing_title' in html and 'missing_h1' in html, str(html))


def test_every_slug_the_detector_emits_is_mapped():
    """A label with no slug silently loses its machine-readable identity."""
    unmapped = [label for label in LEGACY_CHECK_SLUGS if not LEGACY_CHECK_SLUGS[label]]
    result('slugs: every known label maps to a slug', not unmapped, str(unmapped))

    issue = as_standard_issue({'url': URL, 'type': 'error', 'category': 'Technical',
                               'issue': 'DNS Not Found', 'details': 'nope'})
    result('slugs: a legacy dict becomes a standard issue with an id',
           issue.get('check') == 'dns_not_found' and issue.get('id'), str(issue)[:160])


def test_junk_input_does_not_raise():
    for junk in ({}, {'url': URL}, {'url': URL, 'status_code': None},
                 {'url': URL, 'size': None, 'content_type': 'text/html'}):
        try:
            IssueDetector().detect_issues(dict(junk))
        except Exception as exc:
            result('junk: odd results do not raise', False, f'{type(exc).__name__}: {exc}')
            return
    result('junk: odd results do not raise', True)



def test_a_page_that_never_responded_is_not_judged_on_its_content():
    """One unreachable URL used to become ten SEO issues.

    Found by crawling a real site that throttled the crawl: 168 of 220 pages
    came back with no response at all, and every content check fired on the
    emptiness - missing title, missing H1, missing canonical, thin content,
    no structured data. 720 of the 1,054 issues in that report were about
    content that had never been fetched, which buried the ones that were
    real. A report that is mostly fiction is worse than no report.
    """
    detector = IssueDetector()
    detector.detect_issues({
        'url': 'https://site.test/unreachable',
        'status_code': 0,
        'error_type': 'timeout',
        'error': 'Server did not respond',
        'title': '', 'meta_description': '', 'h1': [], 'word_count': 0,
        'content_type': None,
    })
    slugs = {issue.get('check') for issue in detector.detected_issues}

    content_checks = {'missing_title', 'missing_h1', 'missing_meta_description',
                      'missing_canonical', 'missing_viewport', 'missing_lang',
                      'thin_content', 'no_structured_data', 'missing_opengraph',
                      'missing_twitter_card'}
    leaked = slugs & content_checks
    result('a page with no response raises no content issues', not leaked,
           f'still raised: {sorted(leaked)}')
    result('but the failure itself is still reported', bool(slugs),
           'the page failed silently, which is worse')

    # and a page that did respond is still judged normally
    detector = IssueDetector()
    detector.detect_issues({
        'url': 'https://site.test/real', 'status_code': 200,
        'content_type': 'text/html', 'title': '', 'meta_description': '',
        'h1': [], 'word_count': 10,
    })
    fired = {issue.get('check') for issue in detector.detected_issues}
    result('a page that did respond is still checked',
           'missing_title' in fired, str(sorted(fired))[:160])



def test_the_checks_that_fire_on_real_sites_but_had_no_test():
    """Three checks fired across 48 real crawls and no test emitted them.

    Found by instrumenting make_issue and comparing the slugs the suite
    produces against the slugs in the crawl database. They are ordinary
    checks, which is the point: the untested ones were not exotic, they were
    the ones nobody thought to doubt.
    """
    def slugs_for(page):
        detector = IssueDetector()
        detector.detect_issues(page)
        return {issue.get('check') for issue in detector.detected_issues}

    base = {'status_code': 200, 'content_type': 'text/html', 'h1': ['H'],
            'title': 'A perfectly reasonable title for a page', 'word_count': 400}

    # canonical_different: a canonical pointing somewhere else entirely
    fired = slugs_for({**base, 'url': 'https://site.test/a',
                       'canonical_url': 'https://site.test/b'})
    result('a canonical pointing elsewhere is reported',
           'canonical_different' in fired, str(sorted(fired)))

    # and the alias case it must NOT report: same page, tracking stripped
    fired = slugs_for({**base, 'url': 'https://site.test/a?utm_source=x',
                       'canonical_url': 'https://site.test/a'})
    result('a canonical that only drops tracking parameters is not reported',
           'canonical_different' not in fired, str(sorted(fired)))

    fired = slugs_for({**base, 'url': 'https://site.test/a/',
                       'canonical_url': 'https://site.test/a'})
    result('nor is one differing only by a trailing slash',
           'canonical_different' not in fired, str(sorted(fired)))

    # meta_description_too_long: the boundary, not just a long string
    fired = slugs_for({**base, 'url': 'https://site.test/a',
                       'meta_description': 'x' * 161})
    result('a meta description over 160 characters is reported',
           'meta_description_too_long' in fired, str(sorted(fired)))
    fired = slugs_for({**base, 'url': 'https://site.test/a',
                       'meta_description': 'x' * 160})
    result('exactly 160 is not reported, so the boundary is not off by one',
           'meta_description_too_long' not in fired, str(sorted(fired)))

    # moderate vs slow: two bands, and they must not both fire
    moderate = slugs_for({**base, 'url': 'https://site.test/a',
                          'response_time': 1500})
    result('a response over 1s is reported as moderate',
           'moderate_response_time' in moderate, str(sorted(moderate)))
    result('and not also as slow',
           'slow_response_time' not in moderate, str(sorted(moderate)))

    slow = slugs_for({**base, 'url': 'https://site.test/a',
                      'response_time': 4000})
    result('a response over 3s is reported as slow',
           'slow_response_time' in slow, str(sorted(slow)))
    result('and not also as moderate',
           'moderate_response_time' not in slow, str(sorted(slow)))

    quick = slugs_for({**base, 'url': 'https://site.test/a',
                       'response_time': 200})
    result('a fast page is reported as neither',
           not ({'slow_response_time', 'moderate_response_time'} & quick),
           str(sorted(quick)))



def test_every_issue_the_detector_emits_carries_a_slug():
    """One check appended its raw dict straight to the list, skipping the
    standardisation every other legacy check goes through.

    It reached the UI with check=None: unfilterable, with no stable id to
    track it across crawls, linking to /checks#None. It was one unlabelled
    row in a real crawl report of a thousand, which is exactly how a thing
    like this survives. Found by building a fixture to make it fire and
    noticing it still did not show up by name.
    """
    detector = IssueDetector()
    detector.detect_tracking_link_issues([
        {'source_url': 'https://site.test/',
         'target_url': 'https://site.test/b/?utm_source=news&utm_medium=email',
         'is_internal': True},
        {'source_url': 'https://site.test/a/',
         'target_url': 'https://site.test/c/?gclid=abc', 'is_internal': True},
    ])
    issues = detector.detected_issues
    result('the tracking-link check fires when links carry parameters',
           len(issues) == 1, str(len(issues)))
    issue = issues[0]
    result('and the issue carries its slug',
           issue.get('check') == 'tracking_parameters_on_internal_links',
           repr(issue.get('check')))
    result('a stable id, so it can be tracked across crawls',
           bool(issue.get('id')), repr(issue.get('id')))
    result('and a docs link that is not /checks#None',
           issue.get('docs_url') == '/checks#tracking_parameters_on_internal_links',
           repr(issue.get('docs_url')))

    # the guard that would have caught this anywhere in the detector
    detector = IssueDetector()
    detector.detect_issues({'url': 'https://site.test/p', 'status_code': 200,
                            'content_type': 'text/html', 'title': '',
                            'h1': [], 'word_count': 5})
    detector.detect_tracking_link_issues([
        {'source_url': 'https://site.test/', 'is_internal': True,
         'target_url': 'https://site.test/b/?utm_source=x'}])
    unlabelled = [i for i in detector.detected_issues if not i.get('check')]
    result('no issue from any path reaches the UI without a slug',
           not unlabelled, f'{len(unlabelled)} unlabelled: '
                           f'{[i.get("issue") for i in unlabelled][:3]}')


def test_every_classified_error_has_a_label():
    """No fetch failure may fall through to the generic 'No Response'.

    `no_response` is the detector's fallback for an error_type its map does
    not know. That branch cannot currently execute, because the classifier
    only ever returns values the map covers - and the point of this test is
    to keep it that way. Add a sixth error_type to classify_fetch_error
    without adding a label and this fails, instead of the crawl quietly
    reporting "No HTTP response received" with nothing useful in it.

    The classifier's return values are read out of its source rather than
    listed here, so the test cannot drift away from the code it guards.
    """
    import ast
    import inspect
    from src import crawler as crawler_module

    source = inspect.getsource(crawler_module.classify_fetch_error)
    tree = ast.parse(source.lstrip())
    returned = {node.value.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Return)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)}
    result('classifier returns a known set', len(returned) >= 5, str(sorted(returned)))

    labelled = {'dns_not_found', 'connection_refused', 'timeout',
                'ssl_error', 'connection_error'}
    unlabelled = returned - labelled
    result('every classified error has its own label, so no_response stays '
           'unreachable', not unlabelled, f'unlabelled: {sorted(unlabelled)}')


TESTS = (
    test_every_issue_the_detector_emits_carries_a_slug,
    test_the_checks_that_fire_on_real_sites_but_had_no_test,
    test_a_page_that_never_responded_is_not_judged_on_its_content,
    test_network_errors_each_have_their_own_check,
    test_page_size_thresholds,
    test_broken_images,
    test_tracking_parameters_on_internal_links,
    test_non_html_is_never_page_audited,
    test_every_slug_the_detector_emits_is_mapped,
    test_junk_input_does_not_raise,
    test_every_classified_error_has_a_label,
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
