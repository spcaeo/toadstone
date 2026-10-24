"""
Main web crawler orchestrator with smooth rate limiting and modular architecture.
Refactored for better code practices and maintainability.
"""
import requests
import socket
import ssl
import threading
import time
import asyncio
import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import DefaultCookiePolicy
from urllib.robotparser import RobotFileParser
import nest_asyncio

# Groups of methods that live in their own files so no one file runs past
# the project's 1,000-line limit. They are mixins because each reaches for
# crawler state, and threading that through arguments would be a far
# larger change than moving the code.
from src.core.crawl.analysis import AnalysisPassesMixin
from src.core.crawl.fetching import FetchingMixin
from src.core.crawl.lifecycle import LifecycleMixin
from src.core.crawl.pagespeed import PageSpeedMixin
from src.core.crawl.persistence import PersistenceMixin

# Extensions treated as images by the "Crawl Images" setting
IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'ico', 'avif', 'bmp'}

# Parallel HEAD checks used for broken-image detection
IMAGE_CHECK_WORKERS = 5


def classify_fetch_error(exc_or_msg):
    """Classify a failed-fetch error into a coarse error_type.

    Accepts either an exception (walks the cause chain to handle
    requests/urllib3 wrappers) or a raw error string (from Playwright).
    Falls back to message inspection.

    Returns one of: 'dns_not_found', 'timeout', 'connection_refused',
    'ssl_error', 'connection_error'.
    """
    if isinstance(exc_or_msg, BaseException):
        seen = set()
        cur = exc_or_msg
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            if isinstance(cur, socket.gaierror):
                return 'dns_not_found'
            if isinstance(cur, ssl.SSLError) or isinstance(cur, requests.exceptions.SSLError):
                return 'ssl_error'
            if isinstance(cur, ConnectionRefusedError):
                return 'connection_refused'
            if isinstance(cur, (socket.timeout, requests.exceptions.Timeout)):
                return 'timeout'
            cur = getattr(cur, '__cause__', None) or getattr(cur, '__context__', None)

    msg = str(exc_or_msg).lower()
    dns_markers = (
        'getaddrinfo failed',
        'name or service not known',
        'name resolution',
        'nodename nor servname',
        'no address associated',
        'name does not resolve',
        'temporary failure in name resolution',
        'name_not_resolved',
        'err_name_not_resolved',
        'nxdomain',
    )
    if any(m in msg for m in dns_markers):
        return 'dns_not_found'
    if 'timed out' in msg or 'timeout' in msg:
        return 'timeout'
    if 'refused' in msg or 'err_connection_refused' in msg:
        return 'connection_refused'
    if 'ssl' in msg or 'certificate' in msg or 'err_cert' in msg or 'tls' in msg:
        return 'ssl_error'
    return 'connection_error'

from src.core.rate_limiter import RateLimiter
from src.core.seo_extractor import SEOExtractor
from src.core.link_manager import LinkManager
from src.core.js_renderer import JavaScriptRenderer
from src.core.sitemap_parser import SitemapParser
from src.core.issue_detector import IssueDetector
from src.core.memory_monitor import MemoryMonitor
from src.core.memory_profiler import UserMemoryTracker
from src.core.event_log import CrawlEventLog
from src.core import header_checks
from src.utils.url_params import DEFAULT_TRACKING_PARAMS, strip_tracking_params


