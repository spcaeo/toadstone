"""The passes that run once a crawl has finished gathering.

Every one of these looks at the whole crawl rather than one page:
duplication, sitemaps, technical and header checks, the link graph, the
CMS, consistency rules, AMP, spelling, dates, content integrity, the
Google integrations and the agent analysts.

They share a shape. Each builds its findings, takes the issue lock once,
and extends the detector; most run inside `_journal_new_issues` so a new
finding reaches the event log and the database the same way a per-page
finding does.

Lifted out of crawler.py unchanged to bring that file back under the
project's 1,000-line limit.
"""

from src.core import header_checks

class AnalysisPassesMixin:
    """Everything the crawler works out after the fetching is done."""

    def _detect_duplication_issues(self, duplication_threshold):
        """Run duplication detection and journal/track/batch the new issues"""
        self._journal_new_issues(
            lambda: self.issue_detector.detect_duplication_issues(
                self.crawl_results, duplication_threshold))

    def _audit_sitemaps(self):
        """Audit the site's sitemaps against what the crawl actually found.

        Also fills sitemap_lastmod, which the freshness checks read, so this
        runs before them.
        """
        if not self.config.get('audit_sitemaps', True) or not self.base_url:
            return

        def run():
            from src.core import sitemap_audit
            outcome = sitemap_audit.audit_site(
                self.session, self.base_url, self.crawl_results,
                robots_checker=self._check_robots_txt if self.config.get('respect_robots') else None,
                config={'timeout': self.config.get('timeout', 10)},
                previous_entries=self._previous_sitemap_entries())
            self.sitemap_lastmod = {
                url: entry.get('lastmod')
                for url, entry in outcome['data'].get('entries', {}).items()
            }
            self.sitemap_files = outcome['files']
            self.sitemap_coverage = outcome['coverage']
            if self.db_save_enabled and self.crawl_id:
                from src.crawl_db import save_sitemap_audit
                save_sitemap_audit(self.crawl_id, outcome['files'], outcome['coverage'],
                                   entries=self.sitemap_lastmod)
            print(f"Sitemap audit: {len(self.sitemap_lastmod)} URLs across "
                  f"{len(outcome['files'])} file(s), {len(outcome['issues'])} issue(s)")
            with self.issue_detector.issues_lock:
                self.issue_detector.detected_issues.extend(outcome['issues'])

        try:
            self._journal_new_issues(run)
        except Exception as e:
            print(f"Sitemap audit failed: {e}")

    def _previous_sitemap_entries(self):
        """{url: {lastmod, main_text_hash}} from the previous crawl, for the
        "content changed but lastmod did not" check"""
        if not self.baseline_crawl_id:
            return None
        try:
            from src.crawl_db import get_crawl_by_id, load_crawled_urls
            baseline = get_crawl_by_id(self.baseline_crawl_id) or {}
            lastmods = baseline.get('sitemap_entries') or {}
            return {page['url']: {'main_text_hash': page.get('main_text_hash'),
                                  'lastmod': lastmods.get(page['url'])}
                    for page in load_crawled_urls(self.baseline_crawl_id)}
        except Exception as e:
            print(f"Could not load the previous crawl's pages: {e}")
            return None

    def _run_technical_checks(self):
        """The standard technical checklist over the whole crawl"""
        if not self.config.get('technical_checks', True) or not self.base_url:
            return

        def run():
            from src.core.technical_checks import technical_issues
            links = self.link_manager.all_links if self.link_manager else []
            found = technical_issues(
                self.crawl_results, links, self.base_url,
                config={'robots_checker': self._check_robots_txt
                                          if self.config.get('respect_robots') else None,
                        'sitemap_urls': set(self.sitemap_lastmod) or None,
                        'tracking_params': self._get_strip_params()})
            print(f"Technical checks: {len(found)} issue(s)")
            with self.issue_detector.issues_lock:
                self.issue_detector.detected_issues.extend(found)

        try:
            self._journal_new_issues(run)
        except Exception as e:
            print(f"Technical checks failed: {e}")

    def _run_header_checks(self):
        """Security headers, mixed content and pagination over the whole crawl"""
        if not self.config.get('header_checks', True) or not self.base_url:
            return

        def run():
            links = self.link_manager.all_links if self.link_manager else []
            found = header_checks.header_issues(
                self.crawl_results, links, self.base_url,
                config={'tracking_params': self._get_strip_params()})
            print(f"Header checks: {len(found)} issue(s)")
            with self.issue_detector.issues_lock:
                self.issue_detector.detected_issues.extend(found)

        try:
            self._journal_new_issues(run)
        except Exception as e:
            print(f"Header checks failed: {e}")

    def _selector_rules_present(self):
        """True when this site's rules file has a rule that reads the markup.

        Those rules cannot run against a stored page - the HTML is not kept -
        so the crawl has to hold on to it while it passes. Any other crawl
        keeps nothing.
        """
        if not self.config.get('run_rules', True) or not self.base_domain:
            return False
        try:
            from src.core import rules_engine
            loaded = rules_engine.load_site_rules(self.base_domain)
            for rule in loaded.get('rules') or []:
                raw = rule.get('raw', rule) if isinstance(rule, dict) else {}
                if raw.get('selector') or rule.get('selector'):
                    print('A rule reads the page markup, so this crawl keeps it in memory')
                    return True
        except Exception as e:
            print(f"Could not read the rules file: {e}")
        return False

    def _keep_page_html(self, url, html_content):
        """Hold a page's markup for the selector rules, within a budget"""
        if not self._rules_need_html or not html_content:
            return
        limit = self.config.get('rules_html_pages', 2000)
        if len(self._page_html) >= limit:
            return
        self._page_html[url] = html_content[:self.config.get('rules_html_max_bytes', 500000)]

    def _run_consistency_rules(self):
        """The site's own rules file: banned claims, required wording, authors"""
        if not self.config.get('run_rules', True) or not self.base_domain:
            return

        def run():
            from src.core import rules_engine
            loaded = rules_engine.load_site_rules(self.base_domain)
            rules = loaded.get('rules') or []
            for problem in loaded.get('errors') or []:
                print(f"Rules file: {problem}")
            if not rules:
                return
            links = self.link_manager.all_links if self.link_manager else []
            # Selector rules read the markup; attach it to a copy so the stored
            # result rows (and the database) stay as they were
            pages = self.crawl_results
            if self._page_html:
                pages = [dict(page, html=self._page_html.get(page.get('url')))
                         if self._page_html.get(page.get('url')) else page
                         for page in self.crawl_results]
            found = rules_engine.run_rules(rules, pages, links)
            print(f"Rules: {len(rules)} rule(s), {len(found)} issue(s)")
            with self.issue_detector.issues_lock:
                self.issue_detector.detected_issues.extend(found)

        try:
            self._journal_new_issues(run)
        except Exception as e:
            print(f"Consistency rules failed: {e}")

    def _apply_rendered_http_metadata(self, result, timing, url):
        """Copy a rendered navigation's headers and redirects onto the result.

        The plain-requests path records these itself; without this a
        JavaScript-rendered crawl - which is most of them - reports no caching
        headers, no TTFB and no redirect chain, so the freshness, caching and
        redirect checks see nothing to judge.
        """
        http = (timing or {}).get('http') or {}
        headers = http.get('headers') or {}

        result['cache_control'] = headers.get('cache-control', '')
        result['last_modified'] = headers.get('last-modified', '')
        result['etag'] = headers.get('etag', '')
        # Already lowercased by JavaScriptRenderer._http_metadata
        result['response_headers'] = dict(headers)
        declared_length = str(headers.get('content-length') or '')
        result['content_length'] = int(declared_length) if declared_length.isdigit() else None
        # The browser reports time to the response, which is what TTFB means
        # here; the render wait that follows is not the server's doing.
        result['ttfb'] = round(timing.get('response_ms') or 0, 2)

        redirects = http.get('redirects') or []
        if redirects:
            result['redirects'] = redirects
            final_url = http.get('final_url') or url
            if final_url != url:
                result['redirected_to'] = final_url

    def _record_rendered_issues(self, url, observations, console_messages,
                                html_content, observations_by_width=None):
        """Turn one page's browser observations into issues.

        Observations from several viewports merge into one issue naming
        every width a defect was seen at, so a nav hidden only on a phone
        does not masquerade as a desktop finding.
        """
        try:
            from src.core import rendered_checks
            if console_messages is not None:
                observations.setdefault('console_errors', console_messages)
            config = {'raw_html': html_content, 'accessibility': {'enabled': True}}
            seen = [o for o in (observations_by_width or []) if o]
            found = (rendered_checks.merge_checks(seen, url, config) if seen
                     else rendered_checks.check(observations, url, config))
            if not found:
                return
            with self.issue_detector.issues_lock:
                self.issue_detector.detected_issues.extend(found)
            self.event_log.emit_many('issue', found)
            self.user_memory.track_issues(found)
            if self.db_save_enabled:
                self.unsaved_issues.extend(found)
        except Exception as e:
            print(f"Rendered checks failed for {url}: {e}")

    def _analyse_link_graph(self):
        """Internal-linking report: orphans, depth, anchor-text problems"""
        if not self.config.get('analyse_link_graph', True) or not self.base_url:
            return

        def run():
            from src.core import link_graph
            links = self.link_manager.all_links if self.link_manager else []
            graph = link_graph.build_graph(self.crawl_results, links, self.base_url,
                                           cluster_patterns=self.config.get('link_clusters'))
            link_graph.click_depth(graph, self.base_url)
            found = link_graph.analyse(graph, config=self.config.get('link_graph'))
            self.link_clusters = link_graph.cluster_summary(graph)
            print(f"Link graph: {len(graph.get('nodes', []))} pages, {len(found)} issue(s)")
            with self.issue_detector.issues_lock:
                self.issue_detector.detected_issues.extend(found)

        try:
            self._journal_new_issues(run)
        except Exception as e:
            print(f"Link graph analysis failed: {e}")

    def _audit_cms(self):
        """Read-only CMS check: what the CMS holds vs what the site serves"""
        if not self.config.get('audit_cms', True) or not self.base_url:
            return

        def run():
            from src.core import cms_wordpress
            outcome = cms_wordpress.audit_site(
                self.session, self.base_url, self.crawl_results,
                config={'expected_author': self.config.get('cms_expected_author'),
                        'auth_header': self.config.get('cms_auth_header'),
                        'timeout': self.config.get('timeout', 10)},
                sitemap_urls=set(self.sitemap_lastmod) or None)
            if not outcome.get('available'):
                return
            print(f"CMS: {len(outcome['items'])} item(s), {len(outcome['issues'])} issue(s)")
            with self.issue_detector.issues_lock:
                self.issue_detector.detected_issues.extend(outcome['issues'])

        try:
            self._journal_new_issues(run)
        except Exception as e:
            print(f"CMS audit failed: {e}")

    def _run_agent_analysts(self):
        """Judgement a rule cannot reach, when a model is configured.

        Off unless asked for. Everything it produces is info severity and
        marked as a model's opinion: a crawler whose errors are generated
        text is a crawler nobody can trust.
        """
        if (self.config.get('agent_mode') or 'none') == 'none':
            return

        def run():
            from src.core.agents import analysts
            found = analysts.issues(self.crawl_results, config={
                'mode': self.config.get('agent_mode'),
                'model': self.config.get('agent_model'),
                'max_pages': self.config.get('agent_max_pages', 25),
                'cli_command': self.config.get('agent_cli_command') or 'claude',
            })
            with self.issue_detector.issues_lock:
                self.issue_detector.detected_issues.extend(found)

        self._journal_new_issues(run)

    def _run_google_checks(self):
        """Search Console and Analytics, joined to what this crawl found.

        Skipped silently when no credentials are configured. When they are
        and a call fails, the failure is printed rather than swallowed: an
        integration that could not run must never look like a clean result.
        """
        from src.core import google_auth, token_store
        user_id = getattr(self, 'user_id', None)
        credentials = google_auth.from_config(self.config, user_id=user_id)
        if not credentials:
            return
        # What they picked in Settings > Google, used unless this crawl's own
        # config names something more specific.
        picked_site, picked_ga = token_store.chosen_properties(user_id)

        def run():
            found = []
            property_url = (self.config.get('search_console_property')
                            or picked_site or self.base_url)
            if property_url:
                from src.core import search_console
                outcome = search_console.collect(credentials, property_url,
                                                 self.crawl_results)
                found.extend(outcome['issues'])
                if outcome.get('error'):
                    print(f"Search Console: {outcome['error']}")
            ga_property = self.config.get('analytics_property_id') or picked_ga
            if ga_property:
                from src.core import analytics
                outcome = analytics.collect(credentials, ga_property,
                                            self.crawl_results, self.base_url or '')
                found.extend(outcome['issues'])
                if outcome.get('error'):
                    print(f"Analytics: {outcome['error']}")
            with self.issue_detector.issues_lock:
                self.issue_detector.detected_issues.extend(found)

        self._journal_new_issues(run)

    def _run_link_metrics(self):
        """Third-party authority data, joined onto what the crawl found.

        Skipped when no provider is configured, which is the usual case: each
        of them needs a paid subscription.
        """
        from src.core import link_metrics
        if not link_metrics.configured(self.config):
            return

        def run():
            outcome = link_metrics.collect(self.crawl_results, self.config)
            if outcome.get('error'):
                print(f"Link metrics: {outcome['error']}")
            found = link_metrics.metric_issues(self.crawl_results, outcome)
            with self.issue_detector.issues_lock:
                self.issue_detector.detected_issues.extend(found)

        self._journal_new_issues(run)

    def _run_pdf_checks(self):
        """Audit any PDF this crawl fetched.

        Skipped entirely when none were crawled, which is the usual case.
        """
        if not self.config.get('crawl_pdfs'):
            return

        def run():
            from src.core.pdf_checks import pdf_issues
            found = pdf_issues(self.crawl_results)
            with self.issue_detector.issues_lock:
                self.issue_detector.detected_issues.extend(found)

        self._journal_new_issues(run)

    def _run_amp_checks(self):
        """Validate any page that claims to be AMP."""
        if not self.config.get('amp_checks', True):
            return

        def run():
            from src.core import amp_checks
            found = amp_checks.amp_issues(self.crawl_results)
            with self.issue_detector.issues_lock:
                self.issue_detector.detected_issues.extend(found)

        self._journal_new_issues(run)

    def _run_spell_check(self):
        """Spelling and grammar, one request per page, off unless asked for."""
        if not self.config.get('spell_check', False):
            return

        def run():
            from src.core import language_checks
            config = {'enabled': True,
                      'ignore': self.config.get('spell_check_ignore') or []}
            endpoint = self.config.get('spell_check_endpoint')
            if endpoint:
                config['endpoint'] = endpoint
            found = []
            for page in self.crawl_results:
                if not self.is_running:
                    break
                found.extend(language_checks.check_page(page, config))
            with self.issue_detector.issues_lock:
                self.issue_detector.detected_issues.extend(found)

        self._journal_new_issues(run)

    def _detect_freshness_issues(self):
        """Date and freshness checks over every crawled page"""
        def run():
            from src.core.date_checks import date_issues_for_crawl
            found = date_issues_for_crawl(
                self.crawl_results,
                sitemap_lastmod=self.sitemap_lastmod,
                base_url=self.base_url,
                stale_after_months=self.config.get('stale_after_months', 12),
                tolerance_hours=self.config.get('date_tolerance_hours', 24))
            with self.issue_detector.issues_lock:
                self.issue_detector.detected_issues.extend(found)

        self._journal_new_issues(run)

    def _detect_content_integrity_issues(self):
        """What this crawl lost since the previous one (text, internal links)"""
        if not self.baseline_crawl_id or not self.crawl_id:
            return

        def run():
            from src.core.crawl_diff import content_integrity_issues
            found = content_integrity_issues(self.baseline_crawl_id, self.crawl_id)
            with self.issue_detector.issues_lock:
                self.issue_detector.detected_issues.extend(found)

        self._journal_new_issues(run)

