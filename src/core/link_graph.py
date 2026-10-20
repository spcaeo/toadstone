"""The internal-link graph: structure, not a list of links.

The crawler already records every link it saw. That is a list, and a list
cannot answer the questions a site owner actually asks - which pages nothing
links to, how many clicks the homepage is from a money page, which anchor
text is pulling in two directions, whether a section's pages link back to
their hub. This module turns the link list into a directed graph and answers
those questions.

Typical use from the crawler, once a crawl has finished:

    from src.core import link_graph

    graph = link_graph.build_graph(pages, links, base_url)
    link_graph.click_depth(graph, base_url)      # writes depths onto nodes
    issues = link_graph.analyse(graph)
    summary = link_graph.cluster_summary(graph)
    nodes_csv, edges_csv = link_graph.export(graph, 'csv')

`click_depth` must run before `analyse` if the depth checks are wanted;
`analyse` calls it itself when the graph has no depths yet, so the two-line
form above is only for callers that want the depth map on its own.

WHAT IS A NODE
    One per crawled internal HTML page: `is_internal` not False and
    `content_type` empty or containing 'html'. Non-200 pages stay in the
    graph - a link to a 404 is a fact about the graph - but only 200s are
    eligible for the orphan, weak, depth and parent-link checks: a 404 has
    no inbound problem worth fixing, it has a 404 problem.

WHAT IS AN EDGE
    One per internal <a> link whose target was crawled, keeping anchor_text,
    nofollow, in_nav, in_main, in_heading and placement so a check can tell
    an editorial link from template chrome. Image links (`placement ==
    'image'`) are excluded - they are not editorial and would double every
    inbound count on an image-heavy site - and so are links to URLs that
    were never crawled, because the graph can say nothing about a page it
    never saw. Two links between the same pair are two edges, but inbound
    and outbound counts are over distinct pages, so the second one never
    inflates a score.

BOILERPLATE
    An edge is boilerplate when its placement is 'navigation' or 'footer',
    or `in_nav` is set. Boilerplate links repeat on every page of the site,
    so counting them as inbound links would make every page look well
    linked. Inbound counts used by `orphan_page` and `weak_page` therefore
    ignore boilerplate. An edge is "content" when it is not boilerplate.

CLUSTERS
    A cluster is a URL pattern, so a site owner can analyse
    /healthcare-app-development/* as a section. `cluster_patterns` accepts:

        ['/blog/*', '/services/']           patterns, named after themselves
        {'Blog': '/blog/*', 'Hub': '/s/'}   patterns with display names

    A pattern containing '*', '?' or '[' is a GLOB, matched against the
    URL's path with `fnmatch.fnmatchcase` ('/blog/*' matches '/blog/post'
    but not '/blog'); every other pattern is a PREFIX, matched with
    `path.startswith` ('/blog/' matches '/blog/post' and '/blog/x/y'). The
    first matching pattern in the given order wins, and a page matching
    nothing lands in the cluster '(none)'.

    With no patterns, clusters are derived from the first path segment:
    /blog, /blog/post-a and /blog/post-b are all cluster '/blog/', so a
    section hub sits in its own section. Only the homepage is cluster '/',
    and a top-level page with nothing under it is a cluster of one.

    On a multilingual site the first segment is often a locale, not a
    section. A leading locale prefix detected from the crawl's hreflang (see
    src/core/locales.py) is skipped before the segment is taken and kept in
    the name: /ca/blog/post is cluster '/ca/blog/', and the locale home /ca/
    is cluster '/ca/', just as the homepage is '/'.

LOCALES
    Pages that declare each other as hreflang alternates are one logical
    page: an anchor whose targets are all versions of one page is not
    ambiguous, and linking to your own translation with your keyword is not
    cannibalising. A locale home (/ca/, /ca/fr/) reached through navigation
    is treated like the homepage by the orphan and weak-page checks - the
    country switcher in the header is how a visitor is meant to get there.

URL IDENTITY
    Nodes and link targets are matched on `node_key`: tracking parameters
    stripped, scheme and fragment dropped, host lowercased and de-www'd, one
    trailing slash ignored, meaningful query parameters kept - the rules
    sitemap_audit.compare_key uses, restated here so this module stays
    standalone. A link written as http:// or with ?utm_source= is therefore
    the same edge as the clean https:// one.

GROUPING RULE
    As in src/core/sitemap_audit.py: a per-URL check reports one issue per
    URL while at most `group_threshold` URLs (default 5) are affected, and a
    single grouped issue on `base_url` above that - "31 pages are more than
    4 clicks from the homepage" - carrying the offending URLs in
    `evidence.related_urls` (capped at `max_related_urls`). A grouped issue
    uses an empty offending value, so its id stays stable while the list of
    affected URLs shrinks between crawls.

COMPLEXITY
    P = pages, L = links, N = nodes, E = edges, all of it linear; every
    function's docstring states its own bound. Nothing walks the graph per
    node and nothing compares pages pairwise, so a 5,000-page site with
    500,000 links builds, walks and analyses in one pass each.
"""

