"""Deterministic tests for rendered-mode checks (section 9).

A fixture site is served from this process and driven with a real Chromium,
so every check is proved against a browser rather than against a mock:

    python tests/rendered_tests.py

The broken fixture page carries one of each defect the requester shipped:

  1. a 16px icon painted 160px wide by CSS
  2. a <div> styled like an H2
  3. a <nav> hidden by a CSS rule
  4. a paragraph in the HTML with display:none
  5. a JSON-LD block injected by JavaScript, and another removed by it
  6. a deliberate console.error
  7. a box wider than the viewport at 390px but not at 768px

The clean page is the same site done right and must report nothing at all.
The last two tests use no browser: `check` runs over a recorded observations
dict, which is the point of splitting collect from check.
"""
import asyncio
import http.server
import json
import os
import struct
import sys
import threading
import time
import urllib.request
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.rendered_checks import (  # noqa: E402
    DEFAULT_WIDTHS,
    VIEWPORTS,
    attach_console_listener,
    check,
    check_at_widths,
    collect_at_widths,
    merge_checks,
)

PORT = int(os.environ.get('RENDERED_FIXTURE_PORT', '8931'))
BASE = f'http://127.0.0.1:{PORT}'

PASSED, FAILED = [], []


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


# --- fixture site ----------------------------------------------------------

def png_bytes(width, height):
    """A real PNG, so naturalWidth/naturalHeight in the browser are real."""
    rows = b''.join(b'\x00' + b'\x40\x80\xc0\xff' * width for _ in range(height))

    def chunk(tag, data):
        body = tag + data
        return (struct.pack('>I', len(data)) + body
                + struct.pack('>I', zlib.crc32(body) & 0xffffffff))

    header = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


DOOMED_JSONLD = '{"@context":"https://schema.org","@type":"Organization","name":"Doomed Inc"}'
STATIC_JSONLD = '{"@context":"https://schema.org","@type":"Organization","name":"Space-O"}'
INJECTED_TEXT = ('This sentence was written by JavaScript after the HTML was '
                 'served, so an HTML-only crawler never sees it at all.')
HIDDEN_TEXT = ('This paragraph is in the HTML but a CSS rule keeps it off the '
               'screen entirely, which is exactly the defect rendered mode '
               'exists to catch on a real site.')

ANIMATED_TEXT = ('This paragraph fades in as the reader scrolls to it, so it is '
                 'not hidden content and must not be reported as such.')

CAROUSEL_TEXT = ('This slide is the second one in a carousel and is hidden by '
                 'design until the reader advances to it.')

BROKEN_HTML = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Broken fixture</title>
<script type="application/ld+json" id="doomed">{DOOMED_JSONLD}</script>
</head>
<body>
<header><p>Space-O fixture header</p></header>
<nav style="display:none"><a href="/clean">Clean</a> <a href="/broken">Broken</a></nav>
<main>
  <h1>Rendered mode fixture</h1>
  <div style="font-size:28px;font-weight:700">Our Services Look Like A Heading</div>
  <img src="/icon.png" width="16" height="16" style="width:160px;height:160px" alt="icon">
  <p style="display:none">{HIDDEN_TEXT}</p>
  <p style="opacity:0;transition:opacity .4s ease">{ANIMATED_TEXT}</p>
  <div class="swiper"><p style="opacity:0">{CAROUSEL_TEXT}</p></div>
  <p>Visible copy that is present in the served HTML and stays there after the
  page has finished rendering inside the browser.</p>
  <div id="wide" style="width:700px;height:24px;background:#eee">Wider than a phone</div>
  <div id="late"></div>
</main>
<footer><p>Footer text for the fixture site</p></footer>
<script>
console.error('boom: widget failed to start');
var doomed = document.getElementById('doomed');
if (doomed) doomed.remove();
var added = document.createElement('script');
added.type = 'application/ld+json';
added.textContent = JSON.stringify(
  {{"@context": "https://schema.org", "@type": "FAQPage", "name": "Injected by JavaScript"}});
