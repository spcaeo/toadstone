"""Rendered-mode checks: the defects only a real browser can see.

HTML-only crawling reads the bytes the server sent. It cannot tell that a
16px icon is painted 160px wide, that the "heading" is a `<div>`, that a CSS
rule hid the menu, that a paragraph is in the markup but invisible, that
JavaScript rewrote the structured data, or that the page scrolls sideways on
a phone. Each of those shipped to production on a real site; this module is
what catches them next time.

Two halves, deliberately split:

    collect(page, config)      one browser round trip per area, returns plain
                               observations - no issues, no opinions, so the
                               data can be stored and re-checked later
    check(observations, url)   pure logic over those observations, no browser
                               needed, so every threshold is testable offline

`check_at_widths` runs the pair at 1440 / 768 / 390 px and merges the result,
naming the widths a finding came from: "the nav is invisible at 390px" is a
different bug from "the nav is invisible".

Nothing here imports Playwright at module level. The module imports, and
`check` runs, on a machine with no browser installed; only `collect` needs a
live page, and it is handed one rather than opening it (see PAGE_FACTORY_DOC).

Console errors cannot be collected after the fact: the listener must be
attached BEFORE navigation. Call `attach_console_listener(page)` first, then
navigate, then `collect` - see that function's docstring.
"""

import json
import re
import time
from collections import Counter
from html import unescape

from src.utils.issue_factory import make_issue

CATEGORY = 'Rendered'

# The device widths section 9 asks for: desktop, tablet, phone.
DEFAULT_WIDTHS = (1440, 768, 390)

# Viewport sizes the wiring code can hand to Playwright for those widths.
VIEWPORTS = {1440: (1440, 900), 768: (768, 1024), 390: (390, 844)}

PAGE_FACTORY_DOC = """A `page_factory` is an async callable taking one width.

It returns - or awaits to - either a Playwright page already navigated to the
URL with its viewport set to that width, or an async context manager yielding
one (use that form when the page must be released afterwards, which is what
JavaScriptRenderer's page pool wants):

    async def factory(width):
        page = await renderer.get_page()
        await page.set_viewport_size({'width': width, 'height': ...})
        errors = attach_console_listener(page)
        await page.goto(url, wait_until='domcontentloaded')
        return page
"""

DEFAULTS = {
    # --- images -----------------------------------------------------------
    # rendered / source-pixel ratio before an image counts as blown up, and
    # the ratio at which it stops being a warning. 2.0 catches the framework
    # image component that paints a 24px icon at 240px.
    'image_oversize_ratio': 2.0,
    'image_oversize_error_ratio': 4.0,
    # images smaller than this on screen are spacers, trackers and bullets
    'image_min_rendered_px': 8,
    # extensions whose intrinsic size means nothing (vectors scale losslessly)
    'image_ignore_extensions': ('.svg',),
    'max_images': 400,

    # --- headings that are not headings ------------------------------------
    'fake_heading_min_font_px': 20.0,
    'fake_heading_min_weight': 600,
    # a font this large reads as a heading even at a normal weight
    'fake_heading_big_font_px': 26.0,
    'fake_heading_min_chars': 3,
    'fake_heading_max_chars': 140,

    # --- text hidden by CSS -------------------------------------------------
    # short hidden strings are labels, tooltips and skip links; this is the
    # length at which hidden text is worth a developer's attention
    'min_hidden_chars': 120,
    'max_hidden_findings': 10,

    # --- text in the render but not in the HTML ----------------------------
    'min_injected_chars': 60,
    'max_injected_findings': 5,

    # --- console ------------------------------------------------------------
    'max_console_errors': 5,
    'console_ignore_patterns': (),

    # --- layout -------------------------------------------------------------
    # a pixel or two of overflow is rounding, not a broken layout
    'overflow_tolerance_px': 4,
    'max_overflow_elements': 5,

    # --- walk limits (a 500-page crawl cannot afford an unbounded DOM walk) --
    'max_elements': 6000,
    'max_text_chunks': 250,
    'max_snippet_chars': 200,

    # severity per check, overridable per site
    'severities': {
        'rendered_image_oversized': 'warning',
        'rendered_fake_heading': 'warning',
        'rendered_chrome_hidden': 'warning',
        'rendered_hidden_text': 'warning',
        'rendered_text_js_injected': 'info',
        'rendered_jsonld_injected': 'info',
        'rendered_jsonld_removed': 'warning',
        'rendered_console_error': 'warning',
        'rendered_layout_overflow': 'warning',
    },
}

# Checks whose answer depends on the viewport. Their issue id carries the
# widths, so "nav hidden at 390px" and "nav hidden at 1440px" stay distinct
# defects; every other check keeps one id whatever widths were run.
WIDTH_SPECIFIC = frozenset({
    'rendered_image_oversized',
    'rendered_chrome_hidden',
    'rendered_hidden_text',
    'rendered_layout_overflow',
})