import re
from collections import defaultdict, deque
from fnmatch import fnmatchcase
from urllib.parse import urlparse

from src.core import locales
from src.core.link_graph_export import (  # noqa: F401 - part of this module's API
    EDGE_FIELDS,
    NODE_FIELDS,
    cluster_summary,
    export,
)
from src.utils.issue_factory import make_issue
from src.utils.url_params import DEFAULT_TRACKING_PARAMS, strip_tracking_params

CATEGORY = 'Links'

DEFAULTS = {
    # inbound content links below this many make a page "weak"
    'weak_inbound_threshold': 2,
    # click depth above this is a finding
    'max_click_depth': 4,
    # off-topic anchors for one URL at or above this is a finding: anchors
    # that share no word with the target's title, H1, description or URL
    'inconsistent_anchor_threshold': 3,
    # ...and only when they are at least this share of the URL's anchors
    'inconsistent_anchor_share': 0.5,
    # per-URL findings above this many URLs collapse into one grouped issue
    'group_threshold': 5,
    'max_related_urls': 50,
    # a main keyword shorter or longer than this is not a keyword: a one-word
    # H1 is a label, a ten-word H1 is a sentence
    'keyword_min_tokens': 2,
    'keyword_max_tokens': 6,
    # one-way pairs across clusters are normal site structure, not a finding
    'one_way_same_cluster_only': True,
    # checks to skip entirely, by slug
    'skip_checks': (),
}

# Anchors that tell a reader (and a search engine) nothing about the target.
GENERIC_ANCHORS = frozenset([
    'click here', 'click', 'here', 'read more', 'read', 'learn more', 'more',
    'more info', 'more information', 'find out more', 'continue reading',
    'see more', 'view more', 'details', 'this', 'this page', 'link',
    'this link', 'go', 'download', 'website', 'page', 'article', 'post',
    '(no text)',
])

# An anchor that is just the address it points at.
_BARE_URL_RE = re.compile(r'^(?:https?://|www\.)\S+$', re.I)

# Dropped when reducing an H1 or title to its main keyword.
_STOPWORDS = frozenset([
    'a', 'an', 'the', 'and', 'or', 'for', 'to', 'of', 'in', 'on', 'with',
    'your', 'our', 'their', 'my', 'we', 'you', 'is', 'are', 'was', 'be',
    'best', 'top', 'how', 'what', 'why', 'when', 'where', 'that', 'this',
    'it', 'as', 'by', 'at', 'from', 'into', 'about', 'over', 'all', 'new',
])

# Brand suffixes: everything after the last of these in a <title>.
_BRAND_SPLIT_RE = re.compile(r'\s[|–—·»-]\s')

_WORD_SPLIT_RE = re.compile(r'[^a-z0-9]+')

_TRAILING_PUNCT_RE = re.compile(r'^[\s»→>\-–—]+|[\s»→>.!…]+$')


# ---------------------------------------------------------------------------
# configuration, URL identity, text normalisation
# ---------------------------------------------------------------------------

def _config(overrides=None):
    cfg = dict(DEFAULTS)
    for key, value in (overrides or {}).items():
        if key in cfg and value is not None:
            cfg[key] = value
    cfg['skip_checks'] = frozenset(cfg['skip_checks'] or ())
    return cfg


