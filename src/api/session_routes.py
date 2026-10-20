"""Settings, pausing, and the crawls already on disk.

Two groups that sit together because they are the same thing from a
user's point of view: what this session is configured to do, and what it
has done before. Loading a saved crawl, resuming one, archiving and
deleting all live here, as does the pause/resume pair.

Every one of these is scoped to the signed-in user. A crawl belonging to
someone else is a 403, not a 404, except where saying it exists would
leak something.

Lifted out of main.py, which had grown past this project's 1,000-line
limit. Behaviour is unchanged.
"""

import os

from flask import Blueprint, jsonify, request, session


def create_blueprint(login_required, get_or_create_crawler, get_session_settings,
                     demo_mode=False):
    """Build the blueprint. The callables belong to main.py."""
    api = Blueprint('session_routes', __name__)
    DEMO_MODE = demo_mode

    @api.route('/api/get_settings')
    @login_required
    def get_settings():
        try:
            settings_manager = get_session_settings()
            settings = settings_manager.get_settings()
            return jsonify({'success': True, 'settings': settings})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @api.route('/api/save_settings', methods=['POST'])
    @login_required
    def save_settings():
        try:
            data = request.get_json()
            settings_manager = get_session_settings()
            success, message = settings_manager.save_settings(data)
            return jsonify({'success': success, 'message': message})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @api.route('/api/reset_settings', methods=['POST'])
    @login_required
    def reset_settings():
        try:
            settings_manager = get_session_settings()
            success, message = settings_manager.reset_settings()
            return jsonify({'success': success, 'message': message})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @api.route('/api/update_crawler_settings', methods=['POST'])
    @login_required
    def update_crawler_settings():
        try:
            crawler = get_or_create_crawler()
            settings_manager = get_session_settings()
            # Get current settings and update crawler configuration
            crawler_config = settings_manager.get_crawler_config()
            crawler.update_config(crawler_config)
            return jsonify({'success': True, 'message': 'Crawler settings updated'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @api.route('/api/pause_crawl', methods=['POST'])
    @login_required
    def pause_crawl():
        try:
            crawler = get_or_create_crawler()
            success, message = crawler.pause_crawl()
            return jsonify({'success': success, 'message': message})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @api.route('/api/resume_crawl', methods=['POST'])
    @login_required
    def resume_crawl():
        try:
            crawler = get_or_create_crawler()
            success, message = crawler.resume_crawl()
            return jsonify({'success': success, 'message': message})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @api.route('/api/crawls/list')
    @login_required
    def list_crawls():
        """Get all crawls for current user"""
        try:
            user_id = session.get('user_id')
            from src.crawl_db import get_user_crawls, get_crawl_count

            limit = request.args.get('limit', 50, type=int)
            offset = request.args.get('offset', 0, type=int)
            status_filter = request.args.get('status')

            crawls = get_user_crawls(user_id, limit=limit, offset=offset, status_filter=status_filter)
            total_count = get_crawl_count(user_id)

            return jsonify({
                'success': True,
                'crawls': crawls,
                'total': total_count
            })
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @api.route('/api/crawls/<int:crawl_id>')
    @login_required
    def get_crawl(crawl_id):
        """Get complete crawl data by ID"""
        try:
            user_id = session.get('user_id')
            from src.crawl_db import get_crawl_by_id, load_crawled_urls, load_crawl_links, load_crawl_issues

            # Get crawl metadata
            crawl = get_crawl_by_id(crawl_id)
            if not crawl:
                return jsonify({'success': False, 'error': 'Crawl not found'}), 404

            # Check ownership (guests have user_id = None)
            if user_id and crawl.get('user_id') != user_id:
                return jsonify({'success': False, 'error': 'Unauthorized'}), 403

            # Load all data
            urls = load_crawled_urls(crawl_id)
            links = load_crawl_links(crawl_id)
            issues = load_crawl_issues(crawl_id)

            return jsonify({
                'success': True,
                'crawl': crawl,
                'urls': urls,
                'links': links,
                'issues': issues
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500

    @api.route('/api/crawls/<int:crawl_id>/load', methods=['POST'])
    @login_required
    def load_crawl_into_session(crawl_id):
        """Load a historical crawl into the current session"""
        try:
            user_id = session.get('user_id')
            from src.crawl_db import get_crawl_by_id, load_crawled_urls, load_crawl_links, load_crawl_issues

            # Get crawl metadata
            crawl = get_crawl_by_id(crawl_id)
            if not crawl:
                return jsonify({'success': False, 'error': 'Crawl not found'}), 404

            # Check ownership
            if user_id and crawl.get('user_id') != user_id:
                return jsonify({'success': False, 'error': 'Unauthorized'}), 403

            # Get current crawler instance
            crawler = get_or_create_crawler()

            # Stop any running crawl
            if crawler.is_running:
                crawler.stop_crawl()

            # Load all data from database
            urls = load_crawled_urls(crawl_id)
            links = load_crawl_links(crawl_id)
            issues = load_crawl_issues(crawl_id)

            # Inject into the session's crawler. This starts a new event-log
            # epoch, so any polling client resets and replays the loaded data.
            crawler.load_data(crawl, urls, links, issues)

            return jsonify({
                'success': True,
                'message': f'Loaded {len(urls)} URLs, {len(links)} links, {len(issues)} issues',
                'urls_count': len(urls),
                'links_count': len(links),
                'issues_count': len(issues),
                'should_refresh_ui': True
            })

        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500

    @api.route('/api/crawls/<int:crawl_id>/resume', methods=['POST'])
    @login_required
    def resume_crawl_endpoint(crawl_id):
        """Resume an interrupted crawl"""
        try:
            user_id = session.get('user_id')
            session_id = session.get('session_id')

            # Get crawler for this session
            crawler = get_or_create_crawler()

            # Enforce demo mode limits on resumed crawls
            if DEMO_MODE:
                crawler.config['demo_mode'] = True
                crawler.config['demo_memory_limit_bytes'] = int(1.5 * 1024 * 1024 * 1024)

            # Resume from database
            success, message = crawler.resume_from_database(crawl_id, user_id=user_id, session_id=session_id)

            if success:
                session['current_crawl_id'] = crawl_id

            return jsonify({'success': success, 'message': message})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)})

    @api.route('/api/crawls/<int:crawl_id>/delete', methods=['DELETE'])
    @login_required
    def delete_crawl_endpoint(crawl_id):
        """Delete a crawl and all associated data"""
        try:
            user_id = session.get('user_id')
            from src.crawl_db import delete_crawl, get_crawl_by_id

            # Verify ownership
            crawl = get_crawl_by_id(crawl_id)
            if not crawl:
                return jsonify({'success': False, 'error': 'Crawl not found'}), 404

            if user_id and crawl.get('user_id') != user_id:
                return jsonify({'success': False, 'error': 'Unauthorized'}), 403

            success = delete_crawl(crawl_id)
            return jsonify({'success': success, 'message': 'Crawl deleted successfully' if success else 'Failed to delete crawl'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @api.route('/api/crawls/<int:crawl_id>/archive', methods=['POST'])
    @login_required
    def archive_crawl(crawl_id):
        """Archive crawl (mark as archived but keep data)"""
        try:
            user_id = session.get('user_id')
            from src.crawl_db import set_crawl_status, get_crawl_by_id

            # Verify ownership
            crawl = get_crawl_by_id(crawl_id)
            if not crawl:
                return jsonify({'success': False, 'error': 'Crawl not found'}), 404

            if user_id and crawl.get('user_id') != user_id:
                return jsonify({'success': False, 'error': 'Unauthorized'}), 403

            success = set_crawl_status(crawl_id, 'archived')
            return jsonify({'success': success, 'message': 'Crawl archived successfully' if success else 'Failed to archive crawl'})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    @api.route('/api/crawls/stats')
    @login_required
    def crawl_stats():
        """Get statistics about user's crawls"""
        try:
            user_id = session.get('user_id')
            from src.crawl_db import get_crawl_count, get_database_size_mb
            import sqlite3

            # Get counts by status
            conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'users.db'))
            cursor = conn.cursor()

            cursor.execute('''
                SELECT status, COUNT(*) as count
                FROM crawls
                WHERE user_id = ?
                GROUP BY status
            ''', (user_id,))

            status_counts = {row[0]: row[1] for row in cursor.fetchall()}
            conn.close()

            return jsonify({
                'success': True,
                'total_crawls': get_crawl_count(user_id),
                'by_status': status_counts,
                'database_size_mb': get_database_size_mb()
            })
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    return api