CHROME_TAGS = ('header', 'nav', 'footer')


def _config(overrides=None):
    cfg = dict(DEFAULTS)
    cfg['severities'] = dict(DEFAULTS['severities'])
    for key, value in (overrides or {}).items():
        if key == 'severities' and isinstance(value, dict):
            cfg['severities'].update(value)
        elif key in cfg and value is not None:
            cfg[key] = value
        elif key not in cfg:
            cfg[key] = value  # pass-through keys such as raw_html
    return cfg


# ---------------------------------------------------------------------------
# Browser side: one evaluate per area, never one per element
# ---------------------------------------------------------------------------

# Shared helpers, inlined into every script (page.evaluate has no shared scope).
_HELPERS = """
  const lcSelector = (el) => {
    if (!el || el.nodeType !== 1) return '';
    const parts = [];
    let node = el, depth = 0;
    while (node && node.nodeType === 1 && depth < 4) {
      let part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift(part + '#' + node.id); node = null; break; }
      const parent = node.parentElement;
      if (parent) {
        const same = Array.prototype.filter.call(
          parent.children, (c) => c.tagName === node.tagName);
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      node = node.parentElement;
      depth += 1;
    }
    return parts.join(' > ');
  };
  const lcText = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  const lcHiddenReason = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none') return 'display:none';
    if (cs.visibility === 'hidden' || cs.visibility === 'collapse') return 'visibility:' + cs.visibility;
    if (parseFloat(cs.opacity || '1') < 0.05) {
      // Text at opacity:0 that carries a transition or animation on opacity is
      // waiting to appear (scroll-in effects, carousel slides, tab panels) -
      // it is not hidden content, so mark it and let the check decide.
      const animates =
        (cs.transitionProperty || '').indexOf('opacity') >= 0 ||
        (cs.transitionProperty || '').indexOf('all') >= 0 ||
        (cs.animationName || 'none') !== 'none';
      let inSlider = false;
      let node = el, hops = 0;
      while (node && hops < 12) {
        const cls = ((node.className && node.className.baseVal !== undefined
                      ? node.className.baseVal : node.className) || '') + ' ' + (node.id || '');
        if (/carousel|swiper|slick|slider|tab-pane|tabpanel|accordion|marquee/i.test(cls)) {
          inSlider = true;
          break;
        }
        if (node.getAttribute && (node.getAttribute('role') === 'tabpanel'
            || node.hasAttribute('data-carousel'))) {
          inSlider = true;
          break;
        }
        node = node.parentElement;
        hops += 1;
      }
      return (animates || inSlider ? 'pending:' : '') + 'opacity:' + cs.opacity;
    }
    if (parseFloat(cs.fontSize || '16') <= 0) return 'font-size:0';
    const indent = parseFloat(cs.textIndent || '0');
    if (indent <= -1000) return 'text-indent:' + cs.textIndent;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return 'zero size';
    const left = r.left + window.scrollX, top = r.top + window.scrollY;
    if (left + r.width < -500 || top + r.height < -500) return 'positioned off-screen';
    return '';
  };
  const lcSkip = (tag) =>
    tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT' ||
    tag === 'TEMPLATE' || tag === 'SVG' || tag === 'IFRAME' || tag === 'CANVAS';
  // textContent of a hidden container also returns the source of any
  // <script> inside it, which is how a JSON-LD block came to be reported as
  // hidden prose. Walk the subtree instead and take only what a reader could
  // have read.
  const lcProseText = (el) => {
    let out = '';
    const walk = [el];
    let seen = 0;
    while (walk.length && seen < 400) {
      const node = walk.pop();
      seen += 1;
      for (const child of node.childNodes) {
        if (child.nodeType === 3) out += ' ' + child.nodeValue;
        else if (child.nodeType === 1 && !lcSkip(child.tagName)) walk.push(child);
      }
    }
    return lcText(out);
  };
  // Collapsed disclosure widgets and closed menus are how a page is meant to
  // work, not content kept from a reader.
  const lcInCollapsed = (el) =>
    !!(el.closest && (el.closest('details:not([open])') ||
                      el.closest('[aria-expanded="false"]') ||
                      el.closest('[aria-hidden="true"]')));
  const lcInChrome = (el) =>
    !!(el.closest && el.closest('header,nav,footer,[role="banner"],[role="navigation"],[role="contentinfo"]'));
"""


def _script(body):
    return '(opts) => {' + _HELPERS + body + '}'


