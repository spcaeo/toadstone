"""Freeze the real UI, with a real crawl in it, into a static demo.

    python main.py --local --port 5058 &     # a clean install, one crawl done
    python tools/build_demo_app.py --app http://127.0.0.1:5058 --crawl 1

The first demo this project published was a generated report, and a report is
not the software. This one is the software: the actual templates, the actual
stylesheet, the actual JavaScript, with every API response the app asks for on
load captured and replayed from a file. Tabs switch, filters filter, findings
expand, the palette opens - all of it client-side, none of it reimplemented.

How it works: a driver loads the app against a finished crawl, visits every
view so each one makes its calls, and records every GET response. Those become
one JSON map, and a shim installed before the app's own scripts answers fetch()
from it. Anything that would write - starting a crawl, saving settings - is
answered with a polite refusal rather than failing, so the demo degrades into
read-only instead of breaking.

What it cannot do, and says so on screen: start a new crawl.
"""

import argparse
import json
import os
import re
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'demo-site')
STATIC_SRC = os.path.join(REPO, 'web', 'static')

# Views to visit so their fetches are captured. Plugin tabs are clicked.
TABS = ['overview', 'internal', 'external', 'status-codes', 'links', 'issues',
        'pagespeed', 'visualization', 'sitemap', 'compare', 'rules']

SHIM = """<script>
/* Static demo shim — installed before the app's own scripts.
   Answers every GET from a captured map, refuses writes politely, and tells
   the viewer what this copy cannot do. Nothing below is app code; the app
   itself is unmodified. */
(function () {
  var MAP = window.__DEMO_RESPONSES__ || {};
  var realFetch = window.fetch.bind(window);

  function key(url) {
    try { var u = new URL(url, location.href); return u.pathname + u.search; }
    catch (e) { return String(url); }
  }
  function reply(body, status) {
    return Promise.resolve(new Response(JSON.stringify(body),
      { status: status || 200, headers: { 'Content-Type': 'application/json' } }));
  }

  window.fetch = function (input, init) {
    var url = typeof input === 'string' ? input : (input && input.url) || '';
    var method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    var k = key(url);

    if (k.indexOf('/api/') !== 0) return realFetch(input, init);

    if (method !== 'GET') {
      /* Loading a saved crawl is a POST and is the one write this demo needs,
         so it is answered as success; the data itself comes from the captured
         /api/crawl_status below. Everything else that writes is refused. */
      if (/\/api\/crawls\/\d+\/load$/.test(k)) return reply({ success: true });
      return reply({ success: false,
        error: 'This is a read-only demo. Install Toadstone to crawl your own site.' }, 200);
    }
    if (MAP[k] !== undefined) return reply(MAP[k]);
    /* unseen query combination: fall back to the same path without its query */
    var base = k.split('?')[0];
    if (MAP[base] !== undefined) return reply(MAP[base]);
    return reply({ success: false, error: 'Not captured in this static demo.' }, 200);
  };

  window.addEventListener('DOMContentLoaded', function () {
    /* The router reads location.pathname to decide which crawl to open, and on
       a static host that path names none. Point it at this demo's crawl — but
       only now, after every <script src> has resolved: doing it earlier makes
       the app's own relative asset paths resolve against /crawl/ and 404. */
    /* Rather than fake a path and hope the router re-reads it, call the app's
       own hydrate directly once it exists. It fetches /api/crawl_status, which
       the map answers with the real crawl. */
    var tries = 0;
    (function hydrate() {
      if (typeof window.hydrateFromServer === 'function') {
        window.hydrateFromServer();
        return;
      }
      if (tries++ < 60) setTimeout(hydrate, 100);
    })();

    var bar = document.createElement('div');
    bar.id = 'demo-bar';
    bar.innerHTML =
      '<span><b>Live demo</b> — the real interface, frozen over a real crawl of '
      + '<a href="https://freshlycited.com/" target="_blank" rel="noopener">freshlycited.com</a>. '
      + 'Click anything. Starting a new crawl is the one thing it cannot do.</span>'
      + '<a class="demo-cta" href="https://github.com/spcaeo/toadstone">Get it on GitHub →</a>';
    document.body.appendChild(bar);
  });
})();
</script>
<style>
#demo-bar { position:fixed; left:0; right:0; bottom:0; z-index:9999;
  display:flex; align-items:center; gap:16px; flex-wrap:wrap;
  padding:11px 18px; font:13.5px/1.45 ui-sans-serif,system-ui,sans-serif;
  background:#14161c; color:#c8cdd8; border-top:1px solid #262a33; }
#demo-bar b { color:#fff; } #demo-bar a { color:#a5b0ff; }
#demo-bar .demo-cta { margin-left:auto; background:#4f46e5; color:#fff;
  text-decoration:none; padding:7px 14px; border-radius:7px; white-space:nowrap; }
body { padding-bottom:46px !important; }
</style>
"""


