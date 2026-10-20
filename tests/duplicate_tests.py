"""Duplicate-content detection tests.

Runs the detector over page dictionaries built in memory - no crawl, no
network, no test framework:

    python tests/duplicate_tests.py

Each test pins down a property of the main-text duplicate pass:

  1. two pages with the same copy under different template chrome are
     duplicates (the chrome must not hide a real duplicate)
  2. two pages with the same chrome, title and description but different copy
     are NOT duplicates (the bug: hub pages scored 89% similar on their
     layout while the copy a reader sees was completely different)
  3. two addresses of one page (canonical alias) are never reported against
     each other, while a genuine duplicate still is
  4. the issue carries the passages the pages actually share, not just a
     percentage
  5. a page saved before main text was stored still reports duplicates
     through the title/description/H1 fallback
  6. a 300-page templated site is scored well inside the time budget
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bs4 import BeautifulSoup  # noqa: E402

from src.core.duplication import DUPLICATION_TIME_BUDGET_SECONDS  # noqa: E402
from src.core.issue_detector import IssueDetector  # noqa: E402
from src.core.seo_extractor import SEOExtractor  # noqa: E402

PASSED, FAILED = [], []

# Two site templates. Everything here is chrome: the extractor strips it, so
# none of it may influence the duplicate score either way.
CHROME_A = ('<header><a href="/">Acme Widgets</a><p>Call us on 0800 000 000</p></header>'
            '<nav><a href="/a">Alpha</a><a href="/b">Beta</a><a href="/c">Gamma</a></nav>')
CHROME_B = ('<header><a href="/">Acme Widgets - the widget people</a></header>'
            '<nav><a href="/x">Xi</a><a href="/y">Psi</a></nav>'
            '<aside>Newsletter signup: get widget news every week</aside>')
FOOTER_A = '<footer><p>Copyright Acme. Registered in England. Privacy. Terms.</p></footer>'
FOOTER_B = '<footer><p>Acme Widgets Ltd, 1 High Street. Cookies. Sitemap.</p></footer>'

BODY_WIDGETS = ('<h1>Industrial widgets</h1><p>Our hardened steel widgets are machined to a '
                'tolerance of five microns and tested at every stage of assembly, so a line '
                'that runs day and night keeps running. Each batch ships with its own '
                'certificate and a ten year warranty against manufacturing defects.</p>')
BODY_GRIPPLES = ('<h1>Rotary gripples</h1><p>A gripple turns at nine thousand revolutions a '
                 'minute without lubrication, which is why refineries fit them where a bearing '
                 'would seize. We stock eleven diameters and cut custom sizes within two '
                 'working days of the order landing.</p>')


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def page(url, body, chrome=CHROME_A, footer=FOOTER_A, title='Page', description='',
         canonical=None):
    """One crawl result, with its main text extracted exactly as a crawl does."""
    markup = (f'<html><head><title>{title}</title>'
              f'<meta name="description" content="{description}"></head>'
              f'<body>{chrome}<main>{body}</main>{footer}</body></html>')
    soup = BeautifulSoup(markup, 'html.parser')
    row = {
        'url': url,
        'status_code': 200,
        'content_type': 'text/html',
        'title': title,
        'meta_description': description,
        'h1': BeautifulSoup(body, 'html.parser').find('h1').get_text() if '<h1>' in body else '',
        'canonical_url': canonical or '',
    }
    SEOExtractor.extract_main_text(soup, row)
    row['word_count'] = row['main_word_count']
    return row


def legacy_page(url, title, description, h1, word_count):
    """A page saved before the crawler stored main text."""
    return {
        'url': url,
        'status_code': 200,
        'content_type': 'text/html',
        'title': title,
        'meta_description': description,
        'h1': h1,
        'word_count': word_count,
    }


def duplicates(pages, **kwargs):
    """Run the duplicate pass alone and return its issues."""
    detector = IssueDetector([], **kwargs)
    detector.detect_duplication_issues(pages)
    return detector, [i for i in detector.get_issues()
                      if i['issue'] == 'Duplicate Content Detected']


def test_same_copy_under_different_chrome_is_a_duplicate():
    """Template chrome must not hide a real duplicate: the same copy wrapped
    in a different header, nav and footer - and given a different title and
    meta description - is still the same page."""
    pages = [
        page('https://acme.test/widgets', BODY_WIDGETS, CHROME_A, FOOTER_A,
             title='Industrial widgets | Acme', description='Steel widgets machined to spec.'),
        page('https://acme.test/products/widgets', BODY_WIDGETS, CHROME_B, FOOTER_B,
             title='Buy widgets online', description='Order widgets today, shipped tomorrow.'),
    ]
    detector, issues = duplicates(pages)

    result('same copy, different chrome: both pages flagged',
           len(issues) == 2 and {i['url'] for i in issues} == {p['url'] for p in pages},
           str([(i['url'], i['details'][:40]) for i in issues]))
    result('same copy, different chrome: reported as identical content',
           bool(issues) and all('identical page content' in i['details'] for i in issues),
           issues[0]['details'][:120] if issues else 'no issues')
    result('same copy, different chrome: partner URL is in the evidence',
           bool(issues) and issues[0]['evidence'].get('related_urls') == [pages[1]['url']],
           str(issues[0]['evidence'].get('related_urls')) if issues else '')
    result('same copy, different chrome: one issue per page, never per pair',
           len({i['url'] for i in issues}) == len(issues))
    result('same copy, different chrome: nothing else is reported',
           len(detector.get_issues()) == len(issues))


def test_shared_chrome_with_different_copy_is_not_a_duplicate():
    """The bug this rework exists for: two hub pages sharing a layout scored
    89% similar. Identical chrome, title, description AND H1 must not make
    duplicates of pages whose copy is different."""
    shared = dict(chrome=CHROME_A, footer=FOOTER_A, title='Acme Widgets | Services',
                  description='Acme Widgets: engineering services across the UK.')
    pages = [
        page('https://acme.test/hub-one', BODY_WIDGETS, **shared),
        page('https://acme.test/hub-two', BODY_GRIPPLES, **shared),
    ]
    _, issues = duplicates(pages)
    result('shared chrome, different copy: no duplicate reported', not issues,
           str([(i['url'], i['details'][:80]) for i in issues]))

    # And the main text really is what differs - the chrome is not in it.
    result('shared chrome, different copy: chrome is absent from main text',
           all('0800 000 000' not in p['main_text'] and 'Privacy' not in p['main_text']
               for p in pages),
           pages[0]['main_text'][:60])


def test_alias_pair_is_never_reported():
    """Two addresses of one page are identical by design. They must not be
    duplicates of each other - while a genuine duplicate elsewhere on the site
    is still reported."""
    canonical = 'https://acme.test/contact'
    pages = [
        page(canonical, BODY_WIDGETS, title='Contact Acme', canonical=canonical),
        page(f'{canonical}?utm_source=SidebarCTA', BODY_WIDGETS, title='Contact Acme',
             canonical=canonical),
        page('https://acme.test/gripples', BODY_GRIPPLES, title='Rotary gripples'),
        page('https://acme.test/gripples-copy', BODY_GRIPPLES, title='Gripples, rotary'),
    ]
    _, issues = duplicates(pages)
    flagged = sorted(i['url'] for i in issues)

    result('alias pair: canonical alias raises no duplicate issue',
           canonical not in flagged and f'{canonical}?utm_source=SidebarCTA' not in flagged,
           str(flagged))
    result('alias pair: the genuine duplicate is still reported',
           flagged == ['https://acme.test/gripples', 'https://acme.test/gripples-copy'],
           str(flagged))


def test_shared_passages_appear_in_the_evidence():
    """A near-duplicate reports WHAT the two pages share, not just a score."""
    shared = ('<p>Every gripple we ship is balanced on a granite table and then run for one '
              'hour at full speed before it is signed off by the engineer who built it, and '
              'the trace from that run is packed in the box.</p>')
    long_tail = '<p>' + ('Delivery is free on orders over five hundred pounds. ' * 12) + '</p>'
    pages = [
        page('https://acme.test/gripples', '<h1>Gripples</h1>' + shared + long_tail,
             title='Gripples | Acme'),
        page('https://acme.test/gripples-uk', '<h1>Gripples UK</h1>' + shared + long_tail +
             '<p>Our Leeds depot holds stock for next day collection.</p>',
             title='Gripples UK | Acme'),
    ]
    _, issues = duplicates(pages)
    snippets = [i['evidence'].get('snippet', '') for i in issues]

    result('shared passages: the near-duplicate pair is flagged', len(issues) == 2,
           str([(i['url'], i['details'][:60]) for i in issues]))
    result('shared passages: the score is reported as a percentage',
           bool(issues) and '% similar' in issues[0]['details'],
           issues[0]['details'][:120] if issues else 'no issues')
    result('shared passages: the shared paragraph is in the evidence',
           bool(snippets) and all('balanced on a granite table' in s for s in snippets),
           snippets[0][:120] if snippets else 'no snippet')
    result('shared passages: text unique to one page is not in the evidence',
           bool(snippets) and all('leeds depot' not in s.lower() for s in snippets),
           snippets[0][:120] if snippets else 'no snippet')
    result('shared passages: evidence stays short',
           bool(snippets) and all(len(s) <= 610 for s in snippets),
           str([len(s) for s in snippets]))


def test_issue_shape_is_stable_between_crawls():
    """The issue carries the shared fields, and the same defect keeps the same
    id so a fixed issue never comes back as new."""
    pages = [
        page('https://acme.test/widgets', BODY_WIDGETS, title='Widgets'),
        page('https://acme.test/widgets-2', BODY_WIDGETS, CHROME_B, FOOTER_B, title='Widgets 2'),
    ]
    _, first = duplicates(pages)
    _, second = duplicates(list(reversed(pages)))
    by_url = {i['url']: i for i in first}
    again = {i['url']: i for i in second}
    sample = by_url.get('https://acme.test/widgets', {})

    result('issue shape: check slug, severity and category',
           sample.get('check') == 'duplicate_content' and sample.get('type') == 'warning'
           and sample.get('category') == 'Duplication',
           str([sample.get('check'), sample.get('type'), sample.get('category')]))
    result('issue shape: carries a how_to_fix sentence',
           bool(sample.get('how_to_fix')), str(sample.get('how_to_fix'))[:80])
    result('issue shape: id is stable when the crawl order changes',
           bool(by_url) and all(again.get(url, {}).get('id') == issue['id']
                                for url, issue in by_url.items()),
           str([(u, i['id'][:8], again.get(u, {}).get('id', '')[:8]) for u, i in by_url.items()]))
    result('issue shape: each page gets its own id',
           len({i['id'] for i in first}) == len(first))


def test_pages_without_main_text_use_the_metadata_fallback():
    """Crawls saved before the body was stored (and pages that yield no body
    text) must keep reporting duplicates on title/description/H1."""
    pages = [
        legacy_page('https://old.test/area-1/', 'Asbestos Removal Area | Free Quote',
                    'The best asbestos removal experts in your area.', 'Asbestos Removal', 900),
        legacy_page('https://old.test/area-2/', 'Asbestos Removal Area | Free Quote',
                    'The best asbestos removal experts in your area.', 'Asbestos Removal', 940),
        legacy_page('https://old.test/about/', 'About our family business since 1974',
                    'Three generations of builders in one Yorkshire town.', 'About us', 300),
    ]
    _, issues = duplicates(pages)
    flagged = sorted(i['url'] for i in issues)

    result('fallback: pages with no main text are still compared',
           flagged == ['https://old.test/area-1/', 'https://old.test/area-2/'], str(flagged))
    result('fallback: reported as identical metadata',
           bool(issues) and all('identical title, description and H1' in i['details']
                                for i in issues),
           issues[0]['details'][:120] if issues else 'no issues')
    result('fallback: no shared-passage evidence without main text',
           bool(issues) and all(not i['evidence'].get('snippet') for i in issues),
           str([i['evidence'] for i in issues][:1]))

    # A legacy page and a page with main text still meet on their metadata.
    body = ('<h1>Asbestos Removal</h1><p>We strip, bag and dispose of bonded asbestos sheet '
            'under an HSE licence, and hand over the consignment notes the same day.</p>')
    mixed = [pages[0], page('https://old.test/area-3/', body,
                            title='Asbestos Removal Area | Free Quote',
                            description='The best asbestos removal experts in your area.')]
    mixed[1]['word_count'] = 920
    _, mixed_issues = duplicates(mixed)
    result('fallback: a stored-text page and a legacy page still compare',
           len(mixed_issues) == 2, str([i['url'] for i in mixed_issues]))


def test_templated_site_of_300_pages_is_fast():
    """A templated site is the pathological case: every page is a near
    duplicate of every other, so the pass must stay linear and finish well
    inside its budget."""
    count = 300
    boilerplate = ('<p>We have removed asbestos from schools, hospitals and homes for thirty '
                   'years, always to the HSE licensed standard, always with the paperwork '
                   'finished before we leave the site.</p>' * 3)
    pages = [page(f'https://doorway.test/asbestos-removal-area-{i}/',
                  f'<h1>Asbestos Removal Area {i}</h1>{boilerplate}'
                  f'<p>Serving Area {i} and the villages around it.</p>',
                  title=f'Asbestos Removal Area {i} | Free Quote',
                  description=f'The best asbestos removal experts in Area {i}.')
             for i in range(count)]

    detector = IssueDetector([])
    started = time.time()
    detector.detect_duplication_issues(pages)
    elapsed = time.time() - started
    issues = detector.get_issues()
    urls = {i['url'] for i in issues}

    result(f'{count} templated pages: one issue per page',
           len(issues) == count and len(urls) == count,
           f'{len(issues)} issues for {count} pages')
    # The pages differ only in the town they name, so every page should see
    # most of the site as its duplicate (an exact count would pin down the
    # simhash's bit-level behaviour rather than the detector's).
    counted = sorted(int(re.match(r'(\d+) similar', i['details']).group(1)) for i in issues)
    result(f'{count} templated pages: each issue counts the others',
           bool(counted) and counted[0] >= count // 2 and counted[-1] <= count - 1,
           f'{counted[0]}-{counted[-1]} partners per page' if counted else 'no issues')
    result(f'{count} templated pages: scored well inside the {DUPLICATION_TIME_BUDGET_SECONDS}s '
           'budget',
           elapsed < 20 and not detector.duplication_truncated,
           f'{elapsed:.1f}s, truncated={detector.duplication_truncated}')


def test_min_similarity_is_configurable():
    """The threshold comes from the constructor and can be overridden per call."""
    pages = [
        page('https://acme.test/a', '<h1>Widgets</h1>' + BODY_WIDGETS, title='A'),
        page('https://acme.test/b', '<h1>Widgets</h1>' + BODY_WIDGETS +
             '<p>' + ('A different closing paragraph about gripples and bearings. ' * 6) +
             '</p>', title='B'),
    ]
    _, strict = duplicates(pages, min_similarity=0.999)
    _, loose = duplicates(pages, min_similarity=0.5)

    result('config: a strict threshold reports fewer pairs', not strict,
           str([(i['url'], i['details'][:40]) for i in strict]))
    result('config: a loose threshold reports the pair', len(loose) == 2,
           str([i['url'] for i in loose]))


def test_identical_bodies_behind_a_query_string_are_aliases():
    """A parameter the crawler does not strip still addresses one page.

    The UTM request's nice-to-have: when two URLs differ only by a query
    string and their bodies hash identically, that is the crawler's blind
    spot, not a duplicate-content defect on the site.
    """
    body = 'the same page served twice ' * 60
    pages = [
        page('https://site.test/list', body, title='Listing'),
        page('https://site.test/list?sort=asc', body, title='Listing'),
    ]
    _, issues = duplicates(pages)
    result('query-string aliases: identical bodies are not a duplicate defect',
           not issues, str([i['url'] for i in issues]))

    # A different page at a different path is still a genuine duplicate
    other = [
        page('https://site.test/list', body, title='Listing'),
        page('https://site.test/archive', body, title='Listing'),
    ]
    _, real = duplicates(other)
    result('query-string aliases: a real duplicate pair is still reported',
           len(real) == 2, str(len(real)))


def test_session_ids_are_stripped_like_campaign_parameters():
    from src.utils.url_params import strip_tracking_params, tracking_params_in
    for url, expected in (
        ('https://site.test/p?PHPSESSID=abc', 'https://site.test/p'),
        ('https://site.test/p?jsessionid=9&page=2', 'https://site.test/p?page=2'),
        ('https://site.test/p?sid=1', 'https://site.test/p'),
    ):
        result(f'session ids: {url.split("?")[1]} is stripped',
               strip_tracking_params(url) == expected,
               f'{strip_tracking_params(url)} != {expected}')
    result('session ids: a meaningful parameter survives',
           strip_tracking_params('https://site.test/p?page=2') == 'https://site.test/p?page=2')
    result('session ids: reported as tracking parameters',
           tracking_params_in('https://site.test/p?PHPSESSID=abc') == ['PHPSESSID'],
           str(tracking_params_in('https://site.test/p?PHPSESSID=abc')))


TESTS = (
    test_identical_bodies_behind_a_query_string_are_aliases,
    test_session_ids_are_stripped_like_campaign_parameters,
    test_same_copy_under_different_chrome_is_a_duplicate,
    test_shared_chrome_with_different_copy_is_not_a_duplicate,
    test_alias_pair_is_never_reported,
    test_shared_passages_appear_in_the_evidence,
    test_issue_shape_is_stable_between_crawls,
    test_pages_without_main_text_use_the_metadata_fallback,
    test_templated_site_of_300_pages_is_fast,
    test_min_similarity_is_configurable,
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