document.head.appendChild(added);
document.getElementById('late').textContent = {json.dumps(INJECTED_TEXT)};
</script>
</body></html>
"""

# Three shapes that a real site is full of and that this check used to report
# as hidden content. They live on the CLEAN page on purpose: that page asserts
# it produces no issues at all, so any of them firing again fails the suite.
BURIED_JSONLD = ('{"@context":"https://schema.org","@type":"BreadcrumbList",'
                 '"itemListElement":[{"@type":"ListItem","position":1,"name":"Home",'
                 '"item":"https://site.test/"},{"@type":"ListItem","position":2,'
                 '"name":"Guides","item":"https://site.test/guides"}]}')
FAQ_ANSWER = ('Yes. Everything the product does is available over an API, so your '
              'own tools can drive it without a person clicking through the app '
              'each time, which is what most teams ask for first.')
MENU_TEXT = ('Getting started Installation Configuration Crawling a site Reading '
             'the report Exporting data Troubleshooting Release notes Support and '
             'contact details for the team behind the project')

CLEAN_HTML = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clean fixture</title>
<script type="application/ld+json">{STATIC_JSONLD}</script>
</head>
<body>
<header><p>Space-O fixture header</p></header>
<nav><a href="/clean">Clean</a> <a href="/broken">Broken</a>
  <div id="menu-docs" style="display:none">{MENU_TEXT}</div>
</nav>
<main>
  <h1>A clean page</h1>
  <h2>Everything here is what it claims to be</h2>
  <img src="/logo.png" width="64" height="64" style="width:64px;height:64px" alt="logo">
  <p>Copy that is in the HTML, visible on screen, and left alone by JavaScript
  once the page has finished loading in the browser.</p>
  <div style="display:none"><script type="application/ld+json">{BURIED_JSONLD}</script></div>
  <details><summary>Is there an API?</summary><p>{FAQ_ANSWER}</p></details>
</main>
<footer><p>Footer text for the fixture site</p></footer>
</body></html>
"""

ROUTES = {
    '/broken': ('text/html; charset=utf-8', BROKEN_HTML.encode('utf-8')),
    '/clean': ('text/html; charset=utf-8', CLEAN_HTML.encode('utf-8')),
    '/icon.png': ('image/png', png_bytes(16, 16)),
    '/logo.png': ('image/png', png_bytes(64, 64)),
    # served so Chromium's automatic request never becomes a console error
    '/favicon.ico': ('image/png', png_bytes(16, 16)),
}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_GET(self):
        ctype, body = ROUTES.get(self.path.split('?')[0], ('text/plain', b'not found'))
        code = 200 if self.path.split('?')[0] in ROUTES else 404
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve():
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def fetch(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.read().decode('utf-8')


# --- driving the browser ---------------------------------------------------

class PageAt:
    """An async context manager yielding a navigated page at one width.

    This is the page_factory form JavaScriptRenderer's pooled pages want: the
    page is handed over, then released, and the module never owns it.
    """

    def __init__(self, browser, url, width):
        self.browser, self.url, self.width = browser, url, width
        self.context = None

    async def __aenter__(self):
        w, h = VIEWPORTS.get(self.width, (self.width, 900))
        self.context = await self.browser.new_context(viewport={'width': w, 'height': h})
        page = await self.context.new_page()
        attach_console_listener(page)  # BEFORE navigation, or it sees nothing
        await page.goto(self.url, wait_until='domcontentloaded')
        await page.wait_for_timeout(250)  # let the page's own scripts run
        return page

    async def __aexit__(self, *exc):
        await self.context.close()
        return False


RUN = {}


async def drive():
    """One browser, one pass over both fixture pages. Fills RUN."""
    from playwright.async_api import async_playwright

    broken_url, clean_url = BASE + '/broken', BASE + '/clean'
    broken_config = {'raw_html': fetch(broken_url)}
    clean_config = {'raw_html': fetch(clean_url)}

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            args=['--no-sandbox', '--disable-dev-shm-usage'])
        try:
            # broken page: the context-manager factory form
            started = time.monotonic()
            RUN['observations'] = await collect_at_widths(
                lambda width: PageAt(browser, broken_url, width),
                DEFAULT_WIDTHS, broken_config)
            RUN['wall_ms'] = (time.monotonic() - started) * 1000
            RUN['issues'] = merge_checks(RUN['observations'], broken_url, broken_config)

            # clean page: the plain "return a page" factory form, end to end
            contexts = []

            async def factory(width):
                w, h = VIEWPORTS[width]
                context = await browser.new_context(viewport={'width': w, 'height': h})
                contexts.append(context)
                page = await context.new_page()
                attach_console_listener(page)
                await page.goto(clean_url, wait_until='domcontentloaded')
                await page.wait_for_timeout(250)
                return page

            RUN['clean_issues'] = await check_at_widths(
                factory, clean_url, DEFAULT_WIDTHS, clean_config)
            for context in contexts:
                await context.close()
        finally:
            await browser.close()


