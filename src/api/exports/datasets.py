"""The datasets a crawl can be exported as, and the filters that narrow them.

Six are stored - ``issues``, ``pages``, ``links``, ``graph-nodes``,
``graph-edges``, ``sitemap-files`` - and six are views derived at request time
from those same rows, so nothing new is written to the database:
``sitemap-coverage``, ``duplicates``, ``redirects``, ``broken-links``,
``headings``, ``images``. ``DATASETS`` at the foot of this module is the
catalogue: every dataset's columns, and where each one comes from.
"""

import json

from flask import g, session

from src import crawl_db

ISSUE_FIELDS = ('id', 'crawl_id', 'url', 'check', 'type', 'category', 'issue',
                'details', 'evidence', 'how_to_fix', 'docs_url',
                'first_seen_crawl', 'status', 'accepted_reason', 'detected_at')

PAGE_FIELDS = ('url', 'status_code', 'content_type', 'title', 'meta_description',
               'h1', 'canonical_url', 'robots', 'lang', 'word_count',
               'main_word_count', 'main_text_hash', 'depth', 'is_internal',
               'internal_links', 'external_links', 'size', 'response_time',
               'ttfb', 'error_type', 'crawled_at')

LINK_FIELDS = ('source_url', 'target_url', 'anchor_text', 'is_internal',
               'target_domain', 'target_status', 'placement', 'rel', 'nofollow',
               'link_target', 'in_nav', 'in_main', 'in_heading')

GRAPH_NODE_FIELDS = ('url', 'title', 'cluster', 'inbound', 'outbound',
                     'inbound_content', 'click_depth', 'depth_without_nav',
                     'status_code')

GRAPH_EDGE_FIELDS = ('source_url', 'target_url', 'anchor_text', 'placement',
                     'nofollow', 'in_nav', 'in_main', 'in_heading')

SITEMAP_FILE_FIELDS = ('url', 'parent', 'status', 'entries', 'bytes', 'is_index',
                       'error', 'error_kind')

COVERAGE_FIELDS = ('url', 'state')

DUPLICATE_FIELDS = ('group', 'group_size', 'text_hash', 'url', 'title',
                    'status_code', 'word_count', 'canonical_url')

REDIRECT_FIELDS = ('url', 'hop', 'chain_length', 'from_url', 'status_code',
                   'to_url', 'final_status')

BROKEN_LINK_FIELDS = ('source_url', 'target_url', 'anchor_text', 'status_code',
                      'error_type', 'is_internal', 'placement', 'rel', 'nofollow',
                      'in_nav', 'in_main', 'in_heading')

HEADING_FIELDS = ('url', 'dom_order', 'level', 'text', 'is_visible', 'problem')

IMAGE_FIELDS = ('url', 'src', 'alt', 'has_alt', 'width', 'height')


# ---------------------------------------------------------------------------
# loaders - one per dataset, each returning rows of plain dicts
# ---------------------------------------------------------------------------

def load_issues(crawl_id, crawl):
    """Stored issues, with this user's acceptances folded back in."""
    rows = crawl_db.load_crawl_issues(crawl_id)
    accepted = crawl_db.get_accepted_issues(session.get('user_id'),
                                            crawl.get('base_domain'))
    for row in rows:
        if row.get('id') in accepted:
            row['status'] = 'accepted'
            row['accepted_reason'] = accepted[row['id']].get('reason')
    order = {'error': 0, 'warning': 1, 'info': 2}
    rows.sort(key=lambda r: (order.get(r.get('type'), 3), r.get('check') or '',
                             r.get('url') or ''))
    return rows


def _load_pages(crawl_id, crawl):
    rows = crawl_db.load_crawled_urls(crawl_id)
    rows.sort(key=lambda r: r.get('url') or '')
    return rows


def _load_links(crawl_id, crawl):
    rows = crawl_db.load_crawl_links(crawl_id)
    rows.sort(key=lambda r: ((r.get('source_url') or ''), (r.get('target_url') or '')))
    return rows


