"""
Crawl data persistence module
Handles database operations for storing and retrieving crawl data
Enables crash recovery and historical crawl access
"""
import sqlite3
import json
import time
from datetime import datetime
from contextlib import contextmanager

# Database file location (same as auth database) - stored in data/ for Docker volume persistence
import os
DB_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'users.db')

@contextmanager
def get_db():
    """Context manager for database connections"""
    # Wait for a busy database instead of failing immediately: the crawler
    # thread writes batches while request handlers read status and settings,
    # and sqlite's default 5s was not enough during large end-of-crawl saves.
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row  # Return rows as dictionaries
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

# The schema and its migrations live next door; this file is queries.
from src.crawl_schema import init_crawl_tables  # noqa: F401

def create_crawl(user_id, session_id, base_url, base_domain, config_snapshot):
    """
    Create a new crawl record
    Returns the crawl_id
    """
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO crawls (user_id, session_id, base_url, base_domain, config_snapshot, status)
                VALUES (?, ?, ?, ?, ?, 'running')
            ''', (user_id, session_id, base_url, base_domain, json.dumps(config_snapshot)))

            crawl_id = cursor.lastrowid
            print(f"Created new crawl record: ID={crawl_id}, URL={base_url}")
            return crawl_id
    except Exception as e:
        print(f"Error creating crawl: {e}")
        return None

def update_crawl_stats(crawl_id, discovered=None, crawled=None, max_depth=None, peak_memory_mb=None, estimated_size_mb=None):
    """Update crawl statistics"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()

            updates = []
            params = []

            if discovered is not None:
                updates.append("urls_discovered = ?")
                params.append(discovered)
            if crawled is not None:
                updates.append("urls_crawled = ?")
                params.append(crawled)
            if max_depth is not None:
                updates.append("max_depth_reached = ?")
                params.append(max_depth)
            if peak_memory_mb is not None:
                updates.append("peak_memory_mb = ?")
                params.append(peak_memory_mb)
            if estimated_size_mb is not None:
                updates.append("estimated_size_mb = ?")
                params.append(estimated_size_mb)

            updates.append("last_saved_at = CURRENT_TIMESTAMP")
            params.append(crawl_id)

            query = f"UPDATE crawls SET {', '.join(updates)} WHERE id = ?"
            cursor.execute(query, params)

            return True
    except Exception as e:
        print(f"Error updating crawl stats: {e}")
        return False

def save_url_batch(crawl_id, urls):
    """
    Batch save crawled URLs
    urls: list of URL result dictionaries from crawler
    """
    if not urls:
        return True

    try:
        with get_db() as conn:
            cursor = conn.cursor()

            # Prepare batch insert
            rows = []
            for url_data in urls:
                row = (
                    crawl_id,
                    url_data.get('url'),
                    url_data.get('status_code'),
                    url_data.get('content_type'),
                    url_data.get('size'),
                    url_data.get('is_internal'),
                    url_data.get('depth'),
                    url_data.get('title'),
                    url_data.get('meta_description'),
                    url_data.get('h1'),
                    json.dumps(url_data.get('h2', [])),
                    json.dumps(url_data.get('h3', [])),
                    url_data.get('word_count'),
                    url_data.get('canonical_url'),
                    url_data.get('lang'),
                    url_data.get('charset'),
                    url_data.get('viewport'),
                    url_data.get('robots'),
                    json.dumps(url_data.get('meta_tags', {})),
                    json.dumps(url_data.get('og_tags', {})),
                    json.dumps(url_data.get('twitter_tags', {})),
                    json.dumps(url_data.get('json_ld', [])),
                    json.dumps(url_data.get('analytics', {})),
                    json.dumps(url_data.get('images', [])),
                    json.dumps(url_data.get('hreflang', [])),
                    json.dumps(url_data.get('schema_org', [])),
                    json.dumps(url_data.get('redirects', [])),
                    json.dumps(url_data.get('linked_from', [])),
                    url_data.get('external_links'),
                    url_data.get('internal_links'),
                    url_data.get('response_time'),
                    url_data.get('javascript_rendered', False),
                    url_data.get('error_type'),
                    url_data.get('render_time'),
                    url_data.get('main_text'),
                    url_data.get('main_text_hash'),
                    # simhash is a 64-bit value kept as a decimal string: sqlite's
                    # INTEGER is signed, so storing it as a number would wrap the
                    # top bit and break hamming-distance comparisons
                    None if url_data.get('simhash') is None else str(url_data.get('simhash')),
                    url_data.get('main_word_count'),
                    json.dumps(url_data.get('headings', [])),
                    json.dumps(url_data.get('heading_text_issues', [])),
                    url_data.get('cache_control'),
                    url_data.get('last_modified'),
                    url_data.get('etag'),
                    url_data.get('content_length'),
                    url_data.get('ttfb'),
                    url_data.get('processing_time'),
                    json.dumps(url_data.get('json_ld_raw', [])),
                    json.dumps(url_data.get('json_ld_errors', []))
                )
                rows.append(row)

            cursor.executemany('''
                INSERT INTO crawled_urls (
                    crawl_id, url, status_code, content_type, size, is_internal, depth,
                    title, meta_description, h1, h2, h3, word_count,
                    canonical_url, lang, charset, viewport, robots,
                    meta_tags, og_tags, twitter_tags, json_ld, analytics, images,
                    hreflang, schema_org, redirects, linked_from,
                    external_links, internal_links, response_time, javascript_rendered,
                    error_type, render_time,
                    main_text, main_text_hash, simhash, main_word_count,
                    headings, heading_text_issues,
                    cache_control, last_modified, etag, content_length, ttfb,
                    processing_time, json_ld_raw, json_ld_errors
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', rows)

            print(f"Saved {len(urls)} URLs to database for crawl {crawl_id}")
            return True

    except Exception as e:
        print(f"Error saving URL batch: {e}")
        import traceback
        traceback.print_exc()
        return False

def save_links_batch(crawl_id, links):
    """Batch save links"""
    if not links:
        return True

    try:
        with get_db() as conn:
            cursor = conn.cursor()

            rows = []
            for link in links:
                # rel arrives either as the raw attribute string or as the list
                # BeautifulSoup hands back for multi-valued attributes
                rel = link.get('rel')
                if isinstance(rel, (list, tuple)):
                    rel = ' '.join(str(r) for r in rel) or None

                row = (
                    crawl_id,
                    link.get('source_url'),
                    link.get('target_url'),
                    link.get('anchor_text'),
                    link.get('is_internal'),
                    link.get('target_domain'),
                    link.get('target_status'),
                    link.get('placement', 'body'),
                    rel,
                    link.get('nofollow'),
                    # column is link_target: plain "target" would read as the
                    # link's destination next to target_url/target_domain
                    link.get('link_target', link.get('target')),
                    link.get('in_nav'),
                    link.get('in_main'),
                    link.get('in_heading'),
                    link.get('href')
                )
                rows.append(row)

            cursor.executemany('''
                INSERT INTO crawl_links (
                    crawl_id, source_url, target_url, anchor_text,
                    is_internal, target_domain, target_status, placement,
                    rel, nofollow, link_target, in_nav, in_main, in_heading, href
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', rows)

            print(f"Saved {len(links)} links to database for crawl {crawl_id}")
            return True

    except Exception as e:
        print(f"Error saving links batch: {e}")
        return False

def save_issues_batch(crawl_id, issues):
    """Batch save SEO issues"""
    if not issues:
        return True

    try:
        with get_db() as conn:
            cursor = conn.cursor()

            from src.utils.issue_factory import issue_id as build_issue_id

            rows = []
            for issue in issues:
                evidence = issue.get('evidence')
                # Checks that predate the issue factory still need a stable
                # identity, or every crawl would report them as brand new
                stable_id = issue.get('id') or build_issue_id(
                    issue.get('url', ''),
                    issue.get('check') or issue.get('issue', ''),
                    issue.get('details', ''))
                row = (
                    crawl_id,
                    issue.get('url'),
                    issue.get('type'),
                    issue.get('category'),
                    issue.get('issue'),
                    issue.get('details'),
                    stable_id,
                    issue.get('check'),
                    json.dumps(evidence) if evidence else None,
                    issue.get('how_to_fix'),
                    issue.get('docs_url'),
                    issue.get('first_seen_crawl'),
                    issue.get('status', 'open'),
                    issue.get('accepted_reason')
                )
                rows.append(row)

            cursor.executemany('''
                INSERT INTO crawl_issues (
                    crawl_id, url, type, category, issue, details,
                    issue_key, check_slug, evidence, how_to_fix, docs_url,
                    first_seen_crawl, status, accepted_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', rows)

            print(f"Saved {len(issues)} issues to database for crawl {crawl_id}")
            return True

    except Exception as e:
        print(f"Error saving issues batch: {e}")
        return False

def save_checkpoint(crawl_id, checkpoint_data):
    """Save queue checkpoint for crash recovery"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE crawls
                SET resume_checkpoint = ?, last_saved_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (json.dumps(checkpoint_data), crawl_id))

            return True
    except Exception as e:
        print(f"Error saving checkpoint: {e}")
        return False

def set_crawl_status(crawl_id, status):
    """
    Update crawl status
    status: 'running', 'paused', 'completed', 'failed', 'stopped', 'archived'
    """
    try:
        with get_db() as conn:
            cursor = conn.cursor()

            if status in ['completed', 'failed', 'stopped']:
                cursor.execute('''
                    UPDATE crawls
                    SET status = ?, completed_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                ''', (status, crawl_id))
            else:
                cursor.execute('''
                    UPDATE crawls
                    SET status = ?
                    WHERE id = ?
                ''', (status, crawl_id))

            print(f"Updated crawl {crawl_id} status to: {status}")
            return True

    except Exception as e:
        print(f"Error setting crawl status: {e}")
        return False

def stamp_first_seen(crawl_id, first_seen):
    """Record when each issue in this crawl was first seen.

    A defect that has been open for five crawls should not read as new in all
    five; the id is stable, so the earliest crawl that reported it is the
    answer. Issues absent from the map are first seen in this crawl.
    """
    if not crawl_id:
        return 0
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE crawl_issues SET first_seen_crawl = ? WHERE crawl_id = ?',
                (crawl_id, crawl_id))
            updated = 0
            for issue_key, seen_in in (first_seen or {}).items():
                cursor.execute(
                    'UPDATE crawl_issues SET first_seen_crawl = ? '
                    'WHERE crawl_id = ? AND issue_key = ?',
                    (seen_in, crawl_id, issue_key))
                updated += cursor.rowcount
            return updated
    except Exception as e:
        print(f"Error stamping first-seen crawls: {e}")
        return 0


def save_sitemap_audit(crawl_id, files, coverage, entries=None):
    """Keep the sitemap audit's per-file summaries, coverage and lastmod values.

    The lastmod values matter beyond this crawl: the next one compares them to
    catch a CMS that changed a page without bumping its <lastmod>.
    """
    try:
        payload = {'files': files or [], 'entries': entries or {}}
        with get_db() as conn:
            conn.execute('UPDATE crawls SET sitemap_data = ?, sitemap_coverage = ? WHERE id = ?',
                         (json.dumps(payload), json.dumps(coverage or {}), crawl_id))
        return True
    except Exception as e:
        print(f"Error saving sitemap audit: {e}")
        return False


def accept_issue(issue_key, user_id, reason, base_domain=None):
    """Mute a known limitation so later crawls stop reporting it as new"""
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO accepted_issues (issue_key, user_id, base_domain, reason)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(issue_key, user_id) DO UPDATE SET
                    reason = excluded.reason, accepted_at = CURRENT_TIMESTAMP
            """, (issue_key, user_id, base_domain, reason))
        return True
    except Exception as e:
        print(f"Error accepting issue: {e}")
        return False


def unaccept_issue(issue_key, user_id):
    """Un-mute an issue"""
    try:
        with get_db() as conn:
            conn.execute('DELETE FROM accepted_issues WHERE issue_key = ? AND user_id IS ?',
                         (issue_key, user_id))
        return True
    except Exception as e:
        print(f"Error un-accepting issue: {e}")
        return False


def get_accepted_issues(user_id, base_domain=None):
    """{issue_key: {reason, accepted_at, base_domain}} for this user"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            query = 'SELECT * FROM accepted_issues WHERE user_id IS ?'
            params = [user_id]
            if base_domain:
                query += ' AND (base_domain IS NULL OR base_domain = ?)'
                params.append(base_domain)
            cursor.execute(query, params)
            return {row['issue_key']: dict(row) for row in cursor.fetchall()}
    except Exception as e:
        print(f"Error loading accepted issues: {e}")
        return {}


def get_crawl_by_id(crawl_id):
    """Get crawl metadata by ID"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM crawls WHERE id = ?
            ''', (crawl_id,))

            row = cursor.fetchone()
            if row:
                crawl = dict(row)
                # Parse JSON fields
                if crawl.get('config_snapshot'):
                    crawl['config_snapshot'] = json.loads(crawl['config_snapshot'])
                if crawl.get('resume_checkpoint'):
                    crawl['resume_checkpoint'] = json.loads(crawl['resume_checkpoint'])
                for field in ('sitemap_data', 'sitemap_coverage'):
                    try:
                        crawl[field] = json.loads(crawl[field]) if crawl.get(field) else None
                    except (TypeError, ValueError):
                        crawl[field] = None
                # sitemap_data used to be a bare list of files; it now carries
                # the lastmod values too, so read either shape
                data = crawl.get('sitemap_data')
                if isinstance(data, list):
                    crawl['sitemap_data'] = {'files': data, 'entries': {}}
                crawl['sitemap_files'] = (crawl.get('sitemap_data') or {}).get('files', [])
                crawl['sitemap_entries'] = (crawl.get('sitemap_data') or {}).get('entries', {})
                return crawl
            return None

    except Exception as e:
        print(f"Error fetching crawl: {e}")
        return None

def get_user_crawls(user_id, limit=50, offset=0, status_filter=None):
    """Get all crawls for a user"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()

            query = 'SELECT * FROM crawls WHERE user_id = ?'
            params = [user_id]

            if status_filter:
                query += ' AND status = ?'
                params.append(status_filter)

            query += ' ORDER BY started_at DESC LIMIT ? OFFSET ?'
            params.extend([limit, offset])

            cursor.execute(query, params)

            crawls = []
            for row in cursor.fetchall():
                crawl = dict(row)
                # Don't parse full config for list view
                crawl['config_snapshot'] = None  # Save bandwidth
                crawls.append(crawl)

            return crawls

    except Exception as e:
        print(f"Error fetching user crawls: {e}")
        return []

def load_crawled_urls(crawl_id, limit=None, offset=0):
    """Load all crawled URLs for a crawl"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()

            query = 'SELECT * FROM crawled_urls WHERE crawl_id = ? ORDER BY crawled_at'
            params = [crawl_id]

            if limit:
                query += ' LIMIT ? OFFSET ?'
                params.extend([limit, offset])

            cursor.execute(query, params)

            urls = []
            for row in cursor.fetchall():
                url_data = dict(row)
                # Parse JSON fields
                for field in ['h2', 'h3', 'meta_tags', 'og_tags', 'twitter_tags',
                             'json_ld', 'analytics', 'images', 'hreflang',
                             'schema_org', 'redirects', 'linked_from',
                             'headings', 'heading_text_issues',
                             'json_ld_raw', 'json_ld_errors']:
                    if url_data.get(field):
                        try:
                            url_data[field] = json.loads(url_data[field])
                        except:
                            url_data[field] = []

                # Rows written before these columns existed come back as NULL;
                # callers iterate them, so hand back empty lists instead
                for field in ['headings', 'heading_text_issues']:
                    if url_data.get(field) is None:
                        url_data[field] = []

                urls.append(url_data)

            return urls

    except Exception as e:
        print(f"Error loading crawled URLs: {e}")
        return []

def load_crawl_links(crawl_id, limit=None, offset=0):
    """Load all links for a crawl"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()

            query = 'SELECT * FROM crawl_links WHERE crawl_id = ?'
            params = [crawl_id]

            if limit:
                query += ' LIMIT ? OFFSET ?'
                params.extend([limit, offset])

            cursor.execute(query, params)

            return [dict(row) for row in cursor.fetchall()]

    except Exception as e:
        print(f"Error loading links: {e}")
        return []

def load_crawl_issues(crawl_id, limit=None, offset=0):
    """Load all issues for a crawl"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()

            query = 'SELECT * FROM crawl_issues WHERE crawl_id = ?'
            params = [crawl_id]

            if limit:
                query += ' LIMIT ? OFFSET ?'
                params.extend([limit, offset])

            cursor.execute(query, params)

            issues = []
            for row in cursor.fetchall():
                issue = dict(row)
                # Give back the shape src/utils/issue_factory.py produces. The
                # table's own row id is not an identity across crawls, so it
                # never stands in for a missing stable id.
                issue['row_id'] = issue.get('id')
                issue['id'] = issue.pop('issue_key', None)
                issue['check'] = issue.pop('check_slug', None)
                try:
                    issue['evidence'] = json.loads(issue['evidence']) if issue.get('evidence') else {}
                except (TypeError, ValueError):
                    issue['evidence'] = {}
                issues.append(issue)
            return issues

    except Exception as e:
        print(f"Error loading issues: {e}")
        return []

def get_resume_data(crawl_id):
    """Get all data needed to resume a crawl"""
    crawl = get_crawl_by_id(crawl_id)
    if not crawl:
        return None

    # Only allow resume for paused/failed/running crawls
    if crawl['status'] not in ['paused', 'failed', 'running']:
        return None

    return crawl

def delete_crawl(crawl_id):
    """Delete a crawl and all associated data"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            # The ON DELETE CASCADE clauses never fire: sqlite enforces foreign
            # keys only when PRAGMA foreign_keys is on, and enabling it globally
            # here would start rejecting existing inserts. Delete the children
            # explicitly so a removed crawl does not leave orphaned rows behind.
            for table in ['crawl_queue', 'crawl_issues', 'crawl_links', 'crawled_urls']:
                cursor.execute(f'DELETE FROM {table} WHERE crawl_id = ?', (crawl_id,))
            cursor.execute('DELETE FROM crawls WHERE id = ?', (crawl_id,))
            print(f"Deleted crawl {crawl_id} and all associated data")
            return True
    except Exception as e:
        print(f"Error deleting crawl: {e}")
        return False

def get_crashed_crawls():
    """Find crawls that were running when server crashed"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM crawls
                WHERE status = 'running'
                ORDER BY started_at DESC
            ''')

            crawls = []
            for row in cursor.fetchall():
                crawl = dict(row)
                crawls.append(crawl)

            return crawls

    except Exception as e:
        print(f"Error finding crashed crawls: {e}")
        return []

def cleanup_old_crawls(days=90):
    """Delete crawls older than specified days (optional maintenance)"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM crawls
                WHERE started_at < datetime('now', '-' || ? || ' days')
                AND status IN ('completed', 'failed', 'stopped')
            ''', (days,))

            deleted = cursor.rowcount
            print(f"Cleaned up {deleted} old crawls")
            return deleted

    except Exception as e:
        print(f"Error cleaning up old crawls: {e}")
        return 0

def prune_old_crawls(user_id, keep_last):
    """
    Keep only the `keep_last` most recent crawls for a user and delete the rest,
    along with their URLs, links, issues and queued rows.

    Crawls that are still 'running' or 'paused' are never deleted, and
    keep_last <= 0 means "keep everything".
    Returns the list of deleted crawl ids.
    """
    if not keep_last or keep_last <= 0:
        return []

    try:
        with get_db() as conn:
            cursor = conn.cursor()
            # `IS ?` rather than `= ?` so guest crawls, which carry user_id NULL,
            # are matched too
            cursor.execute('''
                SELECT id, status FROM crawls
                WHERE user_id IS ?
                ORDER BY started_at DESC, id DESC
            ''', (user_id,))
            rows = [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error finding crawls to prune: {e}")
        return []

    # The newest keep_last crawls stay whatever their status; beyond that only
    # finished crawls go, so an in-flight crawl is never pulled out from under
    # the crawler thread
    stale = [row['id'] for row in rows[keep_last:]
             if row['status'] not in ('running', 'paused')]

    if not stale:
        return []

    deleted = []
    for crawl_id in stale:
        if delete_crawl(crawl_id):
            deleted.append(crawl_id)

    print(f"Pruned {len(deleted)} old crawls for user {user_id}, keeping the last {keep_last}")
    return deleted

def get_crawl_count(user_id):
    """Get total number of crawls for a user"""
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) as count FROM crawls WHERE user_id = ?', (user_id,))
            result = cursor.fetchone()
            return result['count'] if result else 0
    except Exception as e:
        print(f"Error getting crawl count: {e}")
        return 0

def get_database_size_mb():
    """Get total database size in MB"""
    try:
        import os
        if os.path.exists(DB_FILE):
            size_bytes = os.path.getsize(DB_FILE)
            return round(size_bytes / (1024 * 1024), 2)
        return 0
    except Exception as e:
        print(f"Error getting database size: {e}")
        return 0
