"""Fetching one URL, by plain request or through a browser.

The two paths produce the same result dict, and reading them side by
side is the point: every field one records and the other forgets is a
check that silently stops working on JavaScript-rendered sites. Four
such gaps have been found and fixed here already - response headers,
the redirect chain, image geometry, and the served content type, which
was hardcoded to text/html and so audited images as web pages.

Lifted out of crawler.py unchanged to bring that file under the project's
1,000-line limit.
"""

from src.core import http_status
from src.core.microdata import extract as _other_structured_data
import asyncio
import socket
import ssl
import time

import requests
from bs4 import BeautifulSoup

from src.core import header_checks

# Defined in crawler.py, which imports this module: the import is deferred
# into the functions that need it to avoid a circular import at load time.
def _classify_fetch_error():
    """Imported late: crawler.py defines it and imports this module."""
    from src.crawler import classify_fetch_error
    return classify_fetch_error



# The AMP checks read page['html'], and nothing ever set it, so every one of
# them was dead on a live crawl while passing its unit tests - those pass the
# markup in by hand. The whole document is not kept for every page, because on
# a large crawl that is hundreds of megabytes for a feature most sites do not
# use. It is kept only for pages that say they are AMP, which is the only
# place those checks look.
# `rel="amphtml"` is in here because the pairing checks read the *desktop*
# page's markup to find the link, and that page is not itself AMP - so
# keeping markup only for AMP pages left those three checks just as dead as
# the rest were. Half a fix is its own kind of bug.
_AMP_MARKERS = ('<html amp', '<html \u26a1', 'amp-boilerplate',
                'cdn.ampproject.org', 'rel="amphtml"', "rel='amphtml'")


def _keep_html_if_amp(result, markup):
    """Hold on to the document, but only for pages claiming to be AMP."""
    head = (markup or '')[:8000].lower()
    if any(marker in head for marker in _AMP_MARKERS):
        result['html'] = markup