def issues_of(check_slug, issues=None):
    return [i for i in (RUN['issues'] if issues is None else issues)
            if i['check'] == check_slug]


def observation(width):
    for obs in RUN['observations']:
        if obs['width'] == width:
            return obs
    return {}


# --- tests: the browser ran once, these read what it saw -------------------

def test_oversized_image_is_reported():
    found = issues_of('rendered_image_oversized')
    result('one oversized image reported', len(found) == 1,
           f'got {[i["details"] for i in found]}')
    if not found:
        return
    issue = found[0]
    result('a 10x upscale is an error, not a warning', issue['type'] == 'error',
           issue['type'])
    result('evidence names the source size and the painted size',
           '16' in issue['evidence'].get('expected', '')
           and '160' in issue['evidence'].get('found', ''),
           str(issue['evidence']))
    result('evidence carries a selector and the image src',
           bool(issue['evidence'].get('selector'))
           and issue['evidence'].get('snippet', '').endswith('/icon.png'),
           str(issue['evidence']))


def test_fake_heading_is_reported():
    found = issues_of('rendered_fake_heading')
    texts = [i['evidence'].get('snippet', '') for i in found]
    result('the styled <div> is reported as a fake heading',
           any('Our Services Look Like A Heading' in t for t in texts), str(texts))
    result('real <h1>/<h2> text is not reported',
           not any('Rendered mode fixture' in t for t in texts), str(texts))
    if found:
        result('evidence states what it is and what it should be',
               'div' in found[0]['evidence'].get('found', '')
               and 'h2' in found[0]['evidence'].get('expected', ''),
               str(found[0]['evidence']))


def test_hidden_nav_is_reported_as_chrome():
    found = issues_of('rendered_chrome_hidden')
    result('the hidden <nav> is reported', len(found) == 1 and '<nav>' in found[0]['details'],
           str([i['details'] for i in found]))
    if not found:
        return
    result('the finding names every width it was seen at',
           all(f'{w}px' in found[0]['details'] for w in DEFAULT_WIDTHS),
           found[0]['details'])
    result('visible header and footer are not reported',
           not any(t in found[0]['details'] for t in ('<header>', '<footer>')),
           found[0]['details'])


def test_hidden_paragraph_is_reported():
    found = issues_of('rendered_hidden_text')
    result('the display:none paragraph is reported', len(found) == 1,
           str([i['details'] for i in found]))
    if not found:
        return
    result('evidence quotes the hidden copy and names the rule',
           HIDDEN_TEXT[:40] in found[0]['evidence'].get('snippet', '')
           and 'display:none' in found[0]['details'],
           str(found[0]['evidence'])[:200] + ' | ' + found[0]['details'][:120])
    result('the hidden nav is not double-reported as hidden text',
           'nav' not in (found[0]['evidence'].get('selector') or ''),
           str(found[0]['evidence'].get('selector')))


def test_text_written_by_javascript_is_reported():
    found = issues_of('rendered_text_js_injected')
    result('text absent from the served HTML is reported', len(found) == 1,
           str([i['evidence'].get('snippet', '')[:60] for i in found]))
    if found:
        result('it is the JavaScript-written sentence',
               INJECTED_TEXT[:40] in found[0]['evidence'].get('snippet', ''),
               found[0]['evidence'].get('snippet', '')[:80])


