"""Browser tests for URL routing (web/static/js/router.js).

Drives the real UI with Playwright against a running Toadstone in local mode.
The crawl it needs comes from a fixture site this script serves itself, so run
it where the server can reach this script's localhost - e.g. alongside the
Docker container:

    docker run --rm --network container:toadstone \\
        -v "$PWD/tests:/app/tests:ro" toadstone:latest python tests/router_tests.py

Set TOADSTONE_URL to test another server (default http://127.0.0.1:5000).
The crawls it creates are deleted again at the end.
"""

import http.server
import os
import sys
import threading

from playwright.sync_api import sync_playwright

APP = os.environ.get('TOADSTONE_URL', 'http://127.0.0.1:5000').rstrip('/')
SITE_PORT = int(os.environ.get('ROUTER_FIXTURE_PORT', '8931'))

PASSED, FAILED = [], []


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def serve_fixture_site():
    pages = {
        '/': '<a href="/a">a</a> <a href="/b">b</a> <a href="/missing">missing</a>',
        '/a': '<a href="/">home</a>',
        '/b': '<a href="/">home</a>',
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = pages.get(self.path)
            code = 200 if body is not None else 404
            data = f'<html><head><title>{self.path}</title></head><body>{body or "nope"}</body></html>'.encode()
            self.send_response(code)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = http.server.ThreadingHTTPServer(('127.0.0.1', SITE_PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def path(page):
    return page.evaluate('location.pathname + location.search')


def start_crawl(page, url):
    """Start a crawl from the current page and wait for it to finish; returns its id."""
    page.fill('#urlInput', url)
    page.evaluate('startCrawl()')
    before = path(page)
    page.wait_for_function('p => location.pathname.startsWith("/crawl/") && location.pathname + location.search !== p',
                           arg=before, timeout=15000)
    page.wait_for_function('!crawlState.isRunning && crawlState.urls.length > 0', timeout=60000)
    return int(path(page).split('/')[2].split('?')[0])


def settle(page):
    page.wait_for_timeout(700)


def main():
    site = serve_fixture_site()
    created = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on('dialog', lambda d: d.accept())

            print('\nnew crawl gets a URL:')
            page.goto(f'{APP}/')
            first = start_crawl(page, f'http://127.0.0.1:{SITE_PORT}/')
            created.append(first)
            result('starting a crawl moves to /crawl/<id>', path(page).startswith(f'/crawl/{first}'), path(page))

            print('\nUI changes are written to the URL:')
            page.click(".tab-btn[onclick=\"switchTab('issues')\"]")
            result('tab', path(page) == f'/crawl/{first}/issues', path(page))
            page.click(".filter-item[onclick=\"toggleFilter('4xx')\"]")
            result('sidebar filter', 'filter=4xx' in path(page), path(page))
            page.click("#issues-tab .filter-item[data-filter='warning']")
            result('issue severity', 'severity=warning' in path(page), path(page))
            page.evaluate('openSettings()')
            settle(page)
            page.click(".settings-tab-btn[onclick=\"switchSettingsTab('requests')\"]")
            result('settings tab', 'settings=requests' in path(page), path(page))
            page.evaluate('closeSettings()')
            result('closing settings', 'settings=' not in path(page), path(page))

            print('\ndeep links restore the view on load:')
            deep = (f'/crawl/{first}/links?filter=html&severity=error&links_q=zzz'
                    f'&page=http%3A%2F%2F127.0.0.1%3A{SITE_PORT}%2Fa')
            page.goto(APP + deep)
            page.wait_for_function('crawlState.urls.length > 0', timeout=15000)
            settle(page)
            state = page.evaluate("""() => ({
                tab: document.querySelector('.tab-btn.active')?.getAttribute('onclick'),
                filter: crawlState.filters.active,
                severity: crawlState.filters.issueFilter,
                search: document.querySelector('input[oninput="searchInternalLinks(this.value)"]').value,
                details: document.querySelector('.details-modal-overlay')?.dataset.url
            })""")
            result('tab restored', "'links'" in (state['tab'] or ''), str(state['tab']))
            result('filters restored', state['filter'] == 'html' and state['severity'] == 'error', str(state))
            result('links search restored', state['search'] == 'zzz', state['search'])
            result('page details reopened', state['details'] == f'http://127.0.0.1:{SITE_PORT}/a', str(state['details']))
            result('URL kept as given', path(page) == deep, path(page))

            print('\nBack/Forward move between views and crawls:')
            # A live crawl reports its base URL with a trailing slash, a loaded one without
            first_base = page.evaluate("crawlState.baseUrl.replace(/\\/$/, '')")
            # Another host, so the two crawls have different base URLs
            second = start_crawl(page, f'http://localhost:{SITE_PORT}/')
            created.append(second)
            page.go_back()
            page.wait_for_function(f"location.pathname.startsWith('/crawl/{first}')", timeout=15000)
            page.wait_for_function("b => crawlState.urls.length > 0 && crawlState.baseUrl.replace(/\\/$/, '') === b", arg=first_base, timeout=15000)
            result('Back returns to the previous crawl', path(page).startswith(f'/crawl/{first}'), path(page))
            page.go_forward()
            page.wait_for_function("b => crawlState.urls.length > 0 && crawlState.baseUrl.replace(/\\/$/, '') !== b", arg=first_base, timeout=15000)
            result('Forward returns to the newer crawl', path(page).startswith(f'/crawl/{second}'), path(page))

            print('\nbad links fail safely:')
            page.goto(f'{APP}/crawl/99999999/issues')
            settle(page)
            result('unknown crawl falls back to the start page', path(page) == '/?tab=issues', path(page))
            page.goto(f'{APP}/crawl/{first}?filter=%27);alert(1)//&settings=%22x')
            page.wait_for_function('crawlState.urls.length > 0', timeout=15000)
            settle(page)
            result('invalid values are dropped', 'alert' not in path(page) and '%22x' not in path(page), path(page))

            browser.close()
    finally:
        site.shutdown()
        cleanup(created)

    print('\n' + '=' * 64)
    print(f'  {len(PASSED)} passed, {len(FAILED)} failed')
    if FAILED:
        print('  failed: ' + ', '.join(FAILED))
    return 1 if FAILED else 0


def cleanup(crawl_ids):
    import requests
    session = requests.Session()
    session.get(f'{APP}/')  # local mode signs this session in
    for crawl_id in crawl_ids:
        session.delete(f'{APP}/api/crawls/{crawl_id}/delete')


if __name__ == '__main__':
    sys.exit(main())