class WebCrawler(LifecycleMixin, FetchingMixin, AnalysisPassesMixin,
                 PersistenceMixin, PageSpeedMixin):
    """
    Main web crawler with smooth rate limiting and comprehensive SEO analysis.
    Uses modular architecture with separate components for different responsibilities.
    """

    def __init__(self, crawl_id=None, resume_from_db=False):
        # HTTP session
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Toadstone/1.0 (Web Crawler)'
        })
        self._set_pool_size(self._get_default_config()['concurrency'])

        # Base URL tracking
        self.base_url = None
        self.base_domain = None

        # Component instances (initialized on demand)
        self.rate_limiter = None
        self.link_manager = None
        self.js_renderer = None
        self.sitemap_parser = None
        self.issue_detector = None
        self.seo_extractor = SEOExtractor()
        self.memory_monitor = MemoryMonitor()
        self.user_memory = UserMemoryTracker()

        # Demo mode state
        self._demo_limit_reached = False

        # Sitemap discovery runs in a background thread; the crawl must not
        # declare "no more URLs" while it is still feeding the queue
        self._sitemap_discovery_done = True

        # Results storage
        self.crawl_results = []
        self.results_lock = threading.Lock()

        # Change journal for incremental UI sync
        self.event_log = CrawlEventLog()

        # State flags
        self.is_running = False
        self.is_paused = False
        self.is_running_pagespeed = False

        # Configuration
        self.config = self._get_default_config()

        # Statistics
        self.stats = {
            'discovered': 0,
            'crawled': 0,
            'depth': 0,
            'speed': 0.0,
            'start_time': None
        }

        # Pages actually fetched. stats['crawled'] also counts the rows
        # synthesized from image HEAD checks, which are not fetches, so the
        # max_urls budget is measured with this instead: otherwise one
        # image-heavy page can exhaust the whole limit on its own.
        self.pages_crawled = 0

        # Thread reference
        self.crawl_thread = None

        # Robots.txt cache
        self._robots_cache = {}
        self._robots_delay_applied = False

        # Image status cache (avoids re-checking the same image URL across pages)
        self._image_status_cache = {}
        # Transfer size per image URL, measured by the HEAD check and written
        # back onto each page's images[] so the weight checks can read it
        self._image_size_cache = {}
        # Image URLs already given a synthesized result row from a HEAD check
        self._synthesized_image_urls = set()

        # Database persistence
        self.crawl_id = crawl_id
        # Saved crawl currently shown via load_data (view only, never written to)
        self.loaded_crawl_id = None
        self.user_id = None
        # Previous completed crawl of this site, for "what changed" reporting
        self.baseline_crawl_id = None
        # Sitemap audit output: {url: lastmod}, per-file summaries and coverage
        self.sitemap_lastmod = {}
        # Page markup kept ONLY when a selector rule needs it, and bounded:
        # holding every page's HTML would cost more than the whole crawl.
        self._rules_need_html = False
        self._page_html = {}
        self.sitemap_files = []
        self.sitemap_coverage = {}
        # Per-section internal-linking summary from the link graph
        self.link_clusters = []
        self.resume_mode = resume_from_db
        self.auto_save_interval = 30  # seconds
        self.batch_save_size = 50  # URLs before triggering save
        self.last_save_time = time.time()
        self.unsaved_urls = []
        self.unsaved_links = []
        self.unsaved_issues = []
        self._batch_lock = threading.Lock()
        self.auto_save_thread = None
        self.db_save_enabled = False  # Only enable when crawl_id is set

        # Enable nested asyncio for thread compatibility
        nest_asyncio.apply()

    def _set_pool_size(self, concurrency):
        """Size the urllib3 connection pool to the work actually in flight.

        requests defaults to 10 connections per host, but two things draw from
        the pool at once: the crawl workers, and the image HEAD checks which run
        their own pool of IMAGE_CHECK_WORKERS threads. Exceeding the limit makes
        urllib3 discard established connections ("Connection pool is full"),
        paying a fresh TCP and TLS handshake for the next request to that host.
        """
        size = max(10, int(concurrency) + IMAGE_CHECK_WORKERS + 5)
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=size, pool_maxsize=size, max_retries=0)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def _get_default_config(self):
        """Get default configuration"""
        return {
            'max_depth': 3,
            'max_urls': 1000,
            'delay': 1.0,
            'follow_redirects': True,
            'crawl_external': False,
            'user_agent': 'Toadstone/1.0 (Web Crawler)',
            'timeout': 10,
            'retries': 3,
            'accept_language': 'en-US,en;q=0.9',
            'respect_robots': True,
            'allow_cookies': True,
            'include_extensions': ['html', 'htm', 'php', 'asp', 'aspx', 'jsp'],
            'crawl_images': False,
            'exclude_extensions': ['pdf', 'doc', 'docx', 'zip', 'exe', 'dmg'],
            # PDFs are excluded above by default, which is right for most
            # sites and wrong for the ones where the PDFs *are* the content -
            # a council, a university, a manufacturer's datasheets. Turning
            # this on lets them through and audits them as documents.
            'crawl_pdfs': False,
            # Where PageSpeed Insights lives. Empty means Google's own.
            'pagespeed_endpoint': '',
            # Third-party authority metrics. Each needs a paid subscription,
            # so nothing is sent anywhere until one is configured.
            'moz_access_id': '',
            'moz_secret_key': '',
            'majestic_api_key': '',
            'ahrefs_api_token': '',
            'link_metrics_max_urls': 200,
            'include_patterns': [],
            'exclude_patterns': [],
            # Chrome removed before a page's main text is stored and hashed
            'main_text_ignore_selectors': ['header', 'nav', 'footer', 'aside',
                                           '.cookie', '.breadcrumb'],
            # Crawls kept per user; 0 keeps every crawl
            'keep_last_crawls': 0,
            # URLs that differ only by campaign/click parameters are one page:
            # the parameters are dropped before a URL enters the crawl queue.
            'ignore_tracking_params': True,
            'strip_query_params': list(DEFAULT_TRACKING_PARAMS),
            'max_file_size': 50 * 1024 * 1024,
            'concurrency': 5,
            'memory_limit': 512 * 1024 * 1024,
            'log_level': 'INFO',
            'enable_proxy': False,
            'proxy_url': None,
            'custom_headers': {},
            # Signing in to the site being crawled. See src/core/site_auth.py.
            'auth_mode': 'none',          # none | basic | digest | form | cookies
            'auth_username': '',
            'auth_password': '',
            'auth_login_url': '',
            'auth_username_field': 'username',
            'auth_password_field': 'password',
            'auth_extra_fields': '',
            'auth_cookies': '',
            'auth_expect_text': '',
            'discover_sitemaps': True,
            'enable_pagespeed': False,
            'enable_javascript': False,
            'js_wait_time': 3,
            'js_timeout': 30,
            'js_browser': 'chromium',
            'js_headless': True,
            'js_user_agent': 'Toadstone/1.0 (Web Crawler with JavaScript)',
            'js_viewport_width': 1920,
            'js_viewport_height': 1080,
            'js_max_concurrent_pages': 3,
            'issue_exclusion_patterns': [
                # WordPress admin & system paths
                '/wp-admin/*', '/wp-content/plugins/*', '/wp-content/themes/*', '/wp-content/uploads/*',
                '/wp-includes/*', '/wp-login.php', '/wp-cron.php', '/xmlrpc.php',
                '/wp-json/*', '/wp-activate.php', '/wp-signup.php', '/wp-trackback.php',

                # Auth & user management pages
                '/login*', '/signin*', '/sign-in*', '/log-in*', '/auth/*', '/authenticate/*',
                '/register*', '/signup*', '/sign-up*', '/registration/*',
                '/logout*', '/signout*', '/sign-out*', '/log-out*',
                '/forgot-password*', '/reset-password*', '/password-reset*', '/recover-password*',
                '/change-password*', '/account/password/*', '/user/password/*',
                '/activate/*', '/verification/*', '/verify/*', '/confirm/*',

                # Admin panels & dashboards
                '/admin/*', '/administrator/*', '/_admin/*', '/backend/*', '/dashboard/*',
                '/cpanel/*', '/phpmyadmin/*', '/pma/*', '/webmail/*', '/plesk/*',
                '/control-panel/*', '/manage/*', '/manager/*',

                # E-commerce checkout & cart
                '/checkout/*', '/cart/*', '/basket/*', '/payment/*', '/billing/*',
                '/order/*', '/orders/*', '/purchase/*',

                # User account pages
                '/account/*', '/profile/*', '/settings/*', '/preferences/*',
                '/my-account/*', '/user/*', '/member/*', '/members/*',

                # CGI & server scripts
                '/cgi-bin/*', '/cgi/*', '/fcgi-bin/*',

                # Version control & config
                '/.git/*', '/.svn/*', '/.hg/*', '/.bzr/*', '/.cvs/*',
                '/.env', '/.env.*', '/.htaccess', '/.htpasswd',
                '/web.config', '/app.config', '/composer.json', '/package.json',

                # Development & build artifacts
                '/node_modules/*', '/vendor/*', '/bower_components/*', '/jspm_packages/*',
                '/includes/*', '/lib/*', '/libs/*', '/src/*', '/dist/*', '/build/*', '/builds/*',
                '/_next/*', '/.next/*', '/out/*', '/_nuxt/*', '/.nuxt/*',

                # Testing & development
                '/test/*', '/tests/*', '/spec/*', '/specs/*', '/__tests__/*',
                '/debug/*', '/dev/*', '/development/*', '/staging/*',

                # API internal endpoints
                '/api/internal/*', '/api/admin/*', '/api/private/*',

                # System & internal
                '/private/*', '/system/*', '/core/*', '/internal/*',
                '/tmp/*', '/temp/*', '/cache/*', '/logs/*', '/log/*',
                '/backup/*', '/backups/*', '/old/*', '/archive/*', '/archives/*',
                '/config/*', '/configs/*', '/configuration/*',

                # Media upload forms
                '/upload/*', '/uploads/*', '/uploader/*', '/file-upload/*',

                # Search & filtering (often noisy for SEO)
                '/search*', '*/search/*', '?s=*', '?search=*',
                '*/filter/*', '?filter=*', '*/sort/*', '?sort=*',

                # Printer-friendly & special views
                '/print/*', '?print=*', '/preview/*', '?preview=*',
                '/embed/*', '?embed=*', '/amp/*', '/amp',

                # Feed URLs
                '/feed/*', '/feeds/*', '/rss/*', '*.rss', '/atom/*', '*.atom',

                # Common file types to exclude from issues
                '*.json', '*.xml', '*.yaml', '*.yml', '*.toml', '*.ini', '*.conf',
                '*.log', '*.txt', '*.csv', '*.sql', '*.db',
                '*.bak', '*.backup', '*.old', '*.orig', '*.tmp', '*.swp',
                '*.map', '*.min.js', '*.min.css'
            ]
        }

    def _get_strip_params(self):
        """Tracking-parameter patterns for this crawl, or None when disabled"""
        if not self.config.get('ignore_tracking_params', True):
            return None
        patterns = self.config.get('strip_query_params', DEFAULT_TRACKING_PARAMS)
        if isinstance(patterns, str):
            # A settings textarea can arrive as one comma/newline-separated
            # string; iterating it as a list of characters would strip nothing
            patterns = [p.strip() for p in patterns.replace('\n', ',').split(',')]
        return [p for p in patterns if p] if patterns else None

    def update_config(self, new_config):
        """Update crawler configuration"""
        self.config.update(new_config)

        # Update session headers
        self.session.headers.update({
            'User-Agent': self.config['user_agent'],
            'Accept-Language': self.config['accept_language']
        })

        # Add custom headers
        if self.config['custom_headers']:
            self.session.headers.update(self.config['custom_headers'])

        # Sign in to the site, if this crawl is configured to. Applied again
        # at crawl start, so a config that never came through this setter is
        # still authenticated.
        from src.core import site_auth
        self.auth_description = ''
        if site_auth.configured(self.config):
            self.auth_description = site_auth.apply(self.session, self.config)

        # Configure proxy if enabled
        if self.config['enable_proxy'] and self.config['proxy_url']:
            self.session.proxies = {
                'http': self.config['proxy_url'],
                'https': self.config['proxy_url']
            }
        else:
            self.session.proxies = {}

        # Honour allow_cookies: a blocking policy stops Set-Cookie responses
        # from changing later pages (e.g. a locale cookie from /es-us/ making
        # English URLs render in Spanish)
        if self.config.get('allow_cookies', True):
            self.session.cookies.set_policy(DefaultCookiePolicy())
        else:
            self.session.cookies.clear()
            self.session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=[]))

        # Keep the connection pool in step with the configured concurrency
        self._set_pool_size(self.config.get('concurrency', 5))

        # Update rate limiter if it exists
        if self.rate_limiter:
            if self.config['delay'] > 0:
                self.rate_limiter.update_rate(1.0 / self.config['delay'])
            else:
                self.rate_limiter.update_rate(100.0)

    def _crawl_worker(self):
        """Main crawling worker with smooth rate limiting"""
        # Use async approach if JavaScript rendering is enabled
        if self.config.get('enable_javascript', False):
            print("Initializing JavaScript rendering...")
            asyncio.run(self._crawl_async_with_js())
            return

        # Traditional HTTP crawling with smooth rate limiting
        max_workers = self.config.get('concurrency', 5)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            active_futures = {}

            while self.is_running:
                try:
                    # Check if paused
                    if self.is_paused:
                        time.sleep(1)
                        continue

                    # Submit new tasks - fill ALL available slots, apply rate limiting per task
                    while (len(active_futures) < max_workers and
                           self.pages_crawled < self.config['max_urls']):

                        url_info = self.link_manager.get_next_url()
                        if not url_info:
                            break

                        current_url, depth = url_info

                        # Skip if depth exceeded
                        if depth > self.config['max_depth']:
                            continue

                        # Submit crawl task immediately - rate limiting happens inside the worker
                        print(f"Submitting task for: {current_url}")
                        future = executor.submit(self._crawl_url, current_url, depth)
                        active_futures[future] = current_url

                    # Process completed tasks
                    completed_futures = []
                    for future in list(active_futures.keys()):
                        if future.done():
                            completed_futures.append(future)
                            try:
                                result = future.result()
                                if result:
                                    with self.results_lock:
                                        self.crawl_results.append(result)
                                        self.stats['crawled'] += 1
                                        self.pages_crawled += 1
                                        self.stats['depth'] = max(self.stats['depth'], result.get('depth', 0))
                                        print(f"Added URL to results: {result['url']} - Total in results: {len(self.crawl_results)}")
                                        self.event_log.emit('url', result)
                                        if self.db_save_enabled:
                                            self.unsaved_urls.append(result)

                                    # Track per-user memory
                                    self.user_memory.track_url(result)

                                    # Detect issues
                                    issues_before = len(self.issue_detector.detected_issues)
                                    self.issue_detector.detect_issues(result)
                                    issues_after = len(self.issue_detector.detected_issues)

                                    # Track + batch new issues
                                    if issues_after > issues_before:
                                        new_issues = self.issue_detector.detected_issues[issues_before:issues_after]
                                        self.event_log.emit_many('issue', new_issues)
                                        self.user_memory.track_issues(new_issues)
                                        if self.db_save_enabled:
                                            self.unsaved_issues.extend(new_issues)

                                    if (self.db_save_enabled and
                                            len(self.unsaved_urls) >= self.batch_save_size):
                                        self._save_batch_to_db()
                            except Exception as e:
                                print(f"Error in crawl task: {e}")

                    # Remove completed futures
                    for future in completed_futures:
                        del active_futures[future]

                    # Demo mode: check per-user memory limit
                    if self.config.get('demo_mode') and self.user_memory.total_bytes >= self.config.get('demo_memory_limit_bytes', 0):
                        print(f"DEMO MODE: Per-user memory limit reached ({self.user_memory.total_mb:.0f}MB)")
                        self._demo_limit_reached = True
                        break

                    # Check for completion
                    if self.pages_crawled >= self.config['max_urls']:
                        print(f"Reached maximum URLs limit ({self.config['max_urls']})")
                        break

                    # Check if no more work (sitemap discovery may still be
                    # feeding the queue in the background — don't quit early)
                    link_stats = self.link_manager.get_stats()
                    if link_stats['pending'] == 0 and len(active_futures) == 0:
                        if not self._sitemap_discovery_done:
                            time.sleep(0.2)
                            continue
                        print("No more URLs to crawl")
                        break

                    # Tiny sleep only to yield CPU
                    time.sleep(0.001)

                except Exception as e:
                    print(f"Error in crawl worker: {e}")
                    time.sleep(1)

        # Skip post-processing if demo limit was hit — no further memory use
        if not self._demo_limit_reached:
            # Run PageSpeed analysis if enabled
            if self.config.get('enable_pagespeed', False):
                print("Running PageSpeed analysis...")
                self.is_running_pagespeed = True
                self._run_pagespeed_analysis()
                self.is_running_pagespeed = False

            # Update all linked_from fields before completing
            self._update_all_linked_from()

            # Run duplication detection on all crawled content
            if self.issue_detector and self.config.get('enable_duplication_check', True):
                print("Running duplication detection...")
                duplication_threshold = self.config.get('duplication_threshold', 0.85)
                self._detect_duplication_issues(duplication_threshold)
                print(f"Duplication detection complete. Total issues: {len(self.issue_detector.get_issues())}")

            if self.issue_detector:
                self._detect_tracking_link_issues()
                self._audit_sitemaps()
                self._detect_freshness_issues()
                self._run_amp_checks()
                self._run_pdf_checks()
                self._run_link_metrics()
                self._run_spell_check()
                self._run_google_checks()
                self._run_agent_analysts()
                self._run_technical_checks()
                self._run_header_checks()
                self._analyse_link_graph()
                self._audit_cms()
                self._run_consistency_rules()

        # Save final data and set appropriate status
        if self.db_save_enabled and self.crawl_id:
            self._save_batch_to_db(force=True)
            self._detect_content_integrity_issues()
            self._save_batch_to_db(force=True)
            from src.crawl_db import set_crawl_status
            if self._demo_limit_reached:
                set_crawl_status(self.crawl_id, 'demo_stopped')
            else:
                set_crawl_status(self.crawl_id, 'completed')
            self._stamp_first_seen()
            self._prune_old_crawls()
            self._notify_webhook()

        # Mark crawl as complete
        self.is_running = False
        if self._demo_limit_reached:
            print(f"Crawl stopped (demo limit). User memory: {self.user_memory.total_mb:.0f}MB. Crawled: {self.stats['crawled']}")
        else:
            print(f"Crawl completed. Discovered: {self.stats['discovered']}, Crawled: {self.stats['crawled']}")

    def _notify_webhook(self):
        """POST a summary of the finished crawl, when a webhook is configured.

        Fire-and-forget: a webhook that is slow, down or wrong must never fail
        a crawl that already succeeded.
        """
        url = (self.config.get('webhook_url') or '').strip()
        if not url or not self.crawl_id:
            return

        issues = self.issue_detector.get_issues() if self.issue_detector else []
        counts = {'error': 0, 'warning': 0, 'info': 0}
        for issue in issues:
            severity = issue.get('type', 'info')
            counts[severity] = counts.get(severity, 0) + 1

        payload = {
            'crawl_id': self.crawl_id,
            'url': self.base_url,
            'status': 'completed',
            'urls_crawled': self.stats.get('crawled', 0),
            'counts_by_severity': counts,
            'new_issues': [],
            'fixed_issues': [],
        }

        if self.baseline_crawl_id:
            try:
                from src.core.crawl_diff import regression_report
                report = regression_report(self.baseline_crawl_id, self.crawl_id)
                payload['compared_with'] = self.baseline_crawl_id
                payload['new_issues'] = [self._webhook_issue(i) for i in report['new'][:50]]
                payload['fixed_issues'] = [self._webhook_issue(i) for i in report['fixed'][:50]]
                payload['counts'] = report['counts']

                # Whether this change is worth waking someone for. The
                # monitor has existed since the agent layer was written and
                # nothing called it, so every webhook fired with the same
                # urgency whether a crawl found one new info notice or
                # knocked forty pages into a 500. assess() is deterministic;
                # the model is only asked for a sentence, and only when one
                # is configured.
                from src.core.agents import monitor
                verdict = monitor.assess(
                    report,
                    previous_pages=report.get('previous_pages', 0),
                    current_pages=self.stats.get('crawled', 0),
                    config={'mode': self.config.get('agent_mode', 'none')})
                payload['alert'] = verdict.get('alert', False)
                payload['headline'] = verdict.get('headline', '')
                payload['reasons'] = verdict.get('reasons', [])
                if verdict.get('alert') and \
                        (self.config.get('agent_mode') or 'none') != 'none':
                    try:
                        payload['summary'] = monitor.summarise(
                            verdict, self.base_url,
                            config={'mode': self.config.get('agent_mode'),
                                    'model': self.config.get('agent_model'),
                                    'cli_command': self.config.get('agent_cli_command')})
                    except Exception as exc:
                        print(f'Monitor summary unavailable: {exc}')
            except Exception as e:
                print(f"Webhook: could not build the regression summary: {e}")

        def send():
            try:
                self.session.post(url, json=payload, timeout=self.config.get('timeout', 10))
                print(f"Webhook delivered to {url}")
            except Exception as e:
                print(f"Webhook to {url} failed: {e}")

        threading.Thread(target=send, daemon=True).start()

    @staticmethod
    def _webhook_issue(issue):
        return {key: issue.get(key) for key in ('id', 'url', 'check', 'type', 'issue', 'details')}

    def _stamp_first_seen(self):
        """Mark how long each issue has been open, by its stable id"""
        if not self.db_save_enabled or not self.crawl_id or not self.base_domain:
            return
        try:
            from src.core.crawl_diff import first_seen_map
            from src.crawl_db import stamp_first_seen
            seen = first_seen_map(self.base_domain, self.crawl_id)
            stamped = stamp_first_seen(self.crawl_id, seen)
            if stamped:
                print(f"{stamped} issue(s) carried over from an earlier crawl")
        except Exception as e:
            print(f"Could not stamp first-seen crawls: {e}")

    def _prune_old_crawls(self):
        """Apply the retention setting (keep the last N crawls for this user)"""
        keep = self.config.get('keep_last_crawls', 0)
        if not keep or not self.user_id:
            return
        try:
            from src.crawl_db import prune_old_crawls
            removed = prune_old_crawls(self.user_id, keep)
            if removed:
                print(f"Retention: deleted {len(removed)} crawl(s) beyond the last {keep}: {removed}")
        except Exception as e:
            print(f"Retention pruning failed: {e}")

    def _detect_tracking_link_issues(self):
        """Report internal links that carry tracking parameters (once per crawl)"""
        links = self.link_manager.all_links if self.link_manager else []
        self._journal_new_issues(
            lambda: self.issue_detector.detect_tracking_link_issues(links))

    def _journal_new_issues(self, detect):
        """Run an end-of-crawl check, then journal/track/batch whatever it added"""
        issues_before = len(self.issue_detector.detected_issues)
        detect()
        new_issues = self.issue_detector.detected_issues[issues_before:]
        if new_issues:
            self.event_log.emit_many('issue', new_issues)
            self.user_memory.track_issues(new_issues)
            if self.db_save_enabled:
                self.unsaved_issues.extend(new_issues)

    def _update_all_linked_from(self):
        """Update linked_from field for all crawled URLs based on collected source_pages data"""
        print("Updating linked_from data for all URLs...")
        updated = []

        for result in self.crawl_results:
            sources = self.link_manager.get_source_pages(result['url'])
            if sources and sources != result.get('linked_from'):
                result['linked_from'] = sources
                updated.append(result)

        if updated:
            self.event_log.emit_many('url_update', updated)
        print(f"Updated linked_from data for {len(updated)} URLs")

    def _check_image_statuses(self, image_links, depth=0):
        """HEAD-check image URLs to detect broken images.

        Uses a per-crawl cache so the same image URL is only checked once
        even if it appears on many pages.  Runs up to 5 checks in parallel,
        capped at 50 images per page to avoid blocking the crawl.

        Images that won't be fetched as full crawl targets get a result row
        synthesized from the HEAD response so they appear in the Images tab
        without downloading the file.
        """
        check_external = self.config.get('crawl_external', False)

        # Statuses set here are journalled at the end: these links were already
        # announced by the link manager, so the UI only learns about a HEAD
        # result if we emit an update for it
        mutated = []

        to_check = []
        for link in image_links:
            url = link['target_url']
            # Respect the external-crawling setting: don't request (or mint
            # result rows for) images hosted off-domain unless it's enabled
            if not check_external and not self.link_manager.is_internal(url):
                continue
            cached = self._image_status_cache.get(url)
            if cached is not None:
                if link.get('target_status') != cached:
                    link['target_status'] = cached
                    mutated.append(link)
            elif link.get('target_status') is None:
                to_check.append(link)

        if not to_check:
            self._emit_link_updates(mutated)
            return

        def _head_check(link):
            url = link['target_url']
            content_type = ''
            size = 0
            try:
                # Image checks used to bypass the rate limiter entirely, so a
                # 300-page crawl of an image-heavy site made about 3,000
                # unthrottled requests while claiming to honour a delay. A
                # politeness setting that only some requests obey is not one.
                if self.rate_limiter:
                    self.rate_limiter.acquire()
                resp = self.session.head(url, timeout=5, allow_redirects=True)
                link['target_status'] = resp.status_code
                content_type = resp.headers.get('content-type', '').split(';')[0]
                try:
                    size = int(resp.headers.get('content-length') or 0)
                except ValueError:
                    size = 0
            except Exception:
                link['target_status'] = 0
            self._image_status_cache[url] = link['target_status']
            if size:
                self._image_size_cache[url] = size

            # Skip synthesis for URLs that will be (or were) crawled for real
            if not self._should_crawl_url(url):
                self._record_image_result(url, link['target_status'], content_type, size, depth)

        batch = to_check[:50]
        with ThreadPoolExecutor(max_workers=min(IMAGE_CHECK_WORKERS, len(batch))) as pool:
            pool.map(_head_check, batch)

        mutated.extend(batch)
        self._emit_link_updates(mutated)

    def _emit_link_updates(self, links):
        """Journal status changes made to links outside the link manager"""
        if links:
            self.event_log.emit_many('link_update', links)

    def _apply_image_sizes(self, result):
        """Copy measured image sizes onto the page that embeds them.

        The HEAD check knows each image's transfer size, but it only ever wrote
        it to the image's own synthesized row - so the page's images[] carried
        no bytes and the oversized-image check had nothing to judge.
        """
        images = result.get('images')
        if not isinstance(images, list):
            return
        for image in images:
            if not isinstance(image, dict) or image.get('bytes'):
                continue
            size = self._image_size_cache.get(image.get('src'))
            if size:
                image['bytes'] = size

    def _apply_rendered_geometry(self, result, observations):
        """Copy what the browser measured about each image onto the page.

        The rendered pass already works out whether an image falls inside the
        first screenful, and how far down it sits, but it kept that to itself:
        the page checks only ever saw DOM order, so an image 2,400px down was
        reported as sitting "at the top of the page". Matching on src carries
        the measurement across, and an image the renderer never saw simply
        keeps no verdict.
        """
        images = result.get('images')
        rendered = observations.get('images')
        if not isinstance(images, list) or not isinstance(rendered, list):
            return
        measured = {}
        for item in rendered:
            if isinstance(item, dict) and item.get('src'):
                measured[item['src']] = item
        for image in images:
            if not isinstance(image, dict):
                continue
            seen = measured.get(image.get('src'))
            if not seen:
                continue
            if 'above_fold' in seen:
                image['above_fold'] = bool(seen['above_fold'])
            if seen.get('top_px') is not None:
                image['top_px'] = seen['top_px']
            if seen.get('fetchpriority'):
                image['fetchpriority'] = seen['fetchpriority']

    def _record_image_result(self, url, status_code, content_type, size, depth):
        """Add a result row for an image from its HEAD response (no body download)"""
        sources = self.link_manager.get_source_pages(url)

        with self.results_lock:
            if url in self._synthesized_image_urls:
                return
            self._synthesized_image_urls.add(url)

            result = self.seo_extractor.create_empty_result(url, depth, status_code)
            result['content_type'] = content_type
            result['size'] = size
            result['is_internal'] = self.link_manager.is_internal(url)
            result['linked_from'] = sources
            self.crawl_results.append(result)
            self.stats['crawled'] += 1
            # a result counts as discovered (issue #94)
            self.link_manager.mark_discovered(url)
            self.event_log.emit('url', result)
            if self.db_save_enabled:
                self.unsaved_urls.append(result)

        self.user_memory.track_url(result)

    def _should_crawl_url(self, url):
        """Check if URL should be crawled based on settings"""
        parsed = urlparse(url)

        # Check external domain policy
        if not self.config['crawl_external']:
            if not self.link_manager.is_internal(url):
                return False

        # Check robots.txt
        if self.config['respect_robots']:
            if not self._check_robots_txt(url):
                return False

        # Check file extensions
        path = parsed.path.lower()
        if '.' in path:
            extension = path.split('.')[-1]

            # A PDF asked for explicitly has to clear both filters, because
            # it is on the exclude list by default *and* absent from
            # include_extensions, which is an HTML-only whitelist. Bypassing
            # only the first left "crawl PDFs" crawling none.
            wanted_pdf = extension == 'pdf' and self.config.get('crawl_pdfs')

            if extension in self.config['exclude_extensions'] and not wanted_pdf:
                return False

            # "Crawl Images" setting lets image URLs through the include filter
            if extension in IMAGE_EXTENSIONS and self.config.get('crawl_images', False):
                pass
            elif wanted_pdf:
                pass
            elif self.config['include_extensions'] and extension not in self.config['include_extensions']:
                return False

        # Check URL patterns
        if self.config['exclude_patterns']:
            for pattern in self.config['exclude_patterns']:
                if pattern and re.search(pattern, url):
                    return False

        if self.config['include_patterns']:
            pattern_match = False
            for pattern in self.config['include_patterns']:
                if pattern and re.search(pattern, url):
                    pattern_match = True
                    break
            if not pattern_match:
                return False

        return True

    def _check_robots_txt(self, url):
        """Check if URL is allowed by robots.txt"""
        try:
            parsed = urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"

            if robots_url not in self._robots_cache:
                rp = RobotFileParser()
                rp.set_url(robots_url)
                try:
                    # Fetch robots.txt with our session (proper User-Agent)
                    # instead of rp.read() which uses urllib with Python's
                    # default UA — many servers return 403 for that, causing
                    # RobotFileParser to set disallow_all=True and block everything.
                    response = self.session.get(robots_url, timeout=10)
                    status = response.status_code

                    # RFC 9309 section 2.3.1: a 4xx means robots.txt is
                    # *unavailable*, and an unavailable robots.txt allows
                    # everything. This had it backwards for 401/403, so one
                    # Cloudflare bot challenge on /robots.txt disallowed an
                    # entire site: the crawl returned almost nothing and then
                    # reported canonicals as "blocked by robots.txt" that
                    # nobody had blocked. Found crawling ten real sites.
                    if 400 <= status <= 499:
                        rp.allow_all = True
                        self._note_robots_unreadable(robots_url, f'returned {status}')
                    elif status >= 500:
                        # "Unreachable" in the same section: the file may exist
                        # and we cannot see it, so stay out until it answers.
                        rp.disallow_all = True
                        self._note_robots_unreadable(
                            robots_url, f'returned {status}; treating the site as '
                            'off limits until it answers')
                    elif self._looks_like_a_challenge(response):
                        # 200, but what came back is a bot-check page, not a
                        # robots file. Parsing HTML as robots.txt is garbage in.
                        rp.allow_all = True
                        self._note_robots_unreadable(
                            robots_url, 'answered with a bot-check page rather '
                            'than a robots file')
                    else:
                        rp.parse(response.text.splitlines())
                    self._robots_cache[robots_url] = rp
                except Exception:
                    return True

            rp = self._robots_cache[robots_url]
            user_agent = self.config.get('user_agent', '*')
            # A Crawl-delay the site asked for is a request to go slower,
            # and ignoring it while claiming to respect robots.txt is not
            # honest. Only ever slower than the configured rate, never faster.
            self._note_crawl_delay(rp, user_agent)
            return rp.can_fetch(user_agent, url)

        except Exception:
            return True

    @staticmethod
    def _looks_like_a_challenge(response):
        """True when a 200 response is a bot check, not a robots.txt.

        Cloudflare, Akamai and friends answer an automated client with an
        interstitial that is HTML and has nothing to do with robots. Feeding
        that to a robots parser is meaningless at best; what it must never do
        is read as a rule.
        """
        content_type = (response.headers.get('Content-Type') or '').lower()
        if 'html' in content_type:
            return True
        head = (response.text or '')[:2000].lower()
        if '<html' in head or '<!doctype html' in head:
            return True
        markers = ('just a moment', 'checking your browser', 'cf-browser-verification',
                   'enable javascript and cookies', 'attention required',
                   'access denied', 'ddos protection')
        return any(marker in head for marker in markers)

    def _note_robots_unreadable(self, robots_url, why):
        """Say once, loudly, that robots.txt could not be read, and what follows.

        A crawl that quietly returns nothing because of this is the worst
        outcome available: it looks exactly like a site with no pages.
        """
        if getattr(self, '_robots_notice_given', False):
            return
        self._robots_notice_given = True
        print(f'robots.txt at {robots_url} {why}. '
              f'Proceeding as RFC 9309 directs for an unreadable file.')

    def _note_crawl_delay(self, parser, user_agent):
        """Slow the crawl to a Crawl-delay the site published, if it is slower."""
        if self._robots_delay_applied:
            return
        try:
            asked = parser.crawl_delay(user_agent)
        except Exception:
            return
        self._robots_delay_applied = True
        try:
            asked = float(asked) if asked is not None else 0.0
        except (TypeError, ValueError):
            return
        if asked <= 0 or asked <= float(self.config.get('delay') or 0):
            return
        print(f'robots.txt asks for a {asked}s crawl delay; slowing to match')
        self.config['delay'] = asked
        if self.rate_limiter:
            self.rate_limiter.update_rate(1.0 / asked)