class FetchingMixin:
    """One URL in, one crawl result out."""


    def _inspect_pdf_if_needed(self, result, url, data=None):
        """Read a PDF as a document, on whichever path fetched it.

        The rendering path sends every URL to Chromium, which wraps a PDF in
        its own viewer document - the same thing that once had .webp images
        audited as web pages. So with JavaScript rendering on, which is a
        common setting, no PDF was ever inspected and every PDF check was
        silently dead. When the bytes are not already in hand they are
        fetched once, for PDFs only.
        """
        if not self.config.get('crawl_pdfs'):
            return
        from src.core.pdf_checks import inspect as _inspect_pdf, looks_like_pdf
        content_type = (result.get('content_type') or '')
        if not (looks_like_pdf(data or b'', content_type)
                or url.lower().split('?')[0].endswith('.pdf')):
            return
        if data is None:
            try:
                response = self.session.get(url, timeout=self.config['timeout'])
                data = response.content
            except Exception:
                return
        if looks_like_pdf(data, content_type) or data[:5] == b'%PDF-':
            result['pdf'] = _inspect_pdf(data, content_type or 'application/pdf')

    def _crawl_url(self, url, depth):
        """Crawl a single URL"""
        # Use JavaScript rendering if enabled
        if self.config.get('enable_javascript', False):
            return asyncio.run(self._crawl_url_with_javascript(url, depth))
        else:
            return self._crawl_url_with_requests(url, depth)

    def _crawl_url_with_requests(self, url, depth):
        """Crawl a single URL using traditional HTTP requests"""
        print(f"Starting crawl of {url}")
        retries = self.config.get('retries', 3)
        start_time = time.time()

        try:
            # Check file size if configured
            if self.config.get('max_file_size', 0) > 0:
                try:
                    head_response = self.session.head(
                        url,
                        timeout=self.config['timeout'],
                        allow_redirects=self.config['follow_redirects']
                    )
                    content_length = head_response.headers.get('content-length')
                    if content_length and int(content_length) > self.config['max_file_size']:
                        return self.seo_extractor.create_empty_result(
                            url, depth, 0,
                            f'File too large: {content_length} bytes',
                            error_type='file_too_large'
                        )
                except:
                    pass  # Continue if HEAD request fails

            # Fetch the page with retries
            response = None
            fetch_ms = 0
            for attempt in range(retries + 1):
                try:
                    fetch_started = time.time()
                    response = self.session.get(
                        url,
                        timeout=self.config['timeout'],
                        allow_redirects=self.config['follow_redirects']
                    )
                    # Time the site took to serve the page, measured before any
                    # parsing or extraction: our own work is not the site's
                    # response time (same reasoning as the render wait, #93)
                    fetch_ms = (time.time() - fetch_started) * 1000
                    break
                except Exception as e:
                    if attempt >= retries:
                        raise e
                    time.sleep(1)

            # Determine if URL is internal
            is_internal = self.link_manager.is_internal(url)

            # Where the content actually lives after any redirects — relative
            # links in the HTML must resolve against this, not the requested
            # URL (a redirect to another site would otherwise spray that
            # site's relative links across our own domain)
            final_url = str(response.url) if response.url else url
            content_is_internal = self.link_manager.is_internal(final_url)

            # Create result structure
            result = {
                'url': url,
                'status_code': response.status_code,
                'error_type': None,
                'content_type': response.headers.get('content-type', '').split(';')[0],
                'size': len(response.content),
                'is_internal': is_internal,
                'depth': depth,
                'title': '',
                'meta_description': '',
                'h1': '',
                'h2': [],
                'h3': [],
                'word_count': 0,
                'meta_tags': {},
                'og_tags': {},
                'twitter_tags': {},
                'canonical_url': '',
                'lang': '',
                'charset': '',
                'viewport': '',
                'robots': '',
                'author': '',
                'keywords': '',
                'generator': '',
                'theme_color': '',
                'json_ld': [],
                'json_ld_raw': [],
                'json_ld_errors': [],
                'analytics': {
                    'google_analytics': False,
                    'gtag': False,
                    'ga4_id': '',
                    'gtm_id': '',
                    'facebook_pixel': False,
                    'hotjar': False,
                    'mixpanel': False
                },
                'images': [],
                'external_links': 0,
                'internal_links': 0,
                'response_time': 0,
                'redirects': [],
                'hreflang': [],
                'schema_org': [],
                'linked_from': []
            }

            # Response headers kept for freshness, caching and weight checks
            result['cache_control'] = response.headers.get('cache-control', '')
            result['last_modified'] = response.headers.get('last-modified', '')
            result['etag'] = response.headers.get('etag', '')
            # The whole header set, for the security checks in
            # src/core/header_checks.py. requests joins repeated headers into one
            # comma-separated string, which is lossy for Set-Cookie, so the
            # originals are kept as a list wherever urllib3 still has them.
            result['response_headers'] = {name.lower(): value
                                          for name, value in response.headers.items()}
            try:
                sent_cookies = response.raw.headers.getlist('Set-Cookie')
                if sent_cookies:
                    result['response_headers']['set-cookie'] = list(sent_cookies)
            except Exception:
                pass
            declared_length = response.headers.get('content-length', '')
            result['content_length'] = int(declared_length) if declared_length.isdigit() else None
            result['ttfb'] = round(response.elapsed.total_seconds() * 1000, 2)

            # Record the redirect chain (previously always left empty)
            if response.history:
                result['redirects'] = [
                    {'url': str(h.url), 'status_code': h.status_code} for h in response.history
                ]
                result['redirected_to'] = final_url

            # A PDF is read as a document: title, pages, text layer. The
            # report is kept, not the bytes, so a crawl of a documents-heavy
            # site does not hold hundreds of megabytes in memory.
            self._inspect_pdf_if_needed(result, url, response.content)

            # Only parse HTML content
            if 'text/html' in response.headers.get('content-type', ''):
                soup = BeautifulSoup(response.content, 'html.parser')

                # Extract comprehensive data using SEO extractor
                self.seo_extractor.extract_basic_seo_data(soup, result)
                self.seo_extractor.extract_meta_tags(soup, result)
                self.seo_extractor.extract_opengraph_tags(soup, result)
                self.seo_extractor.extract_twitter_tags(soup, result)
                self.seo_extractor.extract_json_ld(soup, result)
                # Microdata and RDFa too, so a site that marks up in itemprop
                # or typeof is not told it has no structured data at all.
                result.update(_other_structured_data(response.text))
                _keep_html_if_amp(result, response.text)
                self.seo_extractor.extract_analytics_tracking(soup, response.text, result)
                self.seo_extractor.extract_images(soup, final_url, result)
                self.seo_extractor.extract_link_counts(soup, result, self.base_domain)
                self.seo_extractor.extract_hreflang(soup, result)
                self.seo_extractor.extract_schema_org(soup, result)
                self.seo_extractor.extract_main_text(
                    soup, result, self.config.get('main_text_ignore_selectors'))
                self.seo_extractor.extract_headings(soup, result)
                header_checks.extract_page_resources(soup, final_url, result)
                self._keep_page_html(url, response.text)

                # Collect all links (returns only the ones new to this crawl)
                new_links = self.link_manager.collect_all_links(soup, url, self.crawl_results,
                                                                base_url=final_url)

                # Track + batch new links
                if new_links:
                    # HEAD-check image URLs for broken image detection
                    image_links = [l for l in new_links if l.get('placement') == 'image']
                    if image_links:
                        self._check_image_statuses(image_links, depth + 1)
                        self._apply_image_sizes(result)
                        # 401/403/429 mean the CDN refused *this crawler*, not
                        # that the image is missing - see src/core/http_status.py
                        broken = [l for l in image_links
                                  if http_status.is_broken(l.get('target_status'))
                                  or http_status.is_no_response(l.get('target_status'))]
                        blocked = [l for l in image_links
                                   if http_status.is_blocked(l.get('target_status'))]
                        if broken:
                            result['broken_images'] = [
                                {'url': l['target_url'], 'status': l['target_status']}
                                for l in broken
                            ]
                        if blocked:
                            result['blocked_images'] = [
                                {'url': l['target_url'], 'status': l['target_status']}
                                for l in blocked
                            ]

                    # (link events were journalled by the link manager)
                    self.user_memory.track_links(new_links)
                    if self.db_save_enabled:
                        self.unsaved_links.extend(new_links)

                # Extract links for further crawling. Gate on where the
                # content actually lives: a page that redirected off-domain
                # is external content and shouldn't seed the queue unless
                # external crawling is enabled
                should_extract = (
                    (content_is_internal and depth < self.config['max_depth']) or
                    (self.config['crawl_external'] and depth < self.config['max_depth'])
                )

                if should_extract:
                    self.link_manager.extract_links(soup, url, depth + 1, self._should_crawl_url,
                                                    include_images=self.config.get('crawl_images', False),
                                                    base_url=final_url)

            # Populate linked_from after all link collection is complete
            result['linked_from'] = self.link_manager.get_source_pages(url)
            result['response_time'] = round(fetch_ms, 2)
            result['processing_time'] = round((time.time() - start_time) * 1000 - fetch_ms, 2)

            # NB: the DB batch is appended by the worker when it accepts this
            # result into crawl_results, not here. A fetch that finishes after
            # the worker stops (max_urls reached) must not reach the database,
            # or a loaded crawl would show rows the crawl never counted.
            return result

        except Exception as e:
            return self.seo_extractor.create_empty_result(
                url, depth, 0, str(e),
                error_type=_classify_fetch_error()(e)
            )

    async def _crawl_url_with_javascript(self, url, depth):
        """Crawl a single URL using JavaScript rendering"""
        start_time = time.time()

        # Chromium cannot render a PDF, a zip or a font. Sending one there
        # returned a row with status 0, no content type and no size - a
        # perfectly healthy file reported as unreachable, and before the
        # no-response fix, ten invented content findings on top. The renderer
        # has always known which URLs it should not take; nothing asked it.
        if not self.js_renderer.should_use_javascript(url):
            # _crawl_url_with_requests, not _crawl_url: the latter dispatches
            # straight back here when JavaScript rendering is on, which is an
            # infinite loop rather than a fallback.
            return await asyncio.get_running_loop().run_in_executor(
                None, self._crawl_url_with_requests, url, depth)

        try:
            # Rendered-mode checks (opt-in): they need the live page, so they
            # ride along with the render rather than re-visiting the URL
            observations = {}
            observations_by_width = []
            console_messages = None
            before_navigate = observe = None
            if (self.config.get('rendered_checks', False)
                    or self.config.get('accessibility_checks', False)):
                from src.core import rendered_checks

                custom_js = (self.config.get('custom_js') or '').strip()

                async def before_navigate(page):
                    nonlocal console_messages
                    console_messages = rendered_checks.attach_console_listener(page)
                    # An operator's own script, run before every navigation:
                    # dismiss a consent wall, set a feature flag, sign in.
                    # The hook existed and nothing could reach it.
                    if custom_js:
                        try:
                            await page.add_init_script(custom_js)
                        except Exception as e:
                            print(f'Custom JavaScript could not be installed: {e}')

                observe_config = {'accessibility': {
                    'enabled': bool(self.config.get('accessibility_checks', False))}}

                extra_widths = [w for w in self.config.get('rendered_widths') or []
                                if isinstance(w, int) and w > 0]

                async def observe(page):
                    # The first pass is the viewport the page was loaded at.
                    first = await rendered_checks.collect(page, observe_config)
                    observations.update(first)
                    observations_by_width.append(first)
                    # Then look again at each extra width. Resizing reflows the
                    # layout without a second fetch, which is what the
                    # width-dependent checks need - a nav that disappears at
                    # phone width, a table that overflows. Accessibility is not
                    # repeated: axe judges the DOM, not the layout.
                    narrow_config = dict(observe_config, accessibility={'enabled': False})
                    height = int(self.config.get('js_viewport_height', 1080) or 1080)
                    for width in extra_widths:
                        try:
                            await page.set_viewport_size({'width': width, 'height': height})
                            observations_by_width.append(
                                await rendered_checks.collect(page, narrow_config))
                        except Exception as e:
                            print(f'Could not observe {url} at {width}px: {e}')

            # Render page with JavaScript
            html_content, status_code, error, final_url, timing = \
                await self.js_renderer.render_page(
                    url, before_navigate=before_navigate, observe=observe)

            if error:
                return self.seo_extractor.create_empty_result(
                    url, depth, status_code, error,
                    error_type=_classify_fetch_error()(error) if status_code == 0 else None
                )

            # Determine if URL is internal
            is_internal = self.link_manager.is_internal(url)

            # Where the content actually lives after any redirects
            final_url = final_url or url
            content_is_internal = self.link_manager.is_internal(final_url)

            # What the server actually served. Assuming text/html here is
            # how a .webp came to be audited as a web page: Chromium wraps a
            # standalone image in its own viewer document, and every page
            # check then fired against that - missing H1, no meta
            # description, no canonical, thin content. 45 junk findings out
            # of 90 on one crawl.
            served_type = ((timing.get('http') or {}).get('headers') or {}).get('content-type', '')
            served_type = served_type.split(';')[0].strip().lower()

            # Create result structure
            result = {
                'url': url,
                'status_code': status_code,
                'error_type': None,
                'content_type': served_type or 'text/html',
                'size': len(html_content.encode('utf-8')),
                'is_internal': is_internal,
                'depth': depth,
                'title': '',
                'meta_description': '',
                'h1': '',
                'h2': [],
                'h3': [],
                'word_count': 0,
                'meta_tags': {},
                'og_tags': {},
                'twitter_tags': {},
                'canonical_url': '',
                'lang': '',
                'charset': '',
                'viewport': '',
                'robots': '',
                'author': '',
                'keywords': '',
                'generator': '',
                'theme_color': '',
                'json_ld': [],
                'json_ld_raw': [],
                'json_ld_errors': [],
                'analytics': {
                    'google_analytics': False,
                    'gtag': False,
                    'ga4_id': '',
                    'gtm_id': '',
                    'facebook_pixel': False,
                    'hotjar': False,
                    'mixpanel': False
                },
                'images': [],
                'external_links': 0,
                'internal_links': 0,
                'response_time': 0,
                'redirects': [],
                'hreflang': [],
                'schema_org': [],
                'linked_from': [],
                'javascript_rendered': True
            }

            # Parse HTML
            soup = BeautifulSoup(html_content, 'html.parser')

            # Extract comprehensive data
            self.seo_extractor.extract_basic_seo_data(soup, result)
            self.seo_extractor.extract_meta_tags(soup, result)
            self.seo_extractor.extract_opengraph_tags(soup, result)
            self.seo_extractor.extract_twitter_tags(soup, result)
            self.seo_extractor.extract_json_ld(soup, result)
            result.update(_other_structured_data(html_content))
            _keep_html_if_amp(result, html_content)
            self._inspect_pdf_if_needed(result, url)
            self.seo_extractor.extract_analytics_tracking(soup, html_content, result)
            self.seo_extractor.extract_images(soup, final_url, result)
            self.seo_extractor.extract_link_counts(soup, result, self.base_domain)
            self.seo_extractor.extract_hreflang(soup, result)
            self.seo_extractor.extract_schema_org(soup, result)
            self.seo_extractor.extract_main_text(
                soup, result, self.config.get('main_text_ignore_selectors'))
            self.seo_extractor.extract_headings(soup, result)
            header_checks.extract_page_resources(soup, final_url, result)
            self._keep_page_html(url, html_content)

            # Collect all links (returns only the ones new to this crawl)
            new_links = self.link_manager.collect_all_links(soup, url, self.crawl_results,
                                                            base_url=final_url)

            # Track + batch new links
            if new_links:
                # HEAD-check image URLs for broken image detection
                image_links = [l for l in new_links if l.get('placement') == 'image']
                if image_links:
                    self._check_image_statuses(image_links, depth + 1)
                    self._apply_image_sizes(result)
                    # Same rule as the HTTP path: refused is not missing.
                    broken = [l for l in image_links
                              if http_status.is_broken(l.get('target_status'))
                              or http_status.is_no_response(l.get('target_status'))]
                    blocked = [l for l in image_links
                               if http_status.is_blocked(l.get('target_status'))]
                    if broken:
                        result['broken_images'] = [
                            {'url': l['target_url'], 'status': l['target_status']}
                            for l in broken
                        ]
                    if blocked:
                        result['blocked_images'] = [
                            {'url': l['target_url'], 'status': l['target_status']}
                            for l in blocked
                        ]

                # (link events were journalled by the link manager)
                self.user_memory.track_links(new_links)
                if self.db_save_enabled:
                    self.unsaved_links.extend(new_links)

            # Extract links for further crawling. Gated on
            # where the content lives, as in the HTTP path.
            should_extract = (
                (content_is_internal and depth < self.config['max_depth']) or
                (self.config['crawl_external'] and depth < self.config['max_depth'])
            )

            if should_extract:
                self.link_manager.extract_links(soup, url, depth + 1, self._should_crawl_url,
                                                include_images=self.config.get('crawl_images', False),
                                                base_url=final_url)

            if observations:
                self._apply_rendered_geometry(result, observations)
                self._record_rendered_issues(
                    url, observations, console_messages, html_content,
                    observations_by_width)

            # Populate linked_from after all link collection is complete
            result['linked_from'] = self.link_manager.get_source_pages(url)
            # The render wait is a setting, not slowness,
            # so keep it out of response_time (issue #93).
            result['response_time'] = round(timing.get('response_ms') or 0, 2)
            self._apply_rendered_http_metadata(result, timing, url)
            result['render_time'] = round(timing.get('render_ms') or 0, 2)

            # NB: the DB batch is appended by the worker when it accepts this
            # result into crawl_results, not here. A fetch that finishes after
            # the worker stops (max_urls reached) must not reach the database,
            # or a loaded crawl would show rows the crawl never counted.
            return result

        except Exception as e:
            return self.seo_extractor.create_empty_result(
                url, depth, 0, f'JavaScript rendering error: {str(e)}',
                error_type=_classify_fetch_error()(e)
            )

    async def _crawl_async_with_js(self):
        """Async crawling loop for JavaScript rendering"""
        try:
            # Initialize JavaScript renderer
            await self.js_renderer.initialize()

            max_workers = self.config.get('js_max_concurrent_pages', 3)
            active_tasks = set()

            while self.is_running and self.pages_crawled < self.config['max_urls']:
                # Check if paused
                if self.is_paused:
                    await asyncio.sleep(1)
                    continue

                # Submit new tasks - fill ALL available slots
                while len(active_tasks) < max_workers:
                    url_info = self.link_manager.get_next_url()
                    if not url_info:
                        break

                    current_url, depth = url_info

                    if depth <= self.config['max_depth']:
                        # SMOOTH RATE LIMITING: Only apply if delay > 0
                        if self.config.get('delay', 0) > 0:
                            self.rate_limiter.acquire()

                        # Create task
                        task = asyncio.create_task(self._crawl_url_with_javascript(current_url, depth))
                        active_tasks.add(task)

                # Process completed tasks
                if active_tasks:
                    done, active_tasks = await asyncio.wait(active_tasks, timeout=0.01, return_when=asyncio.FIRST_COMPLETED)

                    for task in done:
                        try:
                            result = await task
                            if result:
                                with self.results_lock:
                                    self.crawl_results.append(result)
                                    self.stats['crawled'] += 1
                                    self.pages_crawled += 1
                                    self.stats['depth'] = max(self.stats['depth'], result.get('depth', 0))
                                    print(f"Added URL to results (JS): {result['url']} - Total in results: {len(self.crawl_results)}")
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
                            print(f"Error in async crawl task: {e}")

                # Demo mode: check per-user memory limit
                if self.config.get('demo_mode') and self.user_memory.total_bytes >= self.config.get('demo_memory_limit_bytes', 0):
                    print(f"DEMO MODE: Per-user memory limit reached ({self.user_memory.total_mb:.0f}MB)")
                    self._demo_limit_reached = True
                    break

                # Check completion (sitemap discovery may still be feeding
                # the queue in the background — don't quit early)
                link_stats = self.link_manager.get_stats()
                if link_stats['pending'] == 0 and len(active_tasks) == 0:
                    if not self._sitemap_discovery_done:
                        await asyncio.sleep(0.2)
                        continue
                    print("No more URLs to crawl")
                    break

                await asyncio.sleep(0.001)

            # Skip post-processing if demo limit was hit
            if not self._demo_limit_reached:
                # Run PageSpeed if enabled
                if self.config.get('enable_pagespeed', False):
                    self.is_running_pagespeed = True
                    self._run_pagespeed_analysis()
                    self.is_running_pagespeed = False

        finally:
            if not self._demo_limit_reached:
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

            # Clean up
            await self.js_renderer.cleanup()
            self.is_running = False
            print(f"Crawl completed. Discovered: {self.stats['discovered']}, Crawled: {self.stats['crawled']}")