_IMAGES_JS = _script("""
  const out = [];
  const imgs = document.querySelectorAll('img');
  const fold = window.innerHeight;
  for (let i = 0; i < imgs.length && out.length < opts.maxImages; i += 1) {
    const img = imgs[i];
    const r = img.getBoundingClientRect();
    if (r.width < opts.minRenderedPx && r.height < opts.minRenderedPx) continue;
    const top = r.top + window.scrollY;
    out.push({
      selector: lcSelector(img),
      src: img.currentSrc || img.getAttribute('src') || '',
      alt: img.getAttribute('alt'),
      natural_width: img.naturalWidth || 0,
      natural_height: img.naturalHeight || 0,
      rendered_width: Math.round(r.width * 10) / 10,
      rendered_height: Math.round(r.height * 10) / 10,
      width_attr: img.getAttribute('width'),
      height_attr: img.getAttribute('height'),
      loading: img.getAttribute('loading'),
      fetchpriority: img.getAttribute('fetchpriority'),
      top_px: Math.round(top),
      above_fold: top < fold && (top + r.height) > 0
    });
  }
  return out;
""")

_HEADINGS_JS = _script("""
  const out = [];
  const stack = [document.body];
  let seen = 0;
  const skipInside = 'h1,h2,h3,h4,h5,h6,nav,header,footer,button,select,table,form,label,figcaption,[role="heading"]';
  while (stack.length && seen < opts.maxElements) {
    const el = stack.pop();
    if (!el) continue;
    seen += 1;
    const tag = el.tagName;
    if (lcSkip(tag)) continue;
    for (let i = el.children.length - 1; i >= 0; i -= 1) stack.push(el.children[i]);
    if (tag !== 'DIV' && tag !== 'P' && tag !== 'SPAN') continue;
    const text = lcText(el.textContent);
    if (text.length < opts.minChars || text.length > opts.maxChars) continue;
    if (!/[A-Za-z\\u00C0-\\u024F]/.test(text)) continue;
    // leaf-ish only: an element whose text lives in a child is not the heading
    let childWithText = false;
    for (const child of el.children) { if (lcText(child.textContent)) { childWithText = true; break; } }
    if (childWithText) continue;
    if (el.closest(skipInside)) continue;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const size = parseFloat(cs.fontSize || '0');
    let weight = parseInt(cs.fontWeight, 10);
    if (isNaN(weight)) weight = (cs.fontWeight === 'bold' || cs.fontWeight === 'bolder') ? 700 : 400;
    if (size < opts.minFont) continue;
    if (weight < opts.minWeight && size < opts.bigFont) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    out.push({
      selector: lcSelector(el),
      tag: tag.toLowerCase(),
      text: text.slice(0, opts.maxSnippet),
      font_size: size,
      font_weight: weight,
      display: cs.display
    });
    if (out.length >= 25) break;
  }
  return out;
""")

_VISIBILITY_JS = _script("""
  const hidden = [], chunks = [];
  const stack = [document.body];
  let seen = 0;
  while (stack.length && seen < opts.maxElements) {
    const el = stack.pop();
    if (!el) continue;
    seen += 1;
    const tag = el.tagName;
    if (lcSkip(tag)) continue;
    const reason = lcHiddenReason(el);
    if (reason) {
      const text = lcProseText(el);
      if (text.length >= opts.minHiddenChars && hidden.length < opts.maxHidden
          && !lcInCollapsed(el)) {
        hidden.push({
          selector: lcSelector(el),
          tag: tag.toLowerCase(),
          reason: reason,
          chars: text.length,
          text: text.slice(0, opts.maxSnippet),
          is_chrome: tag === 'HEADER' || tag === 'NAV' || tag === 'FOOTER' || lcInChrome(el)
        });
      }
      continue;  // everything below an invisible element is invisible too
    }
    for (const node of el.childNodes) {
      if (node.nodeType === 3) {
        const t = lcText(node.nodeValue);
        if (t.length >= opts.minChunkChars && chunks.length < opts.maxChunks) {
          chunks.push(t.slice(0, opts.maxChunk));
        }
      } else if (node.nodeType === 1) {
        stack.push(node);
      }
    }
  }
  return {hidden: hidden, chunks: chunks, elements_walked: seen};
""")

_CHROME_JS = _script("""
  const roles = {header: 'banner', nav: 'navigation', footer: 'contentinfo'};
  const out = {};
  for (const tag of opts.tags) {
    const nodes = Array.prototype.slice.call(
      document.querySelectorAll(tag + ',[role="' + roles[tag] + '"]'));
    const hidden = [];
    let visible = 0;
    for (const el of nodes) {
      const reason = lcHiddenReason(el);
      if (reason) hidden.push({selector: lcSelector(el), reason: reason});
      else visible += 1;
    }
    out[tag] = {count: nodes.length, visible: visible, hidden: hidden.slice(0, 5)};
  }
  return out;
""")

