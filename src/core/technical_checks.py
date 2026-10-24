"""The standard technical checklist, over pages a crawl already collected.

Nothing here fetches anything: every check reads the stored page and link
records, so the whole pass is a few linear sweeps no matter how large the site.

What it does NOT do is repeat the per-page checks `src/core/issue_detector.py`
already makes (missing or over-long titles and descriptions, missing H1,
missing or differing canonical, noindex/nofollow tags, missing viewport or
lang, images without alt text, page size, response time, thin content, absent
structured data, social tags). Those stay where they are; this module covers
what needs the whole crawl in view - duplicates across pages, canonical and
redirect chains, link targets, hreflang reciprocity - plus the per-page checks
that were missing entirely.

Pages that declare each other as hreflang alternates are one page in several
locales (src/core/locales.py), so the duplicate title, description, H1 and
templated-title checks never compare them with each other; a duplicate inside
one locale, or between unrelated pages, is still reported.

Structured-data checks live in `src/core/schema_checks.py`, which this module
calls with its own grouping helper so every finding comes out the same shape.

    technical_issues(pages, links, base_url, config=None) -> [issue, ...]

Grouping follows `src/core/sitemap_audit.py`: up to `group_threshold` (5)
offenders are reported one issue per URL; beyond that they collapse into a
single issue carrying the count and the URLs in `evidence.related_urls`, with
an empty offending value so its id stays stable as the list shrinks.
"""

import re
from collections import defaultdict

from src.core import http_status
from urllib.parse import urlsplit, urlunsplit

from src.core import locales, schema_checks
from src.utils.issue_factory import make_issue
from src.utils.url_params import DEFAULT_TRACKING_PARAMS, strip_tracking_params

CATEGORY = 'Technical'

DEFAULTS = {
    'group_threshold': 5,
    'max_related_urls': 50,
    'title_pixel_max': 580,
    'description_pixel_max': 990,
    'ttfb_warn_ms': 800,
    'ttfb_error_ms': 2000,
    'page_weight_warn_bytes': 3 * 1024 * 1024,
    'image_weight_warn_bytes': 300 * 1024,
    'above_the_fold_images': 2,
    'templated_title_min_pages': 3,
    'max_redirect_hops': 1,
    'tracking_params': DEFAULT_TRACKING_PARAMS,
    'robots_checker': None,     # callable(url) -> True when crawling is allowed
    'sitemap_urls': None,       # set of URLs listed in the site's sitemaps
    'skip_checks': (),
}

# Average glyph widths at 20px Arial, the size Google renders a result title
# in, bucketed by character class. A real measurement needs the font; this
# approximation is within a few percent for Latin copy, which is enough to
# tell "fits" from "will be cut off".
_NARROW = set("iíjlItf.,:;'\"!|[]()-‑ ")
_WIDE = set('mwMW@%&')
_CAPS = set('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789')

_LANGUAGE_RE = re.compile(r'^[a-z]{2,3}(-[A-Za-z]{2,4})?(-[A-Za-z0-9]{2,8})*$')
_REGION_FIXES = {'UK': 'GB', 'EU': '(no country code)', 'EN': 'GB or US'}

GENERIC_CACHE = ('no-store', 'no-cache', 'max-age=0')


def technical_issues(pages, links, base_url, config=None):
    """Every technical finding for one crawl, ready to report."""
    cfg = dict(DEFAULTS)
    cfg.update(config or {})

    pages = [page for page in (pages or []) if isinstance(page, dict) and page.get('url')]
    links = [link for link in (links or []) if isinstance(link, dict)]
    html_pages = [page for page in pages if _is_html(page)]

    issues = []

    def emit(check, severity, name, items, group_details, how_to_fix):
        """One issue per offender, or one grouped issue when there are many."""
        if not items or check in cfg['skip_checks']:
            return
        threshold = cfg['group_threshold']
        if len(items) <= threshold:
            for item in items:
                issues.append(make_issue(
                    item['url'], check, severity, item.get('category', CATEGORY), name,
                    item['details'], offending_value=item.get('offending', ''),
                    evidence=item.get('evidence'), how_to_fix=how_to_fix))
            return
        urls = [item['url'] for item in items][:cfg['max_related_urls']]
        issues.append(make_issue(
            base_url or items[0]['url'], check, severity, CATEGORY, name,
            group_details.format(count=len(items)),
            evidence={'found': str(len(items)), 'related_urls': urls},
            how_to_fix=how_to_fix))

    by_url = {_key(page['url'], cfg): page for page in pages}
    alternates = locales.build(html_pages)

    _canonical_checks(html_pages, by_url, base_url, cfg, emit)
    _robots_checks(html_pages, links, by_url, cfg, emit)
    _redirect_checks(pages, links, by_url, cfg, emit)
    _broken_target_checks(links, cfg, emit)
    _duplicate_text_checks(html_pages, alternates, cfg, emit)
    _templated_title_checks(html_pages, alternates, cfg, emit)
    _pixel_width_checks(html_pages, cfg, emit)
    _heading_checks(html_pages, cfg, emit)
    _image_checks(html_pages, cfg, emit)
    _decorative_alt_checks(html_pages, cfg, emit)
    _hreflang_checks(html_pages, by_url, cfg, emit)
    _performance_checks(html_pages, cfg, emit)
    schema_checks.schema_issues(html_pages, cfg, emit)

    return issues