def _graph(crawl_id, crawl):
    """The exported graph, built once per request.

    `export/all` asks for the nodes and then the edges; building the graph
    twice would walk every page and every link twice for one download.
    """
    cache = getattr(g, '_export_graphs', None)
    if cache is None:
        cache = g._export_graphs = {}
    if crawl_id in cache:
        return cache[crawl_id]

    from src.core import link_graph
    pages = crawl_db.load_crawled_urls(crawl_id)
    links = crawl_db.load_crawl_links(crawl_id)
    graph = link_graph.build_graph(pages, links, crawl.get('base_url') or '')
    link_graph.click_depth(graph, crawl.get('base_url'))
    payload = link_graph.export(graph, fmt='json')
    cache[crawl_id] = json.loads(payload) if isinstance(payload, str) else payload
    return cache[crawl_id]


def _load_graph_nodes(crawl_id, crawl):
    return _graph(crawl_id, crawl).get('nodes') or []


def _load_graph_edges(crawl_id, crawl):
    return _graph(crawl_id, crawl).get('edges') or []


def _load_sitemap_files(crawl_id, crawl):
    files = crawl.get('sitemap_files') or []
    return files if isinstance(files, list) else []


def _load_sitemap_coverage(crawl_id, crawl):
    """The three coverage lists as (url, state) rows, one row per URL."""
    coverage = crawl.get('sitemap_coverage') or {}
    if not isinstance(coverage, dict):
        return []
    rows = []
    for state in ('in_both', 'crawled_only', 'sitemap_only'):
        for url in coverage.get(state) or []:
            rows.append({'url': url, 'state': state})
    return rows


def _load_duplicates(crawl_id, crawl):
    """Pages sharing a main-text hash, grouped. Derived from `pages`."""
    groups = {}
    for page in crawl_db.load_crawled_urls(crawl_id):
        digest = (page.get('main_text_hash') or '').strip()
        if digest:
            groups.setdefault(digest, []).append(page)

    rows = []
    shared = sorted(digest for digest, pages in groups.items() if len(pages) > 1)
    for number, digest in enumerate(shared, 1):
        members = sorted(groups[digest], key=lambda p: p.get('url') or '')
        for page in members:
            rows.append({
                'group': number,
                'group_size': len(members),
                'text_hash': digest,
                'url': page.get('url'),
                'title': page.get('title'),
                'status_code': page.get('status_code'),
                'word_count': page.get('word_count'),
                'canonical_url': page.get('canonical_url'),
            })
    return rows


def _load_redirects(crawl_id, crawl):
    """One row per redirect hop. Derived from each page's stored chain."""
    rows = []
    for page in _load_pages(crawl_id, crawl):
        url = page.get('url') or ''
        final_status = page.get('status_code')
        chain = page.get('redirects')
        chain = chain if isinstance(chain, list) else []

        if not chain:
            if _is_redirect(final_status):
                rows.append({'url': url, 'hop': 1, 'chain_length': 1,
                             'from_url': url, 'status_code': final_status,
                             'to_url': '', 'final_status': final_status})
            continue

        for index, hop in enumerate(chain, 1):
            if not isinstance(hop, dict):
                continue
            following = chain[index] if index < len(chain) else None
            target = (following or {}).get('url') if isinstance(following, dict) else None
            rows.append({
                'url': url,
                'hop': index,
                'chain_length': len(chain),
                'from_url': hop.get('url'),
                'status_code': hop.get('status_code'),
                'to_url': target or url,
                'final_status': final_status,
            })
    return rows


def _load_broken_links(crawl_id, crawl):
    """Links whose target failed. Derived from `links` joined to `pages`."""
    pages = {page.get('url'): page for page in crawl_db.load_crawled_urls(crawl_id)}
    rows = []
    for link in _load_links(crawl_id, crawl):
        page = pages.get(link.get('target_url')) or {}
        status = link.get('target_status')
        if status in (None, ''):
            status = page.get('status_code')
        if not _is_broken(status):
            continue
        row = {field: link.get(field) for field in BROKEN_LINK_FIELDS}
        row['status_code'] = status
        row['error_type'] = page.get('error_type')
        rows.append(row)
    return rows


