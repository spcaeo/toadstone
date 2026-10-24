"""SEO issue detection and reporting"""
import re
import threading
from collections import defaultdict
from fnmatch import fnmatch
from urllib.parse import urlparse, urlunparse

from src.core.duplication import (
    DUPLICATION_EXAMPLES_PER_PAGE,
    DUPLICATION_TIME_BUDGET_SECONDS,
    DuplicationDetector,
)
from src.core import http_status
from src.utils.issue_factory import make_issue
from src.utils.url_params import (
    DEFAULT_TRACKING_PARAMS,
    strip_tracking_params,
    tracking_params_in,
)

# Re-exported so importers of this module keep seeing the duplication budget
# where it has always lived; the pass itself now lives in src/core/duplication.
__all__ = ['IssueDetector', 'has_alt_attribute', 'is_tracking_pixel',
           'DUPLICATION_TIME_BUDGET_SECONDS', 'DUPLICATION_EXAMPLES_PER_PAGE']

# Site-wide "internal links carry tracking parameters" report.
TRACKING_LINK_EXAMPLE_TARGETS = 3
TRACKING_LINK_EXAMPLE_SOURCES = 5
TRACKING_LINK_EXAMPLE_PARAMS = 6
TRACKING_LINK_DETAILS_MAX = 500


# The older per-page checks were written before the issue factory, and named
# themselves in prose. These are their slugs; anything unlisted is slugified
# from its name, so a new check still gets a usable one.
LEGACY_CHECK_SLUGS = {
    'Missing Title Tag': 'missing_title',
    'Title Too Long': 'title_too_long',
    'Title Too Short': 'title_too_short',
    'Missing Meta Description': 'missing_meta_description',
    'Meta Description Too Long': 'meta_description_too_long',
    'Meta Description Too Short': 'meta_description_too_short',
    'Missing H1 Tag': 'missing_h1',
    'Thin Content': 'thin_content',
    'Missing Canonical URL': 'missing_canonical',
    'Canonical URL Different': 'canonical_different',
    'Noindex Tag Present': 'noindex',
    'Nofollow Tag Present': 'nofollow',
    'Missing Viewport Meta Tag': 'missing_viewport',
    'Missing Language Attribute': 'missing_lang',
    'Images Without Alt Text': 'images_missing_alt',
    'No Structured Data': 'no_structured_data',
    'Missing OpenGraph Tags': 'missing_opengraph',
    'Missing Twitter Card Tags': 'missing_twitter_card',
    'Moderate Response Time': 'moderate_response_time',
    'Slow Response Time': 'slow_response_time',
    'Moderate Page Size': 'moderate_page_size',
    'Large Page Size': 'large_page_size',
    'Broken Image (No Response)': 'broken_image',
    'Internal Links Carry Tracking Parameters': 'tracking_parameters_on_internal_links',
}


def check_slug_for(name):
    """'Title Too Long' -> 'title_too_long'"""
    if name in LEGACY_CHECK_SLUGS:
        return LEGACY_CHECK_SLUGS[name]
    slug = re.sub(r'[^a-z0-9]+', '_', str(name or 'issue').lower()).strip('_')
    return slug or 'issue'


def as_standard_issue(issue):
    """Return `issue` in the shape src/utils/issue_factory.py produces.

    Already-standard issues pass through untouched. The rest keep their
    wording but gain a slug, a stable id and a docs link - stable meaning it
    does not move when the details do, so "took 2100ms" and "took 2400ms" on
    one page stay the same open defect rather than a fixed one and a new one.
    """
    if not isinstance(issue, dict) or issue.get('check'):
        return issue
    name = issue.get('issue', '')
    standard = make_issue(
        issue.get('url', ''), check_slug_for(name), issue.get('type', 'info'),
        issue.get('category', 'SEO'), name, issue.get('details', ''),
        evidence=issue.get('evidence'), how_to_fix=issue.get('how_to_fix', ''))
    for key, value in issue.items():          # keep anything extra a check added
        standard.setdefault(key, value)
    return standard