# ---------------------------------------------------------------------------
# canonical
# ---------------------------------------------------------------------------

def _canonical_checks(pages, by_url, base_url, cfg, emit):
    relative, cross_domain, chained, to_redirect, to_noindex = [], [], [], [], []
    base_host = _host(base_url)

    for page in pages:
        url = page['url']
        canonical = (page.get('canonical_url') or '').strip()
        if not canonical:
            continue  # issue_detector reports a missing canonical

        if not urlsplit(canonical).scheme:
            relative.append(_item(url, canonical,
                                  f'The canonical is written as "{canonical}", not an absolute URL.',
                                  {'found': canonical, 'expected': 'an absolute https:// URL'}))
            continue

        canonical_host = _host(canonical)
        if base_host and canonical_host and canonical_host != base_host:
            cross_domain.append(_item(url, canonical,
                                      f'The canonical points at another domain: {canonical}.',
                                      {'found': canonical_host, 'expected': base_host,
                                       'related_urls': [canonical]}))
            continue

        target = by_url.get(_key(canonical, cfg))
        if not target or _key(canonical, cfg) == _key(url, cfg):
            continue

        target_canonical = (target.get('canonical_url') or '').strip()
        if target_canonical and _key(target_canonical, cfg) != _key(canonical, cfg):
            chained.append(_item(
                url, canonical,
                f'The canonical points at {canonical}, which canonicalises again to '
                f'{target_canonical}.',
                {'found': f'{url} -> {canonical} -> {target_canonical}',
                 'related_urls': [canonical, target_canonical]}))

        if _redirects(target):
            to_redirect.append(_item(
                url, canonical,
                f'The canonical points at {canonical}, which redirects to '
                f'{target.get("redirected_to") or "another URL"}.',
                {'found': canonical, 'related_urls': [canonical]}))

        if _is_noindex(target):
            to_noindex.append(_item(
                url, canonical, f'The canonical points at {canonical}, which is noindex.',
                {'found': canonical, 'related_urls': [canonical]}))

    emit('canonical_relative', 'warning', 'Relative Canonical URL', relative,
         '{count} pages carry a relative canonical.',
         'Write the canonical as an absolute URL; a relative one is resolved inconsistently.')
    emit('canonical_cross_domain', 'error', 'Canonical Points to Another Domain', cross_domain,
         '{count} pages canonicalise to another domain.',
         'Point the canonical at this site, or remove the page if the other domain owns it.')
    emit('canonical_chain', 'warning', 'Canonical Chain', chained,
         '{count} pages canonicalise to a page that canonicalises somewhere else.',
         'Point every page at the final canonical URL directly.')
    emit('canonical_to_redirect', 'warning', 'Canonical Points to a Redirect', to_redirect,
         '{count} pages canonicalise to a URL that redirects.',
         'Canonicalise to the destination of the redirect.')
    emit('canonical_to_noindex', 'error', 'Canonical Points to a Noindex Page', to_noindex,
         '{count} pages canonicalise to a noindex page.',
         'Canonicalise to an indexable page, or drop the noindex from the target.')


# ---------------------------------------------------------------------------
# robots and indexability conflicts
# ---------------------------------------------------------------------------

