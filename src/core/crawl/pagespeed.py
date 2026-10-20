"""The PageSpeed Insights pass, lifted out of the crawler class.

Mechanically the same code, moved so that crawler.py can come back under
the project's 1,000-line limit. It stays a mixin rather than becoming a
free function because every method here reaches for crawler state - the
config, the running flag, the issue detector - and threading all of that
through arguments would be a bigger change than the one being made.
"""

import os
import time

import requests

from urllib.parse import urlparse

class PageSpeedMixin:
    """PageSpeed Insights: fetch the numbers, then judge them."""

    def _run_pagespeed_analysis(self):
        """Run PageSpeed analysis on selected pages"""
        try:
            selected_pages = self._select_pages_for_pagespeed()

            if not selected_pages:
                print("No suitable pages found for PageSpeed analysis")
                return

            print(f"Running PageSpeed analysis on {len(selected_pages)} pages...")

            pagespeed_results = []
            for i, page_url in enumerate(selected_pages):
                if not self.is_running:
                    print("PageSpeed analysis cancelled")
                    return

                print(f"Analyzing page {i+1}/{len(selected_pages)}: {page_url}")

                # Mobile analysis
                mobile_result = self._call_pagespeed_api(page_url, 'mobile')
                time.sleep(2)

                if not self.is_running:
                    return

                # Desktop analysis
                desktop_result = self._call_pagespeed_api(page_url, 'desktop')

                pagespeed_results.append({
                    'url': page_url,
                    'mobile': mobile_result,
                    'desktop': desktop_result,
                    'analysis_date': time.strftime('%Y-%m-%d %H:%M:%S')
                })

                if i < len(selected_pages) - 1:
                    time.sleep(3)

            self.stats['pagespeed_results'] = pagespeed_results
            print(f"PageSpeed analysis completed for {len(pagespeed_results)} pages")
            self._check_pagespeed_results(pagespeed_results)

        except Exception as e:
            print(f"Error running PageSpeed analysis: {e}")

    def _select_pages_for_pagespeed(self):
        """Select homepage and 2 category pages for PageSpeed analysis"""
        selected_pages = []

        # Find homepage
        homepage = None
        min_path_length = float('inf')

        for result in self.crawl_results:
            if result.get('status_code') == 200 and result.get('is_internal'):
                url = result['url']
                parsed = urlparse(url)
                path = parsed.path.rstrip('/')

                if path == '' or path == '/':
                    homepage = url
                    break
                elif len(path) < min_path_length:
                    homepage = url
                    min_path_length = len(path)

        if homepage:
            selected_pages.append(homepage)

        # Find category pages
        category_pages = []
        for result in self.crawl_results:
            if result.get('status_code') == 200 and result.get('is_internal'):
                url = result['url']
                parsed = urlparse(url)
                path = parsed.path.strip('/')

                if path and '/' not in path and url != homepage:
                    category_pages.append(url)

        selected_pages.extend(category_pages[:2])
        return selected_pages

    def _check_pagespeed_results(self, pagespeed_results):
        """Turn the numbers we just paid API quota for into issues.

        They were fetched, stored and shown on a screen, and nothing ever
        judged them: a four-second Largest Contentful Paint produced no
        finding at all. Field data is asked for too where a key allows it,
        because what real users see outranks a simulation.
        """
        def run():
            from src.core import pagespeed_checks
            config = {'strategy': self.config.get('pagespeed_strategy', 'mobile')}
            found = pagespeed_checks.vitals_issues(pagespeed_results, config)
            # Per-user setting first, then an install-level key from the
            # environment. CrUX is free and the key is read-only, so an
            # operator can switch field data on once for the whole install
            # rather than asking every person to paste the same string into
            # their own settings - the same shape as GOOGLE_CLIENT_SECRET.
            api_key = (self.config.get('google_api_key')
                       or os.environ.get('GOOGLE_API_KEY', '')).strip()
            if api_key:
                for entry in pagespeed_results or []:
                    url = (entry or {}).get('url')
                    if not url:
                        continue
                    crux = pagespeed_checks.fetch_crux(url, api_key)
                    found.extend(pagespeed_checks.field_issues(url, crux, config))
            with self.issue_detector.issues_lock:
                self.issue_detector.detected_issues.extend(found)

        self._journal_new_issues(run)

    def _call_pagespeed_api(self, url, strategy='mobile', retries=3):
        """Call Google PageSpeed Insights API"""
        import random

        try:
            # The only third-party URL in the crawl path that was not
            # configurable. Everything else here either comes from settings or
            # takes an injected opener, which is what let the Google code be
            # verified against real responses. Configurable now for the same
            # reason, and because an install behind a proxy or inside an
            # air-gapped network has nowhere else to point it.
            api_url = (self.config.get('pagespeed_endpoint')
                       or "https://www.googleapis.com/pagespeedonline/v5/runPagespeed")
            params = {
                'url': url,
                'strategy': strategy,
                'category': 'performance'
            }

            key = (self.config.get('google_api_key')
                   or os.environ.get('GOOGLE_API_KEY', '')).strip()
            if key:
                params['key'] = key

            for attempt in range(retries + 1):
                try:
                    response = requests.get(api_url, params=params, timeout=60)

                    if response.status_code == 200:
                        data = response.json()
                        lighthouse_result = data.get('lighthouseResult', {})
                        audits = lighthouse_result.get('audits', {})
                        categories = lighthouse_result.get('categories', {})

                        performance_score = None
                        if 'performance' in categories:
                            score = categories['performance'].get('score')
                            if score is not None:
                                performance_score = int(score * 100)

                        metrics = {}

                        if 'first-contentful-paint' in audits:
                            fcp = audits['first-contentful-paint'].get('numericValue')
                            metrics['first_contentful_paint'] = round(fcp / 1000, 2) if fcp else None

                        if 'largest-contentful-paint' in audits:
                            lcp = audits['largest-contentful-paint'].get('numericValue')
                            metrics['largest_contentful_paint'] = round(lcp / 1000, 2) if lcp else None

                        if 'cumulative-layout-shift' in audits:
                            cls = audits['cumulative-layout-shift'].get('numericValue')
                            metrics['cumulative_layout_shift'] = round(cls, 3) if cls else None

                        if 'max-potential-fid' in audits:
                            fid = audits['max-potential-fid'].get('numericValue')
                            metrics['first_input_delay'] = round(fid, 2) if fid else None

                        if 'speed-index' in audits:
                            si = audits['speed-index'].get('numericValue')
                            metrics['speed_index'] = round(si / 1000, 2) if si else None

                        if 'interactive' in audits:
                            tti = audits['interactive'].get('numericValue')
                            metrics['time_to_interactive'] = round(tti / 1000, 2) if tti else None

                        return {
                            'success': True,
                            'performance_score': performance_score,
                            'metrics': metrics,
                            'strategy': strategy
                        }

                    elif response.status_code == 429:
                        if attempt < retries:
                            delay = (2 ** attempt) * random.uniform(0.5, 1.5)
                            print(f"Rate limited, retrying in {delay:.1f} seconds...")
                            time.sleep(delay)
                            continue

                    return {
                        'success': False,
                        'error': f"API returned status {response.status_code}",
                        'strategy': strategy
                    }

                except requests.exceptions.RequestException as e:
                    if attempt < retries:
                        time.sleep(3)
                        continue
                    return {
                        'success': False,
                        'error': f"Network error: {str(e)}",
                        'strategy': strategy
                    }

        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'strategy': strategy
            }
