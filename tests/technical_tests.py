"""Tests for the technical checklist (src/core/technical_checks.py).

    python tests/technical_tests.py

Every check gets a page that should trip it and a page that should not, because
a checklist that cries wolf is worse than no checklist: the whole point is that
an issue in the list means something is actually wrong.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.technical_checks import pixel_width, technical_issues  # noqa: E402

BASE = 'https://site.test'
PASSED, FAILED = [], []


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def page(url, **fields):
    """A plain, healthy HTML page; keyword arguments spoil it."""
    base = {
        'url': url, 'content_type': 'text/html; charset=utf-8', 'status_code': 200,
        'title': f'Title for {url}', 'meta_description': f'A description of {url} that is '
                                                         f'long enough to read.',
        'h1': f'Heading for {url}', 'canonical_url': url, 'robots': 'index, follow',
        'headings': [{'level': 1, 'text': 'Heading', 'is_visible': True, 'dom_order': 0}],
        'heading_text_issues': [], 'images': [], 'hreflang': [], 'ttfb': 120,
        'cache_control': 'public, max-age=600', 'content_length': 40000,
    }
    base.update(fields)
    return base


def run(pages, links=None, **config):
    return technical_issues(pages, links or [], BASE, config=config)


def checks(issues):
    return sorted({issue['check'] for issue in issues})


def find(issues, check):
    return [issue for issue in issues if issue['check'] == check]


def healthy_site():
    return [page(f'{BASE}/'), page(f'{BASE}/about'), page(f'{BASE}/pricing')]


def test_a_healthy_site_reports_nothing():
    issues = run(healthy_site(), [
        {'source_url': f'{BASE}/', 'target_url': f'{BASE}/about', 'is_internal': True,
         'target_status': 200},
    ])
    result('healthy: a clean site produces no technical issues', not issues,
           str(checks(issues)))


def test_canonical_problems():
    relative = run([page(f'{BASE}/a', canonical_url='/a')])
    result('canonical: a relative canonical is reported',
           checks(relative) == ['canonical_relative'], str(checks(relative)))

    cross = run([page(f'{BASE}/a', canonical_url='https://elsewhere.test/a')])
    result('canonical: a cross-domain canonical is an error',
           find(cross, 'canonical_cross_domain')
           and find(cross, 'canonical_cross_domain')[0]['type'] == 'error',
           str(checks(cross)))

    chain = run([page(f'{BASE}/a', canonical_url=f'{BASE}/b'),
                 page(f'{BASE}/b', canonical_url=f'{BASE}/c'),
                 page(f'{BASE}/c')])
    result('canonical: a chain is reported', find(chain, 'canonical_chain'), str(checks(chain)))

    to_noindex = run([page(f'{BASE}/a', canonical_url=f'{BASE}/hidden'),
                      page(f'{BASE}/hidden', canonical_url=f'{BASE}/hidden', robots='noindex')])
    result('canonical: pointing at a noindex page is an error',
           find(to_noindex, 'canonical_to_noindex'), str(checks(to_noindex)))

    to_redirect = run([page(f'{BASE}/a', canonical_url=f'{BASE}/moved'),
                       page(f'{BASE}/moved', canonical_url=f'{BASE}/moved',
                            status_code=301, redirected_to=f'{BASE}/final'),
                       page(f'{BASE}/final')])
    result('canonical: pointing at a redirect is reported',
           find(to_redirect, 'canonical_to_redirect'), str(checks(to_redirect)))

    self_canonical = run([page(f'{BASE}/a')])
    result('canonical: a correct self-canonical is quiet', not self_canonical,
           str(checks(self_canonical)))


def test_tracking_parameters_are_not_a_canonical_mismatch():
    """A canonical that only drops utm_* is the same URL, not a chain."""
    issues = run([page(f'{BASE}/a?utm_source=x', canonical_url=f'{BASE}/a'), page(f'{BASE}/a')])
    result('canonical: tracking parameters do not make a chain',
           not find(issues, 'canonical_chain'), str(checks(issues)))


def test_robots_conflicts():
    noindex_linked = run(
        [page(f'{BASE}/'), page(f'{BASE}/hidden', robots='noindex')],
        [{'source_url': f'{BASE}/', 'target_url': f'{BASE}/hidden', 'is_internal': True}])
    result('robots: a linked noindex page is reported',
           find(noindex_linked, 'noindex_page_linked'), str(checks(noindex_linked)))

    in_sitemap = run([page(f'{BASE}/hidden', robots='noindex')],
                     sitemap_urls={f'{BASE}/hidden'})
    result('robots: noindex plus sitemap is an error',
           find(in_sitemap, 'noindex_in_sitemap')
           and find(in_sitemap, 'noindex_in_sitemap')[0]['type'] == 'error',
           str(checks(in_sitemap)))

    unlinked = run([page(f'{BASE}/hidden', robots='noindex')])
    result('robots: an unlinked noindex page is fine', not unlinked, str(checks(unlinked)))

    nofollow = run([page(f'{BASE}/')],
                   [{'source_url': f'{BASE}/', 'target_url': f'{BASE}/x',
                     'is_internal': True, 'nofollow': True}])
    result('robots: nofollow on an internal link is reported',
           find(nofollow, 'internal_link_nofollow'), str(checks(nofollow)))

    blocked = run([page(f'{BASE}/a', canonical_url=f'{BASE}/blocked')],
                  robots_checker=lambda url: False)
    result('robots: a canonical blocked by robots.txt is an error',
           find(blocked, 'canonical_blocked_by_robots'), str(checks(blocked)))

    allowed = run([page(f'{BASE}/a')], robots_checker=lambda url: True)
    result('robots: an allowed canonical is quiet', not allowed, str(checks(allowed)))


def test_redirect_problems():
    chain = run([page(f'{BASE}/a', status_code=200, redirected_to=f'{BASE}/c',
                      redirects=[{'url': f'{BASE}/a', 'status_code': 301},
                                 {'url': f'{BASE}/b', 'status_code': 301}])])
    result('redirects: more than one hop is reported', find(chain, 'redirect_chain'),
           str(checks(chain)))

    # A real loop leaves and comes back.
    loop = run([page(f'{BASE}/a', redirected_to=f'{BASE}/a',
                     redirects=[{'url': f'{BASE}/a', 'status_code': 301},
                                {'url': f'{BASE}/b', 'status_code': 301}])])
    result('redirects: a loop is an error',
           find(loop, 'redirect_loop') and find(loop, 'redirect_loop')[0]['type'] == 'error',
           str(checks(loop)))

    # A chain that never leaves the address it started at is the browser
    # restarting a navigation by itself - Critical-CH, an HSTS upgrade - which
    # Chromium reports as a 307 to the same URL. This test previously asserted
    # the opposite and so encoded the false positive: healthy pages were
    # reported as looping, at severity error, on sites that never sent a
    # Location header.
    no_op = run([page(f'{BASE}/a', redirected_to=f'{BASE}/a',
                      redirects=[{'url': f'{BASE}/a', 'status_code': 307}])])
    result('redirects: a self-redirect with nowhere to go is not a loop',
           not find(no_op, 'redirect_loop'), str(checks(no_op)))

    one_hop = run([page(f'{BASE}/a', redirected_to=f'{BASE}/b',
                        redirects=[{'url': f'{BASE}/a', 'status_code': 301}]),
                   page(f'{BASE}/b')])
    result('redirects: a single hop is not reported', not find(one_hop, 'redirect_chain'),
           str(checks(one_hop)))

    linked = run([page(f'{BASE}/'), page(f'{BASE}/old', status_code=301,
                                         redirected_to=f'{BASE}/new'), page(f'{BASE}/new')],
                 [{'source_url': f'{BASE}/', 'target_url': f'{BASE}/old', 'is_internal': True}])
    result('redirects: linking to a redirect is reported',
           find(linked, 'internal_link_to_redirect'), str(checks(linked)))

    mixed = run([page(f'{BASE}/')], [
        {'source_url': f'{BASE}/', 'target_url': 'https://site.test/a', 'is_internal': True},
        {'source_url': f'{BASE}/', 'target_url': 'http://site.test/b', 'is_internal': True},
        {'source_url': f'{BASE}/', 'target_url': 'https://www.site.test/c', 'is_internal': True},
    ])
    result('redirects: mixed http/https internal links are reported',
           find(mixed, 'mixed_protocol_links'), str(checks(mixed)))
    result('redirects: mixed www/non-www internal links are reported',
           find(mixed, 'mixed_host_links'), str(checks(mixed)))


def test_broken_links_are_reported_against_the_linking_page():
    issues = run([page(f'{BASE}/')], [
        {'source_url': f'{BASE}/', 'target_url': f'{BASE}/gone', 'is_internal': True,
         'target_status': 404},
        {'source_url': f'{BASE}/', 'target_url': 'https://other.test/x', 'is_internal': False,
         'target_status': 500},
    ])
    broken = find(issues, 'links_to_broken_urls')
    result('broken links: reported once per source page', len(broken) == 1,
           str([i['details'] for i in broken]))
    result('broken links: the source page is the URL to fix',
           broken and broken[0]['url'] == f'{BASE}/', str(broken[0]['url'] if broken else None))
    result('broken links: both targets are named',
           broken and len(broken[0]['evidence'].get('related_urls', [])) == 2,
           str(broken[0]['evidence'] if broken else None))


def test_duplicates_across_pages():
    issues = run([page(f'{BASE}/a', title='Same Title', h1='Same H1',
                       meta_description='Exactly the same description on both pages here.'),
                  page(f'{BASE}/b', title='Same Title', h1='Same H1',
                       meta_description='Exactly the same description on both pages here.')])
    result('duplicates: a shared title is reported for both pages',
           len(find(issues, 'duplicate_title')) == 2, str(len(find(issues, 'duplicate_title'))))
    result('duplicates: a shared meta description is reported',
           len(find(issues, 'duplicate_meta_description')) == 2,
           str(len(find(issues, 'duplicate_meta_description'))))
    result('duplicates: a shared H1 is reported',
           len(find(issues, 'duplicate_h1')) == 2, str(len(find(issues, 'duplicate_h1'))))
    result('duplicates: the other page is named in the evidence',
           find(issues, 'duplicate_title')[0]['evidence'].get('related_urls') == [f'{BASE}/b'],
           str(find(issues, 'duplicate_title')[0]['evidence']))

    distinct = run([page(f'{BASE}/a'), page(f'{BASE}/b')])
    result('duplicates: distinct pages are quiet', not distinct, str(checks(distinct)))

    # A page that canonicalises elsewhere has already said it is not the
    # version to index. One correctly-canonicalised ?topic= URL on a real site
    # produced nine findings this way: duplicate title, H1 and description for
    # both pages, plus templated_title and the rest.
    aliased = run([
        page(f'{BASE}/contact', title='Talk to us', h1='Talk to a human',
             meta_description='Questions about pricing, trials or a push that went wrong.',
             canonical_url=f'{BASE}/contact'),
        page(f'{BASE}/contact?topic=sales', title='Talk to us', h1='Talk to a human',
             meta_description='Questions about pricing, trials or a push that went wrong.',
             canonical_url=f'{BASE}/contact'),
    ])
    result('duplicates: a page canonicalising elsewhere is not a duplicate',
           not find(aliased, 'duplicate_title'), str(checks(aliased)))
    result('duplicates: nor is its H1 or description',
           not find(aliased, 'duplicate_h1') and not find(aliased, 'duplicate_meta_description'),
           str(checks(aliased)))

    # Self-canonical pages that really do collide are still reported, and a
    # trailing slash is not a different URL.
    both_self = run([
        page(f'{BASE}/a', title='Same Title', canonical_url=f'{BASE}/a/'),
        page(f'{BASE}/b', title='Same Title', canonical_url=f'{BASE}/b'),
    ])
    result('duplicates: self-canonical pages that collide are still reported',
           len(find(both_self, 'duplicate_title')) == 2, str(checks(both_self)))


def test_templated_titles():
    """A template is fine; a template with nothing unique in front is not."""
    good = run([page(f'{BASE}/a', title='Pricing | Space-O'),
                page(f'{BASE}/b', title='Case studies | Space-O'),
                page(f'{BASE}/c', title='Contact us | Space-O')])
    result('templated: a template with distinct pages is fine',
           not find(good, 'templated_title'),
           str([i['details'][:60] for i in find(good, 'templated_title')]))

    repeated = run([page(f'{BASE}/a', title='Services | Space-O'),
                    page(f'{BASE}/b', title='Services | Space-O'),
                    page(f'{BASE}/c', title='Pricing | Space-O')])
    result('templated: pages sharing the unique part are reported',
           len(find(repeated, 'templated_title')) == 2,
           str(len(find(repeated, 'templated_title'))))

    empty = run([page(f'{BASE}/a', title='| Space-O'),
                 page(f'{BASE}/b', title='2 | Space-O'),
                 page(f'{BASE}/c', title='Pricing | Space-O')])
    reported = {i['url'] for i in find(empty, 'templated_title')}
    result('templated: a missing or numeric unique part is reported',
           f'{BASE}/a' in reported and f'{BASE}/b' in reported and f'{BASE}/c' not in reported,
           str(reported))

    result('templated: a title with no template at all is left alone',
           not find(run([page(f'{BASE}/a', title='Just a plain title')]), 'templated_title'))


def test_empty_alt_on_meaningful_images():
    """alt="" is right for decoration and wrong for content."""
    decorative = run([page(f'{BASE}/a', images=[
        {'src': '/swirl.png', 'has_alt': True, 'alt': '', 'width': 24, 'height': 24,
         'is_probably_content': False, 'in_link': False}])])
    result('empty alt: a decorative image is not reported',
           not find(decorative, 'empty_alt_on_content_image'), str(checks(decorative)))

    linked = run([page(f'{BASE}/a', images=[
        {'src': '/logo.png', 'has_alt': True, 'alt': '', 'is_probably_content': True,
         'in_link': True, 'link_has_text': False}])])
    found = find(linked, 'empty_alt_on_content_image')
    result('empty alt: an image that is a link\'s only content is reported',
           len(found) == 1 and 'link' in found[0]['details'], str([i['details'] for i in found]))

    described = run([page(f'{BASE}/a', images=[
        {'src': '/hero.png', 'has_alt': True, 'alt': 'A surgeon reviewing a scan',
         'is_probably_content': True, 'in_link': False}])])
    result('empty alt: a described image is not reported',
           not find(described, 'empty_alt_on_content_image'), str(checks(described)))

    missing = run([page(f'{BASE}/a', images=[
        {'src': '/hero.png', 'has_alt': False, 'alt': '', 'is_probably_content': True}])])
    result('empty alt: a missing alt stays issue_detector\'s to report',
           not find(missing, 'empty_alt_on_content_image'), str(checks(missing)))


def test_pixel_widths():
    wide = 'A very long search result title that will certainly be cut off in Google results'
    issues = run([page(f'{BASE}/a', title=wide)])
    result('pixels: an over-wide title is reported', find(issues, 'title_too_wide'),
           str(checks(issues)))
    result('pixels: a short title is not', not run([page(f'{BASE}/a', title='Pricing')]),
           str(checks(run([page(f'{BASE}/a', title='Pricing')]))))
    result('pixels: width grows with wide characters',
           pixel_width('mmmmm') > pixel_width('iiiii'),
           f"{pixel_width('mmmmm')} vs {pixel_width('iiiii')}")
    fits = ('Get a website down text alert from the uptime or ping monitor you already use. '
            'Add your Welco address as a contact and text the on-call tech. Start free.')
    result('pixels: a 155-character description fits a 14px snippet',
           not find(run([page(f'{BASE}/a', meta_description=fits)]), 'description_too_wide'),
           str(pixel_width(fits, font_size=14)))
    result('pixels: a 300-character description does not',
           find(run([page(f'{BASE}/a', meta_description=fits * 2)]), 'description_too_wide'),
           str(pixel_width(fits * 2, font_size=14)))


def test_heading_problems():
    two_h1 = run([page(f'{BASE}/a', headings=[{'level': 1, 'text': 'One'},
                                              {'level': 1, 'text': 'Two'}])])
    result('headings: two H1s are reported', find(two_h1, 'multiple_h1'), str(checks(two_h1)))

    skipped = run([page(f'{BASE}/a', headings=[{'level': 1, 'text': 'One'},
                                               {'level': 4, 'text': 'Deep'}])])
    result('headings: a skipped level is reported', find(skipped, 'skipped_heading_level'),
           str(checks(skipped)))

    empty = run([page(f'{BASE}/a', headings=[{'level': 1, 'text': 'One'},
                                             {'level': 2, 'text': '   '}])])
    result('headings: an empty heading is reported', find(empty, 'empty_heading'),
           str(checks(empty)))

    spacing = run([page(f'{BASE}/a', heading_text_issues=[
        {'dom_order': 0, 'level': 2, 'problem': 'double_space'}])])
    result('headings: stray whitespace is reported', find(spacing, 'heading_stray_whitespace'),
           str(checks(spacing)))

    tidy = run([page(f'{BASE}/a', headings=[{'level': 1, 'text': 'One'},
                                            {'level': 2, 'text': 'Two'},
                                            {'level': 3, 'text': 'Three'}])])
    result('headings: a correct outline is quiet', not tidy, str(checks(tidy)))


def test_image_problems():
    issues = run([page(f'{BASE}/a', images=[
        {'src': '/hero.png', 'loading': 'lazy', 'width': 1200, 'height': 600},
        {'src': '/huge.jpg', 'bytes': 900 * 1024, 'width': 800, 'height': 400},
        {'src': '/nodims.png'},
    ])])
    result('images: a lazy image above the fold is reported',
           find(issues, 'image_lazy_above_fold'), str(checks(issues)))
    result('images: an oversized image is reported', find(issues, 'image_too_heavy'),
           str(checks(issues)))
    result('images: missing width and height is reported',
           find(issues, 'image_missing_dimensions'), str(checks(issues)))

    # The first two images count as above the fold, so only the third may be lazy
    fine = run([page(f'{BASE}/a', images=[
        {'src': '/hero.png', 'width': 1200, 'height': 600, 'bytes': 40 * 1024},
        {'src': '/second.png', 'width': 400, 'height': 200, 'bytes': 20 * 1024},
        {'src': '/third.png', 'width': 400, 'height': 200, 'loading': 'lazy'},
    ])])
    result('images: correct images are quiet', not fine, str(checks(fine)))


def test_lazy_image_uses_measured_geometry():
    """DOM order is a guess; the renderer's measurement is not.

    The real case this comes from: a homepage whose first <img> in source sat
    2,403px down with nothing above the fold at all, reported as "sits at the
    top of the page" purely because it was `index < 2`.
    """
    # Measured below the fold: quiet, however early it appears in the source.
    below = run([page(f'{BASE}/a', images=[
        {'src': '/first.webp', 'loading': 'lazy', 'width': 1600, 'height': 1000,
         'above_fold': False, 'top_px': 2403},
        {'src': '/second.webp', 'loading': 'lazy', 'width': 1600, 'height': 1000,
         'above_fold': False, 'top_px': 2403},
    ])])
    result('images: a measured below-fold lazy image is quiet',
           not find(below, 'image_lazy_above_fold'), str(checks(below)))

    # Measured inside the first screenful: reported, whatever its DOM index.
    above = run([page(f'{BASE}/a', images=[
        {'src': '/a.webp', 'width': 100, 'height': 100, 'above_fold': False, 'top_px': 4000},
        {'src': '/b.webp', 'width': 100, 'height': 100, 'above_fold': False, 'top_px': 4200},
        {'src': '/c.webp', 'width': 100, 'height': 100, 'above_fold': False, 'top_px': 4400},
        {'src': '/hero.webp', 'loading': 'lazy', 'width': 1200, 'height': 600,
         'above_fold': True, 'top_px': 1000},
    ])])
    found = find(above, 'image_lazy_above_fold')
    result('images: a measured above-fold lazy image is reported at any DOM index',
           len(found) == 1, str(checks(above)))
    result('images: the finding names the image that was measured',
           found and '/hero.webp' in found[0]['details'], str(found))
    result('images: the finding cites how far down the image sits',
           found and '1000px' in found[0]['details'], str(found))

    # No measurement at all: fall back to DOM order, but say so.
    unrendered = run([page(f'{BASE}/a', images=[
        {'src': '/first.webp', 'loading': 'lazy', 'width': 1200, 'height': 600},
    ])])
    guessed = find(unrendered, 'image_lazy_above_fold')
    result('images: an unrendered page still falls back to DOM order',
           len(guessed) == 1, str(checks(unrendered)))
    result('images: the fallback admits the position was not measured',
           guessed and 'not measured' in guessed[0]['details'], str(guessed))

    # Two lazy images on one page stay two separate issues.
    pair = run([page(f'{BASE}/a', images=[
        {'src': '/one.webp', 'loading': 'lazy', 'width': 100, 'height': 100,
         'above_fold': True, 'top_px': 10},
        {'src': '/two.webp', 'loading': 'lazy', 'width': 100, 'height': 100,
         'above_fold': True, 'top_px': 200},
    ])])
    ids = {issue['id'] for issue in find(pair, 'image_lazy_above_fold')}
    result('images: two lazy images on one page keep two distinct issue ids',
           len(ids) == 2, str(ids))


def test_hreflang_problems():
    invalid = run([page(f'{BASE}/a', hreflang=[
        {'hreflang': 'UK', 'href': f'{BASE}/uk'}, {'hreflang': 'en', 'href': f'{BASE}/a'}])])
    result('hreflang: UK is not a valid code', find(invalid, 'hreflang_invalid_code'),
           str(checks(invalid)))
    result('hreflang: the fix names GB',
           'GB' in str(find(invalid, 'hreflang_invalid_code')[0]['evidence']),
           str(find(invalid, 'hreflang_invalid_code')[0]['evidence']))

    no_self = run([page(f'{BASE}/a', hreflang=[{'hreflang': 'fr', 'href': f'{BASE}/fr'}])])
    result('hreflang: a missing self-reference is reported',
           find(no_self, 'hreflang_missing_self'), str(checks(no_self)))

    one_way = run([
        page(f'{BASE}/en', hreflang=[{'hreflang': 'en', 'href': f'{BASE}/en'},
                                     {'hreflang': 'fr', 'href': f'{BASE}/fr'}]),
        page(f'{BASE}/fr', hreflang=[{'hreflang': 'fr', 'href': f'{BASE}/fr'}]),
    ])
    result('hreflang: a one-way pair is an error', find(one_way, 'hreflang_no_return_link'),
           str(checks(one_way)))

    mutual = run([
        page(f'{BASE}/en', hreflang=[{'hreflang': 'en', 'href': f'{BASE}/en'},
                                     {'hreflang': 'fr', 'href': f'{BASE}/fr'}]),
        page(f'{BASE}/fr', hreflang=[{'hreflang': 'fr', 'href': f'{BASE}/fr'},
                                     {'hreflang': 'en', 'href': f'{BASE}/en'}]),
    ])
    result('hreflang: a mutual pair is quiet', not mutual, str(checks(mutual)))


def test_performance_signals():
    slow = run([page(f'{BASE}/a', ttfb=2500)])
    result('performance: a slow first byte is reported', find(slow, 'slow_ttfb'),
           str(checks(slow)))

    uncacheable = run([page(f'{BASE}/a', cache_control='private, no-store, max-age=0')])
    result('performance: an uncacheable content page is reported',
           find(uncacheable, 'uncacheable_content'), str(checks(uncacheable)))

    heavy = run([page(f'{BASE}/a', content_length=8 * 1024 * 1024)])
    result('performance: a heavy page is reported', find(heavy, 'page_too_heavy'),
           str(checks(heavy)))

    quick = run([page(f'{BASE}/a', ttfb=90, cache_control='public, max-age=3600')])
    result('performance: a fast cacheable page is quiet', not quick, str(checks(quick)))


def test_grouping_above_the_threshold():
    many = [page(f'{BASE}/p{n}', ttfb=3000) for n in range(9)]
    issues = find(run(many), 'slow_ttfb')
    result('grouping: nine offenders collapse into one issue', len(issues) == 1,
           str(len(issues)))
    result('grouping: the count and the URLs are carried',
           issues and issues[0]['evidence'].get('found') == '9'
           and len(issues[0]['evidence'].get('related_urls', [])) == 9,
           str(issues[0]['evidence'])[:120] if issues else '')

    few = [page(f'{BASE}/p{n}', ttfb=3000) for n in range(4)]
    result('grouping: four offenders stay as four issues',
           len(find(run(few), 'slow_ttfb')) == 4, str(len(find(run(few), 'slow_ttfb'))))


def test_ids_are_stable_and_specific():
    first = run([page(f'{BASE}/a', canonical_url='/a')])
    second = run([page(f'{BASE}/a', canonical_url='/a')])
    result('ids: the same defect keeps its id across runs',
           first[0]['id'] == second[0]['id'], f"{first[0]['id']} vs {second[0]['id']}")

    other = run([page(f'{BASE}/b', canonical_url='/b')])
    result('ids: a different page gets a different id', first[0]['id'] != other[0]['id'])


def test_junk_input_does_not_raise():
    junk = [
        {'url': f'{BASE}/a', 'content_type': 'text/html', 'headings': 'not a list',
         'images': {'not': 'a list'}, 'hreflang': 'nope', 'redirects': 'nope',
         'ttfb': 'fast', 'content_length': 'big', 'canonical_url': None},
        {'url': None},
        'not even a dict',
    ]
    try:
        issues = technical_issues(junk, [{'source_url': None}, 'junk'], BASE)
        result('junk: odd pages do not raise', True, f'{len(issues)} issue(s)')
    except Exception as exc:
        result('junk: odd pages do not raise', False, f'{type(exc).__name__}: {exc}')


TESTS = (
    test_a_healthy_site_reports_nothing,
    test_canonical_problems,
    test_tracking_parameters_are_not_a_canonical_mismatch,
    test_robots_conflicts,
    test_redirect_problems,
    test_broken_links_are_reported_against_the_linking_page,
    test_duplicates_across_pages,
    test_templated_titles,
    test_empty_alt_on_meaningful_images,
    test_pixel_widths,
    test_heading_problems,
    test_image_problems,
    test_lazy_image_uses_measured_geometry,
    test_hreflang_problems,
    test_performance_signals,
    test_grouping_above_the_threshold,
    test_ids_are_stable_and_specific,
    test_junk_input_does_not_raise,
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