# Fields the crawl carries for its own analysis and the UI never renders in a
# table. A demo that ships them is 10MB of page bodies and JSON-LD for no
# visible gain; one that drops them is 1MB and identical on screen.
HEAVY_URL_FIELDS = ('main_text', 'images', 'json_ld_raw', 'json_ld', 'headings',
                    'meta_tags', 'og_tags', 'twitter_tags', 'linked_from',
                    'h2', 'h3', 'h4', 'h5', 'h6', 'raw_html', 'rendered_html')
LINK_CAP = 1500


def trim(captured):
    """Make the demo small enough to load fast, without changing what shows."""
    before = len(json.dumps(captured))
    for body in captured.values():
        if not isinstance(body, dict):
            continue
        for url_row in (body.get('urls') or []):
            for field in HEAVY_URL_FIELDS:
                url_row.pop(field, None)
        links = body.get('links')
        if isinstance(links, list) and len(links) > LINK_CAP:
            body['links'] = links[:LINK_CAP]
            body['links_truncated_for_demo'] = True
    after = len(json.dumps(captured))
    print(f'  trimmed {before // 1024}KB -> {after // 1024}KB '
          f'(page bodies, JSON-LD and image lists dropped; links capped at {LINK_CAP})')
    return captured


# GitHub Pages serves a project site from /<repo>/, so a path that begins with
# a slash leaves the site. The plugin loader builds its script src that way,
# which is correct in the app and 404s here — and fails silently, because a
# plugin that never loads is a tab that never appears.
ABSOLUTE_ASSET = re.compile(r'(["\'`])/static/')


def relativise_asset_paths(static_root):
    changed = []
    for dirpath, _, files in os.walk(static_root):
        for name in files:
            if not name.endswith(('.js', '.css')):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding='utf-8', errors='replace') as fh:
                text = fh.read()
            fixed = ABSOLUTE_ASSET.sub(r'\1static/', text)
            if fixed != text:
                with open(path, 'w', encoding='utf-8') as fh:
                    fh.write(fixed)
                changed.append(os.path.relpath(path, static_root))
    if changed:
        print(f'  made {len(changed)} absolute /static/ reference(s) relative: '
              + ', '.join(changed[:4]))
    return changed


SITE = 'https://spcaeo.github.io/toadstone/'

# The published demo is a page on the public web, and the first version of it
# shipped with no canonical, no OpenGraph, no Twitter card and no structured
# data - from an SEO tool. Fixed here rather than in the app's template,
# because these values are true of the published copy and of nothing else.
HEAD_TAGS = f"""
<link rel="canonical" href="{SITE}">
<meta name="description" content="A live Toadstone demo: the real interface, frozen over a real crawl of 141 pages. Self-hosted SEO crawler with JavaScript rendering, crawl diffing, axe-core accessibility, server log analysis and an MCP server. No sign-up.">
<meta name="robots" content="index,follow,max-image-preview:large">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Toadstone">
<meta property="og:title" content="Toadstone — live demo, the real interface">
<meta property="og:description" content="A real crawl of 141 pages, in the actual app. Click the tabs, open a finding. No sign-up.">
<meta property="og:url" content="{SITE}">
<meta property="og:image" content="https://raw.githubusercontent.com/spcaeo/toadstone/main/assets/brand/social-preview.png">
<meta property="og:image:width" content="1280">
<meta property="og:image:height" content="640">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Toadstone — live demo, the real interface">
<meta name="twitter:description" content="A real crawl of 141 pages, in the actual app. No sign-up.">
<meta name="twitter:image" content="https://raw.githubusercontent.com/spcaeo/toadstone/main/assets/brand/social-preview.png">
<script type="application/ld+json">{{
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  "name": "Toadstone",
  "applicationCategory": "DeveloperApplication",
  "applicationSubCategory": "SEO crawler",
  "operatingSystem": "Docker, macOS, Linux, Windows",
  "url": "https://github.com/spcaeo/toadstone",
  "downloadUrl": "https://github.com/spcaeo/toadstone",
  "description": "A self-hosted SEO crawler whose checks have been watched to fire: 222 checks, 180 proven against a site built to be wrong. JavaScript rendering, crawl-to-crawl diffing with stable issue ids, axe-core accessibility, server log analysis and an MCP server.",
  "license": "https://www.elastic.co/licensing/elastic-license",
  "offers": {{"@type": "Offer", "price": "0", "priceCurrency": "USD"}},
  "author": {{"@type": "Organization", "name": "LabsMedia", "url": "https://www.labsmedia.com/"}}
}}</script>
"""