def _robots_checks(pages, links, by_url, cfg, emit):
    linked_noindex, sitemap_noindex, blocked_canonical = [], [], []
    nofollowed = defaultdict(list)

    inbound = defaultdict(int)
    for link in links:
        if link.get('is_internal') and link.get('target_url'):
            inbound[_key(link['target_url'], cfg)] += 1
        if link.get('is_internal') and link.get('nofollow'):
            nofollowed[link.get('source_url') or ''].append(link.get('target_url') or '')

    sitemap_urls = cfg.get('sitemap_urls')
    sitemap_keys = {_key(url, cfg) for url in sitemap_urls} if sitemap_urls else set()
    robots_checker = cfg.get('robots_checker')

    for page in pages:
        url = page['url']
        if _is_noindex(page):
            count = inbound.get(_key(url, cfg), 0)
            if count:
                linked_noindex.append(_item(
                    url, 'noindex',
                    f'This page is noindex but {count} internal link(s) point at it.',
                    {'found': f'{count} internal links'}))
            if sitemap_keys and _key(url, cfg) in sitemap_keys:
                sitemap_noindex.append(_item(
                    url, 'noindex+sitemap',
                    'This page is noindex but the sitemap lists it.',
                    {'found': 'noindex', 'expected': 'either indexable or absent from the sitemap'}))

        canonical = (page.get('canonical_url') or '').strip()
        if canonical and robots_checker:
            try:
                allowed = robots_checker(canonical)
            except Exception:
                allowed = True
            if allowed is False:
                blocked_canonical.append(_item(
                    url, canonical,
                    f'robots.txt blocks {canonical}, which this page names as its canonical.',
                    {'found': canonical, 'related_urls': [canonical]}))

    nofollow_items = [
        _item(source, ' '.join(sorted(set(targets))[:5]),
              f'{len(targets)} internal link(s) on this page are nofollow.',
              {'found': f'{len(targets)} links', 'related_urls': sorted(set(targets))[:10]})
        for source, targets in sorted(nofollowed.items()) if source
    ]

    emit('noindex_page_linked', 'warning', 'Noindex Page Linked Internally', linked_noindex,
         '{count} noindex pages are linked from the site.',
         'Drop the links, or make the page indexable if it should rank.')
    emit('noindex_in_sitemap', 'error', 'Noindex Page in the Sitemap', sitemap_noindex,
         '{count} noindex pages are listed in the sitemap.',
         'Remove the URL from the sitemap, or remove its noindex.')
    emit('canonical_blocked_by_robots', 'error', 'Canonical Blocked by robots.txt',
         blocked_canonical, '{count} pages canonicalise to a URL robots.txt blocks.',
         'Allow the canonical URL in robots.txt; a blocked canonical cannot be read.')
    emit('internal_link_nofollow', 'warning', 'Nofollow on Internal Links', nofollow_items,
         '{count} pages carry nofollow internal links.',
         'Remove rel="nofollow" from internal links; it only wastes the link.')


# ---------------------------------------------------------------------------
# redirects
# ---------------------------------------------------------------------------

def _redirect_checks(pages, links, by_url, cfg, emit):
    long_chains, loops, to_redirect, protocol_mix, host_mix = [], [], [], [], []

    for page in pages:
        chain = page.get('redirects') or []
        if not isinstance(chain, list) or not chain:
            continue
        hops = [hop.get('url') for hop in chain if isinstance(hop, dict)]
        final = page.get('redirected_to') or page['url']
        if len(chain) > cfg['max_redirect_hops']:
            long_chains.append(_item(
                page['url'], str(len(chain)),
                f'{len(chain)} redirects before reaching {final}.',
                {'found': ' -> '.join(hops + [final])[:300]}))
        # A loop means the chain came back to an address it had already left.
        # A chain whose every hop is the same address never went anywhere, so
        # it is a no-op the browser invented, not a loop the server built.
        if final in hops and len(set(hops + [final])) > 1:
            loops.append(_item(page['url'], final, f'The redirect chain returns to {final}.',
                               {'found': ' -> '.join(hops + [final])[:300]}))

    redirecting = {_key(page['url'], cfg) for page in pages if _redirects(page)}
    by_source = defaultdict(list)
    for link in links:
        if not link.get('is_internal') or not link.get('target_url'):
            continue
        target = link['target_url']
        if _key(target, cfg) in redirecting:
            by_source[link.get('source_url') or ''].append(target)

    for source, targets in sorted(by_source.items()):
        if not source:
            continue
        unique = sorted(set(targets))
        to_redirect.append(_item(
            source, ' '.join(unique[:5]),
            f'{len(unique)} internal link(s) point at a URL that redirects.',
            {'found': f'{len(unique)} links', 'related_urls': unique[:10]}))

    schemes, hosts = defaultdict(set), defaultdict(set)
    for link in links:
        if not link.get('is_internal'):
            continue
        parts = urlsplit(link.get('target_url') or '')
        if not parts.netloc:
            continue
        schemes[parts.netloc.lower().replace('www.', '', 1)].add(parts.scheme)
        hosts[parts.netloc.split(':')[0].lower().replace('www.', '', 1)].add(parts.netloc.lower())

    for host, found in schemes.items():
        if len(found) > 1:
            protocol_mix.append(_item(
                f'{sorted(found)[0]}://{host}', host,
                f'Internal links to {host} use both {" and ".join(sorted(found))}.',
                {'found': ', '.join(sorted(found)), 'expected': 'https everywhere'}))
    for host, found in hosts.items():
        if len(found) > 1:
            host_mix.append(_item(
                f'https://{sorted(found)[0]}', host,
                f'Internal links use both {" and ".join(sorted(found))}.',
                {'found': ', '.join(sorted(found)), 'expected': 'one canonical hostname'}))

    emit('redirect_chain', 'warning', 'Redirect Chain', long_chains,
         '{count} URLs redirect more than once before resolving.',
         'Point the first URL straight at the final destination.')
    emit('redirect_loop', 'error', 'Redirect Loop', loops,
         '{count} URLs redirect in a loop.',
         'Break the loop: a redirect must end at a URL that serves content.')
    emit('internal_link_to_redirect', 'warning', 'Internal Links Point at Redirects', to_redirect,
         '{count} pages link to URLs that redirect.',
         'Link straight to the destination; every redirect costs a round trip.')
    emit('mixed_protocol_links', 'warning', 'Internal Links Mix http and https', protocol_mix,
         '{count} hosts are linked over both http and https.',
         'Link internally over https only.')
    emit('mixed_host_links', 'warning', 'Internal Links Mix www and non-www', host_mix,
         '{count} hosts are linked under more than one spelling.',
         'Pick one hostname and link to it consistently.')