def test_jsonld_injected_and_removed_are_both_reported():
    injected = issues_of('rendered_jsonld_injected')
    removed = issues_of('rendered_jsonld_removed')
    result('the JavaScript-injected JSON-LD block is reported',
           len(injected) == 1 and 'FAQPage' in injected[0]['evidence'].get('snippet', ''),
           str([i['evidence'].get('snippet', '')[:60] for i in injected]))
    result('the JSON-LD block JavaScript deleted is reported',
           len(removed) == 1 and 'Doomed Inc' in removed[0]['evidence'].get('snippet', ''),
           str([i['evidence'].get('snippet', '')[:60] for i in removed]))
    result('removal is the more serious of the two',
           bool(removed) and bool(injected)
           and removed[0]['type'] == 'warning' and injected[0]['type'] == 'info',
           str([(i['check'], i['type']) for i in injected + removed]))


def test_console_error_is_reported():
    found = issues_of('rendered_console_error')
    result('the console.error is reported once, not once per width',
           len(found) == 1, str([i['evidence'].get('found', '') for i in found]))
    if found:
        result('the message text is kept verbatim',
               'boom: widget failed to start' in found[0]['evidence'].get('snippet', ''),
               found[0]['evidence'].get('snippet', ''))


def test_overflow_is_reported_at_the_width_it_happens():
    found = issues_of('rendered_layout_overflow')
    result('horizontal overflow is reported', len(found) == 1,
           str([i['details'] for i in found]))
    if not found:
        return
    issue = found[0]
    result('it names 390px and only 390px',
           '390px' in issue['details'] and '768px' not in issue['details']
           and '1440px' not in issue['details'], issue['details'])
    result('the widest box is identified',
           'wide' in (issue['evidence'].get('selector') or ''),
           str(issue['evidence'].get('selector')))
    result('the observations agree: overflow at 390 only',
           observation(390).get('layout', {}).get('overflow') is True
           and observation(768).get('layout', {}).get('overflow') is False
           and observation(1440).get('layout', {}).get('overflow') is False,
           str({w: observation(w).get('layout', {}).get('overflow')
                for w in DEFAULT_WIDTHS}))


def test_every_check_fired_exactly_once_and_nothing_else():
    counts = {}
    for issue in RUN['issues']:
        counts[issue['check']] = counts.get(issue['check'], 0) + 1
    expected = {
        'rendered_image_oversized', 'rendered_fake_heading',
        'rendered_chrome_hidden', 'rendered_hidden_text',
        'rendered_text_js_injected', 'rendered_jsonld_injected',
        'rendered_jsonld_removed', 'rendered_console_error',
        'rendered_layout_overflow',
    }
    result('all nine checks fired on the broken page',
           set(counts) == expected, str(sorted(counts.items())))
    result('no check fired more than once despite three widths',
           all(n == 1 for n in counts.values()), str(sorted(counts.items())))


def test_clean_page_reports_nothing():
    result('a correct page produces no issues at any width',
           RUN['clean_issues'] == [],
           str([(i['check'], i['details']) for i in RUN['clean_issues']]))


def test_observations_carry_what_the_checks_need():
    obs = observation(1440)
    image = (obs.get('images') or [{}])[0]
    result('per image: natural, rendered, attributes and above-the-fold',
           image.get('natural_width') == 16 and image.get('rendered_width') == 160
           and image.get('width_attr') == '16' and image.get('above_fold') is True,
           str(image))
    result('chrome presence and visibility are recorded per element',
           obs['chrome']['nav']['count'] == 1
           and obs['chrome']['nav']['visible'] == 0
           and obs['chrome']['nav']['hidden'] == [
               {'selector': 'html > body > nav', 'reason': 'display:none'}]
           and obs['chrome']['header']['visible'] == 1,
           str(obs.get('chrome')))
    result('the rendered JSON-LD blocks are recorded',
           len(obs.get('jsonld') or []) == 1
           and 'FAQPage' in obs['jsonld'][0], str(obs.get('jsonld')))
    result('collect returns observations, not issues',
           not any(key in obs for key in ('issues', 'check', 'type')), str(sorted(obs)))


