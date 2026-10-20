"""Regenerate every screenshot in the README, from a real crawl.

    python main.py --local --port 5058 &
    python tools/screenshots.py --app http://127.0.0.1:5058

It serves the coverage fixture - the site this project keeps deliberately
broken - crawls it through the UI the way a person would, and captures each
view into assets/screens/.

Two things it does that are worth knowing about:

* **It refuses to write a screenshot whose page contains a personal
  identifier.** That is not hypothetical. The first run of this caught six
  images carrying a real connected Google account and real client crawls,
  which would otherwise have been committed to a public repository. Add your
  own strings to --banned.

* **It intercepts the Google status endpoint** so the panel renders its
  not-yet-connected state. The alternative is painting over a screenshot,
  which is worse: a reader cannot tell a redacted image from a doctored one.

Run it against a clean install and the banned list is a backstop rather than
the first line of defence - the connected accounts and old crawls are simply
not there to leak.

Run it against a *clean* install (an empty data/ directory). Screenshots of a
machine with months of crawls on it show a machine with months of crawls on it.
"""

import argparse
import json
import os
import sys
import threading
import http.server

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'assets', 'screens')
# Strings that must never appear in a published screenshot. Yours will be
# different: your own name, your client's domains, the email address your
# Google account is connected with. Pass them with --banned.
DEFAULT_BANNED = ()

# A wildcard DNS name that resolves to 127.0.0.1 for everybody, so the fixture
# can be crawled under something that reads like a site rather than an IP.
DEMO_HOST = 'toadstone-demo.localtest.me'

NOT_CONNECTED = {'success': True, 'google': {
    'account_email': '', 'analytics_property_id': '', 'search_console_property': '',
    'can_connect': True, 'connected': False, 'installation_ready': True, 'reason': '',
    'redirect_uri': 'http://localhost:5000/api/google/callback', 'scope': '',
    'scopes_explained': [
        {'name': 'Search Console (read-only)',
         'why': 'To read impressions, clicks and the indexing verdict for crawled pages.'},
        {'name': 'Analytics (read-only)',
         'why': 'To read sessions and engagement for crawled pages.'},
        {'name': 'Your email address',
         'why': 'So the panel can show which account is connected.'}]}}

TABS = [
    ('overview', 'Every crawled URL with its SEO fields'),
    ('internal', 'Internal URLs'),
    ('status-codes', 'Status codes'),
    ('links', 'Inbound and outbound links per page'),
    ('visualization', 'Site structure'),
    ('sitemap', 'Sitemap audit and coverage'),
    ('pagespeed', 'PageSpeed and Core Web Vitals'),
    ('compare', 'Crawl-to-crawl comparison'),
    ('rules', 'User-defined consistency rules'),
    ('about', 'What a toadstone was, and why this is named after one'),
    ('licence', 'The licence in plain words: what you may and may not do'),
]