def node_key(url, tracking_params=DEFAULT_TRACKING_PARAMS):
    """Identity used when matching a link target to a crawled page.

    Scheme, fragment, 'www.', a single trailing slash and tracking parameters
    are all ignored; meaningful query parameters are kept. O(len(url)).
    """
    if not url:
        return ''
    parsed = urlparse(strip_tracking_params(str(url).strip(), tracking_params))
    host = (parsed.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    path = parsed.path or '/'
    if len(path) > 1:
        path = path.rstrip('/') or '/'
    key = host + path
    if parsed.query:
        key += '?' + parsed.query
    return key


def _path_of(url):
    """The path of a URL, with one trailing slash removed. O(len(url))."""
    path = urlparse(str(url or '')).path or '/'
    if len(path) > 1:
        path = path.rstrip('/') or '/'
    return path


def normalise_anchor(text):
    """Anchor text reduced to its comparable form: lowercase, one space,
    leading arrows and trailing punctuation gone. O(len(text))."""
    if not text:
        return ''
    cleaned = ' '.join(str(text).split()).lower()
    return _TRAILING_PUNCT_RE.sub('', cleaned)


def is_generic_anchor(text):
    """True for 'click here', 'read more', a bare URL and friends."""
    anchor = normalise_anchor(text)
    if not anchor:
        return True
    if anchor in GENERIC_ANCHORS:
        return True
    return bool(_BARE_URL_RE.match(anchor))


def _tokens(text):
    """Content-bearing lowercase tokens of `text`. O(len(text))."""
    words = _WORD_SPLIT_RE.split(str(text or '').lower())
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS]


def _stems(text):
    """Crude word stems of `text`: the first four letters of each token, so
    'approve', 'approved' and 'approval' meet. O(len(text))."""
    return {w[:4] for w in _tokens(text)}


def main_keyword(page, cfg=None):
    """The page's own main keyword, as a tuple of tokens, or ().

    THE RULE, so a site owner can predict what the self-cannibalisation
    check will fire on:

      1. take the H1; fall back to the <title> when there is no H1,
      2. when the source is a <title>, drop the brand suffix - everything
         after the last ' | ', ' - ', ' - ' or ' . ' separator,
      3. lowercase it, split on anything that is not a letter or digit,
      4. drop stopwords and tokens of one or two characters,
      5. the remainder is the main keyword, but only when it is between
         `keyword_min_tokens` (2) and `keyword_max_tokens` (6) tokens long.
         One token is a label ('Contact'), seven are a sentence; neither is
         a keyword worth protecting.

    O(len(h1) + len(title)).
    """
    cfg = cfg or DEFAULTS
    source = (page.get('h1') or '').strip()
    if not source:
        source = (page.get('title') or '').strip()
        if source:
            parts = _BRAND_SPLIT_RE.split(source)
            if len(parts) > 1:
                source = parts[0].strip() or source
    tokens = tuple(_tokens(source))
    if cfg['keyword_min_tokens'] <= len(tokens) <= cfg['keyword_max_tokens']:
        return tokens
    return ()


def _contains_phrase(haystack, phrase):
    """True when `phrase` (a token tuple) is a contiguous run of `haystack`.

    O(len(haystack) * len(phrase)), both of them anchor-sized.
    """
    if not phrase or len(phrase) > len(haystack):
        return False
    first = phrase[0]
    span = len(phrase)
    for index, token in enumerate(haystack):
        if token == first and tuple(haystack[index:index + span]) == phrase:
            return True
    return False


# ---------------------------------------------------------------------------
# clusters
# ---------------------------------------------------------------------------

NO_CLUSTER = '(none)'


def _compile_clusters(cluster_patterns):
    """Normalise `cluster_patterns` into [(name, pattern, is_glob), ...].

    Accepts a list of patterns, a list of (name, pattern) pairs, or a dict
    {name: pattern}. O(number of patterns).
    """
    if not cluster_patterns:
        return []

    if isinstance(cluster_patterns, dict):
        pairs = list(cluster_patterns.items())
    else:
        pairs = []
        for entry in cluster_patterns:
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                pairs.append((str(entry[0]), str(entry[1])))
            else:
                pairs.append((str(entry), str(entry)))

    compiled = []
    for name, pattern in pairs:
        pattern = str(pattern or '').strip()
        if not pattern:
            continue
        compiled.append((str(name), pattern, any(c in pattern for c in '*?[')))
    return compiled