def test_collect_is_cheap_enough_for_a_500_page_crawl():
    per_page = [obs.get('collect_ms', 0) for obs in RUN['observations']]
    worst = max(per_page) if per_page else 0
    print(f'    collect: {[round(ms) for ms in per_page]} ms per width, '
          f'{round(RUN["wall_ms"])} ms for all three including navigation')
    result('one collect stays well under a second', worst < 1000, f'{worst:.0f} ms')
    result('three widths including navigation stay under 15s',
           RUN['wall_ms'] < 15000, f'{RUN["wall_ms"]:.0f} ms')


# --- tests: no browser at all ----------------------------------------------

RECORDED = {
    'url': 'https://example.com/p', 'width': 390, 'height': 844,
    'images': [{'selector': 'main > img', 'src': '/i/icon.png',
                'natural_width': 24, 'natural_height': 24,
                'rendered_width': 240.0, 'rendered_height': 240.0,
                'width_attr': '24', 'height_attr': '24', 'above_fold': True}],
    'fake_headings': [{'selector': 'main > div:nth-of-type(2)', 'tag': 'div',
                       'text': 'Why choose us', 'font_size': 30.0,
                       'font_weight': 700, 'display': 'block'}],
    'chrome': {'header': {'count': 1, 'visible': 1, 'hidden': []},
               'nav': {'count': 1, 'visible': 0,
                       'hidden': [{'selector': 'nav', 'reason': 'display:none'}]},
               'footer': {'count': 1, 'visible': 1, 'hidden': []}},
    'hidden_text': [{'selector': 'main > p', 'tag': 'p', 'reason': 'visibility:hidden',
                     'chars': 300, 'text': 'x' * 200, 'is_chrome': False}],
    'visible_text_chunks': ['A sentence long enough to be worth comparing against '
                            'the served HTML document.'],
    'jsonld': ['{"@type":"FAQPage"}'],
    'raw_jsonld': ['{"@type":"Organization"}'],
    'console_errors': ['TypeError: x is not a function', 'TypeError: x is not a function'],
    'layout': {'scroll_width': 812, 'inner_width': 390, 'overflow': True,
               'offenders': [{'selector': 'main > div#hero', 'width': 812,
                              'right': 812, 'text': 'hero'}]},
}


def test_check_needs_no_browser():
    issues = check(RECORDED, 'https://example.com/p',
                   {'raw_html': '<html><body><p>Nothing in common here.</p></body></html>'})
    checks = sorted({i['check'] for i in issues})
    result('every check runs on a recorded observations dict',
           len(checks) == 9, str(checks))
    result('duplicate console messages collapse into one issue',
           len([i for i in issues if i['check'] == 'rendered_console_error']) == 1,
           str([i['evidence'].get('found') for i in issues
                if i['check'] == 'rendered_console_error']))
    result('the recorded width is named on width-dependent findings',
           all('(at 390px)' in i['details'] for i in issues
               if i['check'] in ('rendered_layout_overflow', 'rendered_chrome_hidden',
                                 'rendered_hidden_text', 'rendered_image_oversized')),
           str([i['details'] for i in issues][:2]))
    result('every issue carries the standard fields',
           all(set(i) >= {'id', 'url', 'check', 'type', 'category', 'issue',
                          'details', 'evidence', 'how_to_fix'}
               and i['category'] == 'Rendered' and i['type'] in ('error', 'warning', 'info')
               for i in issues), str(sorted(issues[0])) if issues else 'no issues')


def test_ids_are_stable_and_width_aware():
    url = 'https://example.com/p'
    first = check(RECORDED, url)
    again = check(RECORDED, url)
    result('the same observations produce the same issue ids',
           [i['id'] for i in first] == [i['id'] for i in again])

    wide = dict(RECORDED, width=1440)
    wide_ids = {i['check']: i['id'] for i in check(wide, url)}
    narrow_ids = {i['check']: i['id'] for i in first}
    result('a width-dependent finding at another width is a different defect',
           wide_ids['rendered_layout_overflow'] != narrow_ids['rendered_layout_overflow'],
           'overflow at 1440 vs 390')
    result('a width-independent finding keeps one id across widths',
           wide_ids['rendered_fake_heading'] == narrow_ids['rendered_fake_heading'])

    merged = merge_checks([RECORDED, wide], url)
    overflow = [i for i in merged if i['check'] == 'rendered_layout_overflow']
    result('merging two widths names both on one issue',
           len(overflow) == 1 and '390px' in overflow[0]['details']
           and '1440px' in overflow[0]['details'],
           overflow[0]['details'] if overflow else 'missing')