_JSONLD_JS = _script("""
  const out = [];
  const nodes = document.querySelectorAll('script[type="application/ld+json"]');
  for (const node of nodes) out.push((node.textContent || '').slice(0, 20000));
  return out;
""")

_LAYOUT_JS = _script("""
  const de = document.documentElement;
  const iw = window.innerWidth;
  const sw = Math.max(de.scrollWidth, document.body ? document.body.scrollWidth : 0);
  const offenders = [];
  if (sw > iw + opts.tolerance) {
    const stack = [document.body];
    let seen = 0;
    while (stack.length && seen < opts.maxElements && offenders.length < opts.maxOffenders) {
      const el = stack.pop();
      if (!el) continue;
      seen += 1;
      if (lcSkip(el.tagName)) continue;
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      const right = r.right + window.scrollX;
      if (right > iw + opts.tolerance || r.width > iw + opts.tolerance) {
        offenders.push({
          selector: lcSelector(el),
          width: Math.round(r.width),
          right: Math.round(right),
          text: lcText(el.textContent).slice(0, 80)
        });
        continue;  // report the outermost overflowing box, not its children
      }
      for (let i = el.children.length - 1; i >= 0; i -= 1) stack.push(el.children[i]);
    }
  }
  return {scroll_width: Math.round(sw), inner_width: iw,
          overflow: sw > iw + opts.tolerance, offenders: offenders};
""")


def attach_console_listener(page, config=None):
    """Start collecting console errors. Call this BEFORE `page.goto`.

    Console messages are events: a listener attached after navigation sees
    nothing, so `collect` cannot do this for you. Returns the list the
    listener fills; pass it back as `config['console_errors']`, or let
    `collect` find it - the list is also stashed on the page object.

        errors = attach_console_listener(page)
        await page.goto(url)
        obs = await collect(page, {'console_errors': errors})
    """
    cfg = _config(config)
    errors = []
    limit = max(int(cfg['max_console_errors']) * 4, 20)

    def on_console(message):
        try:
            if message.type == 'error' and len(errors) < limit:
                errors.append(str(message.text))
        except Exception:
            pass

    def on_page_error(error):
        try:
            if len(errors) < limit:
                errors.append(str(error))
        except Exception:
            pass

    page.on('console', on_console)
    page.on('pageerror', on_page_error)
    try:
        page._toadstone_console_errors = errors
    except Exception:
        pass  # Playwright may refuse the attribute; the returned list still works
    return errors


def _playwright_errors():
    """Playwright's exception types, or an empty tuple when it is not installed."""
    try:
        from playwright.async_api import Error as PlaywrightError
        return (PlaywrightError,)
    except Exception:
        return ()


async def _area(observations, name, coro):
    """Run one area, recording rather than raising when the browser says no."""
    try:
        return await coro
    except Exception as exc:
        observations.setdefault('errors', {})[name] = f'{type(exc).__name__}: {exc}'
        return None