PANELS = [
    ('settings-google', 'Promise.resolve(openSettings()).then(()=>switchSettingsTab("google"))',
     'Connecting a Google account, scope by scope'),
    ('settings-crawler', 'Promise.resolve(openSettings()).then(()=>switchSettingsTab("crawler"))',
     'Crawler settings'),
    ('server-logs', 'openLogs()', 'Server log analysis'),
    ('fixer', 'openFixer()', 'Findings turned into one brief'),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', default='http://127.0.0.1:5058',
                    help='a running Toadstone, ideally on an empty data/')
    ap.add_argument('--port', type=int, default=8975, help='port for the fixture site')
    ap.add_argument('--banned', default=','.join(DEFAULT_BANNED),
                    help='comma-separated strings that must never appear in a screenshot')
    ap.add_argument('--site',
                    help='crawl this URL instead of the bundled fixture. The '
                         'published screenshots use a real site, because a '
                         'fixture at 127.0.0.1 looks like a toy; the default '
                         'is the fixture so anyone can reproduce the shape '
                         'without having a site to point at.')
    args = ap.parse_args()

    banned = tuple(b.strip().lower() for b in args.banned.split(',') if b.strip())
    os.environ['COVERAGE_SITE_PORT'] = str(args.port)
    sys.path.insert(0, REPO)
    sys.path.insert(0, os.path.join(REPO, 'tests'))
    import coverage_site as fixture
    from playwright.sync_api import sync_playwright

    os.makedirs(OUT, exist_ok=True)
    server = None
    if args.site:
        site = args.site
    else:
        http.server.ThreadingHTTPServer.allow_reuse_address = True
        server = http.server.ThreadingHTTPServer(('127.0.0.1', fixture.PORT), fixture.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        site = f'http://{DEMO_HOST}:{fixture.PORT}/'

    written, refused = [], []

    def shot(page, name, note):
        text = page.evaluate('document.body.innerText').lower()
        hit = [b for b in banned if b in text]
        if hit:
            refused.append((name, hit))
            print(f'  REFUSED {name}: the page shows {hit}', flush=True)
            return
        path = os.path.join(OUT, name + '.png')
        page.screenshot(path=path)
        kb = os.path.getsize(path) // 1024
        written.append((name, note, kb))
        print(f'  {name:22s} {kb:4d}KB  {note}', flush=True)

    def theme(page, mode):
        page.evaluate(f'setTheme("{mode}")')
        page.wait_for_timeout(500)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_context(viewport={'width': 1600, 'height': 1000}).new_page()
        page.on('dialog', lambda d: d.accept())
        page.route('**/api/google/status*', lambda route: route.fulfill(
            status=200, content_type='application/json', body=json.dumps(NOT_CONNECTED)))

        page.goto(args.app + '/', wait_until='networkidle')
        page.wait_for_selector('#urlInput', timeout=30000)
        print(f'crawling {site}', flush=True)
        page.fill('#urlInput', site)
        page.evaluate('startCrawl()')
        page.wait_for_function('location.pathname.startsWith("/crawl/")', timeout=30000)
        page.wait_for_function('!crawlState.isRunning && crawlState.urls.length > 0',
                               timeout=1800000)
        print('crawled %s URLs, %s issues' % (page.evaluate('crawlState.urls.length'),
                                              page.evaluate('(crawlState.issues||[]).length')),
              flush=True)
        page.evaluate('switchTab("issues")')
        page.wait_for_timeout(1800)

        collapse_first = """document.querySelectorAll('.issue-group-row').forEach(
            r => issueView.collapsed.add(r.dataset.groupKey)); renderIssueList();"""
        page.evaluate(collapse_first)
        page.wait_for_timeout(800)
        theme(page, 'light'); shot(page, 'hero-light', 'Findings grouped by check')
        theme(page, 'dark');  shot(page, 'hero-dark', 'The same, dark')
        theme(page, 'light')

        page.evaluate("issueView.collapsed.clear(); renderIssueList();")
        page.wait_for_timeout(500)
        page.evaluate(collapse_first)
        page.wait_for_timeout(800)
        shot(page, 'checks-collapsed', 'Every check that fired, with its slug and count')

        for tab, note in TABS:
            if tab in ('about', 'licence'):
                # A plugin tab is not a switchTab() target. Click the control a
                # person clicks - a panel that renders perfectly when you call
                # its function and cannot be reached by clicking is still broken.
                page.locator('.tab-btn', has_text=tab.capitalize()).first.click()
            else:
                page.evaluate(f'switchTab("{tab}")')
            page.wait_for_timeout(1500)
            shot(page, tab, note)

        page.evaluate('switchTab("overview")')
        page.wait_for_timeout(600)
        page.click('#megaTrigger')
        page.wait_for_timeout(900)
        shot(page, 'mega-menu', 'The menu, grouped by what you are trying to do')
        theme(page, 'dark'); shot(page, 'mega-menu-dark', 'The menu, dark'); theme(page, 'light')
        page.keyboard.press('Escape')
        page.wait_for_timeout(400)

        page.keyboard.press('Meta+k')
        page.wait_for_timeout(600)
        page.keyboard.type('exp')
        page.wait_for_timeout(500)
        shot(page, 'command-palette', 'Every action, searchable')
        page.keyboard.press('Escape')
        page.wait_for_timeout(400)

        for name, js, note in PANELS:
            page.keyboard.press('Escape')
            page.wait_for_timeout(400)
            page.evaluate('document.querySelectorAll(".modal").forEach(m => m.style.display="none")')
            page.evaluate(js)
            page.wait_for_timeout(1500)
            shot(page, name, note)

        browser.close()
    if server:
        server.shutdown()

    with open(os.path.join(OUT, 'MANIFEST.txt'), 'w') as fh:
        for name, note, kb in written:
            fh.write(f'{name}\t{kb}KB\t{note}\n')

    total = sum(kb for _, _, kb in written)
    print(f'\n{len(written)} screenshots, {total}KB, listed in assets/screens/MANIFEST.txt')
    if refused:
        print(f'\n{len(refused)} refused because the page showed something it should not:')
        for name, hit in refused:
            print(f'  {name}: {hit}')
        print('Nothing was written for those. Use a clean install.')
        return 1
    print('every screenshot checked: no banned string on screen')
    return 0


if __name__ == '__main__':
    sys.exit(main())