def _cluster_of(url, compiled, alternates=None):
    """The cluster a URL belongs to. O(number of patterns), or O(number of
    locale prefixes) by default (first path segment after the locale)."""
    path = _path_of(url)
    if compiled:
        for name, pattern, is_glob in compiled:
            if is_glob:
                if fnmatchcase(path, pattern) or fnmatchcase(path + '/', pattern):
                    return name
            elif path == pattern.rstrip('/') or path.startswith(pattern):
                return name
        return NO_CLUSTER

    prefix, rest = (alternates or locales.Locales()).split(path)
    prefix = prefix or '/'
    return f'{prefix}{rest[0]}/' if rest else prefix


# ---------------------------------------------------------------------------
# the graph
# ---------------------------------------------------------------------------

def _is_html_page(page):
    if page.get('is_internal') is False:
        return False
    content_type = (page.get('content_type') or '').lower()
    return not content_type or 'html' in content_type


def _is_boilerplate(edge):
    return edge['placement'] in ('navigation', 'footer') or edge['in_nav']


def build_graph(pages, links, base_url, cluster_patterns=None):
    """Build the directed internal-link graph.

    `pages` are the crawl's page dicts (url, status_code, title, h1, depth,
    content_type, is_internal, ...); `links` are the crawler's link records
    (source_url, target_url, anchor_text, is_internal, placement, nofollow,
    in_nav, in_main, in_heading). See the module docstring for which pages
    become nodes and which links become edges.

    Returns a dict:
        base_url, home_key
        nodes      {key: node}, each node carrying url, title, h1,
                   status_code, cluster, keyword, inbound/outbound counts
                   (over distinct pages), inbound_content, click_depth and
                   depth_without_nav (None until click_depth() runs)
        edges      [edge, ...] with source/target keys and urls, anchor,
                   nofollow, in_nav, in_main, in_heading, placement,
                   boilerplate, content
        inbound    {key: [edge index, ...]}
        outbound   {key: [edge index, ...]}
        clusters   {cluster name: [key, ...]}
        pairs      {(source key, target key)} - every edge, for O(1)
                   reciprocity lookups
        locales    the crawl's locales.Locales (alternate groups, locale
                   prefixes)

    Complexity: O(P + L) time, O(N + E) space. One pass over pages, one over
    links, one over edges to count; no pass is nested.
    """
    compiled = _compile_clusters(cluster_patterns)
    base_url = base_url or ''
    alternates = locales.build(pages)

    nodes = {}
    for page in pages or ():
        url = page.get('url') or ''
        if not url or not _is_html_page(page):
            continue
        key = node_key(url)
        if not key or key in nodes:
            continue
        nodes[key] = {
            'key': key,
            'url': url,
            'path': _path_of(url),
            'title': page.get('title') or '',
            'h1': page.get('h1') or '',
            'description': page.get('meta_description') or '',
            'status_code': page.get('status_code'),
            'crawl_depth': page.get('depth'),
            'cluster': _cluster_of(url, compiled, alternates),
            'alternate_group': alternates.group_of(url),
            'locale_home': alternates.is_locale_home(url),
            'keyword': main_keyword(page),
            'inbound': 0,
            'outbound': 0,
            'inbound_content': 0,
            'click_depth': None,
            'depth_without_nav': None,
        }

    edges = []
    inbound = defaultdict(list)
    outbound = defaultdict(list)
    pairs = set()

    for link in links or ():
        if link.get('is_internal') is False:
            continue
        placement = link.get('placement') or 'body'
        if placement == 'image':
            continue
        source = node_key(link.get('source_url') or '')
        target = node_key(link.get('target_url') or '')
        if not source or not target or source not in nodes or target not in nodes:
            continue

        in_nav = bool(link.get('in_nav'))
        edge = {
            'source': source,
            'target': target,
            'source_url': nodes[source]['url'],
            'target_url': nodes[target]['url'],
            'anchor_text': link.get('anchor_text') or '',
            'nofollow': bool(link.get('nofollow')),
            'in_nav': in_nav,
            'in_main': bool(link.get('in_main')),
            'in_heading': bool(link.get('in_heading')),
            'placement': placement,
            'rel': link.get('rel') or '',
        }
        edge['boilerplate'] = _is_boilerplate(edge)
        edge['content'] = not edge['boilerplate']

        index = len(edges)
        edges.append(edge)
        outbound[source].append(index)
        inbound[target].append(index)
        pairs.add((source, target))

    # Distinct-page counts: a page that links to another three times with
    # three anchors is one inbound link, not three.
    seen_in, seen_out, seen_content = set(), set(), set()
    for edge in edges:
        source, target = edge['source'], edge['target']
        if source == target:
            continue
        pair = (source, target)
        if pair not in seen_out:
            seen_out.add(pair)
            nodes[source]['outbound'] += 1
        if pair not in seen_in:
            seen_in.add(pair)
            nodes[target]['inbound'] += 1
        if edge['content'] and pair not in seen_content:
            seen_content.add(pair)
            nodes[target]['inbound_content'] += 1

    clusters = defaultdict(list)
    for key, node in nodes.items():
        clusters[node['cluster']].append(key)

    return {
        'base_url': base_url,
        'home_key': node_key(base_url),
        'nodes': nodes,
        'edges': edges,
        'inbound': dict(inbound),
        'outbound': dict(outbound),
        'clusters': dict(clusters),
        'pairs': pairs,
        'cluster_patterns': [(name, pattern) for name, pattern, _ in compiled],
        'locales': alternates,
    }