def _load_headings(crawl_id, crawl):
    """One row per heading, with any text problem found on it. From `pages`."""
    rows = []
    for page in _load_pages(crawl_id, crawl):
        url = page.get('url')
        problems = {}
        for problem in page.get('heading_text_issues') or []:
            if isinstance(problem, dict):
                problems.setdefault(problem.get('dom_order'), []).append(
                    str(problem.get('problem') or ''))
        for heading in page.get('headings') or []:
            if not isinstance(heading, dict):
                continue
            rows.append({
                'url': url,
                'dom_order': heading.get('dom_order'),
                'level': heading.get('level'),
                'text': heading.get('text'),
                'is_visible': heading.get('is_visible'),
                'problem': '; '.join(problems.get(heading.get('dom_order'), [])),
            })
    return rows


def _load_images(crawl_id, crawl):
    """One row per <img> found, with the page that carries it. From `pages`."""
    rows = []
    for page in _load_pages(crawl_id, crawl):
        url = page.get('url')
        for image in page.get('images') or []:
            if not isinstance(image, dict):
                continue
            rows.append({
                'url': url,
                'src': image.get('src'),
                'alt': image.get('alt'),
                'has_alt': image.get('has_alt'),
                'width': image.get('width'),
                'height': image.get('height'),
            })
    return rows


# ---------------------------------------------------------------------------
# the catalogue
# ---------------------------------------------------------------------------

DATASETS = {
    'issues': {
        'fields': ISSUE_FIELDS, 'url_field': 'url', 'loader': load_issues,
        'issues': True, 'stored': True,
        'about': 'Every issue this crawl reported, errors first. Accepted '
                 'issues are left out unless include_accepted=true.',
    },
    'pages': {
        'fields': PAGE_FIELDS, 'url_field': 'url', 'loader': _load_pages,
        'stored': True,
        'about': 'One row per crawled URL. Pass fields= for any other stored '
                 'column (og_tags, hreflang, schema_org, ...).',
    },
    'links': {
        'fields': LINK_FIELDS, 'url_field': 'source_url', 'loader': _load_links,
        'stored': True,
        'about': 'Every link found, with its anchor, rel and where in the page '
                 'it sits.',
    },
    'graph-nodes': {
        'fields': GRAPH_NODE_FIELDS, 'url_field': 'url', 'loader': _load_graph_nodes,
        'stored': True,
        'about': 'Internal link graph nodes: inbound/outbound counts and click '
                 'depth, ready for Gephi.',
    },
    'graph-edges': {
        'fields': GRAPH_EDGE_FIELDS, 'url_field': 'source_url',
        'loader': _load_graph_edges, 'stored': True,
        'about': 'Internal link graph edges, one row per followed internal link.',
    },
    'sitemap-files': {
        'fields': SITEMAP_FILE_FIELDS, 'url_field': 'url',
        'loader': _load_sitemap_files, 'stored': True,
        'about': 'Per sitemap file: status, entry count, size and any parse error.',
    },
    'sitemap-coverage': {
        'fields': COVERAGE_FIELDS, 'url_field': 'url',
        'loader': _load_sitemap_coverage,
        'about': 'Derived from the stored coverage lists: one row per URL, '
                 'state being in_both, crawled_only or sitemap_only.',
    },
    'duplicates': {
        'fields': DUPLICATE_FIELDS, 'url_field': 'url', 'loader': _load_duplicates,
        'about': 'Derived from pages: URLs sharing a main-text hash, numbered '
                 'by group so a spreadsheet sorts them together.',
    },
    'redirects': {
        'fields': REDIRECT_FIELDS, 'url_field': 'url', 'loader': _load_redirects,
        'about': 'Derived from each page\'s stored redirect chain: one row per '
                 'hop, plus any page that answered 3xx with no chain recorded.',
    },
    'broken-links': {
        'fields': BROKEN_LINK_FIELDS, 'url_field': 'source_url',
        'loader': _load_broken_links,
        'about': 'Derived from links joined to pages: links whose target '
                 'answered 4xx, 5xx, or did not answer at all.',
    },
    'headings': {
        'fields': HEADING_FIELDS, 'url_field': 'url', 'loader': _load_headings,
        'about': 'Derived from pages: one row per heading in DOM order, with '
                 'its level, visibility and any heading text problem.',
    },
    'images': {
        'fields': IMAGE_FIELDS, 'url_field': 'url', 'loader': _load_images,
        'about': 'Derived from pages: one row per <img>, with alt text and '
                 'whether the alt attribute was present at all.',
    },
}