async def collect(page, config=None):
    """Observe one already-navigated page. Returns plain data, never issues.

    `page` is a Playwright page that has finished navigating (and whatever
    settle wait the caller uses). Nothing here navigates, resizes or closes
    it: the caller owns the page's life, exactly as JavaScriptRenderer's pool
    does. Six `page.evaluate` calls, one per area - never one per element.

    `config['raw_html']` (the bytes the server sent) is optional; when given,
    the raw JSON-LD blocks and the text-not-in-the-HTML diff are computed here
    too, so the stored observations can be re-checked without the HTML.
    """
    cfg = _config(config)
    started = time.monotonic()
    obs = {
        'url': '', 'width': 0, 'height': 0,
        'images': [], 'fake_headings': [], 'chrome': {},
        'hidden_text': [], 'visible_text_chunks': [],
        'jsonld': [], 'console_errors': [], 'layout': {},
    }

    try:
        obs['url'] = page.url
    except Exception:
        pass
    try:
        viewport = page.viewport_size or {}
        obs['width'] = int(viewport.get('width') or 0)
        obs['height'] = int(viewport.get('height') or 0)
    except Exception:
        pass

    images = await _area(obs, 'images', page.evaluate(_IMAGES_JS, {
        'maxImages': cfg['max_images'],
        'minRenderedPx': cfg['image_min_rendered_px'],
    }))
    obs['images'] = images or []

    headings = await _area(obs, 'fake_headings', page.evaluate(_HEADINGS_JS, {
        'maxElements': cfg['max_elements'],
        'minFont': cfg['fake_heading_min_font_px'],
        'minWeight': cfg['fake_heading_min_weight'],
        'bigFont': cfg['fake_heading_big_font_px'],
        'minChars': cfg['fake_heading_min_chars'],
        'maxChars': cfg['fake_heading_max_chars'],
        'maxSnippet': cfg['max_snippet_chars'],
    }))
    obs['fake_headings'] = headings or []

    visibility = await _area(obs, 'visibility', page.evaluate(_VISIBILITY_JS, {
        'maxElements': cfg['max_elements'],
        'minHiddenChars': cfg['min_hidden_chars'],
        'maxHidden': cfg['max_hidden_findings'],
        'minChunkChars': cfg['min_injected_chars'],
        'maxChunks': cfg['max_text_chunks'],
        'maxChunk': cfg['max_snippet_chars'],
        'maxSnippet': cfg['max_snippet_chars'],
    })) or {}
    obs['hidden_text'] = visibility.get('hidden', [])
    obs['visible_text_chunks'] = visibility.get('chunks', [])
    obs['elements_walked'] = visibility.get('elements_walked', 0)

    chrome = await _area(obs, 'chrome', page.evaluate(_CHROME_JS, {'tags': list(CHROME_TAGS)}))
    obs['chrome'] = chrome or {}

    jsonld = await _area(obs, 'jsonld', page.evaluate(_JSONLD_JS, {}))
    obs['jsonld'] = jsonld or []

    layout = await _area(obs, 'layout', page.evaluate(_LAYOUT_JS, {
        'tolerance': cfg['overflow_tolerance_px'],
        'maxElements': cfg['max_elements'],
        'maxOffenders': cfg['max_overflow_elements'],
    }))
    obs['layout'] = layout or {}

    obs['console_errors'] = list(
        cfg.get('console_errors')
        or getattr(page, '_toadstone_console_errors', None)
        or [])

    # The renderer attaches the pre-render body to the page, because the
    # observe hook runs inside the navigation and this config was built
    # before it. Falling back to cfg keeps direct callers working.
    raw_html = cfg.get('raw_html') or getattr(page, '_toadstone_raw_html', '')
    if raw_html:
        obs['raw_jsonld'] = raw_jsonld_blocks(raw_html)
        obs['js_injected_text'] = text_missing_from_html(
            obs['visible_text_chunks'], raw_html, cfg)

    # Accessibility rides along with the render rather than paying for a
    # second one. Off unless asked for: axe costs 100-300ms a page.
    axe_config = cfg.get('accessibility')
    if axe_config and axe_config.get('enabled'):
        from src.core import accessibility
        obs['accessibility'] = await accessibility.collect(page, axe_config)

    obs['collect_ms'] = round((time.monotonic() - started) * 1000, 1)
    return obs


async def collect_at_widths(page_factory, widths=DEFAULT_WIDTHS, config=None):
    """Observe the same URL at each width. See PAGE_FACTORY_DOC for the factory."""
    observations = []
    for width in widths:
        handle = page_factory(width)
        if hasattr(handle, '__await__'):
            handle = await handle
        if hasattr(handle, '__aenter__'):
            async with handle as page:
                obs = await collect(page, config)
        else:
            obs = await collect(handle, config)
        if not obs.get('width'):
            obs['width'] = width
        observations.append(obs)
    return observations


async def check_at_widths(page_factory, url, widths=DEFAULT_WIDTHS, config=None):
    """Collect at each device width and return one merged list of issues.

    A finding seen at several widths becomes one issue naming them all; a
    finding seen only at 390px says so. See PAGE_FACTORY_DOC for `page_factory`.
    """
    observations = await collect_at_widths(page_factory, widths, config)
    return merge_checks(observations, url, config)


# ---------------------------------------------------------------------------
# Pure side: observations in, issues out. No browser, no network.
# ---------------------------------------------------------------------------

_JSONLD_RE = re.compile(
    r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r'(?s)<[^>]+>')
_BLOCK_RE = re.compile(r'(?is)<(script|style|template|noscript)\b.*?</\1\s*>')
_COMMENT_RE = re.compile(r'(?s)<!--.*?-->')


def raw_jsonld_blocks(raw_html):
    """The JSON-LD blocks in the HTML as it came off the wire."""
    return [match.group(1) for match in _JSONLD_RE.finditer(raw_html or '')]


def _normalize_jsonld(block):
    """Compare JSON-LD by meaning, not by whitespace."""
    text = (block or '').strip()
    try:
        return json.dumps(json.loads(text), sort_keys=True, separators=(',', ':'))
    except Exception:
        return re.sub(r'\s+', ' ', text)


def _normalize_text(text):
    return re.sub(r'\s+', ' ', unescape(text or '')).strip().lower()


