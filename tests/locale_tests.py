"""Tests for hreflang-aware checks on a multilingual site.

One tiny two-locale fixture - the site root in en-us (also x-default) and
/ca/ in en-ca - where every page names its alternates both ways, exactly as
a real multilingual site does:

    python tests/locale_tests.py

Each rule has a quiet case (locale versions of one page are not a finding)
and a firing case (the same defect inside one locale still is), because
hiding real duplicates would be worse than the false alarms this removes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import link_graph, locales  # noqa: E402
from src.core.duplication import DuplicationDetector  # noqa: E402
from src.core.technical_checks import technical_issues  # noqa: E402

BASE = 'https://example.com'

PASSED, FAILED = [], []


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


# ---------------------------------------------------------------------------
# fixture
# ---------------------------------------------------------------------------

BODY = ('Alarm notification software sends every critical alarm from your plant '
        'to the phones of the people on call, with escalation, acknowledgement '
        'and a full audit trail of who was told and when they answered.')

# (us path, ca path) pairs that are one logical page.
PAIRS = [('/', '/ca/'), ('/pricing/', '/ca/pricing/'),
         ('/glossary/alarm/', '/ca/glossary/alarm/'),
         ('/glossary/scada/', '/ca/glossary/scada/')]

# Copy of each logical page, unrelated to the others so only the locale
# versions of one page are near-duplicates.
COPY = {
    '/': 'Welcome to the platform that keeps operations teams informed around '
         'the clock wherever the plant is and whoever happens to be on shift tonight.',
    '/pricing/': 'Plans start with a free trial, then a monthly price per site; '
                 'annual billing saves two months and every plan includes support.',
    '/glossary/alarm/': 'An alarm is a signal that a measured value crossed a limit '
                        'and someone must act before equipment or product is harmed.',
    '/glossary/scada/': 'Supervisory control and data acquisition systems gather '
                        'readings from remote stations and let operators steer valves and pumps.',
}


def page(path, title, h1=None, text=BODY, hreflang=None, **extra):
    data = {
        'url': BASE + path,
        'status_code': 200,
        'content_type': 'text/html',
        'is_internal': True,
        'title': title,
        'h1': h1 if h1 is not None else title.split(' | ')[0],
        'meta_description': title + ' description',
        'main_text': text,
        'main_word_count': len(text.split()),
        'word_count': len(text.split()),
        'hreflang': hreflang or [],
        'depth': 1,
    }
    data.update(extra)
    return data


def alternates_of(us, ca):
    return [{'lang': 'en-us', 'url': BASE + us}, {'lang': 'en-ca', 'url': BASE + ca},
            {'lang': 'x-default', 'url': BASE + us}]


def site(extra_pages=()):
    """Every PAIRS page in both locales with the same title, H1 and copy."""
    pages = []
    for us, ca in PAIRS:
        name = 'Home' if us == '/' else us.strip('/').split('/')[-1].title()
        title = f'{name} alarm alerts | Acme'
        for path in (us, ca):
            pages.append(page(path, title, text=COPY[us],
                              hreflang=alternates_of(us, ca)))
    pages.extend(extra_pages)
    return pages


def link(source, target, anchor, placement='body', in_nav=False):
    return {'source_url': BASE + source, 'target_url': BASE + target,
            'anchor_text': anchor, 'is_internal': True, 'placement': placement,
            'in_nav': in_nav, 'in_main': placement == 'body', 'in_heading': False,
            'nofollow': False}


def checks(issues, check):
    return [issue for issue in issues if issue['check'] == check]


def urls_in(issues):
    found = set()
    for issue in issues:
        found.add(issue['url'])
        found.update((issue.get('evidence') or {}).get('related_urls') or [])
    return found


# ---------------------------------------------------------------------------
# locales: groups and prefixes
# ---------------------------------------------------------------------------

def test_alternates_form_one_group_and_ca_is_a_locale_prefix():
    built = locales.build(site())
    result('mutual alternates are one page',
           built.same_page(BASE + '/pricing/', BASE + '/ca/pricing'))
    result('different pages are not',
           not built.same_page(BASE + '/pricing/', BASE + '/ca/glossary/alarm/'))
    result('/ca/ is the only prefix', built.prefixes == (('ca',),), repr(built.prefixes))
    result('split drops the prefix',
           built.split('/ca/glossary/alarm') == ('/ca/', ('glossary', 'alarm')))
    result('/ca/ is a locale home, / and /ca/pricing/ are not',
           built.is_locale_home(BASE + '/ca/') and not built.is_locale_home(BASE + '/')
           and not built.is_locale_home(BASE + '/ca/pricing/'))


def test_a_one_way_hreflang_groups_nothing():
    pages = [page('/a/', 'A', hreflang=[{'lang': 'en-ca', 'url': BASE + '/ca/a/'}]),
             page('/ca/a/', 'A')]
    built = locales.build(pages)
    result('one-way declaration is not a group',
           not built.same_page(BASE + '/a/', BASE + '/ca/a/'))


def test_no_hreflang_means_no_prefixes():
    built = locales.build([page('/ca/a/', 'A'), page('/ca/b/', 'B')])
    result('no prefixes without hreflang', built.prefixes == ())
    result('split leaves the path alone', built.split('/ca/a') == ('', ('ca', 'a')))


def test_a_prefix_needs_its_locale_home_declared():
    # en-ca is only declared on two deep pages: '/ca/glossary/' is their
    # common path but it is not a locale home, so no prefix is guessed.
    pages = [page('/glossary/a/', 'A', hreflang=alternates_of('/glossary/a/', '/ca/glossary/a/')),
             page('/glossary/b/', 'B', hreflang=alternates_of('/glossary/b/', '/ca/glossary/b/'))]
    result('no prefix from deep pages only', locales.build(pages).prefixes == ())


# ---------------------------------------------------------------------------
# duplicate content
# ---------------------------------------------------------------------------

def test_duplicate_content_skips_alternates_but_not_same_locale_copies():
    extra = [page('/ca/about/', 'About | Acme', text='Shared copy. ' + BODY),
             page('/ca/team/', 'Team | Acme', text='Shared copy. ' + BODY)]
    issues = DuplicationDetector().detect(site(extra))
    flagged = {issue['url'] for issue in issues}
    alternate = {BASE + us: BASE + ca for us, ca in PAIRS}
    alternate.update({ca: us for us, ca in alternate.items()})
    against_own = [issue['url'] for issue in issues
                   if alternate.get(issue['url']) in issue['evidence'].get('related_urls', [])]
    result('no page is reported against its own alternate', not against_own,
           repr(against_own))
    result('the identical home pages are not reported at all',
           not flagged & {BASE + '/', BASE + '/ca/'}, repr(sorted(flagged)))
    result('two en-ca pages with the same copy still are',
           {BASE + '/ca/about/', BASE + '/ca/team/'} <= flagged, repr(sorted(flagged)))


# ---------------------------------------------------------------------------
# duplicate title, description, H1, templated title
# ---------------------------------------------------------------------------

def test_duplicate_title_h1_description_skip_alternates():
    issues = technical_issues(site(), [], BASE)
    for check in ('duplicate_title', 'duplicate_h1', 'duplicate_meta_description',
                  'templated_title'):
        result(f'{check} quiet on alternates', not checks(issues, check),
               repr(urls_in(checks(issues, check))))


def test_duplicate_title_h1_still_fire_inside_one_locale():
    extra = [page('/ca/about/', 'About alarm alerts | Acme', h1='About us'),
             page('/ca/team/', 'About alarm alerts | Acme', h1='About us'),
             page('/ca/careers/', 'About alarm alerts | Acme', h1='About us')]
    issues = technical_issues(site(extra), [], BASE)
    expected = {BASE + '/ca/about/', BASE + '/ca/team/', BASE + '/ca/careers/'}
    for check in ('duplicate_title', 'duplicate_h1', 'templated_title'):
        found = urls_in(checks(issues, check))
        result(f'{check} fires on three en-ca pages', found == expected, repr(found))


# ---------------------------------------------------------------------------
# link graph
# ---------------------------------------------------------------------------

def graph_for(pages, links):
    graph = link_graph.build_graph(pages, links, BASE + '/')
    link_graph.click_depth(graph, BASE + '/')
    return graph, link_graph.analyse(graph)


def test_an_anchor_pointing_at_versions_of_one_page_is_not_ambiguous():
    links = [link('/glossary/alarm/', '/pricing/', 'alarm pricing'),
             link('/ca/glossary/alarm/', '/ca/pricing/', 'alarm pricing')]
    _, issues = graph_for(site(), links)
    result('alternates share an anchor quietly', not checks(issues, 'ambiguous_anchor'))

    links.append(link('/ca/glossary/scada/', '/ca/glossary/alarm/', 'alarm pricing'))
    _, issues = graph_for(site(), links)
    found = checks(issues, 'ambiguous_anchor')
    result('a second real target still makes it ambiguous',
           len(found) == 1 and found[0]['evidence'].get('found') == 'alarm pricing')


def test_clusters_skip_the_locale_prefix():
    graph, _ = graph_for(site(), [])
    nodes = graph['nodes']
    result('/ca/glossary/alarm/ is in /ca/glossary/',
           nodes['example.com/ca/glossary/alarm']['cluster'] == '/ca/glossary/')
    result('/glossary/alarm/ is in /glossary/',
           nodes['example.com/glossary/alarm']['cluster'] == '/glossary/')
    result('the locale home is its own cluster',
           nodes['example.com/ca']['cluster'] == '/ca/')


def test_one_way_pairs_stay_inside_a_locale_section():
    links = [link('/ca/glossary/alarm/', '/ca/pricing/', 'see the plans'),
             link('/ca/glossary/alarm/', '/ca/glossary/scada/', 'scada alarms')]
    _, issues = graph_for(site(), links)
    found = [(i['url'], (i['evidence'].get('related_urls') or [''])[0])
             for i in checks(issues, 'one_way_pair')]
    result('glossary -> pricing is across sections now',
           (BASE + '/ca/glossary/alarm/', BASE + '/ca/pricing/') not in found, repr(found))
    result('glossary -> glossary inside en-ca still fires',
           (BASE + '/ca/glossary/alarm/', BASE + '/ca/glossary/scada/') in found, repr(found))


def test_a_locale_home_reached_by_navigation_is_not_an_orphan():
    links = [link('/', '/ca/', 'Canada', placement='navigation', in_nav=True),
             link('/', '/ca/pricing/', 'Pricing', placement='navigation', in_nav=True)]
    _, issues = graph_for(site(), links)
    orphans = {issue['url'] for issue in checks(issues, 'orphan_page')}
    result('/ca/ is not an orphan', BASE + '/ca/' not in orphans, repr(orphans))
    result('/ca/pricing/ still is', BASE + '/ca/pricing/' in orphans, repr(orphans))


def test_linking_your_own_translation_with_your_keyword_is_not_cannibalising():
    links = [link('/ca/pricing/', '/pricing/', 'pricing alarm alerts'),
             link('/ca/pricing/', '/ca/glossary/alarm/', 'pricing alarm alerts')]
    _, issues = graph_for(site(), links)
    found = {(i['url'], i['evidence']['related_urls'][0])
             for i in checks(issues, 'self_cannibalising_anchor')}
    result('alternate target is quiet',
           (BASE + '/ca/pricing/', BASE + '/pricing/') not in found, repr(found))
    result('another page still fires',
           (BASE + '/ca/pricing/', BASE + '/ca/glossary/alarm/') in found, repr(found))



def test_a_translated_page_with_no_hreflang_at_all_is_a_one_way_pair():
    """The commonest way hreflang goes wrong, and it was being skipped.

    The English page names the French one; the French page never got its
    tags. `hreflang_no_return_link` read the target's own declarations and
    did `if back is None: continue` - so the case where the target declares
    nothing, which is most of them, fell through the one check that exists
    to catch it. The pages that *were* reported were the rarer kind, where
    the translation has hreflang but omits one language.

    The guard above it already excludes pages this crawl never fetched, so
    there was nothing defensive left for the skip to do.
    """
    base = 'https://site.test'

    def page(url, hreflang=None):
        row = {'url': url, 'status_code': 200, 'content_type': 'text/html',
               'title': 'T', 'h1': ['H'], 'word_count': 300}
        if hreflang:
            row['hreflang'] = hreflang
        return row

    english = page(f'{base}/en', [{'hreflang': 'en', 'href': f'{base}/en'},
                                  {'hreflang': 'fr', 'href': f'{base}/fr'}])

    # the target was crawled and declares nothing
    found = [i for i in technical_issues([english, page(f'{base}/fr')], [], base)
             if i['check'] == 'hreflang_no_return_link']
    result('a target with no hreflang at all is reported', len(found) == 1,
           str(len(found)))

    # a mutual pair stays silent
    mutual = page(f'{base}/fr', [{'hreflang': 'fr', 'href': f'{base}/fr'},
                                 {'hreflang': 'en', 'href': f'{base}/en'}])
    result('a mutual pair is not reported',
           not [i for i in technical_issues([english, mutual], [], base)
                if i['check'] == 'hreflang_no_return_link'])

    # a target this crawl never fetched stays silent: we know nothing about it
    uncrawled = page(f'{base}/en', [{'hreflang': 'en', 'href': f'{base}/en'},
                                    {'hreflang': 'de', 'href': f'{base}/de'}])
    result('a target that was never crawled is not reported',
           not [i for i in technical_issues([uncrawled], [], base)
                if i['check'] == 'hreflang_no_return_link'])


TESTS = (
    test_a_translated_page_with_no_hreflang_at_all_is_a_one_way_pair,
    test_alternates_form_one_group_and_ca_is_a_locale_prefix,
    test_a_one_way_hreflang_groups_nothing,
    test_no_hreflang_means_no_prefixes,
    test_a_prefix_needs_its_locale_home_declared,
    test_duplicate_content_skips_alternates_but_not_same_locale_copies,
    test_duplicate_title_h1_description_skip_alternates,
    test_duplicate_title_h1_still_fire_inside_one_locale,
    test_an_anchor_pointing_at_versions_of_one_page_is_not_ambiguous,
    test_clusters_skip_the_locale_prefix,
    test_one_way_pairs_stay_inside_a_locale_section,
    test_a_locale_home_reached_by_navigation_is_not_an_orphan,
    test_linking_your_own_translation_with_your_keyword_is_not_cannibalising,
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