def _broken_target_checks(links, cfg, emit):
    """Which page links to each broken URL - the fix is on the source page.

    A 403 is not a broken link. Crawling ten well-known SEO sites reported
    "301 pages link to broken URLs" on one of them, and every one of those URLs
    loaded fine in a browser: they sat behind a bot wall. Those are now their
    own finding, at info, described as what they are.
    """
    broken = defaultdict(list)
    blocked = defaultdict(list)
    for link in links:
        status = link.get('target_status')
        if not isinstance(status, int):
            continue
        source = link.get('source_url') or ''
        if not source:
            continue
        if http_status.is_broken(status):
            broken[source].append((link.get('target_url') or '', status))
        elif http_status.is_blocked(status):
            blocked[source].append((link.get('target_url') or '', status))

    def group(bucket, phrase):
        items = []
        for source, targets in sorted(bucket.items()):
            unique = sorted(set(targets))
            listed = '; '.join(f'{url} ({status})' for url, status in unique[:3])
            items.append(_item(
                source, ' '.join(url for url, _ in unique),
                f'{len(unique)} link(s) on this page {phrase}: {listed}'
                + (f' (+{len(unique) - 3} more)' if len(unique) > 3 else ''),
                {'found': f'{len(unique)} {phrase}',
                 'related_urls': [url for url, _ in unique][:10]}))
        return items

    emit('links_to_broken_urls', 'error', 'Page Links to Broken URLs',
         group(broken, 'are broken'),
         '{count} pages link to URLs that are gone or erroring.',
         'Update or remove the link; a visitor following it hits an error.')

    emit('links_to_blocked_urls', 'info', 'Page Links to URLs That Refused the Crawler',
         group(blocked, 'refused this crawler'),
         '{count} pages link to URLs behind a bot wall or a login.',
         'Usually nothing to fix: these are 401/403/429 responses to an '
         'automated client, and the page may well load fine in a browser. '
         'Worth a look only if the URL should be public.')


# ---------------------------------------------------------------------------
# duplicate and over-wide text
# ---------------------------------------------------------------------------

def _canonicalises_away(page, cfg):
    """True when the page names a different URL as the canonical one.

    Such a page has already told search engines it is not the version to
    index, so repeating its title or description is the intended behaviour,
    not a defect. Reporting it was how one `?topic=sales` URL - correctly
    canonicalised to `/contact` - produced nine findings on a healthy site.
    """
    canonical = (page.get('canonical_url') or '').strip()
    if not canonical:
        return False
    return _key(canonical, cfg) != _key(page.get('url'), cfg)


def _duplicate_text_checks(pages, alternates, cfg, emit):
    titles, descriptions, h1s = defaultdict(list), defaultdict(list), defaultdict(list)
    pages = [page for page in pages if not _canonicalises_away(page, cfg)]
    for page in pages:
        if _collapse(page.get('title')):
            titles[_collapse(page.get('title'))].append(page['url'])
        if _collapse(page.get('meta_description')):
            descriptions[_collapse(page.get('meta_description'))].append(page['url'])
        if _collapse(page.get('h1')):
            h1s[_collapse(page.get('h1'))].append(page['url'])

    emit('duplicate_title', 'warning', 'Duplicate Title Across Pages',
         _shared_items(titles, 'title', alternates),
         '{count} pages share a title with another page.',
         'Give every page a title that describes that page alone.')
    emit('duplicate_meta_description', 'warning', 'Duplicate Meta Description Across Pages',
         _shared_items(descriptions, 'meta description', alternates),
         '{count} pages share a meta description with another page.',
         'Write a description per page; a shared one is usually rewritten by Google.')
    emit('duplicate_h1', 'info', 'Duplicate H1 Across Pages', _shared_items(h1s, 'H1', alternates),
         '{count} pages share their H1 with another page.',
         'Make each H1 name what is on that page.')


