"""Link management and extraction"""
import threading
from urllib.parse import urljoin, urlparse
from collections import deque

from src.utils.url_params import strip_tracking_params

# Ancestry hints for link context flags. Kept module-level so the tuples are
# allocated once, not per link: collect_all_links runs for every link on every
# page, so a 500-page site walks these tens of thousands of times.
NAV_ANCESTOR_TAGS = frozenset(('nav', 'header', 'footer'))
MAIN_ANCESTOR_TAGS = frozenset(('main', 'article'))
HEADING_ANCESTOR_TAGS = frozenset(('h1', 'h2', 'h3', 'h4', 'h5', 'h6'))
NAV_HINTS = ('nav', 'menu', 'header', 'footer', 'breadcrumb')
MAIN_HINTS = ('content', 'main')
# How far up the tree to look. Deeply nested wrappers are common, but an
# unbounded walk on every link is what makes big crawls crawl.
MAX_ANCESTOR_DEPTH = 25


def normalize_url(url, strip_params=None):
    """Normalize a URL for deduplication: strip fragments and give bare domains a '/' path
    so e.g. https://example.com and https://example.com/ dedupe to one URL.

    When strip_params is a non-empty list of patterns, tracking parameters
    matching them are removed too, so /contact-us/?utm_source=x and
    /contact-us/ normalize to the same URL and are crawled once.
    """
    if strip_params:
        url = strip_tracking_params(url, strip_params)
    parsed = urlparse(url)
    clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"
    if parsed.query:
        clean_url += f"?{parsed.query}"
    return clean_url



def _link_rank(link_data):
    """Lower is better: body copy beats main-area chrome beats header/footer."""
    if link_data.get('placement') in ('navigation', 'footer') or link_data.get('in_nav'):
        return 2
    return 0 if link_data.get('in_main') else 1