# ---------------------------------------------------------------------------
# click depth
# ---------------------------------------------------------------------------

def _home_key(graph, home_url=None):
    """The node the walk starts from: the given homepage, the graph's
    base_url, or the node whose path is '/'. O(N) worst case."""
    for candidate in (home_url, graph.get('base_url')):
        key = node_key(candidate or '')
        if key and key in graph['nodes']:
            return key
    for key, node in graph['nodes'].items():
        if node['path'] == '/':
            return key
    return None


def _bfs(graph, start, edge_filter):
    """Breadth-first distances from `start` over the edges `edge_filter`
    accepts. O(N + E)."""
    depths = {start: 0}
    queue = deque([start])
    edges, outbound = graph['edges'], graph['outbound']
    while queue:
        key = queue.popleft()
        depth = depths[key] + 1
        for index in outbound.get(key, ()):
            edge = edges[index]
            if not edge_filter(edge):
                continue
            target = edge['target']
            if target not in depths:
                depths[target] = depth
                queue.append(target)
    return depths


def click_depth(graph, home_url=None):
    """Clicks from the homepage to every page, as {url: depth}.

    NAV EDGES COUNT. A link in the header or the mega-menu is a link a
    visitor and a crawler can both follow, so ignoring it would report half
    a well-built site as buried. Footer links count too; image links are not
    in the graph at all.

    But a page reachable only through the mega-menu is weaker than one an
    article links to, so every node also gets `depth_without_nav`: the same
    walk over content edges only (nav and footer excluded). A page with
    click_depth 1 and depth_without_nav None sits in the menu and nowhere
    else.

    Both values are written onto the graph's nodes; the returned map is
    keyed by URL, for callers that want depth alone. Unreachable pages are
    absent from the map and keep click_depth None.

    Complexity: O(N + E) - two breadth-first walks, each visiting every node
    and every edge at most once.
    """
    nodes = graph['nodes']
    for node in nodes.values():
        node['click_depth'] = None
        node['depth_without_nav'] = None

    start = _home_key(graph, home_url)
    graph['home_key'] = start
    if start is None:
        return {}

    with_nav = _bfs(graph, start, lambda edge: True)
    without_nav = _bfs(graph, start, lambda edge: edge['content'])

    depths = {}
    for key, depth in with_nav.items():
        nodes[key]['click_depth'] = depth
        depths[nodes[key]['url']] = depth
    for key, depth in without_nav.items():
        nodes[key]['depth_without_nav'] = depth
    return depths


# ---------------------------------------------------------------------------
# the analysis
# ---------------------------------------------------------------------------

def _emit(issues, base_url, check, severity, name, items, group_details,
          how_to_fix, cfg):
    """One issue per URL while the list is short, one grouped issue above it.

    `items` are dicts with 'url', 'details' and optionally 'offending',
    'evidence' and 'group_urls' (the URLs a grouped issue should list, for
    checks whose findings all sit on the same URL). `group_details` is a
    format string taking {count}. O(len(items) log len(items)).
    """
    if not items or check in cfg['skip_checks']:
        return
    items = sorted(items, key=lambda item: (item['url'], item.get('offending', '')))

    if len(items) <= cfg['group_threshold']:
        for item in items:
            issues.append(make_issue(
                url=item['url'], check=check, severity=severity,
                category=CATEGORY, issue=name, details=item['details'],
                offending_value=item.get('offending', ''),
                evidence=item.get('evidence') or {},
                how_to_fix=how_to_fix,
            ))
        return

    related = []
    for item in items:
        related.extend(item.get('group_urls') or [item['url']])
    seen, unique = set(), []
    for url in related:
        if url not in seen:
            seen.add(url)
            unique.append(url)

    issues.append(make_issue(
        url=base_url, check=check, severity=severity, category=CATEGORY,
        issue=name, details=group_details.format(count=len(items)),
        offending_value='',
        evidence={'found': str(len(items)),
                  'related_urls': unique[:cfg['max_related_urls']]},
        how_to_fix=how_to_fix,
    ))