def test_thresholds_are_configurable():
    url = 'https://example.com/p'
    relaxed = check(RECORDED, url, {'image_oversize_ratio': 20.0,
                                    'min_hidden_chars': 10000,
                                    'severities': {'rendered_fake_heading': 'info'}})
    slugs = {i['check'] for i in relaxed}
    result('raising the upscale ratio silences the image finding',
           'rendered_image_oversized' not in slugs, str(sorted(slugs)))
    result('raising the hidden-text length silences that finding',
           'rendered_hidden_text' not in slugs, str(sorted(slugs)))
    result('severity is overridable per check',
           [i['type'] for i in relaxed if i['check'] == 'rendered_fake_heading'] == ['info'])
    ignored = check(RECORDED, url, {'console_ignore_patterns': ('TypeError',)})
    result('console noise can be filtered by pattern',
           not [i for i in ignored if i['check'] == 'rendered_console_error'])


def test_empty_observations_are_silent():
    result('an empty observations dict reports nothing',
           check({}, 'https://example.com/p') == []
           and check(None, 'https://example.com/p') == [])


def test_text_waiting_to_appear_is_not_hidden_content():
    """A scroll-in animation and a carousel slide are not hidden text.

    Reported per element, a site's own animations buried the real findings:
    31 pages of one live site produced 142 of these, all of them fades and slides.
    """
    found = issues_of('rendered_hidden_text')
    blob = ' '.join(str(i.get('evidence')) + i['details'] for i in found)
    result('an animated paragraph is not reported as hidden',
           ANIMATED_TEXT[:40] not in blob, blob[:160])
    result('a carousel slide is not reported as hidden',
           CAROUSEL_TEXT[:40] not in blob, blob[:160])
    result('the genuinely hidden paragraph is still reported',
           len(found) == 1 and HIDDEN_TEXT[:40] in blob,
           str(len(found)))
    result('hidden text is one issue per page, not one per block',
           len(found) <= 1, str([i['details'][:60] for i in found]))


def test_browser_invented_redirects_are_dropped():
    """Chromium restarts some navigations itself and calls it a 307.

    A Critical-CH header asking for a client hint the browser did not send,
    or an HSTS upgrade, makes Chromium reissue the request. Playwright reports
    that restart through `redirected_from` exactly like a server redirect, so
    a page that never sent a Location header was reported as redirecting to
    itself - and `redirect_loop`, an error, fired against healthy sites.

    A server redirect always points somewhere else, so a hop whose address
    equals the address it leads to belongs to the browser.
    """
    from src.core.js_renderer import JavaScriptRenderer
    drop = JavaScriptRenderer._drop_internal_restarts
    base = 'https://site.test'

    restart = drop([{'url': f'{base}/a', 'status_code': 307}], f'{base}/a')
    result('redirects: a restart to the same address is dropped', restart == [], str(restart))

    real = drop([{'url': f'{base}/old', 'status_code': 301}], f'{base}/new')
    result('redirects: a genuine one-hop redirect survives', len(real) == 1, str(real))

    mixed = drop([{'url': f'{base}/a', 'status_code': 301},
                  {'url': f'{base}/b', 'status_code': 307},
                  {'url': f'{base}/b', 'status_code': 301}], f'{base}/c')
    result('redirects: a restart inside a real chain is dropped, the rest kept',
           [hop['url'] for hop in mixed] == [f'{base}/a', f'{base}/b'], str(mixed))

    result('redirects: an empty chain stays empty', drop([], f'{base}/a') == [], 'ok')