ROBOTS = f"""User-agent: *
Allow: /

Sitemap: {SITE}sitemap.xml
"""


def sitemap(today):
    pages = [(SITE, '1.0'), (SITE + 'watch.html', '0.8')]
    urls = '\n'.join(
        f'  <url><loc>{loc}</loc><lastmod>{today}</lastmod>'
        f'<priority>{pri}</priority></url>' for loc, pri in pages)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f'{urls}\n</urlset>\n')



WATCH_PAGE = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Toadstone — the tour</title>
<meta name="description" content="Why a self-hosted SEO crawler exists when a per-machine licence does not fit a team of 22, and what it found: 84 of 225 checks had ever fired.">
<link rel="canonical" href="{SITE}watch.html">
<meta name="robots" content="index,follow,max-image-preview:large">
<meta property="og:type" content="video.other">
<meta property="og:site_name" content="Toadstone">
<meta property="og:title" content="Toadstone — the tour">
<meta property="og:description" content="Why it exists, what it found, and every view in the app.">
<meta property="og:url" content="{SITE}watch.html">
<meta property="og:image" content="{SITE}poster.jpg">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Toadstone — the tour">
<meta name="twitter:description" content="Why it exists, what it found, and every view in the app.">
<meta name="twitter:image" content="{SITE}poster.jpg">
<link rel="icon" href="static/favicon.svg">
<style>
:root{{color-scheme:dark}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d0f14;color:#e8eaef;font:16px/1.6 ui-sans-serif,system-ui,sans-serif;
     min-height:100vh;display:flex;align-items:center;justify-content:center;padding:36px}}
