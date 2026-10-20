"""Exports and per-cluster summaries of the internal-link graph.

Split out of src/core/link_graph.py, which re-exports every name here, so
`link_graph.export(graph)` and `link_graph.cluster_summary(graph)` keep
working. Both take the dict `link_graph.build_graph` returns.
"""

import csv
import io
import json


NODE_FIELDS = ('url', 'title', 'cluster', 'inbound', 'outbound',
               'inbound_content', 'click_depth', 'depth_without_nav',
               'status_code')

EDGE_FIELDS = ('source_url', 'target_url', 'anchor_text', 'placement',
               'nofollow', 'in_nav', 'in_main', 'in_heading')


def _node_rows(graph):
    for key in sorted(graph['nodes']):
        node = graph['nodes'][key]
        yield {field: node.get(field) for field in NODE_FIELDS}


def _edge_rows(graph):
    for edge in graph['edges']:
        yield {field: edge.get(field) for field in EDGE_FIELDS}


def export(graph, fmt='json'):
    """The graph as something a site owner can load into a graph tool.

    fmt='json' returns one JSON string: {"base_url", "nodes": [...],
    "edges": [...]}. fmt='csv' returns a (nodes_csv, edges_csv) tuple of
    strings, each with a header row. Node rows carry url, title, cluster,
    inbound, outbound, inbound_content, click_depth, depth_without_nav and
    status_code; edge rows carry source_url, target_url, anchor_text,
    placement, nofollow, in_nav, in_main, in_heading.

    Complexity: O(N log N + E) - the node sort is the only non-linear part,
    and it is there so two exports of the same crawl diff cleanly.
    """
    if fmt == 'json':
        return json.dumps({
            'base_url': graph['base_url'],
            'nodes': list(_node_rows(graph)),
            'edges': list(_edge_rows(graph)),
        }, ensure_ascii=False)

    if fmt == 'csv':
        return _csv(NODE_FIELDS, _node_rows(graph)), _csv(EDGE_FIELDS, _edge_rows(graph))

    raise ValueError(f"fmt must be 'json' or 'csv', got {fmt!r}")


def _csv(fields, rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(fields), lineterminator='\n')
    writer.writeheader()
    for row in rows:
        writer.writerow({field: ('' if row.get(field) is None else row[field])
                         for field in fields})
    return buffer.getvalue()


def cluster_summary(graph):
    """Per cluster: size, links in and out, average depth, orphan count.

    Returns a list of dicts sorted by cluster name:
        cluster, pages, links_in (edges from another cluster), links_out
        (edges to another cluster), links_internal (edges inside it),
        avg_click_depth (over the reachable pages, rounded to 2dp),
        max_click_depth, orphans (pages with no content link in),
        unreachable (pages with no click depth at all).

    Complexity: O(N + E) - one pass over nodes, one over edges.
    """
    nodes = graph['nodes']
    stats = {}
    for cluster, keys in graph['clusters'].items():
        depths = [nodes[key]['click_depth'] for key in keys
                  if nodes[key]['click_depth'] is not None]
        orphans = sum(1 for key in keys if nodes[key]['inbound_content'] == 0)
        stats[cluster] = {
            'cluster': cluster,
            'pages': len(keys),
            'links_in': 0,
            'links_out': 0,
            'links_internal': 0,
            'avg_click_depth': round(sum(depths) / len(depths), 2) if depths else None,
            'max_click_depth': max(depths) if depths else None,
            'orphans': orphans,
            'unreachable': len(keys) - len(depths),
        }

    for edge in graph['edges']:
        source_cluster = nodes[edge['source']]['cluster']
        target_cluster = nodes[edge['target']]['cluster']
        if source_cluster == target_cluster:
            stats[source_cluster]['links_internal'] += 1
        else:
            stats[source_cluster]['links_out'] += 1
            stats[target_cluster]['links_in'] += 1

    return [stats[name] for name in sorted(stats)]
