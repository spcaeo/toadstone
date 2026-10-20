"""Deterministic tests for the internal-link graph.

Runs src/core/link_graph.py against hand-built page and link fixtures, so
every check is pinned down without a crawl, a server or a network:

    python tests/link_graph_tests.py

The fixtures are the crawler's own contracts - page dicts (url, title, h1,
status_code, content_type, is_internal) and link records (source_url,
target_url, anchor_text, placement, in_nav, in_main, in_heading, nofollow).
Fixing them here keeps the checks independent of crawler behaviour, exactly
as tests/sitemap_tests.py does for the sitemap audit.

Every check has a positive case (it fires on the defect) and a negative one
(it stays quiet on the same shape without the defect), because a check that
only ever fires is as useless as one that never does. The last test builds a
2,000-page / 100,000-link graph and times the whole pipeline: the module is
linear by design and this is what says so.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import link_graph  # noqa: E402

BASE = 'https://example.com'

PASSED, FAILED = [], []


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


# ---------------------------------------------------------------------------
# fixture builders
# ---------------------------------------------------------------------------

def page(path, title='A page', h1='A page', status=200, **extra):
    """One crawled page dict. `path` is appended to BASE ('/' for home)."""
    data = {
        'url': path if path.startswith('http') else BASE + path,
        'status_code': status,
        'title': title,
        'h1': h1,
        'depth': 0,
        'content_type': 'text/html',
        'is_internal': True,
    }
    data.update(extra)
    return data


def link(source, target, anchor='a descriptive anchor', placement='body',
         in_nav=False, in_main=True, in_heading=False, nofollow=False,
         is_internal=True):
    """One link record, defaulting to an editorial body link."""
    return {
        'source_url': source if source.startswith('http') else BASE + source,
        'target_url': target if target.startswith('http') else BASE + target,
        'anchor_text': anchor,
        'is_internal': is_internal,
        'target_status': 200,
        'placement': placement,
        'rel': 'nofollow' if nofollow else '',
        'nofollow': nofollow,
        'link_target': '',
        'in_nav': in_nav,
        'in_main': in_main,
        'in_heading': in_heading,
    }


def nav_link(source, target, anchor='Services'):
    return link(source, target, anchor, placement='navigation',
                in_nav=True, in_main=False)


def footer_link(source, target, anchor='Privacy'):
    return link(source, target, anchor, placement='footer',
                in_nav=False, in_main=False)


def checks(issues):
    """The check slugs in a list of issues."""
    return [issue['check'] for issue in issues]


def by_check(issues, slug):
    return [issue for issue in issues if issue['check'] == slug]


def graph_of(pages, links, cluster_patterns=None):
    return link_graph.build_graph(pages, links, BASE, cluster_patterns)


# ---------------------------------------------------------------------------
# the graph itself
# ---------------------------------------------------------------------------

def test_graph_keeps_only_crawled_internal_html_pages():
    pages = [page('/'), page('/a'),
             page('/feed.xml', content_type='application/xml'),
             page('https://other.test/x', is_internal=False)]
    graph = graph_of(pages, [link('/', '/a')])

    keys = sorted(graph['nodes'])
    result('test_graph_keeps_only_crawled_internal_html_pages',
           keys == ['example.com/', 'example.com/a'],
           f'nodes={keys}')


def test_edges_exclude_images_and_uncrawled_targets():
    pages = [page('/'), page('/a')]
    links = [
        link('/', '/a'),
        link('/', '/logo.png', anchor='logo', placement='image', in_main=False),
        link('/', '/never-crawled'),
        link('/', 'https://other.test/x', is_internal=False),
    ]
    graph = graph_of(pages, links)

    targets = [edge['target_url'] for edge in graph['edges']]
    result('test_edges_exclude_images_and_uncrawled_targets',
           targets == [BASE + '/a'], f'edges={targets}')


def test_edge_keeps_anchor_nofollow_and_context_flags():
    graph = graph_of([page('/'), page('/a')],
                     [link('/', '/a', anchor='Widget guide', nofollow=True,
                           in_heading=True)])
    edge = graph['edges'][0]
    ok = (edge['anchor_text'] == 'Widget guide' and edge['nofollow'] is True
          and edge['in_main'] is True and edge['in_heading'] is True
          and edge['in_nav'] is False and edge['content'] is True)
    result('test_edge_keeps_anchor_nofollow_and_context_flags', ok, str(edge))


def test_repeat_links_count_once_and_boilerplate_is_separated():
    pages = [page('/'), page('/a')]
    links = [link('/', '/a', anchor='one'), link('/', '/a', anchor='two'),
             nav_link('/', '/a'), footer_link('/', '/a')]
    graph = graph_of(pages, links)
    node = graph['nodes']['example.com/a']

    ok = (len(graph['edges']) == 4 and node['inbound'] == 1
          and node['inbound_content'] == 1
          and graph['nodes']['example.com/']['outbound'] == 1)
    result('test_repeat_links_count_once_and_boilerplate_is_separated', ok,
           f"inbound={node['inbound']} content={node['inbound_content']} "
           f"edges={len(graph['edges'])}")


def test_tracking_params_and_trailing_slash_are_one_node():
    pages = [page('/'), page('/a/')]
    links = [link('/', '/a?utm_source=nav'), link('/', 'http://www.example.com/a')]
    graph = graph_of(pages, links)
    node = graph['nodes'].get('example.com/a')

    ok = (len(graph['nodes']) == 2 and node is not None
          and len(graph['edges']) == 2 and node['inbound'] == 1)
    result('test_tracking_params_and_trailing_slash_are_one_node', ok,
           f"nodes={sorted(graph['nodes'])}")


# ---------------------------------------------------------------------------
# clusters
# ---------------------------------------------------------------------------

def test_default_clusters_come_from_the_first_path_segment():
    pages = [page('/'), page('/about'), page('/blog'), page('/blog/one'),
             page('/blog/two')]
    graph = graph_of(pages, [])
    clusters = {key: node['cluster'] for key, node in graph['nodes'].items()}

    # The hub joins its own section; only the homepage is '/'.
    ok = (clusters['example.com/'] == '/'
          and clusters['example.com/about'] == '/about/'
          and clusters['example.com/blog'] == '/blog/'
          and clusters['example.com/blog/one'] == '/blog/'
          and clusters['example.com/blog/two'] == '/blog/')
    result('test_default_clusters_come_from_the_first_path_segment', ok,
           str(clusters))


def test_cluster_patterns_accept_globs_and_prefixes():
    pages = [page('/'), page('/blog'), page('/blog/one'),
             page('/services'), page('/services/seo'), page('/random')]
    graph = graph_of(pages, [], cluster_patterns={'Blog': '/blog/*',
                                                  'Services': '/services/'})
    clusters = {key: node['cluster'] for key, node in graph['nodes'].items()}

    ok = (clusters['example.com/blog'] == 'Blog'
          and clusters['example.com/blog/one'] == 'Blog'
          and clusters['example.com/services'] == 'Services'
          and clusters['example.com/services/seo'] == 'Services'
          and clusters['example.com/random'] == link_graph.NO_CLUSTER)
    result('test_cluster_patterns_accept_globs_and_prefixes', ok, str(clusters))


def test_cluster_summary_counts_pages_links_depth_and_orphans():
    pages = [page('/'), page('/blog/one'), page('/blog/two'), page('/blog/lost')]
    links = [link('/', '/blog/one'), link('/blog/one', '/blog/two'),
             link('/blog/two', '/blog/one'), nav_link('/', '/blog/lost')]
    graph = graph_of(pages, links)
    link_graph.click_depth(graph, BASE)
    summary = {row['cluster']: row for row in link_graph.cluster_summary(graph)}
    blog = summary['/blog/']

    ok = (sorted(summary) == ['/', '/blog/']
          and blog['pages'] == 3 and blog['links_internal'] == 2
          and blog['links_in'] == 2 and blog['links_out'] == 0
          and blog['orphans'] == 1
          and blog['avg_click_depth'] == round((1 + 2 + 1) / 3, 2))
    result('test_cluster_summary_counts_pages_links_depth_and_orphans', ok,
           str(blog))


# ---------------------------------------------------------------------------
# click depth
# ---------------------------------------------------------------------------

def test_click_depth_counts_nav_edges_but_records_depth_without_nav():
    pages = [page('/'), page('/a'), page('/b'), page('/menu-only')]
    links = [link('/', '/a'), link('/a', '/b'), nav_link('/', '/menu-only')]
    graph = graph_of(pages, links)
    depths = link_graph.click_depth(graph, BASE)
    nodes = graph['nodes']

    ok = (depths[BASE + '/a'] == 1 and depths[BASE + '/b'] == 2
          and depths[BASE + '/menu-only'] == 1
          and nodes['example.com/menu-only']['depth_without_nav'] is None
          and nodes['example.com/b']['depth_without_nav'] == 2)
    result('test_click_depth_counts_nav_edges_but_records_depth_without_nav',
           ok, str(depths))


def test_click_depth_leaves_unreachable_pages_out():
    pages = [page('/'), page('/a'), page('/island'), page('/islet')]
    links = [link('/', '/a'), link('/island', '/islet')]
    graph = graph_of(pages, links)
    depths = link_graph.click_depth(graph, BASE)

    ok = (BASE + '/island' not in depths
          and graph['nodes']['example.com/island']['click_depth'] is None
          and depths[BASE + '/'] == 0)
    result('test_click_depth_leaves_unreachable_pages_out', ok, str(depths))


# ---------------------------------------------------------------------------
# inbound checks: sitemap_only_page, orphan_page, weak_page
# ---------------------------------------------------------------------------

def test_orphan_page_fires_only_when_no_content_link_points_at_it():
    pages = [page('/'), page('/nav-only'), page('/linked')]
    links = [nav_link('/', '/nav-only'), link('/', '/linked')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})
    orphans = by_check(issues, 'orphan_page')

    ok = (len(orphans) == 1 and orphans[0]['url'] == BASE + '/nav-only'
          and orphans[0]['type'] == 'warning'
          and orphans[0]['category'] == 'Links')
    result('test_orphan_page_fires_only_when_no_content_link_points_at_it', ok,
           str(checks(issues)))


def test_orphan_page_stays_quiet_when_a_body_link_exists():
    pages = [page('/'), page('/a')]
    links = [nav_link('/', '/a'), link('/', '/a', anchor='widget guide')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})

    result('test_orphan_page_stays_quiet_when_a_body_link_exists',
           not by_check(issues, 'orphan_page'), str(checks(issues)))


def test_sitemap_only_page_replaces_orphan_when_nothing_links_at_all():
    pages = [page('/'), page('/a'), page('/unlinked')]
    links = [link('/', '/a')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})
    only = by_check(issues, 'sitemap_only_page')

    ok = (len(only) == 1 and only[0]['url'] == BASE + '/unlinked'
          and not by_check(issues, 'orphan_page'))
    result('test_sitemap_only_page_replaces_orphan_when_nothing_links_at_all',
           ok, str(checks(issues)))


def test_weak_page_uses_the_threshold_on_content_links():
    pages = [page('/'), page('/a'), page('/b'), page('/c')]
    # /c has two content links in, /b has one.
    links = [link('/', '/a'), link('/', '/b'), link('/', '/c'), link('/a', '/c')]
    issues = link_graph.analyse(graph_of(pages, links))
    weak = sorted(issue['url'] for issue in by_check(issues, 'weak_page'))

    relaxed = link_graph.analyse(graph_of(pages, links),
                                 {'weak_inbound_threshold': 1})
    ok = (weak == [BASE + '/a', BASE + '/b']
          and not by_check(relaxed, 'weak_page'))
    result('test_weak_page_uses_the_threshold_on_content_links', ok,
           f'weak={weak}')


def test_non_200_pages_are_not_reported_as_orphans():
    pages = [page('/'), page('/a'), page('/gone', status=404)]
    issues = link_graph.analyse(graph_of(pages, [link('/', '/a')]),
                                {'weak_inbound_threshold': 1})
    urls = [issue['url'] for issue in issues]

    result('test_non_200_pages_are_not_reported_as_orphans',
           BASE + '/gone' not in urls, str(urls))


# ---------------------------------------------------------------------------
# missing_parent_link
# ---------------------------------------------------------------------------

def test_missing_parent_link_fires_when_a_child_ignores_its_hub():
    pages = [page('/'), page('/blog'), page('/blog/post')]
    links = [link('/', '/blog'), link('/', '/blog/post'),
             link('/blog', '/blog/post')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})
    found = by_check(issues, 'missing_parent_link')

    ok = (len(found) == 1 and found[0]['url'] == BASE + '/blog/post'
          and found[0]['evidence'].get('expected') == BASE + '/blog')
    result('test_missing_parent_link_fires_when_a_child_ignores_its_hub', ok,
           str([(i['url'], i['evidence']) for i in found]))


def test_missing_parent_link_is_quiet_when_the_child_links_back():
    pages = [page('/'), page('/blog'), page('/blog/post')]
    links = [link('/', '/blog'), link('/', '/blog/post'),
             link('/blog', '/blog/post'),
             link('/blog/post', '/blog', anchor='All blog posts')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})

    result('test_missing_parent_link_is_quiet_when_the_child_links_back',
           not by_check(issues, 'missing_parent_link'), str(checks(issues)))


# ---------------------------------------------------------------------------
# anchor-text checks
# ---------------------------------------------------------------------------

def test_ambiguous_anchor_fires_when_one_phrase_points_two_ways():
    pages = [page('/'), page('/a'), page('/b'), page('/c')]
    links = [link('/', '/a', anchor='mobile app development'),
             link('/', '/b', anchor='mobile app development'),
             link('/', '/c', anchor='cloud migration guide')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})
    found = by_check(issues, 'ambiguous_anchor')

    ok = (len(found) == 1
          and found[0]['evidence']['found'] == 'mobile app development'
          and sorted(found[0]['evidence']['related_urls'])
          == [BASE + '/a', BASE + '/b'])
    result('test_ambiguous_anchor_fires_when_one_phrase_points_two_ways', ok,
           str([i['evidence'] for i in found]))


def test_ambiguous_anchor_is_quiet_when_each_phrase_owns_one_url():
    pages = [page('/'), page('/a'), page('/b')]
    links = [link('/', '/a', anchor='mobile app development'),
             link('/b', '/a', anchor='mobile app development'),
             link('/', '/b', anchor='cloud migration guide')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})

    result('test_ambiguous_anchor_is_quiet_when_each_phrase_owns_one_url',
           not by_check(issues, 'ambiguous_anchor'), str(checks(issues)))


def test_inconsistent_anchors_reports_off_topic_anchors():
    pages = [page('/'), page('/a', title='Widget guide', h1='Widget guide'),
             page('/s1'), page('/s2'), page('/s3')]
    links = [link('/', '/a', anchor='pricing plans'),
             link('/s1', '/a', anchor='contact the team'),
             link('/s2', '/a', anchor='company news'),
             link('/s3', '/a', anchor='careers page'),
             link('/', '/s1'), link('/', '/s2'), link('/', '/s3')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})
    found = by_check(issues, 'inconsistent_anchors')

    ok = (len(found) == 1 and found[0]['url'] == BASE + '/a'
          and found[0]['evidence']['found'] == '4 off-topic anchors'
          and 'pricing plans' in found[0]['evidence']['snippet'])
    result('test_inconsistent_anchors_reports_off_topic_anchors', ok,
           str([i['evidence'] for i in found]))


def test_inconsistent_anchors_is_quiet_for_varied_on_topic_anchors():
    pages = [page('/'), page('/a', title='Widget guide', h1='Widget guide'),
             page('/s1'), page('/s2'), page('/s3')]
    links = [link('/', '/a', anchor='widget guide'),
             link('/s1', '/a', anchor='the widget handbook'),
             link('/s2', '/a', anchor='everything about widgets'),
             link('/s3', '/a', anchor='guides to choosing one'),
             link('/', '/s1'), link('/', '/s2'), link('/', '/s3')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})

    result('test_inconsistent_anchors_is_quiet_for_varied_on_topic_anchors',
           not by_check(issues, 'inconsistent_anchors'), str(checks(issues)))


def test_inconsistent_anchors_is_quiet_below_the_threshold():
    pages = [page('/'), page('/a', title='Widget guide', h1='Widget guide'),
             page('/s1'), page('/s2')]
    links = [link('/', '/a', anchor='pricing plans'),
             link('/s1', '/a', anchor='contact the team'),
             link('/s2', '/a', anchor='widget reference manual'),
             link('/', '/s1'), link('/', '/s2')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})

    result('test_inconsistent_anchors_is_quiet_below_the_threshold',
           not by_check(issues, 'inconsistent_anchors'), str(checks(issues)))


def test_generic_anchor_catches_click_here_and_bare_urls():
    pages = [page('/'), page('/a'), page('/b'), page('/c')]
    links = [link('/', '/a', anchor='Click here'),
             link('/', '/b', anchor='https://example.com/b'),
             link('/', '/c', anchor='Widget sizing guide')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})
    found = sorted(issue['evidence']['found']
                   for issue in by_check(issues, 'generic_anchor'))

    result('test_generic_anchor_catches_click_here_and_bare_urls',
           found == ['click here', 'https://example.com/b'], str(found))


def test_generic_anchor_ignores_boilerplate_and_descriptive_text():
    pages = [page('/'), page('/a'), page('/b')]
    links = [nav_link('/', '/a', anchor='More'),
             footer_link('/', '/b', anchor='Read more'),
             link('/', '/a', anchor='Widget sizing guide'),
             link('/', '/b', anchor='Cloud migration checklist')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})

    result('test_generic_anchor_ignores_boilerplate_and_descriptive_text',
           not by_check(issues, 'generic_anchor'), str(checks(issues)))


def test_self_cannibalising_anchor_fires_on_the_pages_own_keyword():
    pages = [page('/'),
             page('/healthcare-app-development', h1='Healthcare App Development'),
             page('/contact', h1='Contact Us')]
    links = [link('/', '/healthcare-app-development', anchor='healthcare work'),
             link('/', '/contact', anchor='talk to the team'),
             link('/healthcare-app-development', '/contact',
                  anchor='Healthcare app development quote')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})
    found = by_check(issues, 'self_cannibalising_anchor')

    ok = (len(found) == 1
          and found[0]['url'] == BASE + '/healthcare-app-development'
          and found[0]['evidence']['expected'] == 'healthcare app development'
          and found[0]['evidence']['related_urls'] == [BASE + '/contact'])
    result('test_self_cannibalising_anchor_fires_on_the_pages_own_keyword', ok,
           str([i['evidence'] for i in found]))


def test_self_cannibalising_anchor_is_quiet_for_other_phrases():
    pages = [page('/'),
             page('/healthcare-app-development', h1='Healthcare App Development'),
             page('/contact', h1='Contact Us')]
    links = [link('/', '/healthcare-app-development', anchor='healthcare work'),
             link('/', '/contact', anchor='talk to the team'),
             link('/healthcare-app-development', '/contact',
                  anchor='book a discovery call')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})

    result('test_self_cannibalising_anchor_is_quiet_for_other_phrases',
           not by_check(issues, 'self_cannibalising_anchor'), str(checks(issues)))


def test_main_keyword_uses_h1_then_title_and_rejects_labels_and_sentences():
    from_h1 = link_graph.main_keyword({'h1': 'Healthcare App Development',
                                       'title': 'Ignored | Brand'})
    from_title = link_graph.main_keyword(
        {'h1': '', 'title': 'Custom CRM Software | Space-O Technologies'})
    label = link_graph.main_keyword({'h1': 'Contact', 'title': ''})
    sentence = link_graph.main_keyword(
        {'h1': 'Everything your growing clinic needs to know before choosing '
               'a custom mobile platform', 'title': ''})

    ok = (from_h1 == ('healthcare', 'app', 'development')
          and from_title == ('custom', 'crm', 'software')
          and label == () and sentence == ())
    result('test_main_keyword_uses_h1_then_title_and_rejects_labels_and_sentences',
           ok, f'{from_h1} {from_title} {label} {sentence}')


# ---------------------------------------------------------------------------
# depth and reciprocity
# ---------------------------------------------------------------------------

def test_deep_page_fires_past_the_configured_depth():
    pages = [page('/')] + [page(f'/step{n}') for n in range(1, 6)]
    links = [link('/', '/step1')] + [link(f'/step{n}', f'/step{n + 1}')
                                     for n in range(1, 5)]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})
    deep = [issue['url'] for issue in by_check(issues, 'deep_page')]

    shallow = link_graph.analyse(graph_of(pages, links),
                                 {'weak_inbound_threshold': 1,
                                  'max_click_depth': 10})
    ok = deep == [BASE + '/step5'] and not by_check(shallow, 'deep_page')
    result('test_deep_page_fires_past_the_configured_depth', ok, str(deep))


def test_one_way_pair_fires_inside_a_cluster_only():
    pages = [page('/'), page('/blog/x'), page('/blog/y'), page('/svc/z')]
    links = [link('/', '/blog/x'), link('/', '/blog/y'), link('/', '/svc/z'),
             link('/blog/x', '/blog/y', anchor='the widget essay'),
             link('/blog/x', '/svc/z', anchor='our widget service')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})
    found = by_check(issues, 'one_way_pair')

    ok = (len(found) == 1 and found[0]['url'] == BASE + '/blog/x'
          and found[0]['evidence']['related_urls'] == [BASE + '/blog/y'])
    result('test_one_way_pair_fires_inside_a_cluster_only', ok,
           str([(i['url'], i['evidence']) for i in found]))


def test_one_way_pair_is_quiet_when_the_link_is_returned():
    pages = [page('/'), page('/blog/x'), page('/blog/y')]
    links = [link('/', '/blog/x'), link('/', '/blog/y'),
             link('/blog/x', '/blog/y', anchor='the widget essay'),
             link('/blog/y', '/blog/x', anchor='the gadget essay')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})

    result('test_one_way_pair_is_quiet_when_the_link_is_returned',
           not by_check(issues, 'one_way_pair'), str(checks(issues)))


# ---------------------------------------------------------------------------
# grouping, configuration and export
# ---------------------------------------------------------------------------

def test_findings_above_five_urls_collapse_into_one_grouped_issue():
    pages = [page('/')] + [page(f'/nav{n}') for n in range(6)]
    links = [nav_link('/', f'/nav{n}') for n in range(6)]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})
    orphans = by_check(issues, 'orphan_page')

    ok = (len(orphans) == 1 and orphans[0]['url'] == BASE
          and orphans[0]['evidence']['found'] == '6'
          and len(orphans[0]['evidence']['related_urls']) == 6)
    result('test_findings_above_five_urls_collapse_into_one_grouped_issue', ok,
           str(orphans and orphans[0]['evidence']))


def test_five_urls_stay_as_individual_issues():
    pages = [page('/')] + [page(f'/nav{n}') for n in range(5)]
    links = [nav_link('/', f'/nav{n}') for n in range(5)]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1})
    orphans = by_check(issues, 'orphan_page')

    ok = (len(orphans) == 5
          and sorted(i['url'] for i in orphans)
          == sorted(BASE + f'/nav{n}' for n in range(5)))
    result('test_five_urls_stay_as_individual_issues', ok,
           str(len(orphans)))


def test_skip_checks_leaves_a_check_out():
    pages = [page('/'), page('/nav-only')]
    links = [nav_link('/', '/nav-only')]
    issues = link_graph.analyse(graph_of(pages, links),
                                {'weak_inbound_threshold': 1,
                                 'skip_checks': ['orphan_page']})

    result('test_skip_checks_leaves_a_check_out',
           not by_check(issues, 'orphan_page'), str(checks(issues)))


def test_json_export_carries_nodes_and_edges():
    pages = [page('/', title='Home'), page('/blog/one', title='One')]
    links = [link('/', '/blog/one', anchor='the first post')]
    graph = graph_of(pages, links)
    link_graph.click_depth(graph, BASE)
    data = json.loads(link_graph.export(graph, 'json'))

    node = [n for n in data['nodes'] if n['url'].endswith('/blog/one')][0]
    edge = data['edges'][0]
    ok = (data['base_url'] == BASE and len(data['nodes']) == 2
          and node['title'] == 'One' and node['cluster'] == '/blog/'
          and node['inbound'] == 1 and node['outbound'] == 0
          and node['click_depth'] == 1 and node['status_code'] == 200
          and edge['anchor_text'] == 'the first post'
          and edge['source_url'] == BASE + '/' and edge['nofollow'] is False)
    result('test_json_export_carries_nodes_and_edges', ok,
           json.dumps({'node': node, 'edge': edge}))


def test_csv_export_returns_two_tables_with_headers():
    pages = [page('/', title='Home'), page('/blog/one', title='One')]
    links = [link('/', '/blog/one', anchor='the first post')]
    graph = graph_of(pages, links)
    link_graph.click_depth(graph, BASE)
    nodes_csv, edges_csv = link_graph.export(graph, 'csv')

    node_lines = nodes_csv.strip().split('\n')
    edge_lines = edges_csv.strip().split('\n')
    ok = (node_lines[0] == ','.join(link_graph.NODE_FIELDS)
          and len(node_lines) == 3
          and edge_lines[0] == ','.join(link_graph.EDGE_FIELDS)
          and len(edge_lines) == 2
          and 'the first post' in edge_lines[1]
          and node_lines[1].startswith(BASE + '/,Home,/,'))
    result('test_csv_export_returns_two_tables_with_headers', ok,
           f'{node_lines[:2]} | {edge_lines}')


def test_export_rejects_an_unknown_format():
    graph = graph_of([page('/')], [])
    try:
        link_graph.export(graph, 'graphml')
        ok, detail = False, 'no ValueError raised'
    except ValueError as exc:
        ok, detail = True, str(exc)
    result('test_export_rejects_an_unknown_format', ok, detail)


def test_an_empty_crawl_produces_no_issues_and_empty_exports():
    graph = graph_of([], [])
    issues = link_graph.analyse(graph)
    nodes_csv, edges_csv = link_graph.export(graph, 'csv')

    ok = (issues == [] and link_graph.cluster_summary(graph) == []
          and nodes_csv.strip() == ','.join(link_graph.NODE_FIELDS)
          and edges_csv.strip() == ','.join(link_graph.EDGE_FIELDS))
    result('test_an_empty_crawl_produces_no_issues_and_empty_exports', ok,
           str(issues))


# ---------------------------------------------------------------------------
# performance guard
# ---------------------------------------------------------------------------

def test_a_2000_page_100k_link_graph_is_analysed_in_seconds():
    """The whole pipeline on a mid-size site, timed.

    2,000 pages across 20 clusters, 50 outbound links each = 100,000 links.
    Everything in the module is linear, so this must finish in well under a
    few seconds; a quadratic pass would take minutes here and the crawler
    would appear to hang at the end of every crawl.
    """
    page_count, per_page = 2000, 50
    pages = [page('/', title='Home', h1='Widget Company')]
    for n in range(page_count):
        pages.append(page(f'/c{n % 20}/page-{n}', title=f'Page {n}',
                          h1=f'Widget Model {n}'))

    links = [link('/', f'/c{n % 20}/page-{n}', anchor=f'model {n} overview')
             for n in range(0, page_count, 10)]
    for n in range(page_count):
        source = f'/c{n % 20}/page-{n}'
        for step in range(per_page):
            target = (n * 7 + step * 13 + 1) % page_count
            links.append(link(source, f'/c{target % 20}/page-{target}',
                              anchor=f'topic {step} of model {target}'))

    started = time.perf_counter()
    graph = link_graph.build_graph(pages, links, BASE)
    built = time.perf_counter() - started

    started = time.perf_counter()
    link_graph.click_depth(graph, BASE)
    walked = time.perf_counter() - started

    started = time.perf_counter()
    issues = link_graph.analyse(graph)
    analysed = time.perf_counter() - started

    started = time.perf_counter()
    summary = link_graph.cluster_summary(graph)
    link_graph.export(graph, 'json')
    link_graph.export(graph, 'csv')
    exported = time.perf_counter() - started

    total = built + walked + analysed + exported
    fired = sorted(set(checks(issues)))
    detail = (f'{len(graph["nodes"])} nodes, {len(graph["edges"])} edges, '
              f'{len(issues)} issues {fired}, {len(summary)} clusters | '
              f'build {built:.2f}s, depth {walked:.3f}s, '
              f'analyse {analysed:.2f}s, export {exported:.2f}s, '
              f'total {total:.2f}s')

    # The checks really ran on this graph rather than bailing out early:
    # thousands of in-cluster links are never reciprocated, which collapses
    # into one grouped issue. Every target collects dozens of anchors, but
    # they all name its model, so the anchor check stays quiet.
    ok = (len(graph['nodes']) == page_count + 1
          and len(graph['edges']) == len(links)
          and fired == ['one_way_pair']
          and analysed < 5.0 and total < 20.0)
    result('test_a_2000_page_100k_link_graph_is_analysed_in_seconds', ok, detail)


TESTS = (
    test_graph_keeps_only_crawled_internal_html_pages,
    test_edges_exclude_images_and_uncrawled_targets,
    test_edge_keeps_anchor_nofollow_and_context_flags,
    test_repeat_links_count_once_and_boilerplate_is_separated,
    test_tracking_params_and_trailing_slash_are_one_node,
    test_default_clusters_come_from_the_first_path_segment,
    test_cluster_patterns_accept_globs_and_prefixes,
    test_cluster_summary_counts_pages_links_depth_and_orphans,
    test_click_depth_counts_nav_edges_but_records_depth_without_nav,
    test_click_depth_leaves_unreachable_pages_out,
    test_orphan_page_fires_only_when_no_content_link_points_at_it,
    test_orphan_page_stays_quiet_when_a_body_link_exists,
    test_sitemap_only_page_replaces_orphan_when_nothing_links_at_all,
    test_weak_page_uses_the_threshold_on_content_links,
    test_non_200_pages_are_not_reported_as_orphans,
    test_missing_parent_link_fires_when_a_child_ignores_its_hub,
    test_missing_parent_link_is_quiet_when_the_child_links_back,
    test_ambiguous_anchor_fires_when_one_phrase_points_two_ways,
    test_ambiguous_anchor_is_quiet_when_each_phrase_owns_one_url,
    test_inconsistent_anchors_reports_off_topic_anchors,
    test_inconsistent_anchors_is_quiet_for_varied_on_topic_anchors,
    test_inconsistent_anchors_is_quiet_below_the_threshold,
    test_generic_anchor_catches_click_here_and_bare_urls,
    test_generic_anchor_ignores_boilerplate_and_descriptive_text,
    test_self_cannibalising_anchor_fires_on_the_pages_own_keyword,
    test_self_cannibalising_anchor_is_quiet_for_other_phrases,
    test_main_keyword_uses_h1_then_title_and_rejects_labels_and_sentences,
    test_deep_page_fires_past_the_configured_depth,
    test_one_way_pair_fires_inside_a_cluster_only,
    test_one_way_pair_is_quiet_when_the_link_is_returned,
    test_findings_above_five_urls_collapse_into_one_grouped_issue,
    test_five_urls_stay_as_individual_issues,
    test_skip_checks_leaves_a_check_out,
    test_json_export_carries_nodes_and_edges,
    test_csv_export_returns_two_tables_with_headers,
    test_export_rejects_an_unknown_format,
    test_an_empty_crawl_produces_no_issues_and_empty_exports,
    test_a_2000_page_100k_link_graph_is_analysed_in_seconds,
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
