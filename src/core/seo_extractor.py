"""SEO data extraction from HTML content"""
import copy
import re
import json
from urllib.parse import urljoin, urlparse

from src.utils.text_fingerprint import simhash, text_hash

# Main-text extraction tuning. NON_CONTENT_TAGS are dropped outright; the
# selectors in DEFAULT_IGNORE_SELECTORS are the site chrome that repeats on
# every page and would otherwise make unrelated pages look like duplicates.
MAIN_TEXT_MAX_CHARS = 200000
NON_CONTENT_TAGS = ['script', 'style', 'noscript', 'template', 'svg',
                    'header', 'nav', 'footer', 'aside', 'form']
DEFAULT_IGNORE_SELECTORS = ['header', 'nav', 'footer', 'aside', '.cookie', '.breadcrumb']
MAIN_CONTENT_SELECTORS = ['main', 'article', '[role=main]', '#content', '.content']

# Class-name hints for content that exists only for screen readers.
HIDDEN_CLASS_HINTS = ('sr-only', 'visually-hidden', 'screen-reader')

# Stray spacing in raw heading text. A run of whitespace containing a newline
# or a tab is source indentation, not a defect, so every pattern below is
# written in terms of literal spaces.
HEADING_TEXT_PROBLEMS = [
    (re.compile(r'^ +\S'), 'leading_whitespace'),
    (re.compile(r'\S +$'), 'trailing_whitespace'),
    (re.compile(r'\S {2,}\S'), 'double_space')
]


def _first_number(*values):
    """The first value that reads as a pixel count, or None."""
    for value in values:
        digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
        if digits:
            return int(digits)
    return None