def is_tracking_pixel(image):
    """True when an img is a counter, not content.

    A 1x1 image is a tracking beacon or a spacer GIF. It carries no meaning to
    anybody, sighted or not, and a screen reader that meets one has nothing to
    announce - so demanding alt text on it produces noise and nothing else.
    Crawling ten real SEO sites, every one of them tripped this.

    Judged on the declared width and height only. An image that declares no
    size is not assumed to be a pixel: guessing would hide real missing alt
    text, which is the failure that actually matters.
    """
    def side(value):
        try:
            return int(str(value).strip().rstrip('px') or 0)
        except (TypeError, ValueError):
            return 0

    width, height = side(image.get('width')), side(image.get('height'))
    return 0 < width <= 2 and 0 < height <= 2


def has_alt_attribute(image):
    """True when the img carried an alt attribute, empty or not.

    alt="" is the correct way to mark an image decorative, so it counts as
    present. Crawls saved before has_alt existed carry no flag, and are treated
    as fine rather than guessed at (issue #95).
    """
    if 'has_alt' in image:
        return bool(image['has_alt'])
    return True


def _number(value, default=0):
    """A crawl result can carry None where a number is expected."""
    try:
        return type(default)(value) if value is not None else default
    except (TypeError, ValueError):
        return default


class IssueDetector:
    """Detects SEO and technical issues in crawled pages"""

    def __init__(self, exclusion_patterns=None, tracking_params=None,
                 min_similarity=0.85):
        self.exclusion_patterns = exclusion_patterns or []
        if tracking_params is None:
            self.tracking_params = list(DEFAULT_TRACKING_PARAMS)
        elif isinstance(tracking_params, str):
            self.tracking_params = [p.strip() for p in tracking_params.split(',') if p.strip()]
        else:
            self.tracking_params = list(tracking_params)
        self.detected_issues = []
        self.issues_lock = threading.Lock()
        self.duplication_truncated = False
        self.min_similarity = min_similarity
        self._duplication = DuplicationDetector(min_similarity=min_similarity,
                                                should_exclude=self._should_exclude,
                                                alias_key=self._alias_key)

    def detect_issues(self, result):
        """Detect SEO issues for a crawled URL"""
        url = result.get('url', '')
        issues = []

        # Skip if URL matches exclusion patterns
        if self._should_exclude(url):
            return

        # SEO checks only apply to HTML pages, not images/CSS/JS/etc.
        content_type = result.get('content_type', '')
        if content_type and 'html' not in content_type.lower():
            return

        # A page that never returned a response has no content to judge, and
        # every content check would otherwise fire on the emptiness: one
        # unreachable URL became ten issues reading "missing title", "missing
        # H1", "no structured data", "thin content". Found by crawling a real
        # site that throttled the crawl - 168 of 220 pages came back with no
        # response, and 68% of the issues raised were about their absent
        # content, burying the ones that were real. The technical check still
        # runs, because "this page did not respond" is the one true thing
        # there is to say about it.
        try:
            status_code = int(result.get('status_code') or 0)
        except (TypeError, ValueError):
            status_code = 0
        if status_code == 0:
            self._check_technical_issues(result, issues)
            with self.issues_lock:
                self.detected_issues.extend(
                    as_standard_issue(issue) for issue in issues)
            return

        # Critical SEO Issues
        self._check_title_issues(result, issues)
        self._check_meta_description_issues(result, issues)
        self._check_heading_issues(result, issues)
        self._check_content_issues(result, issues)
        self._check_technical_issues(result, issues)
        self._check_mobile_issues(result, issues)
        self._check_accessibility_issues(result, issues)
        self._check_social_media_issues(result, issues)
        self._check_structured_data_issues(result, issues)
        self._check_performance_issues(result, issues)
        self._check_indexability_issues(result, issues)
        self._check_broken_image_issues(result, issues)

        # Give the older checks the same shape as every other check: a stable
        # id, a machine-readable slug and a link to what it means.
        with self.issues_lock:
            self.detected_issues.extend(as_standard_issue(issue) for issue in issues)

    def _check_title_issues(self, result, issues):
        """Check for title-related issues"""
        url = result.get('url', '')
        title = result.get('title', '')

        if not title:
            issues.append({
                'url': url,
                'type': 'error',
                'category': 'SEO',
                'issue': 'Missing Title Tag',
                'details': 'Page has no title tag'
            })
        elif len(title) > 60:
            issues.append({
                'url': url,
                'type': 'warning',
                'category': 'SEO',
                'issue': 'Title Too Long',
                'details': f"Title is {len(title)} characters (recommended: ≤60)"
            })
        elif len(title) < 30:
            issues.append({
                'url': url,
                'type': 'warning',
                'category': 'SEO',
                'issue': 'Title Too Short',
                'details': f"Title is {len(title)} characters (recommended: 30-60)"
            })

    def _check_meta_description_issues(self, result, issues):
        """Check for meta description issues"""
        url = result.get('url', '')
        meta_desc = result.get('meta_description', '')

        if not meta_desc:
            issues.append({
                'url': url,
                'type': 'error',
                'category': 'SEO',
                'issue': 'Missing Meta Description',
                'details': 'Page has no meta description'
            })
        elif len(meta_desc) > 160:
            issues.append({
                'url': url,
                'type': 'warning',
                'category': 'SEO',
                'issue': 'Meta Description Too Long',
                'details': f"Description is {len(meta_desc)} characters (recommended: ≤160)"
            })
        elif len(meta_desc) < 120:
            issues.append({
                'url': url,
                'type': 'warning',
                'category': 'SEO',
                'issue': 'Meta Description Too Short',
                'details': f"Description is {len(meta_desc)} characters (recommended: 120-160)"
            })

    def _check_heading_issues(self, result, issues):
        """Check for heading-related issues"""
        url = result.get('url', '')

        if not result.get('h1'):
            issues.append({
                'url': url,
                'type': 'error',
                'category': 'SEO',
                'issue': 'Missing H1 Tag',
                'details': 'Page has no H1 heading'
            })

    def _check_content_issues(self, result, issues):
        """Check for content-related issues"""
        url = result.get('url', '')
        word_count = _number(result.get('word_count'))

        if word_count < 300:
            issues.append({
                'url': url,
                'type': 'warning',
                'category': 'Content',
                'issue': 'Thin Content',
                'details': f'Page has only {word_count} words (recommended: ≥300)'
            })

    def _check_technical_issues(self, result, issues):
        """Check for technical SEO issues"""
        url = result.get('url', '')
        # A result whose status never arrived carries None, not 0, and the
        # band comparisons below raise on it.
        try:
            status_code = int(result.get('status_code') or 0)
        except (TypeError, ValueError):
            status_code = 0

        # No HTTP response at all (DNS failure, connection refused, timeout, etc.)
        if status_code == 0:
            error_type = result.get('error_type')
            error_label_map = {
                'dns_not_found': ('DNS Not Found',
                                  'Domain does not resolve. The site may be expired or misconfigured.'),
                'connection_refused': ('Connection Refused',
                                       'Server actively refused the connection.'),
                'timeout': ('Request Timeout',
                            'Server did not respond before the request timed out.'),
                'ssl_error': ('SSL/TLS Error',
                              'Could not establish a secure connection (certificate or TLS issue).'),
                'connection_error': ('Connection Error',
                                     'Could not connect to the server.'),
            }
            if error_type and error_type != 'file_too_large':
                title, default_details = error_label_map.get(
                    error_type, ('No Response', 'No HTTP response received.')
                )
                issues.append({
                    'url': url,
                    'type': 'error',
                    'category': 'Technical',
                    'issue': title,
                    'details': result.get('error') or default_details
                })
            # Nothing below this line has anything to read: no status band, no
            # headers, no HTML. Saying "missing canonical" about a page that
            # never answered is noise on top of the real problem.
            return

        if status_code >= 400 and status_code < 500:
            issues.append({
                'url': url,
                'type': 'error',
                'category': 'Technical',
                'issue': f'{status_code} Client Error',
                'details': self._get_status_code_message(status_code)
            })
        elif status_code >= 500:
            issues.append({
                'url': url,
                'type': 'error',
                'category': 'Technical',
                'issue': f'{status_code} Server Error',
                'details': self._get_status_code_message(status_code)
            })
        elif status_code >= 300 and status_code < 400:
            issues.append({
                'url': url,
                'type': 'info',
                'category': 'Technical',
                'issue': f'{status_code} Redirect',
                'details': 'URL redirects to another location'
            })

        # Canonical URL checks
        canonical_url = result.get('canonical_url', '')
        if not canonical_url:
            issues.append({
                'url': url,
                'type': 'warning',
                'category': 'Technical',
                'issue': 'Missing Canonical URL',
                'details': 'Page has no canonical URL specified'
            })
        elif self._alias_key(canonical_url) != self._alias_key(url):
            # A canonical that only drops tracking parameters (or differs by a
            # trailing slash / fragment) points at this very page and is right.
            issues.append({
                'url': url,
                'type': 'warning',
                'category': 'Technical',
                'issue': 'Canonical URL Different',
                'details': f"Canonical points to: {canonical_url}"
            })

    def _check_mobile_issues(self, result, issues):
        """Check for mobile optimization issues"""
        url = result.get('url', '')

        if not result.get('viewport'):
            issues.append({
                'url': url,
                'type': 'error',
                'category': 'Mobile',
                'issue': 'Missing Viewport Meta Tag',
                'details': 'Page is not mobile-optimized'
            })

    def _check_accessibility_issues(self, result, issues):
        """Check for accessibility issues"""
        url = result.get('url', '')

        if not result.get('lang'):
            issues.append({
                'url': url,
                'type': 'warning',
                'category': 'Accessibility',
                'issue': 'Missing Language Attribute',
                'details': 'HTML tag has no lang attribute'
            })

        # Image alt text. Only a missing attribute is a defect;
        # alt="" marks a decorative image and is correct.
        images = [img for img in result.get('images', [])
                  if not is_tracking_pixel(img)]
        missing = [img for img in images if not has_alt_attribute(img)]
        decorative = [img for img in images
                      if has_alt_attribute(img) and not (img.get('alt') or '').strip()]

        if missing:
            detail = f'{len(missing)} of {len(images)} images have no alt attribute'
            if decorative:
                count = len(decorative)
                noun = 'image' if count == 1 else 'images'
                verb = 'is' if count == 1 else 'are'
                detail += f' ({count} decorative {noun} with alt="" {verb} fine)'
            issues.append({
                'url': url,
                'type': 'warning',
                'category': 'Accessibility',
                'issue': 'Images Without Alt Text',
                'details': detail
            })

    def _check_social_media_issues(self, result, issues):
        """Check for social media optimization issues"""
        url = result.get('url', '')

        if not result.get('og_tags'):
            issues.append({
                'url': url,
                'type': 'warning',
                'category': 'Social',
                'issue': 'Missing OpenGraph Tags',
                'details': 'Page has no OpenGraph tags for social sharing'
            })

        if not result.get('twitter_tags'):
            issues.append({
                'url': url,
                'type': 'warning',
                'category': 'Social',
                'issue': 'Missing Twitter Card Tags',
                'details': 'Page has no Twitter Card tags'
            })

    def _check_structured_data_issues(self, result, issues):
        """Check for structured data issues"""
        url = result.get('url', '')

        if not result.get('json_ld') and not result.get('schema_org'):
            issues.append({
                'url': url,
                'type': 'error',
                'category': 'Structured Data',
                'issue': 'No Structured Data',
                'details': 'Page has no JSON-LD or Schema.org markup'
            })

    def _check_performance_issues(self, result, issues):
        """Check for performance issues"""
        url = result.get('url', '')
        response_time = _number(result.get('response_time'), 0.0)
        page_size = _number(result.get('size'))

        if response_time > 3000:
            issues.append({
                'url': url,
                'type': 'error',
                'category': 'Performance',
                'issue': 'Slow Response Time',
                'details': f'Page took {response_time}ms to respond (recommended: <3000ms)'
            })
        elif response_time > 1000:
            issues.append({
                'url': url,
                'type': 'warning',
                'category': 'Performance',
                'issue': 'Moderate Response Time',
                'details': f'Page took {response_time}ms to respond (recommended: <1000ms)'
            })

        if page_size > 3 * 1024 * 1024:
            issues.append({
                'url': url,
                'type': 'error',
                'category': 'Performance',
                'issue': 'Large Page Size',
                'details': f'Page size is {page_size / 1024 / 1024:.1f}MB (recommended: <3MB)'
            })
        elif page_size > 1 * 1024 * 1024:
            issues.append({
                'url': url,
                'type': 'warning',
                'category': 'Performance',
                'issue': 'Moderate Page Size',
                'details': f'Page size is {page_size / 1024 / 1024:.1f}MB (recommended: <1MB)'
            })

    def _check_indexability_issues(self, result, issues):
        """Check for indexability issues"""
        url = result.get('url', '')
        robots = result.get('robots', '').lower()

        if 'noindex' in robots:
            issues.append({
                'url': url,
                'type': 'error',
                'category': 'Indexability',
                'issue': 'Noindex Tag Present',
                'details': 'Page is BLOCKED from search engines - has noindex directive'
            })

        if 'nofollow' in robots:
            issues.append({
                'url': url,
                'type': 'error',
                'category': 'Indexability',
                'issue': 'Nofollow Tag Present',
                'details': 'Links on this page are NOT followed by search engines - has nofollow directive'
            })

    def _check_broken_image_issues(self, result, issues):
        """Check for broken image URLs on the page"""
        url = result.get('url', '')
        broken_images = result.get('broken_images', [])
        for img in broken_images:
            status = img.get('status', 0)
            img_url = img.get('url', '')
            if status == 0:
                issues.append({
                    'url': url,
                    'type': 'error',
                    'category': 'Content',
                    'issue': 'Broken Image (No Response)',
                    'details': f'Image does not respond: {img_url}'
                })
            elif status >= 400:
                issues.append({
                    'url': url,
                    'type': 'error',
                    'category': 'Content',
                    'issue': f'Broken Image ({status})',
                    'details': f'Image returned {status}: {img_url}'
                })

        # Images the CDN refused *this crawler*. Reported, because silently
        # dropping them would hide a real misconfiguration; at info, because
        # the usual cause is a bot wall and the image loads fine in a browser.
        for img in result.get('blocked_images', []) or []:
            status = img.get('status', 0)
            issues.append({
                'url': url,
                'type': 'info',
                'category': 'Content',
                'issue': 'Image Refused the Crawler',
                'details': f'Image {http_status.describe_blocked(status)}, so it '
                           f'could not be verified: {img.get("url", "")}'
            })

    def detect_duplication_issues(self, all_results, similarity_threshold=None):
        """Flag pages whose main text is identical or near-identical to another
        crawled page's.

        The work is done by src/core/duplication.py; this is the public entry
        point the crawler has always called. Pages are compared on the body
        text the extractor stored (template chrome already stripped), falling
        back to title/description/H1 for pages that carry no main text. At
        most ONE issue per page - a site of N templated pages yields N issues,
        not N^2 - and two addresses of one page are never duplicates of each
        other.

        Args:
            all_results: List of all crawled result dictionaries
            similarity_threshold: Minimum similarity ratio to flag as duplicate
                (0.0-1.0); defaults to the configured min_similarity.
        """
        issues = self._duplication.detect(all_results, similarity_threshold)
        self.duplication_truncated = self._duplication.truncated

        # Add all detected duplication issues
        with self.issues_lock:
            self.detected_issues.extend(issues)

    def detect_tracking_link_issues(self, all_links):
        """Report internal links that point at tracking-parameter URLs.

        Campaign parameters belong on links that arrive from somewhere else;
        on an internal link they only split one page into several addresses.
        The whole crawl gets at most ONE informational issue naming the worst
        offenders, since the fix is a site-wide one.

        Args:
            all_links: the link manager's link dicts ('source_url',
                'target_url', 'is_internal'); None or empty is fine.
        """
        if not all_links:
            return

        affected = 0
        seen_params = {}  # name -> None, kept in the order first seen
        target_counts = defaultdict(int)
        sources = []
        seen_sources = set()

        for link in all_links:
            if not isinstance(link, dict) or not link.get('is_internal'):
                continue
            target = link.get('target_url') or ''
            if not target or self._should_exclude(target):
                continue
            names = tracking_params_in(target, self.tracking_params)
            if not names:
                continue
            source = link.get('source_url') or ''
            if source and self._should_exclude(source):
                continue

            affected += 1
            for name in names:
                seen_params.setdefault(name.lower(), None)
            target_counts[target] += 1
            if source and source not in seen_sources:
                seen_sources.add(source)
                sources.append(source)

        if not affected:
            return

        ranked = sorted(target_counts.items(), key=lambda item: (-item[1], item[0]))
        issue = {
            'url': ranked[0][0],
            'type': 'info',
            'category': 'Links',
            'issue': 'Internal Links Carry Tracking Parameters',
            'details': self._tracking_link_details(affected, seen_params, ranked, sources)
        }

        with self.issues_lock:
            # as_standard_issue, like every other legacy-shaped check: without
            # it this issue reached the UI with check=None, so it could not be
            # filtered by check, had no stable id to track across crawls, and
            # linked to /checks#None. It was the one unlabelled row in a real
            # crawl report and nobody had noticed.
            self.detected_issues.append(as_standard_issue(issue))

    @staticmethod
    def _tracking_link_details(affected, seen_params, ranked, sources):
        """One readable line summarising the tracking-parameter links."""
        names = list(seen_params)[:TRACKING_LINK_EXAMPLE_PARAMS]
        hidden_params = len(seen_params) - len(names)
        params_text = ', '.join(names) + (f", +{hidden_params} more" if hidden_params > 0 else '')

        top = ranked[:TRACKING_LINK_EXAMPLE_TARGETS]
        targets_text = '; '.join(
            f"{url} ({count} link{'s' if count != 1 else ''})" for url, count in top
        )
        hidden_targets = len(ranked) - len(top)
        if hidden_targets > 0:
            targets_text += (f" (+{hidden_targets} other target"
                             f"{'s' if hidden_targets != 1 else ''})")

        details = (f"{affected} internal link{'s' if affected != 1 else ''} carry tracking "
                   f"parameters ({params_text}). Most linked: {targets_text}")
        if sources:
            shown = sources[:TRACKING_LINK_EXAMPLE_SOURCES]
            hidden_sources = len(sources) - len(shown)
            details += ' from ' + ', '.join(shown)
            if hidden_sources > 0:
                details += (f" (+{hidden_sources} more page"
                            f"{'s' if hidden_sources != 1 else ''})")
        details += '. Link to the clean URL instead.'

        if len(details) > TRACKING_LINK_DETAILS_MAX:
            details = details[:TRACKING_LINK_DETAILS_MAX - 1].rstrip() + '\u2026'
        return details

    def _calculate_content_similarity(self, result1, result2):
        """
        Metadata similarity of two page results in [0.0, 1.0]: word-level Dice
        similarity of title (35%), meta description (35%) and H1 (20%), plus
        the ratio of their word counts (10%). This is the fallback score;
        duplicate detection itself compares main text.
        """
        return self._duplication.metadata_similarity(
            self._duplication.page(result1), self._duplication.page(result2), 0.0)

    def _alias_key(self, url):
        """`url` reduced to what identifies the page it serves.

        Tracking parameters, the fragment and a trailing-slash-only difference
        all leave the page unchanged, so two URLs with the same key are the
        same page: /contact-us/?utm_source=SidebarCTA and /contact-us both key
        to https://site/contact-us.
        """
        if not url or not isinstance(url, str):
            return ''
        parsed = urlparse(strip_tracking_params(url.strip(), self.tracking_params))
        path = parsed.path or '/'
        if len(path) > 1:
            path = path.rstrip('/') or '/'
        return urlunparse(parsed._replace(path=path, fragment=''))

    def _should_exclude(self, url):
        """Check if URL should be excluded from issue detection"""
        parsed = urlparse(url)
        path = parsed.path

        for pattern in self.exclusion_patterns:
            if '*' in pattern:
                if fnmatch(path, pattern):
                    return True
            elif path == pattern or path.startswith(pattern.rstrip('*')):
                return True

        return False

    def _get_status_code_message(self, status_code):
        """Get descriptive message for HTTP status codes"""
        messages = {
            400: 'Bad Request',
            401: 'Unauthorized',
            403: 'Forbidden',
            404: 'Not Found',
            405: 'Method Not Allowed',
            406: 'Not Acceptable',
            408: 'Request Timeout',
            410: 'Gone',
            429: 'Too Many Requests',
            500: 'Internal Server Error',
            501: 'Not Implemented',
            502: 'Bad Gateway',
            503: 'Service Unavailable',
            504: 'Gateway Timeout',
            505: 'HTTP Version Not Supported'
        }
        return messages.get(status_code, f'HTTP {status_code} Error')

    def get_issues(self):
        """Get all detected issues"""
        with self.issues_lock:
            return self.detected_issues.copy()

    def reset(self):
        """Reset detected issues"""
        with self.issues_lock:
            self.detected_issues.clear()