def test_ordinary_hidden_markup_is_not_content():
    """The three shapes that made this check 95% noise on a real site.

    Auditing a 66-page site produced 66 `rendered_hidden_text` findings. Every
    one was a JSON-LD block inside a hidden wrapper, a closed <details>, or a
    collapsed mega-menu. None was content withheld from a reader. All three
    now sit on the clean fixture, which must stay silent.
    """
    hidden = issues_of('rendered_hidden_text', RUN['clean_issues'])
    result('a JSON-LD block in a hidden wrapper is not hidden prose',
           not any('@context' in (i['evidence'].get('snippet') or '') for i in hidden),
           str([i['evidence'].get('snippet', '')[:60] for i in hidden]))
    result('a closed <details> is progressive disclosure, not hidden content',
           not any(FAQ_ANSWER[:30] in (i['evidence'].get('snippet') or '') for i in hidden),
           str([i['evidence'].get('snippet', '')[:60] for i in hidden]))
    result('a collapsed menu inside <nav> is chrome, not hidden content',
           not any(MENU_TEXT[:30] in (i['evidence'].get('snippet') or '') for i in hidden),
           str([i['evidence'].get('snippet', '')[:60] for i in hidden]))
    result('the clean page reports no hidden text at all', not hidden,
           str([(i['check'], i['details'][:70]) for i in hidden]))

    # The real defect must still be caught on the broken page.
    still = issues_of('rendered_hidden_text')
    result('a genuinely hidden paragraph is still reported',
           any(HIDDEN_TEXT[:40] in (i['evidence'].get('snippet') or '') for i in still),
           str([i['evidence'].get('snippet', '')[:60] for i in still]))


def test_non_html_urls_are_not_rendered_as_pages():
    """A .webp is a file, not a web page.

    With image crawling on, modern image formats were handed to Chromium,
    which wraps a standalone image in its own viewer document - a page with
    a title, one <img> and no other markup. Every page check then fired
    against it: missing H1, missing meta description, missing canonical,
    missing lang, no OpenGraph, thin content. On one crawl that was 45 junk
    findings out of 90.
    """
    from src.core.js_renderer import JavaScriptRenderer
    renderer = JavaScriptRenderer.__new__(JavaScriptRenderer)
    base = 'https://site.test'

    never_rendered = ['/product/hero.webp', '/photo.avif', '/logo.svg', '/favicon.ico',
                      '/doc.pdf', '/data.json', '/feed.xml', '/sheet.xlsx',
                      '/font.woff2', '/clip.mp4', '/styles.css', '/app.mjs']
    missed = [p for p in never_rendered
              if renderer.should_use_javascript(base + p)]
    result('non-HTML URLs are never handed to the browser', not missed, str(missed))

    pages = ['/', '/about', '/guides/what-is-aeo', '/index.html', '/product.php']
    skipped = [p for p in pages if not renderer.should_use_javascript(base + p)]
    result('real pages are still rendered', not skipped, str(skipped))


def test_one_broken_check_does_not_lose_the_others():
    """The last of the checks that shipped with no test at all.

    Each builder runs inside its own try, so a crash in one area still lets
    the rest report. That guarantee had never been exercised, which is the
    worst kind of safety net to have: the first time it matters is in
    production, on someone else's crawl.
    """
    from src.core import rendered_checks

    observations = {'url': 'https://site.test/a', 'width': 1920,
                    'images': 'not a list, which the image builder will choke on',
                    'console_errors': [{'text': 'boom: a real console error'}]}
    found = rendered_checks.check(observations, 'https://site.test/a')
    slugs = [issue['check'] for issue in found]

    result('resilience: a builder that raises reports rendered_check_failed',
           'rendered_check_failed' in slugs, str(slugs))
    result('resilience: the other builders still report',
           'rendered_console_error' in slugs, str(slugs))

    failure = [i for i in found if i['check'] == 'rendered_check_failed']
    result('resilience: the failure names the area that broke',
           failure and 'image' in failure[0]['details'], str(failure[0]['details'] if failure else None))
    result('resilience: and says what to do about it',
           failure and 'bug' in failure[0]['how_to_fix'].lower(),
           str(failure[0]['how_to_fix'] if failure else None))

    healthy = rendered_checks.check(
        {'url': 'https://site.test/a', 'width': 1920, 'images': [], 'console_errors': []},
        'https://site.test/a')
    result('resilience: a sound page never reports a failed check',
           not [i for i in healthy if i['check'] == 'rendered_check_failed'],
           str([i['check'] for i in healthy]))