class LinkManager:
    """Manages link discovery, tracking, and extraction"""

    def __init__(self, base_domain, event_log=None, strip_params=None):
        self.base_domain = base_domain
        # Tracking-parameter patterns removed when normalising a URL for the
        # crawl frontier. None/[] keeps every parameter (feature disabled).
        self.strip_params = strip_params
        # Journal for incremental UI sync. Links are emitted while links_lock
        # is held so a status backfill can never be announced before the link
        # it refers to.
        self.event_log = event_log
        self.visited_urls = set()
        self.discovered_urls = deque()
        self.all_discovered_urls = set()
        self.all_links = []
        self.links_set = set()
        self.source_pages = {}  # Maps target_url -> list of source_urls

        self.urls_lock = threading.Lock()
        self.links_lock = threading.Lock()

    def extract_links(self, soup, current_url, depth, should_crawl_callback, include_images=False, base_url=None):
        """Extract links from HTML and add to discovery queue.

        base_url overrides the URL relative hrefs resolve against — required
        when the page was reached via redirect, because its content lives at
        a different URL than the one that was requested.
        """
        resolve_base = base_url or current_url
        links = soup.find_all('a', href=True)

        for link in links:
            href = link['href'].strip()
            if not href or href.startswith('#') or href.startswith('mailto:') or href.startswith('tel:'):
                continue

            # Convert relative URLs to absolute
            absolute_url = urljoin(resolve_base, href)

            # Clean URL (remove fragment + tracking parameters)
            clean_url = normalize_url(absolute_url, self.strip_params)

            # Thread-safe checking and adding
            with self.urls_lock:
                # Track source page for this URL
                if clean_url not in self.source_pages:
                    self.source_pages[clean_url] = []
                if current_url not in self.source_pages[clean_url]:
                    self.source_pages[clean_url].append(current_url)

                if (clean_url not in self.visited_urls and
                    clean_url not in self.all_discovered_urls and
                    clean_url != current_url):

                    # Check if this URL should be crawled
                    if should_crawl_callback(clean_url):
                        self.all_discovered_urls.add(clean_url)
                        self.discovered_urls.append((clean_url, depth))

        # Optionally queue images as crawl targets (full download)
        if not include_images:
            return

        imgs = soup.find_all('img', src=True)
        for img in imgs:
            src = img.get('src', '').strip()
            if not src or src.startswith('data:'):
                continue

            try:
                absolute_url = urljoin(resolve_base, src)
                parsed = urlparse(absolute_url)

                if parsed.scheme not in ('http', 'https'):
                    continue

                clean_url = normalize_url(absolute_url, self.strip_params)

                with self.urls_lock:
                    if clean_url not in self.source_pages:
                        self.source_pages[clean_url] = []
                    if current_url not in self.source_pages[clean_url]:
                        self.source_pages[clean_url].append(current_url)

                    if (clean_url not in self.visited_urls and
                        clean_url not in self.all_discovered_urls and
                        clean_url != current_url):

                        if should_crawl_callback(clean_url):
                            self.all_discovered_urls.add(clean_url)
                            self.discovered_urls.append((clean_url, depth))
            except Exception:
                continue

    def collect_all_links(self, soup, source_url, crawl_results, base_url=None):
        """Collect all links for the Links tab display.

        base_url overrides the URL relative hrefs resolve against (for pages
        reached via redirect); source_url stays the requested URL so link
        attribution matches the results table.

        Returns the links newly added by this call, so callers don't have to
        diff all_links by index (which is racy with concurrent workers).
        """
        candidates = []
        resolve_base = base_url or source_url
        links = soup.find_all('a', href=True)

        for link in links:
            href = link['href'].strip()
            if not href or href.startswith('#'):
                continue

            # Get anchor text
            anchor_text = link.get_text().strip()[:100]

            # Handle special link types
            if href.startswith('mailto:') or href.startswith('tel:'):
                continue

            # Convert relative URLs to absolute
            try:
                absolute_url = urljoin(resolve_base, href)
                parsed_target = urlparse(absolute_url)

                # The Links tab shows the link as authored (parameters and
                # all); only the crawl frontier drops tracking parameters, so
                # keep both forms: display_url for the record, crawl_url for
                # every lookup that has to match a crawled URL.
                display_url = normalize_url(absolute_url)
                crawl_url = normalize_url(absolute_url, self.strip_params)

                # Determine if link is internal or external
                target_domain_clean = parsed_target.netloc.replace('www.', '', 1)
                base_domain_clean = self.base_domain.replace('www.', '', 1)
                is_internal = target_domain_clean == base_domain_clean

                # Find the status of the target URL if we've crawled it
                target_status = None
                for result in crawl_results:
                    if result['url'] == crawl_url:
                        target_status = result['status_code']
                        break

                # Determine placement (navigation, footer, body)
                placement = self._detect_link_placement(link)

                # rel is a multi-valued attribute, so BeautifulSoup hands back
                # a list; normalise to a space-joined lowercase string and keep
                # the raw value so checks for sponsored/ugc/noopener can read
                # it too. Only 'nofollow' sets the nofollow flag.
                rel_attr = link.get('rel')
                if isinstance(rel_attr, (list, tuple)):
                    rel = ' '.join(rel_attr).lower()
                else:
                    rel = (rel_attr or '').strip().lower()

                target_attr = link.get('target')
                if isinstance(target_attr, (list, tuple)):
                    target_attr = ' '.join(target_attr)
                link_target = (target_attr or '').strip().lower()

                in_nav, in_main, in_heading = self._detect_link_context(link)

                link_data = {
                    'source_url': source_url,
                    # The href as authored: only this distinguishes a relative
                    # link from an absolute one after the crawl
                    'href': href,
                    'target_url': display_url,
                    'anchor_text': anchor_text or '(no text)',
                    'is_internal': is_internal,
                    'target_domain': parsed_target.netloc,
                    'target_status': target_status,
                    'placement': placement,
                    'rel': rel,
                    'nofollow': 'nofollow' in rel.split(),
                    'link_target': link_target,
                    'in_nav': in_nav,
                    'in_main': in_main,
                    'in_heading': in_heading
                }

                # Track source page for this URL (for "Linked From" feature).
                # Keyed by the crawled URL so the results table finds it.
                with self.urls_lock:
                    if crawl_url not in self.source_pages:
                        self.source_pages[crawl_url] = []
                    if source_url not in self.source_pages[crawl_url]:
                        self.source_pages[crawl_url].append(source_url)

                candidates.append(link_data)

            except Exception:
                continue

        # Also collect <img src> as links so broken images are discoverable
        imgs = soup.find_all('img', src=True)
        for img in imgs:
            src = img.get('src', '').strip()
            if not src or src.startswith('data:'):
                continue

            # keep "decorative" apart from "no attribute" (issue #95)
            raw_alt = img.get('alt')
            alt_text = (raw_alt or '').strip()[:100]
            if not alt_text:
                alt_text = '(decorative)' if raw_alt is not None else '(no alt attribute)'

            try:
                absolute_url = urljoin(resolve_base, src)
                parsed_target = urlparse(absolute_url)

                # Only HTTP(S) images
                if parsed_target.scheme not in ('http', 'https'):
                    continue

                # Image records feed the crawler's HEAD-check / image-result
                # pipeline by target_url, so these stay fully normalised.
                clean_url = normalize_url(absolute_url, self.strip_params)

                target_domain_clean = parsed_target.netloc.replace('www.', '', 1)
                base_domain_clean = self.base_domain.replace('www.', '', 1)
                is_internal = target_domain_clean == base_domain_clean

                target_status = None
                for result in crawl_results:
                    if result['url'] == clean_url:
                        target_status = result['status_code']
                        break

                link_data = {
                    'source_url': source_url,
                    'target_url': clean_url,
                    'anchor_text': alt_text,
                    'is_internal': is_internal,
                    'target_domain': parsed_target.netloc,
                    'target_status': target_status,
                    'placement': 'image',
                    # Images carry the same keys so every record has one shape,
                    # but the anchor-only context flags stay at their defaults.
                    'rel': '',
                    'nofollow': False,
                    'link_target': '',
                    'in_nav': False,
                    'in_main': False,
                    'in_heading': False
                }

                candidates.append(link_data)

            except Exception:
                continue

        return self._commit_links(candidates)

    def _commit_links(self, candidates):
        """Append new links and journal them in one locked step.

        Emitting inside links_lock is what keeps update_link_statuses from
        announcing a status backfill for a link the UI has not received yet.
        """
        # A page often links to one URL twice: once in the header or footer,
        # once in its body copy. Only one record per source/target is kept,
        # so keep the body copy; otherwise every page the template links to
        # (sign-up, pricing, contact) looks like a navigation-only orphan.
        best = {}
        for link_data in candidates:
            link_key = f"{link_data['source_url']}|{link_data['target_url']}"
            kept = best.get(link_key)
            if kept is None or (_link_rank(link_data) < _link_rank(kept)):
                best[link_key] = link_data
        candidates = list(best.values())

        new_links = []
        with self.links_lock:
            for link_data in candidates:
                link_key = f"{link_data['source_url']}|{link_data['target_url']}"
                if link_key not in self.links_set:
                    self.links_set.add(link_key)
                    self.all_links.append(link_data)
                    new_links.append(link_data)

            if new_links and self.event_log:
                self.event_log.emit_many('link', new_links)

        return new_links

    def _detect_link_context(self, link_element):
        """Classify a link by its ancestors: nav/main/heading.

        One bounded walk up the tree fills all three flags, instead of running
        a CSS query per flag per link.

        Returns (in_nav, in_main, in_heading). in_main is mutually exclusive
        with in_nav — a nav that happens to sit inside <main> is still nav.
        """
        in_nav = False
        in_main = False
        in_heading = False

        current = link_element.parent
        steps = 0
        while current is not None and steps < MAX_ANCESTOR_DEPTH:
            name = current.name
            if not name:
                break

            if name in HEADING_ANCESTOR_TAGS:
                in_heading = True

            if name in NAV_ANCESTOR_TAGS:
                in_nav = True
            elif name in MAIN_ANCESTOR_TAGS:
                in_main = True

            if not in_nav:
                role = current.get('role')
                if isinstance(role, (list, tuple)):
                    role = ' '.join(role)
                if role and role.strip().lower() == 'main':
                    in_main = True

            # Build the class+id haystack once per ancestor
            classes = current.get('class')
            element_id = current.get('id')
            if classes or element_id:
                if isinstance(classes, (list, tuple)):
                    haystack = ' '.join(classes)
                else:
                    haystack = classes or ''
                if element_id:
                    haystack = f"{haystack} {element_id}" if haystack else element_id
                haystack = haystack.lower()

                if any(hint in haystack for hint in NAV_HINTS):
                    in_nav = True
                elif any(hint in haystack for hint in MAIN_HINTS):
                    in_main = True

            current = current.parent
            steps += 1

        return in_nav, (in_main and not in_nav), in_heading

    def _detect_link_placement(self, link_element):
        """Detect where on the page a link is placed"""
        # Check parent elements up the tree
        current = link_element.parent

        while current and current.name:
            # Check for footer
            if current.name == 'footer':
                return 'footer'

            # Check for footer by class/id
            classes = current.get('class', [])
            element_id = current.get('id', '')
            classes_str = ' '.join(classes).lower() if classes else ''

            if 'footer' in classes_str or 'footer' in element_id.lower():
                return 'footer'

            # Check for navigation
            if current.name in ['nav', 'header']:
                return 'navigation'

            # Check for navigation by class/id
            if any(keyword in classes_str or keyword in element_id.lower()
                   for keyword in ['nav', 'menu', 'header']):
                return 'navigation'

            current = current.parent

        # Default to body if not in nav or footer
        return 'body'

    def is_internal(self, url):
        """Check if URL is internal to the base domain"""
        parsed_url = urlparse(url)
        url_domain_clean = parsed_url.netloc.replace('www.', '', 1)
        base_domain_clean = self.base_domain.replace('www.', '', 1)
        return url_domain_clean == base_domain_clean

    def add_url(self, url, depth):
        """Add a URL to the discovery queue"""
        url = normalize_url(url, self.strip_params)
        with self.urls_lock:
            if url not in self.all_discovered_urls and url not in self.visited_urls:
                self.all_discovered_urls.add(url)
                self.discovered_urls.append((url, depth))

    def mark_discovered(self, url):
        """Count a URL as discovered without queueing it.

        Image rows synthesized from HEAD checks are real results but were never
        queued, so without this the "URLs Discovered" counter sits at the HTML
        page count while the crawled count climbs past it.
        """
        with self.urls_lock:
            self.all_discovered_urls.add(normalize_url(url, self.strip_params))

    def mark_visited(self, url):
        """Mark a URL as visited"""
        with self.urls_lock:
            self.visited_urls.add(normalize_url(url, self.strip_params))

    def get_next_url(self):
        """Get the next URL to crawl"""
        with self.urls_lock:
            if self.discovered_urls:
                return self.discovered_urls.popleft()
        return None

    def get_stats(self):
        """Get current statistics"""
        with self.urls_lock:
            return {
                'discovered': len(self.all_discovered_urls),
                'visited': len(self.visited_urls),
                'pending': len(self.discovered_urls)
            }

    def update_link_statuses(self, crawl_results):
        """Update target_status for all links based on crawl results.

        Returns the links whose status actually changed, so callers can
        emit update events for just those.
        """
        # Build a fast lookup dict
        status_lookup = {result['url']: result['status_code'] for result in crawl_results}

        changed = []
        with self.links_lock:
            for link in self.all_links:
                target_url = link['target_url']
                status = status_lookup.get(target_url)
                if status is None and self.strip_params:
                    # Link records keep their tracking parameters; the crawl
                    # stored the stripped URL, so fall back to that key.
                    status = status_lookup.get(normalize_url(target_url, self.strip_params))
                if status is not None and link['target_status'] != status:
                    link['target_status'] = status
                    changed.append(link)

            # Inside the lock: a link is only ever updated after its own
            # 'link' event has been journalled
            if changed and self.event_log:
                self.event_log.emit_many('link_update', changed)
        return changed

    def get_source_pages(self, url):
        """Get list of source pages that link to this URL.

        Falls back to the normalised URL so a caller holding a link target
        that still carries tracking parameters finds the crawled page.
        """
        with self.urls_lock:
            sources = self.source_pages.get(url)
            if sources is None and self.strip_params:
                sources = self.source_pages.get(normalize_url(url, self.strip_params))
            return list(sources) if sources else []

    def reset(self):
        """Reset all state"""
        with self.urls_lock:
            self.visited_urls.clear()
            self.discovered_urls.clear()
            self.all_discovered_urls.clear()
            self.source_pages.clear()

        with self.links_lock:
            self.all_links.clear()
            self.links_set.clear()