def _html_to_text(raw_html):
    stripped = _BLOCK_RE.sub(' ', raw_html or '')
    stripped = _COMMENT_RE.sub(' ', stripped)
    return _normalize_text(_TAG_RE.sub(' ', stripped))


def text_missing_from_html(chunks, raw_html, config=None):
    """Visible text that is not in the served HTML - i.e. written by JavaScript."""
    cfg = _config(config)
    if not raw_html or not chunks:
        return []
    haystack = _html_to_text(raw_html)
    out, seen = [], set()
    for chunk in chunks:
        needle = _normalize_text(chunk)
        if len(needle) < cfg['min_injected_chars'] or needle in seen:
            continue
        if needle not in haystack:
            seen.add(needle)
            out.append(chunk[:cfg['max_snippet_chars']])
        if len(out) >= cfg['max_injected_findings']:
            break
    return out


def _finding(check, issue, details, base, evidence, how_to_fix, width_specific=False):
    return {'check': check, 'issue': issue, 'details': details, 'base': base,
            'evidence': evidence, 'how_to_fix': how_to_fix,
            'width_specific': width_specific}


def _image_findings(obs, cfg):
    ratio_limit = float(cfg['image_oversize_ratio'])
    ignore = tuple(cfg['image_ignore_extensions'])
    out = []
    for img in obs.get('images') or []:
        src = (img.get('src') or '')
        path = src.split('?')[0].split('#')[0].lower()
        if path.endswith(ignore):
            continue
        rendered = float(img.get('rendered_width') or 0)
        natural = float(img.get('natural_width') or 0)
        try:
            attr = float(str(img.get('width_attr') or '').strip().rstrip('px') or 0)
        except ValueError:
            attr = 0.0
        if rendered <= 0:
            continue
        # The intended size is whichever the author stated: the source pixels
        # the image actually has, or the width attribute they declared.
        candidates = [(natural, 'source is %dpx wide' % natural, 'natural size'),
                      (attr, 'width="%d"' % attr, 'width attribute')]
        worst = None
        for value, expected, label in candidates:
            if value <= 0:
                continue
            ratio = rendered / value
            if ratio >= ratio_limit and (worst is None or ratio > worst[0]):
                worst = (ratio, expected, label)
        if not worst:
            continue
        ratio, expected, label = worst
        out.append(_finding(
            'rendered_image_oversized',
            'Image Rendered Larger Than Its Source',
            'Image is painted %.0fpx wide but its %s (%.1fx upscale), so it '
            'renders blurred' % (rendered, label, ratio),
            img.get('selector') or src,
            {'selector': img.get('selector'), 'snippet': src,
             'expected': expected, 'found': 'rendered %.0f x %.0fpx' % (
                 rendered, float(img.get('rendered_height') or 0))},
            'Serve the image at the size it is displayed, or cap its CSS width '
            'to the source width.',
            width_specific=True))
        out[-1]['ratio'] = ratio
    return out


def _heading_findings(obs, cfg):
    out = []
    for item in obs.get('fake_headings') or []:
        # thresholds are re-applied here, not only in the browser, so stored
        # observations can be re-checked against a different configuration
        size = float(item.get('font_size') or 0)
        weight = int(item.get('font_weight') or 0)
        text = item.get('text') or ''
        if size < float(cfg['fake_heading_min_font_px']):
            continue
        if weight < int(cfg['fake_heading_min_weight']) and \
                size < float(cfg['fake_heading_big_font_px']):
            continue
        if not (cfg['fake_heading_min_chars'] <= len(text) <= cfg['fake_heading_max_chars']):
            continue
        out.append(_finding(
            'rendered_fake_heading',
            'Heading Styled But Not Marked Up',
            'A <%s> is styled like a heading (%.0fpx, weight %s) but carries no '
            'heading semantics' % (item.get('tag', 'div'),
                                   float(item.get('font_size') or 0),
                                   item.get('font_weight')),
            item.get('selector') or item.get('text', ''),
            {'selector': item.get('selector'), 'snippet': item.get('text'),
             'expected': 'an <h2>-<h6> element',
             'found': '<%s> at %.0fpx/%s' % (item.get('tag', 'div'),
                                             float(item.get('font_size') or 0),
                                             item.get('font_weight'))},
            'Replace the element with the heading level it looks like, or drop '
            'the heading styling.'))
    return out


def _chrome_findings(obs, cfg):
    out = []
    chrome = obs.get('chrome') or {}
    for tag in CHROME_TAGS:
        info = chrome.get(tag) or {}
        count = int(info.get('count') or 0)
        if count and not int(info.get('visible') or 0):
            hidden = (info.get('hidden') or [{}])[0]
            out.append(_finding(
                'rendered_chrome_hidden',
                'Page Chrome Not Visible',
                'The page has %d <%s> element%s in the DOM but none of them are '
                'visible (%s)' % (count, tag, '' if count == 1 else 's',
                                  hidden.get('reason') or 'not rendered'),
                tag,
                {'selector': hidden.get('selector') or tag,
                 'expected': 'a visible <%s>' % tag,
                 'found': '%d hidden <%s>, 0 visible' % (count, tag)},
                'Remove the CSS rule hiding the %s, or the layout the rule '
                'belongs to.' % tag,
                width_specific=True))
    return out