def _eligible(node):
    """A page the linking checks can sensibly complain about: crawled, 200."""
    status = node.get('status_code')
    return status is None or status == 200


def _same_page(nodes, key_a, key_b):
    """True when two nodes are hreflang alternates of each other."""
    group = nodes[key_a].get('alternate_group')
    return group is not None and group == nodes[key_b].get('alternate_group')


def _check_parent_links(graph, cfg):
    """missing_parent_link. O(N + E)."""
    nodes = graph['nodes']
    pairs = graph['pairs']
    by_path = {}
    for key, node in nodes.items():
        by_path.setdefault(node['path'], key)

    alternates = graph.get('locales') or locales.Locales()
    items = []
    for key, node in nodes.items():
        if not _eligible(node):
            continue
        path = node['path']
        # Segments below the locale: /ca/pricing is top level in its locale.
        prefix, segments = alternates.split(path)
        if len(segments) < 2:
            continue

        # The hub is the page one level up; when that was never crawled,
        # fall back to the section root (/first-segment/).
        prefix = prefix or '/'
        candidates = [prefix + '/'.join(segments[:-1]), prefix + segments[0]]
        parent_key = None
        for candidate in candidates:
            if candidate != path and candidate in by_path:
                parent_key = by_path[candidate]
                break
        if parent_key is None or (key, parent_key) in pairs:
            continue

        items.append({
            'url': node['url'],
            'details': f"This page sits under {nodes[parent_key]['path']} but "
                       f"never links back to that section hub.",
            'evidence': {'expected': nodes[parent_key]['url'],
                         'related_urls': [nodes[parent_key]['url']]},
        })
    return items


def _check_anchors(graph, cfg):
    """ambiguous_anchor, inconsistent_anchors, generic_anchor,
    self_cannibalising_anchor. O(E) plus anchor-sized token work per edge."""
    nodes = graph['nodes']
    base = graph['base_url']

    anchor_targets = defaultdict(set)      # anchor -> {target key}
    target_anchors = defaultdict(set)      # target key -> {anchor}
    generic_seen = set()                   # (source, anchor, target)
    cannibal_seen = set()
    generic, cannibal = [], []

    for edge in graph['edges']:
        if edge['source'] == edge['target']:
            continue
        anchor = normalise_anchor(edge['anchor_text'])
        generic_anchor = is_generic_anchor(edge['anchor_text'])

        if edge['content']:
            if generic_anchor:
                signature = (edge['source'], anchor, edge['target'])
                if signature not in generic_seen:
                    generic_seen.add(signature)
                    generic.append({
                        'url': edge['source_url'],
                        'offending': anchor or '(empty)',
                        'details': f"Link to {edge['target_url']} uses the "
                                   f"anchor {anchor or '(empty)'!r}, which says "
                                   f"nothing about the target.",
                        'evidence': {'snippet': edge['anchor_text'][:200],
                                     'found': anchor or '(empty)',
                                     'related_urls': [edge['target_url']]},
                    })
            else:
                anchor_targets[anchor].add(edge['target'])
                target_anchors[edge['target']].add(anchor)

            keyword = nodes[edge['source']]['keyword']
            if keyword and not _same_page(nodes, edge['source'], edge['target']) \
                    and _contains_phrase(_tokens(anchor), keyword):
                phrase = ' '.join(keyword)
                signature = (edge['source'], anchor, edge['target'])
                if signature not in cannibal_seen:
                    cannibal_seen.add(signature)
                    cannibal.append({
                        'url': edge['source_url'],
                        'offending': anchor,
                        'details': f"Anchor {anchor!r} repeats this page's own "
                                   f"main keyword ({phrase!r}) but points at "
                                   f"{edge['target_url']}.",
                        'evidence': {'snippet': edge['anchor_text'][:200],
                                     'expected': phrase,
                                     'found': anchor,
                                     'related_urls': [edge['target_url']]},
                    })

    ambiguous = []
    for anchor, targets in anchor_targets.items():
        if len(targets) < 2:
            continue
        # Versions of one page in several locales are one target.
        if len({nodes[key]['alternate_group'] or key for key in targets}) < 2:
            continue
        urls = sorted(nodes[key]['url'] for key in targets)
        ambiguous.append({
            'url': base,
            'offending': anchor,
            'details': f"The anchor {anchor!r} points at {len(urls)} different "
                       f"URLs, so no page owns it.",
            'evidence': {'found': anchor,
                         'related_urls': urls[:cfg['max_related_urls']]},
            'group_urls': urls,
        })

    # Varied anchors that all describe the target are healthy linking; the
    # problem is a page reached through words that have nothing to do with
    # it. An anchor is on-topic when it shares a word stem with the target's
    # title, H1, meta description or URL path.
    inconsistent = []
    for key, anchors in target_anchors.items():
        if len(anchors) < cfg['inconsistent_anchor_threshold']:
            continue
        node = nodes[key]
        topic = _stems(' '.join((node['title'], node['h1'],
                                 node.get('description', ''), node['path'])))
        off = sorted(a for a in anchors if not (_stems(a) & topic))
        if (len(off) < cfg['inconsistent_anchor_threshold']
                or len(off) < cfg['inconsistent_anchor_share'] * len(anchors)):
            continue
        inconsistent.append({
            'url': node['url'],
            'details': f"{len(off)} of {len(anchors)} anchors pointing at this "
                       f"page share no word with its title, H1, description "
                       f"or URL: " + '; '.join(repr(a) for a in off[:10]),
            'evidence': {'found': f'{len(off)} off-topic anchors',
                         'snippet': ' | '.join(off[:20])},
        })

    return ambiguous, inconsistent, generic, cannibal