def test_several_widths_merge_into_one_issue():
    """A defect seen at three widths is one issue naming three widths.

    The multi-width code existed and the crawler only ever called one width,
    so a nav that vanishes on a phone was invisible. Merging matters as much
    as collecting: three widths must not become three copies of one finding.
    """
    from src.core import rendered_checks

    def seen_at(width):
        # scroll_width beyond inner_width is what horizontal overflow means;
        # the key is inner_width, not client_width, which the first version of
        # this test got wrong and so proved nothing at all.
        return {'url': 'https://site.test/a', 'width': width,
                'images': [], 'console_errors': [],
                'layout': {'scroll_width': 900, 'inner_width': width,
                           'offenders': [{'selector': 'div.wide', 'text': 'too wide',
                                          'width': 900}]}}

    one = rendered_checks.merge_checks([seen_at(390)], 'https://site.test/a')
    three = rendered_checks.merge_checks(
        [seen_at(1440), seen_at(768), seen_at(390)], 'https://site.test/a')

    overflow = [i for i in three if i['check'] == 'rendered_layout_overflow']
    result('widths: a defect at several widths is still one issue',
           len(overflow) <= 1, str([i['check'] for i in three]))
    if overflow:
        result('widths: the issue names the widths it was seen at',
               'px' in overflow[0]['details'], overflow[0]['details'])
    result('widths: the finding actually fired, so this test proves something',
           len(one) == 1 and one[0]['check'] == 'rendered_layout_overflow',
           str([i['check'] for i in one]))
    result('widths: three widths collapse to one issue, not three copies',
           len(overflow) == 1 and len(three) == 1, f'{len(one)} at one width, {len(three)} at three')
    # 900px of content does not overflow a 1440px viewport, so that width
    # must NOT appear - naming it would be the merge over-reporting.
    result('widths: only the widths where it actually overflowed are named',
           overflow and '768px' in overflow[0]['details']
           and '390px' in overflow[0]['details']
           and '1440px' not in overflow[0]['details'],
           overflow[0]['details'] if overflow else 'no finding')
    result('widths: an empty list of observations is silent',
           rendered_checks.merge_checks([], 'https://site.test/a') == [], 'ok')


TESTS = (
    test_several_widths_merge_into_one_issue,
    test_browser_invented_redirects_are_dropped,
    test_non_html_urls_are_not_rendered_as_pages,
    test_one_broken_check_does_not_lose_the_others,
    test_ordinary_hidden_markup_is_not_content,
    test_text_waiting_to_appear_is_not_hidden_content,
    test_oversized_image_is_reported,
    test_fake_heading_is_reported,
    test_hidden_nav_is_reported_as_chrome,
    test_hidden_paragraph_is_reported,
    test_text_written_by_javascript_is_reported,
    test_jsonld_injected_and_removed_are_both_reported,
    test_console_error_is_reported,
    test_overflow_is_reported_at_the_width_it_happens,
    test_every_check_fired_exactly_once_and_nothing_else,
    test_clean_page_reports_nothing,
    test_observations_carry_what_the_checks_need,
    test_collect_is_cheap_enough_for_a_500_page_crawl,
    test_check_needs_no_browser,
    test_ids_are_stable_and_width_aware,
    test_thresholds_are_configurable,
    test_empty_observations_are_silent,
)


def main():
    server = serve()
    try:
        asyncio.run(drive())
    except Exception as exc:
        print(f'\n  FAIL  browser run - {type(exc).__name__}: {exc}')
        FAILED.append('browser run')
        RUN.setdefault('observations', [])
        RUN.setdefault('issues', [])
        RUN.setdefault('clean_issues', [])
        RUN.setdefault('wall_ms', 0.0)

    for test in TESTS:
        print(f'\n{test.__name__}:')
        try:
            test()
        except Exception as exc:  # a raised test is a failed test
            result(test.__name__, False, f'raised {type(exc).__name__}: {exc}')

    server.shutdown()
    print('\n' + '=' * 64)
    print(f'  {len(PASSED)} passed, {len(FAILED)} failed')
    if FAILED:
        print('  failed: ' + ', '.join(FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
