"""Starting, stopping and resuming a crawl, and reporting where it is.

Two kinds of status live here and the difference matters: get_status
returns everything, and get_status_light returns the counters alone.
A browser polling the full snapshot every second on a fifty-thousand
page crawl is the difference between a responsive screen and an
unusable one.

Lifted out of crawler.py unchanged to bring that file under the project's
1,000-line limit.
"""

import asyncio
import threading
import time

from urllib.parse import urlparse

from src.core.issue_detector import IssueDetector
from src.core.js_renderer import JavaScriptRenderer
from src.core.link_manager import LinkManager
from src.core.rate_limiter import RateLimiter
from src.core.sitemap_parser import SitemapParser
from src.utils.url_params import strip_tracking_params

class LifecycleMixin:
    """The crawl's life, from start_crawl to the final status."""

    def start_crawl(self, url, user_id=None, session_id=None):
        """Start crawling from the given URL"""
        if self.is_running:
            return False, "Crawl already in progress"

        try:
            # Validate and normalize URL
            if not url.startswith(('http://', 'https://')):
                url = 'https://' + url

            # Strip the start URL the same way discovered links are stripped,
            # so a start URL pasted with ?utm_source=... is the same page as
            # the one every internal link points at
            strip_params = self._get_strip_params()
            if strip_params:
                url = strip_tracking_params(url, strip_params)

            parsed = urlparse(url)
            self.base_url = f"{parsed.scheme}://{parsed.netloc}"
            self.base_domain = parsed.netloc

            # Sign in here rather than only in update_config, so the crawl is
            # authenticated however the configuration arrived. Doing it in one
            # setter meant a config assembled any other way started a crawl
            # that quietly fetched the login page for every URL.
            from src.core import site_auth
            self.auth_description = ''
            if site_auth.configured(self.config):
                try:
                    self.auth_description = site_auth.apply(self.session, self.config)
                    self.auth_description += '; ' + (
                        site_auth.verify(self.session, url, self.config) or 'not verified')
                    print(f'Site authentication: {self.auth_description}')
                except site_auth.AuthError as exc:
                    # Refusing to start beats crawling four hundred copies of
                    # a login page and calling it a report.
                    return False, f'Could not sign in to the site: {exc}'

            self.loaded_crawl_id = None
            self.user_id = user_id
            self.baseline_crawl_id = None
            self._rules_need_html = self._selector_rules_present()
            self._page_html = {}

            # Create database crawl record if session_id provided
            if session_id:
                from src.crawl_db import create_crawl
                from src.core.site_auth import redacted
                self.crawl_id = create_crawl(
                    user_id=user_id,
                    session_id=session_id,
                    base_url=self.base_url,
                    base_domain=self.base_domain,
                    # Redacted: this snapshot is written to the database and
                    # travels with every export of the crawl, which is the
                    # last place a site password should end up.
                    config_snapshot=redacted(self.config)
                )
                if self.crawl_id:
                    self.db_save_enabled = True
                    print(f"Database persistence enabled for crawl {self.crawl_id}")
                    try:
                        from src.core.crawl_diff import previous_crawl
                        baseline = previous_crawl(self.base_domain, self.crawl_id, user_id)
                        self.baseline_crawl_id = baseline['id'] if baseline else None
                        if self.baseline_crawl_id:
                            print(f"Comparing against crawl {self.baseline_crawl_id}")
                    except Exception as e:
                        print(f"Could not find a previous crawl to compare against: {e}")

            # Initialize components
            self._initialize_components()

            # Reset state
            self._reset_state()

            # Add initial URL
            self.link_manager.add_url(url, 0)
            self.stats['discovered'] = 1

            # Discover sitemaps in the background so the crawl (and the UI)
            # starts immediately — sitemap probes can take many seconds on
            # slow sites and used to block this request until they finished
            if self.config.get('discover_sitemaps', True):
                self._sitemap_discovery_done = False
                threading.Thread(target=self._discover_sitemaps_background,
                                 args=(url,), daemon=True).start()

            # Start auto-save thread if DB enabled
            if self.db_save_enabled:
                self._start_auto_save_thread()

            # Start crawling in separate thread
            self.is_running = True
            self.crawl_thread = threading.Thread(target=self._crawl_worker)
            self.crawl_thread.start()

            return True, "Crawl started successfully"

        except Exception as e:
            return False, f"Error starting crawl: {str(e)}"

    def _initialize_components(self):
        """Initialize all crawler components"""
        # Calculate requests per second from delay
        if self.config['delay'] > 0:
            requests_per_second = 1.0 / self.config['delay']
        else:
            # If delay is 0, set high rate but still smooth
            requests_per_second = 100.0

        self.rate_limiter = RateLimiter(requests_per_second)
        self.link_manager = LinkManager(self.base_domain, event_log=self.event_log,
                                        strip_params=self._get_strip_params())
        self.sitemap_parser = SitemapParser(self.session, self.base_domain, self.config['timeout'])
        self.issue_detector = IssueDetector(self.config.get('issue_exclusion_patterns', []),
                                            tracking_params=self.config.get('strip_query_params'))

        # Initialize JS renderer if needed
        if self.config.get('enable_javascript', False):
            self.js_renderer = JavaScriptRenderer(self.config)

    def _reset_state(self):
        """Reset crawler state"""
        if self.link_manager:
            self.link_manager.reset()
        if self.issue_detector:
            self.issue_detector.reset()

        self.crawl_results.clear()
        self.pages_crawled = 0
        self.event_log.new_epoch()
        self._image_status_cache.clear()
        self._synthesized_image_urls.clear()
        self.stats = {
            'discovered': 0,
            'crawled': 0,
            'depth': 0,
            'speed': 0.0,
            'start_time': time.time()
        }

        # Start memory monitoring
        self.memory_monitor.start_monitoring()
        self.user_memory.reset()
        self._demo_limit_reached = False
        self._sitemap_discovery_done = True

    def _discover_sitemaps_background(self, base_url):
        """Run sitemap discovery off the request thread; the completion
        checks in the crawl workers wait on _sitemap_discovery_done"""
        try:
            print(f"Starting sitemap discovery for {base_url}")
            self._discover_and_add_sitemap_urls(base_url)
            print(f"Sitemap discovery completed. Total discovered URLs: {self.stats['discovered']}")
        except Exception as e:
            print(f"Sitemap discovery error: {e}")
        finally:
            self._sitemap_discovery_done = True

    def _discover_and_add_sitemap_urls(self, base_url):
        """Discover sitemaps and add URLs to crawl queue"""
        sitemap_urls = self.sitemap_parser.discover_sitemaps(base_url)

        added_count = 0
        filtered_count = 0

        for url in sitemap_urls:
            if self._should_crawl_url(url):
                self.link_manager.add_url(url, 0)
                added_count += 1
            else:
                filtered_count += 1

        self.stats['discovered'] = self.link_manager.get_stats()['discovered']
        print(f"Sitemap processing: {added_count} added, {filtered_count} filtered")

    def stop_crawl(self):
        """Stop the current crawl"""
        self.is_running = False
        self.is_paused = False
        self.is_running_pagespeed = False

        if self.crawl_thread and self.crawl_thread.is_alive():
            self.crawl_thread.join(timeout=5)

        # Save final data to database
        if self.db_save_enabled and self.crawl_id:
            self._save_batch_to_db(force=True)
            from src.crawl_db import get_crawl_by_id, set_crawl_status
            crawl = get_crawl_by_id(self.crawl_id)
            # Don't overwrite 'completed' status (e.g. from cleanup thread)
            if crawl and crawl['status'] != 'completed':
                set_crawl_status(self.crawl_id, 'stopped')

        # Clean up JavaScript resources if enabled
        if self.js_renderer:
            asyncio.run(self.js_renderer.cleanup())
            self.js_renderer = None

        return True, "Crawl and PageSpeed analysis stopped"

    def pause_crawl(self):
        """Pause the current crawl"""
        if not self.is_running:
            return False, "No crawl in progress"
        self.is_paused = True

        # Save checkpoint when pausing
        if self.db_save_enabled and self.crawl_id:
            self._save_batch_to_db(force=True)
            self._save_queue_checkpoint()
            from src.crawl_db import set_crawl_status
            set_crawl_status(self.crawl_id, 'paused')

        return True, "Crawl paused"

    def resume_crawl(self):
        """Resume the paused crawl"""
        if not self.is_running:
            return False, "No crawl in progress"
        if not self.is_paused:
            return False, "Crawl is not paused"
        self.is_paused = False

        # Update status in database
        if self.db_save_enabled and self.crawl_id:
            from src.crawl_db import set_crawl_status
            set_crawl_status(self.crawl_id, 'running')

        return True, "Crawl resumed"

    def resume_from_database(self, crawl_id, user_id=None, session_id=None):
        """Resume a previously interrupted crawl from database"""
        if self.is_running:
            return False, "Crawl already in progress"

        try:
            from src.crawl_db import get_resume_data, load_crawled_urls, set_crawl_status
            from collections import deque

            # Load crawl data
            crawl_data = get_resume_data(crawl_id)

            if not crawl_data:
                return False, "Cannot resume this crawl - not found"

            if crawl_data['status'] not in ['paused', 'failed', 'running', 'stopped']:
                return False, f"Cannot resume crawl with status: {crawl_data['status']}"

            # Verify user owns this crawl (if not guest)
            if user_id and crawl_data.get('user_id') != user_id:
                return False, "Unauthorized - you don't own this crawl"

            # Restore basic state
            self.crawl_id = crawl_id
            self.loaded_crawl_id = None
            self.base_url = crawl_data['base_url']
            self.base_domain = crawl_data['base_domain']
            # Preserve demo keys across config restore
            demo_mode = self.config.get('demo_mode', False)
            demo_limit = self.config.get('demo_memory_limit_bytes', 0)
            self.config = crawl_data.get('config_snapshot', self._get_default_config())
            if demo_mode:
                self.config['demo_mode'] = True
                self.config['demo_memory_limit_bytes'] = demo_limit
            self.db_save_enabled = True

            # Initialize components
            self._initialize_components()

            # Load already crawled URLs from database
            from src.crawl_db import load_crawl_links, load_crawl_issues

            print(f"Loading crawled data from database...")
            self.crawl_results = load_crawled_urls(crawl_id)

            # Mark all crawled URLs as discovered to prevent re-discovery
            for url_data in self.crawl_results:
                url = url_data.get('url')
                if url:
                    self.link_manager.all_discovered_urls.add(url)

            # Load links and restore to link manager
            loaded_links = load_crawl_links(crawl_id)
            if loaded_links:
                self.link_manager.all_links = loaded_links
                # Rebuild links_set for duplicate detection
                for link in loaded_links:
                    link_key = f"{link['source_url']}|{link['target_url']}"
                    self.link_manager.links_set.add(link_key)

            # Load issues and restore to issue detector
            loaded_issues = load_crawl_issues(crawl_id)
            if loaded_issues:
                self.issue_detector.detected_issues = loaded_issues

            print(f"Loaded {len(self.crawl_results)} URLs, {len(loaded_links)} links, {len(loaded_issues)} issues from database")

            # Account for loaded data in per-user memory tracker
            self.user_memory.reset()
            self._demo_limit_reached = False
            for url_data in self.crawl_results:
                self.user_memory.track_url(url_data)
            if loaded_links:
                self.user_memory.track_links(loaded_links)
            if loaded_issues:
                self.user_memory.track_issues(loaded_issues)
            print(f"User memory tracker: {self.user_memory.total_mb:.0f}MB from loaded data")

            # Rebuild the event log so polling clients resync the loaded data
            self.event_log.new_epoch()
            self.event_log.emit_many('url', self.crawl_results)
            self.event_log.emit_many('link', loaded_links)
            self.event_log.emit_many('issue', loaded_issues)

            # Restore statistics. Resumed rows count against max_urls so a
            # resume cannot push the crawl past the configured limit.
            self.stats['crawled'] = len(self.crawl_results)
            self.pages_crawled = len(self.crawl_results)
            self.stats['discovered'] = crawl_data.get('urls_discovered', 0)
            self.stats['depth'] = crawl_data.get('max_depth_reached', 0)
            self.stats['start_time'] = time.time()  # New start time for resume

            # Restore queue state from checkpoint
            checkpoint = crawl_data.get('resume_checkpoint', {})
            if checkpoint:
                # Restore discovered URLs queue
                if 'discovered_urls' in checkpoint:
                    discovered_list = checkpoint['discovered_urls']
                    self.link_manager.discovered_urls = deque(discovered_list)

                # Restore visited URLs set
                if 'visited_urls' in checkpoint:
                    self.link_manager.visited_urls = set(checkpoint['visited_urls'])

                print(f"Restored queue: {len(self.link_manager.discovered_urls)} pending, "
                      f"{len(self.link_manager.visited_urls)} visited")

            # If queue is empty (no checkpoint or crawl crashed early), rebuild queue from links
            if not self.link_manager.discovered_urls:
                print("Queue is empty - rebuilding from discovered links")

                # Get all URLs from loaded links that haven't been crawled yet
                crawled_urls = set(url_data.get('url') for url_data in self.crawl_results)

                # Add any linked URLs that haven't been crawled yet
                added_count = 0
                for link in loaded_links:
                    target_url = link.get('target_url')
                    if target_url and target_url not in crawled_urls and link.get('is_internal'):
                        self.link_manager.add_url(target_url, link.get('depth', 1))
                        added_count += 1

                print(f"Added {added_count} pending URLs to queue from links")

                # If still empty, crawl is complete
                if not self.link_manager.discovered_urls:
                    print("No pending URLs found - crawl was already complete")

                self.stats['discovered'] = len(self.link_manager.all_discovered_urls)

            # Update status to running
            set_crawl_status(crawl_id, 'running')

            # Start auto-save thread
            self._start_auto_save_thread()

            # Start crawling
            self.is_running = True
            self.crawl_thread = threading.Thread(target=self._crawl_worker)
            self.crawl_thread.start()

            return True, f"Resumed crawl from {self.stats['crawled']} URLs"

        except Exception as e:
            print(f"Error resuming crawl: {e}")
            import traceback
            traceback.print_exc()
            return False, f"Error resuming crawl: {str(e)}"

    def load_data(self, crawl, urls, links, issues):
        """Inject a historical crawl's data into this instance for viewing/export.

        Rebuilds the event log under a new epoch, so any polling client
        drops its accumulated state and replays the loaded data.
        """
        self.base_url = crawl['base_url']
        self.base_domain = crawl['base_domain']
        self.loaded_crawl_id = crawl['id']

        # A freshly created instance has no components yet (they normally
        # initialize on start_crawl) — without them, loaded links/issues
        # would silently vanish
        if not self.link_manager or not self.issue_detector:
            self._initialize_components()

        with self.results_lock:
            self.crawl_results = urls
        self.stats['crawled'] = len(urls)
        self.stats['discovered'] = crawl.get('urls_discovered') or len(urls)
        self.stats['depth'] = crawl.get('max_depth_reached') or max(
            (url.get('depth') or 0 for url in urls), default=0)
        self.pages_crawled = len(urls)

        # The discovered count the UI shows comes from the link manager, which
        # a load leaves empty - a loaded crawl would report "0 discovered"
        # beside its own crawled pages.
        self.link_manager.all_discovered_urls = {url['url'] for url in urls if url.get('url')}
        self.link_manager.visited_urls = set(self.link_manager.all_discovered_urls)

        self.link_manager.all_links = links
        self.link_manager.links_set.clear()
        for link in links:
            self.link_manager.links_set.add(f"{link['source_url']}|{link['target_url']}")

        self.issue_detector.detected_issues = issues

        # Rebuild per-user memory tracker for loaded data
        self.user_memory.reset()
        self._demo_limit_reached = False
        for url_data in urls:
            self.user_memory.track_url(url_data)
        if links:
            self.user_memory.track_links(links)
        if issues:
            self.user_memory.track_issues(issues)

        self.event_log.new_epoch()
        self.event_log.emit_many('url', urls)
        self.event_log.emit_many('link', links)
        self.event_log.emit_many('issue', issues)

    def get_status_light(self):
        """Current status, stats and progress — without the data arrays.
        Data reaches polling clients through the event log instead."""
        if self._demo_limit_reached and not self.is_running:
            status = 'demo_stopped'
        elif not self.is_running and self.stats['crawled'] > 0:
            status = 'completed'
        elif not self.is_running and self.stats['crawled'] == 0:
            status = 'idle'
        else:
            status = 'running'

        # Calculate speed
        if self.stats['start_time']:
            elapsed = time.time() - self.stats['start_time']
            self.stats['speed'] = round(self.stats['crawled'] / max(elapsed, 1), 2)

        # Get link manager stats
        link_stats = self.link_manager.get_stats() if self.link_manager else {'discovered': 0}

        # Backfill link target statuses from crawled URLs. The link manager
        # journals the ones that changed itself, under its own lock.
        if self.link_manager:
            self.link_manager.update_link_statuses(self.crawl_results)

        # Update memory stats
        self.memory_monitor.update()

        # Per-user data sizes from incremental tracker (O(1), no recursion)
        data_sizes = self.user_memory.get_stats()

        return {
            'status': status,
            'crawl_id': self.loaded_crawl_id or self.crawl_id,
            'stats': {
                **self.stats,
                # A live crawl's link manager is authoritative; a loaded crawl
                # has only the URLs it stored, so keep whichever is larger or a
                # loaded crawl reports fewer discovered than it actually found.
                'discovered': max(link_stats['discovered'], self.stats.get('discovered', 0))
            },
            'progress': min(100, (self.stats['crawled'] / max(link_stats['discovered'], 1)) * 100),
            'is_running_pagespeed': self.is_running_pagespeed,
            'memory': self.memory_monitor.get_stats(),
            'memory_data': data_sizes,
            'demo_stopped': self._demo_limit_reached,
            'demo_mode': self.config.get('demo_mode', False)
        }

    def get_status(self):
        """Full status snapshot including data arrays (initial load, save, export)"""
        status_data = self.get_status_light()
        status_data['urls'] = self.crawl_results.copy()
        status_data['links'] = self.link_manager.all_links.copy() if self.link_manager else []
        status_data['issues'] = self.issue_detector.get_issues() if self.issue_detector else []
        return status_data

