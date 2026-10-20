"""The crawl database schema, and the migrations that grew it.

One function, because the schema is one thing: tables, the ALTER TABLE
loop that brings an older database up to date, and the indexes.

The ALTER loop exists because this project ships as a container people
update in place. A new column added without it means a crawl that saves
zero issues and reports success - which happened once, and is the reason
a test now upgrades an old-schema database and checks the result.

Split out of crawl_db.py to bring that file under the 1,000-line limit.
Nothing changed but the location.
"""

import sqlite3

from src.crawl_db import get_db


def init_crawl_tables():
    """Initialize crawl persistence tables"""
    with get_db() as conn:
        cursor = conn.cursor()

        # Main crawls table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS crawls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                session_id TEXT NOT NULL,
                base_url TEXT NOT NULL,
                base_domain TEXT,
                status TEXT DEFAULT 'running',

                config_snapshot TEXT,

                urls_discovered INTEGER DEFAULT 0,
                urls_crawled INTEGER DEFAULT 0,
                max_depth_reached INTEGER DEFAULT 0,

                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                last_saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                peak_memory_mb REAL,
                estimated_size_mb REAL,

                can_resume BOOLEAN DEFAULT 1,
                resume_checkpoint TEXT,

                sitemap_data TEXT,
                sitemap_coverage TEXT,

                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        ''')

        # Crawled URLs table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS crawled_urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crawl_id INTEGER NOT NULL,
                url TEXT NOT NULL,

                status_code INTEGER,
                content_type TEXT,
                size INTEGER,
                is_internal BOOLEAN,
                depth INTEGER,

                title TEXT,
                meta_description TEXT,
                h1 TEXT,
                h2 TEXT,
                h3 TEXT,
                word_count INTEGER,

                canonical_url TEXT,
                lang TEXT,
                charset TEXT,
                viewport TEXT,
                robots TEXT,

                meta_tags TEXT,
                og_tags TEXT,
                twitter_tags TEXT,
                json_ld TEXT,
                analytics TEXT,
                images TEXT,
                hreflang TEXT,
                schema_org TEXT,
                redirects TEXT,
                linked_from TEXT,

                external_links INTEGER,
                internal_links INTEGER,

                main_text TEXT,
                main_text_hash TEXT,
                simhash TEXT,
                main_word_count INTEGER,
                headings TEXT,
                heading_text_issues TEXT,

                cache_control TEXT,
                last_modified TEXT,
                etag TEXT,
                content_length INTEGER,
                ttfb REAL,
                processing_time REAL,
                json_ld_raw TEXT,
                json_ld_errors TEXT,

                response_time REAL,
                javascript_rendered BOOLEAN DEFAULT 0,
                error_type TEXT,

                crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (crawl_id) REFERENCES crawls(id) ON DELETE CASCADE
            )
        ''')

        # Migration: add error_type column to existing crawled_urls tables
        try:
            cursor.execute('ALTER TABLE crawled_urls ADD COLUMN error_type TEXT')
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Migration: render_time, kept apart from
        # response_time so the JS render wait is not
        # mistaken for a slow server
        try:
            cursor.execute('ALTER TABLE crawled_urls ADD COLUMN render_time REAL')
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Migration: per-page body text, near-duplicate fingerprints, heading
        # outline and the caching/timing response headers
        for column_def in ['main_text TEXT', 'main_text_hash TEXT', 'simhash TEXT',
                           'main_word_count INTEGER', 'headings TEXT',
                           'heading_text_issues TEXT', 'cache_control TEXT',
                           'last_modified TEXT', 'etag TEXT',
                           'content_length INTEGER', 'ttfb REAL',
                           'processing_time REAL', 'json_ld_raw TEXT',
                           'json_ld_errors TEXT']:
            try:
                cursor.execute(f'ALTER TABLE crawled_urls ADD COLUMN {column_def}')
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Links table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS crawl_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crawl_id INTEGER NOT NULL,

                source_url TEXT NOT NULL,
                target_url TEXT NOT NULL,
                anchor_text TEXT,

                is_internal BOOLEAN,
                target_domain TEXT,
                target_status INTEGER,
                placement TEXT,

                rel TEXT,
                nofollow BOOLEAN,
                link_target TEXT,
                in_nav BOOLEAN,
                in_main BOOLEAN,
                in_heading BOOLEAN,
                href TEXT,

                discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (crawl_id) REFERENCES crawls(id) ON DELETE CASCADE
            )
        ''')

        # Migration: anchor rel/target attributes and where in the page the
        # link sits, so internal-linking reports can weight links by context
        for column_def in ['rel TEXT', 'nofollow BOOLEAN', 'link_target TEXT',
                           'in_nav BOOLEAN', 'in_main BOOLEAN', 'in_heading BOOLEAN',
                           'href TEXT']:
            try:
                cursor.execute(f'ALTER TABLE crawl_links ADD COLUMN {column_def}')
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Issues table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS crawl_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crawl_id INTEGER NOT NULL,

                url TEXT NOT NULL,
                type TEXT,
                category TEXT,
                issue TEXT,
                details TEXT,

                issue_key TEXT,
                check_slug TEXT,
                evidence TEXT,
                how_to_fix TEXT,
                docs_url TEXT,
                first_seen_crawl INTEGER,
                status TEXT DEFAULT 'open',
                accepted_reason TEXT,

                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (crawl_id) REFERENCES crawls(id) ON DELETE CASCADE
            )
        ''')

        # Migration: sitemap audit output kept per crawl
        for column_def in ['sitemap_data TEXT', 'sitemap_coverage TEXT']:
            try:
                cursor.execute(f'ALTER TABLE crawls ADD COLUMN {column_def}')
            except sqlite3.OperationalError:
                pass  # column already exists

        # Issues a user has accepted as known limitations. Keyed by the issue's
        # stable id, so an accepted issue stays accepted in later crawls.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS accepted_issues (
                issue_key TEXT NOT NULL,
                user_id INTEGER,
                base_domain TEXT,
                reason TEXT,
                accepted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (issue_key, user_id)
            )
        """)

        # Migration: the richer issue shape (stable id, check slug, evidence,
        # fix hint, regression tracking) on databases created before it
        for column_def in ['issue_key TEXT', 'check_slug TEXT', 'evidence TEXT',
                           'how_to_fix TEXT', 'docs_url TEXT',
                           'first_seen_crawl INTEGER', "status TEXT DEFAULT 'open'",
                           'accepted_reason TEXT']:
            try:
                cursor.execute(f'ALTER TABLE crawl_issues ADD COLUMN {column_def}')
            except sqlite3.OperationalError:
                pass  # column already exists

        # Queue table for crash recovery
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS crawl_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                crawl_id INTEGER NOT NULL,

                url TEXT NOT NULL,
                depth INTEGER,
                priority INTEGER DEFAULT 0,

                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (crawl_id) REFERENCES crawls(id) ON DELETE CASCADE,
                UNIQUE(crawl_id, url)
            )
        ''')

        # A connected Google account, per user, encrypted. Its own table
        # rather than a settings key: a refresh token is a standing key to
        # someone's search data, and settings are stored as plain JSON.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS google_accounts (
                user_id INTEGER PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Scheduled crawls. A job is a standing instruction; each firing
        # becomes an ordinary crawl, so everything downstream - diffs,
        # exports, the regression report - works on it unchanged.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scheduled_crawls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                url TEXT NOT NULL,
                name TEXT,
                every_minutes INTEGER NOT NULL,
                settings TEXT,
                enabled INTEGER DEFAULT 1,
                last_run_at TIMESTAMP,
                last_crawl_id INTEGER,
                last_error TEXT,
                next_run_at TIMESTAMP,
                runs INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Create indexes for performance
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_crawls_user_status ON crawls(user_id, status)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_scheduled_due ON scheduled_crawls(enabled, next_run_at)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_crawls_session ON crawls(session_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_crawled_urls_crawl ON crawled_urls(crawl_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_crawled_urls_url ON crawled_urls(crawl_id, url)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_crawled_urls_status ON crawled_urls(crawl_id, status_code)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_crawled_urls_text_hash ON crawled_urls(crawl_id, main_text_hash)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_crawl_links_crawl ON crawl_links(crawl_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_crawl_links_source ON crawl_links(crawl_id, source_url)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_crawl_links_target ON crawl_links(crawl_id, target_url)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_crawl_issues_crawl ON crawl_issues(crawl_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_crawl_issues_url ON crawl_issues(crawl_id, url)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_crawl_issues_category ON crawl_issues(crawl_id, category)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_crawl_queue_crawl ON crawl_queue(crawl_id)')

        print("Crawl persistence tables initialized successfully")