def _check_depth(graph, cfg):
    """deep_page. O(N)."""
    items = []
    home = graph.get('home_key')
    for key, node in graph['nodes'].items():
        if key == home or not _eligible(node):
            continue
        depth = node['click_depth']
        if depth is None or depth <= cfg['max_click_depth']:
            continue
        without = node['depth_without_nav']
        items.append({
            'url': node['url'],
            'details': f"{depth} clicks from the homepage"
                       + (f" ({without} ignoring navigation links)."
                          if without is not None
                          else ", and unreachable without navigation links."),
            'evidence': {'found': str(depth),
                         'expected': str(cfg['max_click_depth'])},
        })
    return items


def _check_one_way(graph, cfg):
    """one_way_pair. O(E) - every pair is a set lookup, never a walk."""
    nodes = graph['nodes']
    pairs = graph['pairs']
    seen = set()
    items = []

    for edge in graph['edges']:
        source, target = edge['source'], edge['target']
        if source == target or not edge['content']:
            continue
        if cfg['one_way_same_cluster_only'] and \
                nodes[source]['cluster'] != nodes[target]['cluster']:
            continue
        if (target, source) in pairs:
            continue
        pair = (source, target)
        if pair in seen:
            continue
        seen.add(pair)
        items.append({
            'url': nodes[source]['url'],
            'offending': nodes[target]['url'],
            'details': f"Links to {nodes[target]['url']} from its content, and "
                       f"that page never links back - inside the same section.",
            'evidence': {'related_urls': [nodes[target]['url']]},
        })
    return items