def _hidden_text_findings(obs, cfg):
    """Text in the markup that a reader never sees - reported once per page.

    Two things are deliberately NOT reported. Text waiting on a transition or
    animation (scroll-in effects) appears as soon as the reader reaches it, and
    a carousel slide or tab panel is hidden by design; flagging either buried
    the real findings under a hundred rows of a site's own animation.
    """
    blocks = []
    for item in (obs.get('hidden_text') or []):
        if item.get('is_chrome'):
            continue  # the chrome check already reports this element
        if int(item.get('chars') or 0) < int(cfg['min_hidden_chars']):
            continue
        if str(item.get('reason') or '').startswith('pending:'):
            continue
        blocks.append(item)

    if not blocks:
        return []

    total = sum(int(block.get('chars') or 0) for block in blocks)
    reasons = sorted({str(block.get('reason')) for block in blocks})
    examples = blocks[:cfg['max_hidden_findings']]
    where = '; '.join(block.get('selector') or '?' for block in examples)
    return [_finding(
        'rendered_hidden_text',
        'Text Hidden By CSS',
        '%d block%s of text (%d characters) are in the HTML but invisible on '
        'screen (%s)' % (len(blocks), '' if len(blocks) == 1 else 's', total,
                         ', '.join(reasons)),
        ','.join(sorted(block.get('selector') or '' for block in blocks))[:200],
        {'selector': where[:300],
         'snippet': (examples[0].get('text') if examples else '') or '',
         'found': '%d blocks, %d characters' % (len(blocks), total)},
        'Delete the text, or remove the rule that hides it, so the markup '
        'matches the page.',
        width_specific=True)]


def _injected_text_findings(obs, cfg):
    chunks = obs.get('js_injected_text')
    if chunks is None:
        chunks = text_missing_from_html(
            obs.get('visible_text_chunks') or [], cfg.get('raw_html'), cfg)
    out = []
    for chunk in (chunks or [])[:cfg['max_injected_findings']]:
        if len(_normalize_text(chunk)) < int(cfg['min_injected_chars']):
            continue
        out.append(_finding(
            'rendered_text_js_injected',
            'Visible Text Missing From The HTML',
            'Text is on screen after rendering but absent from the served '
            'HTML, so an HTML-only crawler never sees it',
            _normalize_text(chunk)[:120],
            {'snippet': chunk, 'found': 'present only after JavaScript ran'},
            'Render this copy server-side so it is in the HTML response.'))
    return out


def _jsonld_findings(obs, cfg):
    raw_blocks = obs.get('raw_jsonld')
    if raw_blocks is None:
        raw_html = cfg.get('raw_html')
        if raw_html is None:
            return []
        raw_blocks = raw_jsonld_blocks(raw_html)
    rendered = Counter(_normalize_jsonld(b) for b in (obs.get('jsonld') or []))
    served = Counter(_normalize_jsonld(b) for b in (raw_blocks or []))
    out = []
    for block, _count in (rendered - served).items():
        out.append(_finding(
            'rendered_jsonld_injected',
            'Structured Data Added By JavaScript',
            'A JSON-LD block exists only after rendering; it is not in the '
            'served HTML',
            block[:200],
            {'snippet': block[:cfg['max_snippet_chars']],
             'found': 'present after rendering, absent from the HTML'},
            'Emit this JSON-LD in the server response so every parser sees it.'))
    for block, _count in (served - rendered).items():
        out.append(_finding(
            'rendered_jsonld_removed',
            'Structured Data Removed By JavaScript',
            'A JSON-LD block in the served HTML is gone after rendering',
            block[:200],
            {'snippet': block[:cfg['max_snippet_chars']],
             'expected': 'the block still present after rendering',
             'found': 'removed while the page loaded'},
            'Stop the script that rewrites the page from discarding the '
            'JSON-LD block.'))
    return out


def _console_findings(obs, cfg):
    patterns = [re.compile(p) for p in (cfg['console_ignore_patterns'] or ())]
    out, seen = [], set()
    for message in obs.get('console_errors') or []:
        text = re.sub(r'\s+', ' ', str(message)).strip()
        if not text or text in seen:
            continue
        if any(p.search(text) for p in patterns):
            continue
        seen.add(text)
        out.append(_finding(
            'rendered_console_error',
            'JavaScript Console Error',
            'The browser logged an error while loading the page',
            text[:200],
            {'snippet': text[:cfg['max_snippet_chars']],
             'found': text[:120]},
            'Fix the script that throws, or remove it from the page.'))
        if len(out) >= cfg['max_console_errors']:
            break
    return out


