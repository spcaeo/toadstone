"""The link-graph visualisation, and what the process is doing to memory.

Two diagnostics that share a property: both are expensive enough that
they are asked for rather than computed on every request. The
visualisation walks the whole link graph, and the memory profile walks
every live crawler instance.

Lifted out of main.py, which had grown past this project's 1,000-line
limit. Behaviour is unchanged.
"""

from flask import Blueprint, jsonify


def create_blueprint(login_required, get_or_create_crawler, get_instances):
    """Build the blueprint.

    `get_instances()` returns (crawler_instances, instances_lock) from
    main.py, read live so a crawler started after mount is still seen.
    """
    api = Blueprint('diagnostics', __name__)

    @api.route('/api/visualization_data')
    @login_required
    def visualization_data():
        """Get graph data for site structure visualization"""
        try:
            crawler = get_or_create_crawler()
            status_data = crawler.get_status()

            # Get URLs from the status data
            crawled_pages = status_data.get('urls', [])
            all_links = status_data.get('links', [])

            # Build nodes and edges for the graph
            nodes = []
            edges = []
            url_to_id = {}

            # Create nodes from crawled pages (limit to prevent lag)
            max_nodes = 500  # Optimization: limit nodes for performance
            pages_to_visualize = crawled_pages[:max_nodes]

            for idx, page in enumerate(pages_to_visualize):
                url = page.get('url', '')
                status_code = page.get('status_code', 0)

                # Assign color based on status code
                if 200 <= status_code < 300:
                    color = '#10b981'  # Green for 2xx
                elif 300 <= status_code < 400:
                    color = '#3b82f6'  # Blue for 3xx
                elif 400 <= status_code < 500:
                    color = '#f59e0b'  # Orange for 4xx
                elif 500 <= status_code < 600:
                    color = '#ef4444'  # Red for 5xx
                else:
                    color = '#6b7280'  # Gray for other

                # Create node
                node = {
                    'data': {
                        'id': f'node-{idx}',
                        'label': url.split('/')[-1] or url.split('//')[-1],  # Use last path segment or domain
                        'url': url,
                        'status_code': status_code,
                        'title': page.get('title', ''),
                        'color': color,
                        'size': 30 if idx == 0 else 20,  # Make root node larger
                        'depth': page.get('depth', 0)
                    }
                }
                nodes.append(node)
                url_to_id[url] = f'node-{idx}'

            # Create edges from links data
            # Links are stored as: {'source_url': url, 'target_url': url, 'is_internal': bool, ...}
            edges_set = set()  # Use set to avoid duplicate edges
            for link in all_links:
                if link.get('is_internal'):  # Only use internal links
                    source_url = link.get('source_url', '')
                    target_url = link.get('target_url', '')

                    source_id = url_to_id.get(source_url)
                    target_id = url_to_id.get(target_url)

                    if source_id and target_id and source_id != target_id:
                        edge_key = f'{source_id}-{target_id}'
                        if edge_key not in edges_set:
                            edges_set.add(edge_key)
                            edge = {
                                'data': {
                                    'id': f'edge-{edge_key}',
                                    'source': source_id,
                                    'target': target_id
                                }
                            }
                            edges.append(edge)

            return jsonify({
                'success': True,
                'nodes': nodes,
                'edges': edges,
                'total_pages': len(crawled_pages),
                'visualized_pages': len(nodes),
                'truncated': len(crawled_pages) > max_nodes
            })

        except Exception as e:
            print(f"Error generating visualization data: {e}")
            import traceback
            traceback.print_exc()
            return jsonify({
                'success': False,
                'error': str(e),
                'nodes': [],
                'edges': []
            })

    @api.route('/api/debug/memory')
    @login_required
    def debug_memory():
        """Debug endpoint showing memory stats for all active crawler instances"""
        with get_instances()[1]:
            memory_stats = {
                'total_instances': len(get_instances()[0]),
                'instances': []
            }

            for session_id, instance_data in get_instances()[0].items():
                crawler = instance_data['crawler']
                stats = crawler.memory_monitor.get_stats()

                memory_stats['instances'].append({
                    'session_id': session_id[:8] + '...',  # Truncate for privacy
                    'last_accessed': instance_data['last_accessed'].isoformat(),
                    'urls_crawled': len(crawler.crawl_results),
                    'memory': stats,
                    'data_sizes': crawler.user_memory.get_stats()
                })

            return jsonify(memory_stats)

    @api.route('/api/debug/memory/profile')
    @login_required
    def debug_memory_profile():
        """Detailed memory profiling - what's actually using the RAM"""
        from src.core.memory_profiler import MemoryProfiler

        with get_instances()[1]:
            profiles = []

            for session_id, instance_data in get_instances()[0].items():
                crawler = instance_data['crawler']

                # Get object breakdown
                breakdown = MemoryProfiler.get_object_memory_breakdown()

                profiles.append({
                    'session_id': session_id[:8] + '...',
                    'urls_crawled': len(crawler.crawl_results),
                    'object_breakdown': breakdown,
                    'data_sizes': crawler.user_memory.get_stats()
                })

            return jsonify({
                'total_instances': len(get_instances()[0]),
                'profiles': profiles
            })

    return api