.w{{max-width:1100px;width:100%}}
h1{{font-size:30px;letter-spacing:-.02em;margin-bottom:6px}}
p.s{{color:#9aa0ad;margin-bottom:22px}}
video{{width:100%;max-height:72vh;object-fit:contain;border-radius:14px;
       border:1px solid #262a33;background:#000;display:block}}
.cta{{display:flex;gap:12px;flex-wrap:wrap;margin-top:22px}}
a.b{{border:1px solid #262a33;background:#14161c;color:#e8eaef;text-decoration:none;
    padding:11px 18px;border-radius:9px;font-size:14px}}
a.b.p{{background:#4f46e5;border-color:#4f46e5;color:#fff}}
small{{display:block;margin-top:24px;color:#6f7584;line-height:1.7}}
a{{color:#a5b0ff}}
</style></head><body><div class="w">
<h1>Toadstone — the tour</h1>
<p class="s">Why it exists, what it found, and every view in the app. Nothing staged.<br>
Looking for the software itself? <a href="./">Open the live demo</a> — that one you can click.</p>
<video src="launch.mp4" controls playsinline preload="metadata" poster="poster.jpg"></video>
<div class="cta">
  <a class="b p" href="./">Open the live demo</a>
  <a class="b" href="https://github.com/spcaeo/toadstone">Source on GitHub</a>
  <a class="b" href="launch.mp4" download>Download the video</a>
</div>
<small>A self-hosted SEO crawler, source-available under the Elastic License 2.0.
Built by <a href="https://www.labsmedia.com/">LabsMedia</a>. Other products named in
this project's documentation are trademarks of their respective owners; Toadstone is
not affiliated with or endorsed by any of them.</small>
</div></body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', default='http://127.0.0.1:5058')
    ap.add_argument('--crawl', type=int, default=1)
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    captured = {}
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_context(viewport={'width': 1600, 'height': 1000}).new_page()
        page.on('dialog', lambda d: d.dismiss())

        def record(response):
            url = response.url
            if '/api/' not in url or response.request.method != 'GET':
                return
            try:
                body = response.json()
            except Exception:
                return
            from urllib.parse import urlparse
            u = urlparse(url)
            captured[u.path + (('?' + u.query) if u.query else '')] = body

        page.on('response', record)
        page.goto(f'{args.app}/crawl/{args.crawl}', wait_until='networkidle')
        page.wait_for_function('crawlState && crawlState.urls.length > 0', timeout=90000)
        page.wait_for_timeout(2500)

        for tab in TABS:
            page.evaluate(f'switchTab("{tab}")')
            page.wait_for_timeout(900)
        for label in ('About', 'Licence'):
            try:
                page.locator('.tab-btn', has_text=label).first.click()
                page.wait_for_timeout(900)
            except Exception:
                pass
        for js in ('openLogs()', 'openFixer()', 'openDashboard()',
                   'Promise.resolve(openSettings()).then(()=>switchSettingsTab("crawler"))'):
            try:
                page.keyboard.press('Escape')
                page.evaluate(js)
                page.wait_for_timeout(1100)
            except Exception:
                pass
        page.keyboard.press('Escape')
        page.evaluate('switchTab("issues")')
        page.wait_for_timeout(1200)

        browser.close()

    import urllib.request
    with urllib.request.urlopen(f'{args.app}/crawl/{args.crawl}', timeout=30) as r:
        html = r.read().decode('utf-8')

    captured = trim(captured)

    # the app's own assets, unmodified except for one thing that cannot survive
    # being served from a subpath
    shutil.copytree(STATIC_SRC, os.path.join(OUT, 'static'),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    relativise_asset_paths(os.path.join(OUT, 'static'))

    # the page itself, rewritten to load those assets from here
    html = html.replace('href="/static/', 'href="static/').replace('src="/static/', 'src="static/')
    html = re.sub(r'<script[^>]*src="https://[^"]*"[^>]*></script>', '', html)
    # Our own head tags go in before the shim, and the app's own <title> is
    # replaced: "Toadstone - SEO Spider" is right in the app and wrong as the
    # title of a public demo page.
    html = re.sub(r'<title>.*?</title>',
                  '<title>Toadstone live demo — a real crawl, in the real interface</title>',
                  html, count=1, flags=re.S)
    # The app template carries its own description now (4dc3dd6). Two
    # description tags on one page is itself a defect - and one this tool
    # reports - so the template's is replaced rather than joined.
    html = re.sub(r'<meta[^>]*name=["\']description["\'][^>]*>', '', html,
                  flags=re.I)
    html = html.replace('</head>', HEAD_TAGS + '</head>', 1)

    data = f'<script>window.__DEMO_RESPONSES__ = {json.dumps(captured, separators=(",", ":"))};</script>'
    shim = SHIM.replace('__CRAWL__', str(args.crawl))
    html = html.replace('</head>', data + shim + '</head>', 1)

    with open(os.path.join(OUT, 'index.html'), 'w') as fh:
        fh.write(html)
    with open(os.path.join(OUT, '.nojekyll'), 'w') as fh:
        fh.write('')

    # The tour page and its assets belong to the published site too, and this
    # function empties the directory - so it owns all of it or the next build
    # silently drops them, which is what happened the first time.
    with open(os.path.join(OUT, 'watch.html'), 'w') as fh:
        fh.write(WATCH_PAGE)
    for src, dest in (('assets/video/launch.mp4', 'launch.mp4'),
                      ('assets/brand/video-poster.jpg', 'poster.jpg')):
        source = os.path.join(REPO, src)
        if os.path.exists(source):
            shutil.copy2(source, os.path.join(OUT, dest))
        else:
            print(f'  WARNING: {src} is missing, so the tour page will not play')

    import datetime
    today = datetime.date.today().isoformat()
    with open(os.path.join(OUT, 'robots.txt'), 'w') as fh:
        fh.write(ROBOTS)
    with open(os.path.join(OUT, 'sitemap.xml'), 'w') as fh:
        fh.write(sitemap(today))

    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk(OUT) for f in fs)
    print(f'{OUT}: {len(captured)} API responses captured, '
          f'{total // 1024}KB total')
    biggest = sorted(((len(json.dumps(v)), k) for k, v in captured.items()), reverse=True)[:4]
    for size, k in biggest:
        print(f'   {size // 1024:>5}KB  {k[:72]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
