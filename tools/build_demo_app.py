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

    # the app's own assets, unmodified
    shutil.copytree(STATIC_SRC, os.path.join(OUT, 'static'),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))

    # the page itself, rewritten to load those assets from here
    html = html.replace('href="/static/', 'href="static/').replace('src="/static/', 'src="static/')
    html = re.sub(r'<script[^>]*src="https://[^"]*"[^>]*></script>', '', html)
    data = f'<script>window.__DEMO_RESPONSES__ = {json.dumps(captured, separators=(",", ":"))};</script>'
    shim = SHIM.replace('__CRAWL__', str(args.crawl))
    html = html.replace('</head>', data + shim + '</head>', 1)

    with open(os.path.join(OUT, 'index.html'), 'w') as fh:
        fh.write(html)
    with open(os.path.join(OUT, '.nojekyll'), 'w') as fh:
        fh.write('')

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