def _layout_findings(obs, cfg):
    layout = obs.get('layout') or {}
    scroll_width = float(layout.get('scroll_width') or 0)
    inner_width = float(layout.get('inner_width') or 0)
    if not inner_width or scroll_width <= inner_width + float(cfg['overflow_tolerance_px']):
        return []
    offenders = layout.get('offenders') or []
    widest = offenders[0] if offenders else {}
    related = [o.get('selector') for o in offenders[1:] if o.get('selector')]
    return [_finding(
        'rendered_layout_overflow',
        'Horizontal Overflow',
        'The page is %spx wide in a %spx viewport, so it scrolls sideways' % (
            layout.get('scroll_width'), layout.get('inner_width')),
        widest.get('selector') or 'document',
        {'selector': widest.get('selector'),
         'snippet': widest.get('text') or '',
         'expected': 'content within %spx' % layout.get('inner_width'),
         'found': 'scrollWidth %spx (widest box %spx)' % (
             layout.get('scroll_width'), widest.get('width') or '?'),
         'related_urls': related},
        'Constrain the overflowing element with max-width:100% or overflow-x '
        'handling at this breakpoint.',
        width_specific=True)]


def findings(observations, config=None):
    """Every finding for one set of observations, before it becomes an issue.

    Exposed so the merge across widths can group the same defect seen at
    several viewports into one issue.
    """
    cfg = _config(config)
    out = []
    for builder in (_image_findings, _heading_findings, _chrome_findings,
                    _hidden_text_findings, _injected_text_findings,
                    _jsonld_findings, _console_findings, _layout_findings):
        try:
            out.extend(builder(observations, cfg))
        except Exception as exc:  # one broken area must not lose the others
            out.append(_finding(
                'rendered_check_failed', 'Rendered Check Failed',
                '%s could not run: %s: %s' % (
                    builder.__name__.strip('_'), type(exc).__name__, exc),
                builder.__name__, {}, 'Report this as a Toadstone bug.'))
    return out


def _width_tag(widths):
    if not widths:
        return ''
    return ' (at %s)' % '/'.join('%dpx' % w for w in widths)


def merge_checks(observations_list, url, config=None):
    """Issues for the same URL observed at one or more widths.

    The same defect at several widths collapses into one issue naming every
    width it was seen at; width-dependent checks keep the widths in their id,
    so "nav hidden at 390px" never masquerades as the desktop finding.
    """
    cfg = _config(config)
    merged = {}
    order = []
    for obs in observations_list or []:
        width = int(obs.get('width') or 0)
        for item in findings(obs, cfg):
            key = (item['check'], item['base'])
            if key not in merged:
                merged[key] = {'finding': item, 'widths': []}
                order.append(key)
            if width and width not in merged[key]['widths']:
                merged[key]['widths'].append(width)
            # keep the worst upscale when an image is worse at another width
            if item.get('ratio', 0) > merged[key]['finding'].get('ratio', 0):
                kept_widths = merged[key]['widths']
                merged[key] = {'finding': item, 'widths': kept_widths}

    issues = []
    for key in order:
        item = merged[key]['finding']
        widths = merged[key]['widths']
        check = item['check']
        severity = cfg['severities'].get(check, 'warning')
        if check == 'rendered_image_oversized' and \
                item.get('ratio', 0) >= float(cfg['image_oversize_error_ratio']):
            severity = 'error'
        offending = item['base']
        if check in WIDTH_SPECIFIC and widths:
            offending = '%s@%s' % (offending, ','.join(str(w) for w in sorted(widths)))
        issues.append(make_issue(
            url=url,
            check=check,
            severity=severity,
            category=CATEGORY,
            issue=item['issue'],
            details=item['details'] + _width_tag(widths),
            offending_value=offending,
            evidence=item['evidence'],
            how_to_fix=item['how_to_fix'],
        ))

    # Accessibility findings are already whole issues with their own slugs and
    # their own documentation links, so they are appended rather than merged.
    # One width is enough: axe judges the DOM, not the layout, and running it
    # at three widths would triple the cost to repeat the same answer.
    for obs in observations_list or []:
        if obs.get('accessibility'):
            from src.core import accessibility
            issues.extend(accessibility.check(obs, url, cfg.get('accessibility')))
            break
    return issues


def check(observations, url, config=None):
    """Issues for one page observed at one width."""
    return merge_checks([observations or {}], url, config)