def _shared_items(index, label, alternates):
    items = []
    for value, urls in sorted(index.items()):
        if len(urls) < 2:
            continue
        for url in urls:
            others = [other for other in urls
                      if other != url and not alternates.same_page(url, other)]
            if not others:
                continue
            items.append(_item(
                url, value[:120],
                f'{len(others)} other page(s) use the same {label}: "{value[:80]}".',
                {'found': value[:200], 'related_urls': others[:10]}))
    return items


def _templated_title_checks(pages, alternates, cfg, emit):
    """Titles built from one template, where the unique part says nothing.

    "Services | Brand" and "Pricing | Brand" are fine: the part that varies
    identifies the page. These are the ones where it does not - the variable
    part is missing, a bare number, or repeated across pages - so the title is
    a duplicate in everything but the template.
    """
    skeletons = defaultdict(list)
    for page in pages:
        title = _collapse(page.get('title'))
        if not title:
            continue
        # A title may also START with the separator, which is the worst case:
        # the part that should identify the page is simply missing.
        parts = re.split(r'\s*[|\u2013\u2014]\s+|\s+-\s+', title)
        if len(parts) < 2:
            continue
        # The template is what the pages share; the rest is meant to identify
        skeleton = ' | '.join(part.strip() for part in parts[1:]).lower()
        unique = parts[0].strip()
        skeletons[skeleton].append((page['url'], unique, title))

    items = []
    for skeleton, entries in sorted(skeletons.items()):
        # Two pages sharing a template is enough to call it one; a title whose
        # unique part says nothing is worth reporting even then, while a
        # repeated unique part waits for the template to be established.
        if len(entries) < 2:
            continue
        seen = defaultdict(list)
        for url, unique, title in entries:
            seen[unique.lower()].append((url, title))
        for unique, pages_with in sorted(seen.items()):
            weak = (not unique) or unique.isdigit() or len(unique) < 3
            # Locale versions of one page sharing the unique part are one page.
            distinct = {alternates.logical(url) for url, _ in pages_with}
            if not weak and (len(distinct) < 2
                             or len(entries) < cfg['templated_title_min_pages']):
                continue
            reason = ('carries no unique part' if weak
                      else f'{len(distinct)} pages share the part before the template')
            for url, title in pages_with:
                items.append(_item(
                    url, title[:120],
                    f'This title is the template "{skeleton}" and {reason}.',
                    {'found': title[:200], 'expected': 'a title that names this page',
                     'related_urls': [other for other, _ in pages_with
                                      if other != url
                                      and not alternates.same_page(url, other)][:10]}))

    emit('templated_title', 'warning', 'Templated Title Says Nothing', items,
         '{count} pages carry a templated title whose unique part does not identify them.',
         'Put what the page is about before the template, and make it different per page.')


def _decorative_alt_checks(pages, cfg, emit):
    """alt="" on an image that carries meaning.

    An empty alt is correct for decoration and is deliberately NOT reported as
    missing alt text. It is wrong when the image is a link's only content - the
    link then has no accessible name at all - or when the image is large enough
    to be content rather than ornament.
    """
    items = []
    for page in pages:
        images = page.get('images')
        if not isinstance(images, list):
            continue
        offenders = [image for image in images
                     if isinstance(image, dict)
                     and image.get('has_alt') and not (image.get('alt') or '').strip()
                     and image.get('is_probably_content')]
        if not offenders:
            continue
        linked = [image for image in offenders if image.get('in_link')]
        detail = f'{len(offenders)} image(s) carry alt="" but are not decorative'
        if linked:
            detail += f'; {len(linked)} of them are a link\'s only content'
        items.append(_item(
            page['url'], str(len(offenders)), detail + '.',
            {'found': f'{len(offenders)} images',
             'expected': 'alt text describing the image, or a text label on the link',
             'related_urls': [image.get('src', '') for image in offenders][:5]}))

    emit('empty_alt_on_content_image', 'warning', 'Empty Alt on an Image That Means Something',
         items, '{count} pages serve meaningful images with an empty alt attribute.',
         'Describe the image in its alt, or give the link a text label; keep alt="" for '
         'decoration only.')