# ---------------------------------------------------------------------------
# filtering - the same names the reporting API uses
# ---------------------------------------------------------------------------

def filtered(rows, spec, args):
    """Apply whichever filters this dataset's rows can answer.

    A filter is skipped for a dataset whose rows have no such column, so
    ?severity=error on the pages export is ignored rather than emptying it.
    """
    url_field = spec['url_field']
    prefix = args.get('url_prefix')
    status = args.get('status_code', type=int)
    internal = _flag(args.get('internal'))
    severity = _values(args.get('severity'))
    checks = _values(args.get('check'))
    categories = _values(args.get('category'))
    states = _values(args.get('status')) | _values(args.get('state'))
    status_classes = _values(args.get('status_class'))
    content_types = _values(args.get('content_type'))
    include_accepted = _flag(args.get('include_accepted')) is True
    is_issues = spec.get('issues', False)

    for row in rows:
        if prefix and not str(row.get(url_field) or '').startswith(prefix):
            continue
        if status is not None and 'status_code' in row and row.get('status_code') != status:
            continue
        if internal is not None and 'is_internal' in row \
                and bool(row.get('is_internal')) is not internal:
            continue
        if severity and 'type' in row and row.get('type') not in severity:
            continue
        if checks and 'check' in row and (row.get('check') or '') not in checks:
            continue
        if categories and 'category' in row and (row.get('category') or '') not in categories:
            continue
        if status_classes and 'status_code' in row \
                and _status_class(row.get('status_code')) not in status_classes:
            continue
        if content_types and 'content_type' in row \
                and _content_kind(row.get('content_type')) not in content_types:
            continue
        # 'state' is what the coverage rows call it; issues call theirs 'status'
        if states:
            column = 'status' if 'status' in row else ('state' if 'state' in row else None)
            if column and (row.get(column) or '') not in states:
                continue
        if is_issues and not include_accepted and row.get('status') == 'accepted':
            continue
        yield row


def _status_class(status_code):
    """200 -> '2xx'; anything unanswered -> 'no_response', as the UI's rail calls it."""
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return 'no_response'
    if code <= 0:
        return 'no_response'
    return f'{code // 100}xx'


# The content-type buckets the filter rail offers
_CONTENT_KINDS = (
    ('html', ('text/html', 'application/xhtml')),
    ('css', ('text/css',)),
    ('js', ('javascript', 'ecmascript')),
    ('images', ('image/',)),
    ('pdf', ('application/pdf',)),
)


def _content_kind(content_type):
    lowered = (content_type or '').lower()
    for kind, needles in _CONTENT_KINDS:
        if any(needle in lowered for needle in needles):
            return kind
    return 'other'


def _values(raw):
    return {v.strip() for v in (raw or '').split(',') if v.strip()}


def _flag(raw):
    if raw is None:
        return None
    return str(raw).lower() in ('1', 'true', 'yes', 'on')


def _is_redirect(status):
    try:
        return 300 <= int(status) < 400
    except (TypeError, ValueError):
        return False


def _is_broken(status):
    try:
        code = int(status)
    except (TypeError, ValueError):
        return False
    return code == 0 or code >= 400
