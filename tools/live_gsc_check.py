"""Run the Search Console checks against a real property and a real crawl.

Everything else that proves these checks uses a stubbed transport. This
one makes the live call with the connected account, so the parsers meet
whatever Google actually returns today rather than a payload shaped from
the Discovery document.

It needs a property you have authorised, because there is no sensible
default for someone else's Search Console:

    LIVE_PROPERTY=sc-domain:example.com LIVE_SITE=https://example.com/ \
        python tools/live_gsc_check.py

LIVE_MAX_URLS (default 25) bounds the crawl.
"""
import os
import sys
import time

sys.path.insert(0, '/app')

PROPERTY = os.environ.get('LIVE_PROPERTY')
SITE = os.environ.get('LIVE_SITE')
MAX_URLS = int(os.environ.get('LIVE_MAX_URLS', '25'))

if not PROPERTY or not SITE:
    print(__doc__.strip())
    sys.exit(2)

from src.core import analytics, google_auth, search_console, token_store

stored = token_store.load(1, token_store.app_secret())
if not stored:
    print('no connected account'); sys.exit(2)

credentials = google_auth.Credentials(
    os.environ.get('GOOGLE_CLIENT_ID', ''),
    os.environ.get('GOOGLE_CLIENT_SECRET', ''),
    stored['refresh_token'])

from src.crawler import WebCrawler
c = WebCrawler()
c.config.update({'max_urls': MAX_URLS, 'max_depth': 5,
                 # A courtesy on someone's live site, even their own.
                 'delay': float(os.environ.get('LIVE_DELAY', '0.15')),
                 'enable_javascript': False, 'crawl_images': False})
print(f'crawling {SITE} (max {MAX_URLS}) ...', flush=True)
c.start_crawl(SITE)
deadline = time.time() + 600
while time.time() < deadline:
    if not c.is_running and c.crawl_results:
        break
    time.sleep(0.5)

pages = [r for r in c.crawl_results if r.get('url')]
ok = [r for r in pages if r.get('status_code') == 200]
print(f'crawled {len(pages)} url(s), {len(ok)} answered 200', flush=True)

out = search_console.collect(credentials, PROPERTY, pages,
                             config={'inspect_budget': int(os.environ.get('LIVE_INSPECT', '5'))})
if out.get('error'):
    print('Search Console error:', out['error']); sys.exit(1)

perf = out.get('performance') or {}
print(f'performance rows from Google: {len(perf)}')
print(f'inspections run: {len(out.get("inspections") or {})}')
print(f'issues: {len(out.get("issues") or [])}')
seen = {}
for i in out.get('issues') or []:
    seen.setdefault(i.get('check'), 0)
    seen[i['check']] += 1
for slug, n in sorted(seen.items()):
    print(f'  FIRED {slug} x{n}')


GA_PROPERTY = os.environ.get('LIVE_GA_PROPERTY', '')
if GA_PROPERTY:
    base = SITE.rstrip('/')
    ga = analytics.collect(credentials, GA_PROPERTY, pages, base,
                           config=None)
    if ga.get('error'):
        print('Analytics error:', ga['error'])
    else:
        traffic = ga.get('traffic') or {}
        print(f'GA4 rows from Google: {len(traffic)}')
        print(f'analytics issues: {len(ga.get("issues") or [])}')
        seen = {}
        for i in ga.get('issues') or []:
            seen[i.get('check')] = seen.get(i.get('check'), 0) + 1
        for slug, n in sorted(seen.items()):
            print(f'  FIRED {slug} x{n}')