def _pixel_width_checks(pages, cfg, emit):
    wide_titles, wide_descriptions = [], []
    for page in pages:
        title = _collapse(page.get('title'))
        if title:
            width = pixel_width(title)
            if width > cfg['title_pixel_max']:
                wide_titles.append(_item(
                    page['url'], title[:120],
                    f'The title is about {width}px wide ({len(title)} characters); '
                    f'search results cut off near {cfg["title_pixel_max"]}px.',
                    {'found': f'{width}px', 'expected': f'{cfg["title_pixel_max"]}px',
                     'snippet': title[:200]}))
        description = _collapse(page.get('meta_description'))
        if description:
            # Snippets are set at 14px, titles at 20px: measure each at its own size.
            width = pixel_width(description, font_size=14)
            if width > cfg['description_pixel_max']:
                wide_descriptions.append(_item(
                    page['url'], description[:120],
                    f'The meta description is about {width}px wide ({len(description)} '
                    f'characters); it is cut off near {cfg["description_pixel_max"]}px.',
                    {'found': f'{width}px', 'expected': f'{cfg["description_pixel_max"]}px',
                     'snippet': description[:200]}))

    emit('title_too_wide', 'info', 'Title Too Wide for Search Results', wide_titles,
         '{count} titles are wider than a search result shows.',
         'Shorten the title, or move the wide part to the end where it can be cut.')
    emit('description_too_wide', 'info', 'Meta Description Too Wide', wide_descriptions,
         '{count} meta descriptions are wider than a search result shows.',
         'Put the point in the first 900px; the rest is cut off.')


def pixel_width(text, font_size=20):
    """Approximate rendered width in pixels at `font_size` (Arial-ish).

    Character-class averages rather than real metrics: enough to tell a title
    that fits from one that will be truncated, without shipping a font.
    """
    width = 0.0
    for character in text:
        if character in _NARROW:
            width += 0.28
        elif character in _WIDE:
            width += 0.85
        elif character in _CAPS:
            width += 0.62
        elif character == ' ':
            width += 0.27
        else:
            width += 0.52
    return int(width * font_size)


# ---------------------------------------------------------------------------
# headings
# ---------------------------------------------------------------------------

def _heading_checks(pages, cfg, emit):
    multiple, skipped, empty, whitespace = [], [], [], []

    for page in pages:
        headings = page.get('headings')
        if not isinstance(headings, list) or not headings:
            continue
        levels = [h.get('level') for h in headings if isinstance(h, dict)]

        h1_count = levels.count(1)
        if h1_count > 1:
            multiple.append(_item(
                page['url'], str(h1_count), f'The page has {h1_count} H1 headings.',
                {'found': f'{h1_count} H1s', 'expected': 'one H1',
                 'snippet': ' | '.join(h.get('text', '')[:40] for h in headings
                                       if isinstance(h, dict) and h.get('level') == 1)[:200]}))

        previous = None
        for heading in headings:
            if not isinstance(heading, dict):
                continue
            level = heading.get('level')
            if previous and isinstance(level, int) and level > previous + 1:
                skipped.append(_item(
                    page['url'], f'h{previous}->h{level}',
                    f'The headings jump from H{previous} to H{level}.',
                    {'found': f'H{previous} then H{level}',
                     'snippet': (heading.get('text') or '')[:120]}))
                break
            if isinstance(level, int):
                previous = level

        blank = [h for h in headings if isinstance(h, dict) and not (h.get('text') or '').strip()]
        if blank:
            empty.append(_item(
                page['url'], str(len(blank)),
                f'{len(blank)} heading(s) on this page are empty.',
                {'found': f'{len(blank)} empty headings'}))

        defects = page.get('heading_text_issues')
        if isinstance(defects, list) and defects:
            kinds = sorted({d.get('problem', '') for d in defects if isinstance(d, dict)})
            whitespace.append(_item(
                page['url'], ','.join(kinds),
                f'{len(defects)} heading(s) carry stray spacing ({", ".join(kinds).replace("_", " ")}).',
                {'found': ', '.join(kinds)}))

    emit('multiple_h1', 'warning', 'More Than One H1', multiple,
         '{count} pages carry more than one H1.',
         'Keep one H1 per page and demote the others to H2.')
    emit('skipped_heading_level', 'info', 'Heading Level Skipped', skipped,
         '{count} pages skip a heading level.',
         'Step heading levels one at a time so the outline reads correctly.')
    emit('empty_heading', 'warning', 'Empty Heading', empty,
         '{count} pages carry an empty heading.',
         'Remove the empty heading, or give it text.')
    emit('heading_stray_whitespace', 'info', 'Stray Whitespace in Heading Text', whitespace,
         '{count} pages carry headings with leading, trailing or doubled spaces.',
         'Trim the heading text; the spacing shows up in search results and anchors.')


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------