class SEOExtractor:
    """Extracts SEO-related data from HTML content"""

    @staticmethod
    def extract_basic_seo_data(soup, result):
        """Extract basic SEO data (title, headings, meta description, etc.)"""
        # Extract title
        title_tag = soup.find('title')
        result['title'] = title_tag.get_text().strip() if title_tag else ''

        # Extract meta description
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        result['meta_description'] = meta_desc.get('content', '').strip() if meta_desc else ''

        # Extract headings
        h1_tag = soup.find('h1')
        result['h1'] = h1_tag.get_text().strip() if h1_tag else ''

        h2_tags = soup.find_all('h2')
        result['h2'] = [h2.get_text().strip() for h2 in h2_tags[:10]]

        h3_tags = soup.find_all('h3')
        result['h3'] = [h3.get_text().strip() for h3 in h3_tags[:10]]

        # Count words
        text_content = soup.get_text()
        words = re.findall(r'\b\w+\b', text_content)
        result['word_count'] = len(words)

        # Extract language
        html_tag = soup.find('html')
        result['lang'] = html_tag.get('lang', '') if html_tag else ''

        # Extract charset
        charset_meta = soup.find('meta', attrs={'charset': True})
        if charset_meta:
            result['charset'] = charset_meta.get('charset', '')
        else:
            content_type_meta = soup.find('meta', attrs={'http-equiv': 'Content-Type'})
            if content_type_meta:
                content = content_type_meta.get('content', '')
                charset_match = re.search(r'charset=([^;]+)', content)
                result['charset'] = charset_match.group(1) if charset_match else ''

    @staticmethod
    def extract_meta_tags(soup, result):
        """Extract all meta tags"""
        meta_tags = soup.find_all('meta')

        for meta in meta_tags:
            name = meta.get('name', '').lower()
            content = meta.get('content', '')

            if name:
                result['meta_tags'][name] = content

                # Extract specific important meta tags
                if name == 'viewport':
                    result['viewport'] = content
                elif name == 'robots':
                    result['robots'] = content
                elif name == 'author':
                    result['author'] = content
                elif name == 'keywords':
                    result['keywords'] = content
                elif name == 'generator':
                    result['generator'] = content
                elif name == 'theme-color':
                    result['theme_color'] = content

        # Extract canonical URL
        canonical = soup.find('link', attrs={'rel': 'canonical'})
        result['canonical_url'] = canonical.get('href', '') if canonical else ''

    @staticmethod
    def extract_opengraph_tags(soup, result):
        """Extract OpenGraph meta tags"""
        og_metas = soup.find_all('meta', attrs={'property': re.compile(r'^og:')})

        for meta in og_metas:
            property_name = meta.get('property', '')
            content = meta.get('content', '')
            if property_name:
                key = property_name.replace('og:', '')
                result['og_tags'][key] = content

    @staticmethod
    def extract_twitter_tags(soup, result):
        """Extract Twitter Card meta tags"""
        twitter_metas = soup.find_all('meta', attrs={'name': re.compile(r'^twitter:')})

        for meta in twitter_metas:
            name = meta.get('name', '')
            content = meta.get('content', '')
            if name:
                key = name.replace('twitter:', '')
                result['twitter_tags'][key] = content

    @staticmethod
    def extract_json_ld(soup, result):
        """Extract JSON-LD structured data, and remember what failed.

        A block that does not parse is invisible in `json_ld` - it is exactly
        the defect worth reporting, so the raw body and the parser's complaint
        are kept beside the parsed blocks.
        """
        json_ld_scripts = soup.find_all('script', attrs={'type': 'application/ld+json'})
        result.setdefault('json_ld_raw', [])
        result.setdefault('json_ld_errors', [])

        for index, script in enumerate(json_ld_scripts, start=1):
            raw = script.string or script.get_text() or ''
            result['json_ld_raw'].append(raw[:20000])
            try:
                result['json_ld'].append(json.loads(raw))
            except (json.JSONDecodeError, AttributeError, TypeError) as error:
                result['json_ld_errors'].append({
                    'block': index,
                    'message': str(error),
                    'line': getattr(error, 'lineno', None),
                    'snippet': ' '.join(raw.split())[:300],
                })

    @staticmethod
    def extract_analytics_tracking(soup, html_content, result):
        """Detect analytics and tracking scripts"""
        # Google Analytics patterns
        ga_patterns = [
            r'gtag\(',
            r'ga\(',
            r'GoogleAnalyticsObject',
            r'google-analytics\.com',
            r'googletagmanager\.com'
        ]

        # GA4 ID pattern
        ga4_match = re.search(r'G-[A-Z0-9]{10}', html_content)
        if ga4_match:
            result['analytics']['ga4_id'] = ga4_match.group()
            result['analytics']['gtag'] = True

        # GTM ID pattern
        gtm_match = re.search(r'GTM-[A-Z0-9]+', html_content)
        if gtm_match:
            result['analytics']['gtm_id'] = gtm_match.group()

        # Check for various analytics
        for pattern in ga_patterns:
            if re.search(pattern, html_content, re.IGNORECASE):
                result['analytics']['google_analytics'] = True
                break

        # Facebook Pixel
        if re.search(r'fbq\(|facebook\.com/tr', html_content, re.IGNORECASE):
            result['analytics']['facebook_pixel'] = True

        # Hotjar
        if re.search(r'hotjar\.com|hj\(', html_content, re.IGNORECASE):
            result['analytics']['hotjar'] = True

        # Mixpanel
        if re.search(r'mixpanel\.com|mixpanel\.track', html_content, re.IGNORECASE):
            result['analytics']['mixpanel'] = True

    @staticmethod
    def extract_images(soup, base_url, result):
        """Extract image information"""
        images = soup.find_all('img')

        for img in images:
            src = img.get('src', '')
            # alt="" is correct markup for a decorative image, and is not the
            # same as having no alt attribute. Keep the two apart (issue #95).
            raw_alt = img.get('alt')
            alt = raw_alt if raw_alt is not None else ''

            if src:
                # Convert relative URLs to absolute
                if src.startswith('//'):
                    src = 'https:' + src
                elif src.startswith('/'):
                    parsed_base = urlparse(base_url)
                    src = f"{parsed_base.scheme}://{parsed_base.netloc}{src}"
                elif not src.startswith(('http://', 'https://')):
                    src = urljoin(base_url, src)

                # loading and the declared size are what the lazy-above-the-fold
                # and oversized-image checks read; without them those checks can
                # never fire, however well they are tested
                declared_bytes = (img.get('data-filesize') or img.get('data-size') or '').strip()
                # An empty alt is correct for decoration, but not for an image
                # that carries meaning. The two cases a crawler can tell apart:
                # an image that IS a link's only content (the link then has no
                # accessible name), and one large enough to be content.
                link = img.find_parent('a')
                link_text = ' '.join((link.get_text() or '').split()) if link else ''
                declared_width = _first_number(img.get('width'), img.get('data-width'))
                declared_height = _first_number(img.get('height'), img.get('data-height'))
                result['images'].append({
                    'src': src,
                    'alt': alt,
                    'has_alt': raw_alt is not None,
                    'width': img.get('width', ''),
                    'height': img.get('height', ''),
                    'loading': (img.get('loading') or '').strip().lower(),
                    'fetchpriority': (img.get('fetchpriority') or '').strip().lower(),
                    'srcset': (img.get('srcset') or '').strip(),
                    'sizes': (img.get('sizes') or '').strip(),
                    'bytes': int(declared_bytes) if declared_bytes.isdigit() else None,
                    'dom_order': len(result['images']),
                    'in_link': bool(link),
                    'link_has_text': bool(link_text),
                    'is_probably_content': bool(
                        (link and not link_text)
                        or (declared_width and declared_width >= 200)
                        or (declared_height and declared_height >= 200)),
                })

    @staticmethod
    def extract_link_counts(soup, result, base_domain):
        """Count internal vs external links"""
        links = soup.find_all('a', href=True)

        for link in links:
            href = link.get('href', '')
            if href and not href.startswith(('#', 'mailto:', 'tel:', 'javascript:')):
                absolute_url = urljoin(result['url'], href)
                parsed_url = urlparse(absolute_url)

                # Handle www vs non-www domains
                url_domain_clean = parsed_url.netloc.replace('www.', '', 1)
                base_domain_clean = base_domain.replace('www.', '', 1)

                if url_domain_clean == base_domain_clean:
                    result['internal_links'] += 1
                else:
                    result['external_links'] += 1

    @staticmethod
    def extract_hreflang(soup, result):
        """Extract hreflang links"""
        hreflang_links = soup.find_all('link', attrs={'rel': 'alternate', 'hreflang': True})

        for link in hreflang_links:
            hreflang = link.get('hreflang', '')
            href = link.get('href', '')
            if hreflang and href:
                result['hreflang'].append({
                    'lang': hreflang,
                    'url': href
                })

    @staticmethod
    def extract_schema_org(soup, result):
        """Extract Schema.org microdata"""
        schema_items = soup.find_all(attrs={'itemtype': True})

        for item in schema_items:
            itemtype = item.get('itemtype', '')
            if itemtype:
                result['schema_org'].append({
                    'type': itemtype,
                    'properties': SEOExtractor._extract_microdata_properties(item)
                })

    @staticmethod
    def _extract_microdata_properties(element):
        """Extract microdata properties from an element"""
        properties = {}

        # Find all elements with itemprop
        prop_elements = element.find_all(attrs={'itemprop': True})

        for prop_elem in prop_elements:
            prop_name = prop_elem.get('itemprop', '')

            # Get content based on element type
            if prop_elem.name in ['meta']:
                content = prop_elem.get('content', '')
            elif prop_elem.name in ['img']:
                content = prop_elem.get('src', '')
            elif prop_elem.name in ['a']:
                content = prop_elem.get('href', '')
            else:
                content = prop_elem.get_text().strip()

            if prop_name and content:
                properties[prop_name] = content

        return properties

    @staticmethod
    def extract_main_text(soup, result, ignore_selectors=None):
        """Extract the page's main body text and its fingerprints.

        Template chrome (header/nav/footer/aside/form, plus anything matching
        ignore_selectors) is stripped first, so two pages that share a layout
        but differ in content stop looking identical - and two pages with the
        same content under different chrome start looking identical, which is
        what duplicate detection needs.

        Container preference: main, article, [role=main], #content, .content,
        otherwise the cleaned body.

        Runs on a copy of the tree, so the extractors that run after this one
        still see the full document.

        Sets main_text (whitespace-collapsed, capped at MAIN_TEXT_MAX_CHARS),
        main_text_hash, simhash (as a string, so SQLite and JSON keep all 64
        bits) and main_word_count. The hash, simhash and word count all
        describe the stored - i.e. possibly truncated - text.
        """
        if ignore_selectors is None:
            ignore_selectors = DEFAULT_IGNORE_SELECTORS

        try:
            working = copy.copy(soup)
        except Exception:
            # Never fall back to mutating the caller's tree.
            from bs4 import BeautifulSoup
            working = BeautifulSoup(str(soup), 'html.parser')

        for tag in working.find_all(NON_CONTENT_TAGS):
            if not getattr(tag, 'decomposed', False):
                tag.decompose()

        for selector in ignore_selectors:
            try:
                elements = working.select(selector)
            except Exception:
                # An unsupported selector must not take the crawl down.
                continue
            for element in elements:
                if not getattr(element, 'decomposed', False):
                    element.decompose()

        container = None
        for selector in MAIN_CONTENT_SELECTORS:
            try:
                container = working.select_one(selector)
            except Exception:
                container = None
            if container is not None:
                break

        if container is None:
            container = working.body or working

        text = re.sub(r'\s+', ' ', container.get_text(' ')).strip()
        if len(text) > MAIN_TEXT_MAX_CHARS:
            text = text[:MAIN_TEXT_MAX_CHARS].rstrip()

        result['main_text'] = text
        result['main_text_hash'] = text_hash(text)
        result['simhash'] = str(simhash(text))
        result['main_word_count'] = len(text.split())

    @staticmethod
    def _is_heading_visible(element):
        """Best-effort visibility check for a heading, HTML attributes only.

        Real CSS is out of scope: a heading hidden by a stylesheet rule, a
        zero height container or an off-screen transform still reports True.
        Returns False only when the element or one of its ancestors carries
        the hidden attribute, an inline display:none / visibility:hidden,
        aria-hidden="true", or a screen-reader-only class name.
        """
        for node in [element] + list(element.parents):
            attrs = getattr(node, 'attrs', None)
            if not attrs:
                continue

            if 'hidden' in attrs:
                return False

            if str(attrs.get('aria-hidden', '')).strip().lower() == 'true':
                return False

            style = str(attrs.get('style', '')).lower().replace(' ', '')
            if 'display:none' in style or 'visibility:hidden' in style:
                return False

            classes = attrs.get('class') or []
            if isinstance(classes, str):
                classes = classes.split()
            for class_name in classes:
                lowered = class_name.lower()
                if any(hint in lowered for hint in HIDDEN_CLASS_HINTS):
                    return False

        return True

    @staticmethod
    def extract_headings(soup, result):
        """Extract every h1-h6 in document order, plus their text defects.

        Sets headings to a list of {level, text, is_visible, dom_order} and
        heading_text_issues to a list of {dom_order, level, problem} for
        headings whose raw text has stray spacing - 'leading_whitespace',
        'trailing_whitespace' or 'double_space'. A heading with more than one
        defect contributes one entry per defect.

        Only literal spaces count as a defect: runs of whitespace containing a
        newline or tab are source indentation, which the browser collapses
        anyway, and flagging those would mean flagging every pretty-printed
        page.
        """
        headings = []
        issues = []

        for dom_order, heading in enumerate(soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])):
            raw_text = heading.get_text()
            level = int(heading.name[1])

            headings.append({
                'level': level,
                'text': re.sub(r'\s+', ' ', raw_text).strip(),
                'is_visible': SEOExtractor._is_heading_visible(heading),
                'dom_order': dom_order
            })

            for pattern, problem in HEADING_TEXT_PROBLEMS:
                if pattern.search(raw_text):
                    issues.append({
                        'dom_order': dom_order,
                        'level': level,
                        'problem': problem
                    })

        result['headings'] = headings
        result['heading_text_issues'] = issues

    @staticmethod
    def create_empty_result(url, depth, status_code=0, error=None, error_type=None):
        """Create an empty result structure.

        error_type: optional classification when status_code is 0 (no HTTP
        response). Values: 'dns_not_found', 'connection_refused', 'timeout',
        'ssl_error', 'connection_error', 'file_too_large', or None.
        """
        return {
            'url': url,
            'status_code': status_code,
            'error_type': error_type,
            'content_type': '',
            'size': 0,
            'is_internal': False,
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
            'main_text': '',
            'main_text_hash': '',
            'simhash': '',
            'main_word_count': 0,
            'headings': [],
            'heading_text_issues': [],
            'error': error
        }