def analyse(graph, config=None):
    """Every internal-link check, as a list of issues from `make_issue`.

    Checks, with their severities:

        sitemap_only_page         warning  no internal link at all
        orphan_page               warning  no link outside nav/footer
        weak_page                 info     fewer than N content links in
        missing_parent_link       info     never links to its section hub
        deep_page                 warning  more than N clicks from home
        ambiguous_anchor          warning  one anchor, several targets
        inconsistent_anchors      info     one target, many off-topic anchors
        generic_anchor            info     'click here' and friends
        self_cannibalising_anchor warning  own keyword, someone else's URL
        one_way_pair              info     A -> B in content, never B -> A

    A page is reported by at most one of sitemap_only_page / orphan_page /
    weak_page: they are a ladder, not three opinions of the same fact. A
    locale home with any inbound link is on none of its rungs.

    `config` overrides any key of DEFAULTS, including `skip_checks`, a list
    of slugs to leave out. Click depths are computed here when the graph has
    none yet.

    Complexity: O(N + E). Every check is a single pass over nodes or edges;
    reciprocity and parent lookups are set/dict hits, never re-walks.
    """
    cfg = _config(config)
    base = graph['base_url']
    issues = []

    if graph['nodes'] and all(node['click_depth'] is None
                              for node in graph['nodes'].values()):
        click_depth(graph)

    sitemap_only, orphans, weak = [], [], []
    home = graph.get('home_key')
    for key, node in graph['nodes'].items():
        if key == home or not _eligible(node):
            continue
        # A locale home is that locale's homepage; navigation is its route in.
        if node.get('locale_home') and node['inbound'] > 0:
            continue
        if node['inbound'] == 0:
            sitemap_only.append({
                'url': node['url'],
                'details': 'No page on the site links to this URL; it was found '
                           'through the sitemap or the seed list only.',
                'evidence': {'found': '0 internal links', 'expected': '1 or more'},
            })
        elif node['inbound_content'] == 0:
            orphans.append({
                'url': node['url'],
                'details': f"Reached only through navigation or footer links "
                           f"({node['inbound']} boilerplate, 0 from page content).",
                'evidence': {'found': '0 content links', 'expected': '1 or more'},
            })
        elif node['inbound_content'] < cfg['weak_inbound_threshold']:
            weak.append({
                'url': node['url'],
                'details': f"Only {node['inbound_content']} page links to this URL "
                           f"from its content.",
                'evidence': {'found': str(node['inbound_content']),
                             'expected': str(cfg['weak_inbound_threshold'])},
            })

    _emit(issues, base, 'sitemap_only_page', 'warning',
          'Page Reachable Only From the Sitemap', sitemap_only,
          '{count} crawled pages have no internal link pointing at them.',
          'Link to the page from a relevant page, or drop it from the sitemap.',
          cfg)
    _emit(issues, base, 'orphan_page', 'warning', 'Orphan Page', orphans,
          '{count} pages are linked only from navigation or footer boilerplate.',
          'Add a link from the body copy of a related page.', cfg)
    _emit(issues, base, 'weak_page', 'info', 'Weakly Linked Page', weak,
          '{count} pages have fewer content links in than expected.',
          'Add content links from related pages so the page earns its place.',
          cfg)

    _emit(issues, base, 'missing_parent_link', 'info',
          'Section Hub Not Linked', _check_parent_links(graph, cfg),
          '{count} pages never link back to their section hub.',
          'Link to the section hub from the page, usually in a breadcrumb.',
          cfg)

    ambiguous, inconsistent, generic, cannibal = _check_anchors(graph, cfg)
    _emit(issues, base, 'ambiguous_anchor', 'warning', 'Ambiguous Anchor Text',
          ambiguous, '{count} anchor texts each point at more than one URL.',
          'Give each target its own anchor, so one phrase means one page.', cfg)
    _emit(issues, base, 'inconsistent_anchors', 'info',
          'Inconsistent Anchor Text', inconsistent,
          '{count} pages are linked with a wide spread of anchor texts.',
          'Settle on one or two anchors per target and use them consistently.',
          cfg)
    _emit(issues, base, 'generic_anchor', 'info', 'Generic Anchor Text', generic,
          '{count} content links use a generic anchor such as "click here".',
          'Replace the anchor with words describing the target page.', cfg)
    _emit(issues, base, 'self_cannibalising_anchor', 'warning',
          'Self-Cannibalising Anchor Text', cannibal,
          '{count} links use a page\'s own main keyword to point somewhere else.',
          'Point the keyword anchor at the page that targets it, or reword it.',
          cfg)

    _emit(issues, base, 'deep_page', 'warning', 'Page Too Deep',
          _check_depth(graph, cfg),
          '{count} pages are more than the allowed number of clicks from the homepage.',
          'Link to the page from a shallower hub so it sits within a few clicks.',
          cfg)

    _emit(issues, base, 'one_way_pair', 'info', 'One-Way Internal Link',
          _check_one_way(graph, cfg),
          '{count} content links inside a section are never reciprocated.',
          'Add a link back where the two pages are genuinely related.', cfg)

    return issues