def _image_checks(pages, cfg, emit):
    no_dimensions, heavy, lazy_above_fold = [], [], []

    for page in pages:
        images = page.get('images')
        if not isinstance(images, list) or not images:
            continue

        missing = [img for img in images if isinstance(img, dict)
                   and not (img.get('width') or img.get('width_attr'))
                   and not (img.get('height') or img.get('height_attr'))]
        if missing:
            no_dimensions.append(_item(
                page['url'], str(len(missing)),
                f'{len(missing)} image(s) have no width or height attribute.',
                {'found': f'{len(missing)} images',
                 'related_urls': [img.get('src', '') for img in missing][:5]}))

        # Did the renderer measure this page? One image carrying a geometry
        # verdict is enough: the rest were seen and judged not to qualify.
        measured_page = any(isinstance(img, dict) and 'above_fold' in img
                            for img in images)

        for index, image in enumerate(images):
            if not isinstance(image, dict):
                continue
            size = image.get('bytes') or image.get('size')
            if isinstance(size, int) and size > cfg['image_weight_warn_bytes']:
                heavy.append(_item(
                    page['url'], image.get('src', '') or str(index),
                    f'{image.get("src", "An image")} weighs {size // 1024} KB.',
                    {'found': f'{size // 1024} KB',
                     'expected': f'under {cfg["image_weight_warn_bytes"] // 1024} KB',
                     'related_urls': [image.get('src', '')]}))
            if str(image.get('loading', '')).lower() != 'lazy':
                continue
            # Prefer what the browser measured. DOM order is a guess: on a
            # rendered page the first <img> in the source routinely sits
            # thousands of pixels down, and claiming it "sits at the top of
            # the page" on that basis is how this check used to lie.
            if measured_page:
                if image.get('above_fold') is not True:
                    continue
                top = image.get('top_px')
                where = f'is {top}px down, inside the first screenful' if top is not None \
                    else 'is inside the first screenful'
                lazy_above_fold.append(_item(
                    page['url'], image.get('src', '') or str(index),
                    f'{image.get("src", "An image")} is lazy-loaded but {where}.',
                    {'found': 'loading="lazy"',
                     'expected': 'eager for an image inside the first screenful',
                     'related_urls': [image.get('src', '')]}))
            elif index < cfg['above_the_fold_images']:
                lazy_above_fold.append(_item(
                    page['url'], image.get('src', '') or str(index),
                    f'{image.get("src", "An image")} is one of the first '
                    f'{cfg["above_the_fold_images"]} images in the page source and is '
                    f'lazy-loaded. This page was not rendered, so its position on '
                    f'screen was not measured.',
                    {'found': 'loading="lazy"',
                     'expected': 'eager when the image is inside the first screenful',
                     'related_urls': [image.get('src', '')]}))

    emit('image_missing_dimensions', 'warning', 'Image Without Width and Height',
         no_dimensions, '{count} pages serve images with no width or height.',
         'Set width and height so the browser can reserve the space and the layout stops jumping.')
    emit('image_too_heavy', 'warning', 'Oversized Image', heavy,
         '{count} images are heavier than they need to be.',
         'Compress the image and serve a modern format at the size it is displayed.')
    emit('image_lazy_above_fold', 'warning', 'Lazy-Loaded Image Above the Fold',
         lazy_above_fold, '{count} pages lazy-load an image that is visible immediately.',
         'Load the first images eagerly; lazy-loading them delays the largest paint.')


# ---------------------------------------------------------------------------
# hreflang
# ---------------------------------------------------------------------------

def _hreflang_checks(pages, by_url, cfg, emit):
    invalid, no_self, no_return = [], [], []
    declared = {}

    for page in pages:
        entries = locales.hreflang_entries(page)
        if not entries:
            continue
        url_key = _key(page['url'], cfg)
        declared[url_key] = entries

        codes = {code for code, _ in entries}
        for code, target in entries:
            if code.lower() == 'x-default':
                continue
            if not _LANGUAGE_RE.match(code):
                hint = _REGION_FIXES.get(code.split('-')[-1].upper())
                invalid.append(_item(
                    page['url'], code,
                    f'"{code}" is not a valid hreflang value'
                    + (f'; use {hint} instead.' if hint else '.'),
                    {'found': code, 'expected': hint or 'a language[-REGION] code, e.g. en-GB'}))

        if not any(_key(target, cfg) == url_key for _, target in entries):
            no_self.append(_item(
                page['url'], 'self', 'The hreflang set does not include this page itself.',
                {'found': ', '.join(sorted(codes))[:200],
                 'expected': 'a self-referencing hreflang'}))

    for url_key, entries in declared.items():
        for code, target in entries:
            target_key = _key(target, cfg)
            if target_key == url_key or target_key not in by_url:
                continue
            # A target that declares no hreflang at all is the commonest form
            # of this fault, not a reason to skip it: the translated page
            # simply never got its tags. `target_key not in by_url` above has
            # already excluded pages this crawl did not fetch, so reaching
            # here means the page was crawled and names nobody back.
            back = declared.get(target_key) or []
            if not any(_key(other, cfg) == url_key for _, other in back):
                no_return.append(_item(
                    by_url[url_key]['url'], target,
                    f'{target} is listed as the "{code}" version but does not link back.',
                    {'found': target, 'related_urls': [target]}))

    emit('hreflang_invalid_code', 'error', 'Invalid hreflang Code', invalid,
         '{count} pages declare an hreflang value that is not a valid code.',
         'Use a language code, optionally with a region: en, en-GB, es-US.')
    emit('hreflang_missing_self', 'warning', 'hreflang Set Has No Self-Reference', no_self,
         '{count} pages declare hreflang without naming themselves.',
         'Add an hreflang entry pointing at the page itself.')
    emit('hreflang_no_return_link', 'error', 'hreflang Return Link Missing', no_return,
         '{count} hreflang pairs are one-way.',
         'Every hreflang must be mutual: if A names B, B must name A.')


# ---------------------------------------------------------------------------
# performance signals the crawl already measured
# ---------------------------------------------------------------------------

def _performance_checks(pages, cfg, emit):
    slow_ttfb, uncacheable, heavy_pages = [], [], []

    for page in pages:
        ttfb = page.get('ttfb')
        if isinstance(ttfb, (int, float)) and ttfb >= cfg['ttfb_warn_ms']:
            severity_note = 'very slow' if ttfb >= cfg['ttfb_error_ms'] else 'slow'
            slow_ttfb.append(_item(
                page['url'], str(int(ttfb)),
                f'The server took {int(ttfb)}ms to send the first byte ({severity_note}).',
                {'found': f'{int(ttfb)}ms', 'expected': f'under {cfg["ttfb_warn_ms"]}ms'}))

        cache = (page.get('cache_control') or '').lower()
        if cache and any(token in cache for token in GENERIC_CACHE):
            uncacheable.append(_item(
                page['url'], cache[:80],
                f'This content page is served uncacheable: Cache-Control: {cache}.',
                {'found': cache[:200], 'expected': 'a positive max-age for content pages'}))

        size = page.get('content_length') or page.get('size')
        if isinstance(size, int) and size > cfg['page_weight_warn_bytes']:
            heavy_pages.append(_item(
                page['url'], str(size),
                f'The page weighs {size // 1024} KB.',
                {'found': f'{size // 1024} KB',
                 'expected': f'under {cfg["page_weight_warn_bytes"] // 1024} KB'}))

    emit('slow_ttfb', 'warning', 'Slow Time to First Byte', slow_ttfb,
         '{count} pages are slow to send their first byte.',
         'Cache the response or speed up the server; nothing renders before the first byte.')
    emit('uncacheable_content', 'info', 'Content Page Cannot Be Cached', uncacheable,
         '{count} content pages forbid caching.',
         'Allow caching for pages that do not change per visitor.')
    emit('page_too_heavy', 'warning', 'Heavy Page', heavy_pages,
         '{count} pages are heavier than the budget.',
         'Trim the payload: images, embedded fonts and scripts dominate page weight.')


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _item(url, offending, details, evidence=None, category=CATEGORY):
    return {'url': url, 'offending': str(offending or ''), 'details': details,
            'evidence': evidence or {}, 'category': category}


def _key(url, cfg):
    """Compare URLs the way a browser would: no tracking noise, one slash."""
    if not url:
        return ''
    cleaned = strip_tracking_params(url.strip(), cfg.get('tracking_params')
                                    or DEFAULT_TRACKING_PARAMS)
    parts = urlsplit(cleaned)
    host = parts.netloc.lower()
    if host.startswith('www.'):
        host = host[4:]
    path = parts.path or '/'
    if len(path) > 1 and path.endswith('/'):
        path = path[:-1]
    return urlunsplit(('', host, path, parts.query, ''))


def _host(url):
    host = urlsplit(url or '').netloc.lower()
    return host[4:] if host.startswith('www.') else host


def _collapse(value):
    return ' '.join(value.split()) if isinstance(value, str) else ''


def _is_html(page):
    content_type = (page.get('content_type') or '').lower()
    return 'html' in content_type or (not content_type and page.get('title') is not None)


def _is_noindex(page):
    return 'noindex' in (page.get('robots') or '').lower()


def _redirects(page):
    status = page.get('status_code')
    return bool(page.get('redirected_to')) or (isinstance(status, int) and 300 <= status < 400)
