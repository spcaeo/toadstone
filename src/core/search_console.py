"""Search Console: what Google did with the pages, next to what we crawled.

A crawl can tell you a page is well built. Only Search Console can tell you
Google never indexed it, or that it has been shown eighty thousand times and
clicked forty. Those are the findings a site owner acts on first, and no
amount of crawling produces them.

Everything here joins Google's view to the crawl's view, because either on
its own is half a picture:

  * a page we crawled that Google has no record of
  * a page Google reports that nothing on the site links to
  * a page with impressions and almost no clicks, which is a title and
    description problem rather than a ranking one
  * two pages competing for the same query, which is usually one page too many

Field names and endpoints here are taken from Google's published Discovery
documents rather than from memory, and a test pins them. That is as far as
verification goes without an account: nobody has yet made a real call with
real credentials against a real property.

The URL Inspection API is used sparingly and deliberately. It is rate-limited
to 2,000 calls a day per property, so inspecting every URL of a large site
would exhaust the quota and finish nothing. Pages are inspected in a
configurable budget, worst-first: the ones with no impressions at all, since
those are the ones most likely not to be indexed.
"""

import urllib.parse
from collections import defaultdict
from datetime import date, timedelta

from src.core.google_auth import GoogleAuthError, call
from src.utils.issue_factory import make_issue

CATEGORY = 'Search Console'

# Both paths come from Google's own Discovery document, checked rather
# than guessed: searchconsole.googleapis.com is the documented host, and
# www.googleapis.com/webmasters/v3 is the legacy alias it replaced.
SEARCH_ANALYTICS = ('https://searchconsole.googleapis.com/webmasters/v3/sites/'
                    '{site}/searchAnalytics/query')
INSPECT = 'https://searchconsole.googleapis.com/v1/urlInspection/index:inspect'

DEFAULTS = {
    'days': 28,                     # Search Console's own default window
    'row_limit': 5000,
    'inspect_budget': 50,           # of 2,000 a day, per property
    'low_ctr_impressions': 100,     # below this, a low CTR is noise
    'low_ctr_threshold': 0.01,      # 1%
    'cannibalisation_min_impressions': 50,
    'max_examples': 20,
}


def _config(config=None):
    cfg = dict(DEFAULTS)
    cfg.update(config or {})
    return cfg


def site_param(site_url):
    """Search Console wants the property id percent-encoded, including sc-domain."""
    return urllib.parse.quote(site_url or '', safe='')


def fetch_pages(credentials, site_url, config=None, opener=None, today=None):
    """Clicks, impressions, CTR and position for every page with data."""
    cfg = _config(config)
    today = today or date.today()
    body = {
        'startDate': str(today - timedelta(days=cfg['days'])),
        'endDate': str(today),
        'dimensions': ['page'],
        'rowLimit': cfg['row_limit'],
    }
    payload = call(credentials, SEARCH_ANALYTICS.format(site=site_param(site_url)),
                   method='POST', body=body, opener=opener)
    out = {}
    for row in (payload or {}).get('rows') or []:
        keys = row.get('keys') or []
        if not keys:
            continue
        out[keys[0]] = {
            'clicks': row.get('clicks', 0),
            'impressions': row.get('impressions', 0),
            'ctr': row.get('ctr', 0.0),
            'position': row.get('position', 0.0),
        }
    return out


def fetch_queries(credentials, site_url, config=None, opener=None, today=None):
    """Query-and-page pairs, which is what cannibalisation is visible in."""
    cfg = _config(config)
    today = today or date.today()
    body = {
        'startDate': str(today - timedelta(days=cfg['days'])),
        'endDate': str(today),
        'dimensions': ['query', 'page'],
        'rowLimit': cfg['row_limit'],
    }
    payload = call(credentials, SEARCH_ANALYTICS.format(site=site_param(site_url)),
                   method='POST', body=body, opener=opener)
    rows = []
    for row in (payload or {}).get('rows') or []:
        keys = row.get('keys') or []
        if len(keys) < 2:
            continue
        rows.append({'query': keys[0], 'page': keys[1],
                     'clicks': row.get('clicks', 0),
                     'impressions': row.get('impressions', 0),
                     'position': row.get('position', 0.0)})
    return rows


def inspect_url(credentials, site_url, page_url, opener=None):
    """Whether Google has this page indexed, and why not if it does not."""
    payload = call(credentials, INSPECT, method='POST',
                   body={'inspectionUrl': page_url, 'siteUrl': site_url},
                   opener=opener)
    status = (((payload or {}).get('inspectionResult') or {})
              .get('indexStatusResult') or {})
    return {
        'verdict': status.get('verdict', ''),
        'coverage': status.get('coverageState', ''),
        'robots': status.get('robotsTxtState', ''),
        'indexing': status.get('indexingState', ''),
        'last_crawl': status.get('lastCrawlTime', ''),
        'canonical': status.get('googleCanonical', ''),
        'user_canonical': status.get('userCanonical', ''),
    }


def issues(pages, performance, inspections=None, queries=None, base_url='', config=None):
    """Everything Search Console knows that the crawl could not."""
    cfg = _config(config)
    performance = performance or {}
    inspections = inspections or {}
    found = []

    crawled = {}
    for page in pages or []:
        url = (page or {}).get('url')
        if url:
            crawled[url.rstrip('/') or url] = page

    def known(url):
        return (url or '').rstrip('/') in crawled or (url or '') in crawled

    # Pages Google reports that the crawl never reached.
    orphaned = [url for url in performance if not known(url)]
    if orphaned:
        found.append(make_issue(
            url=base_url, check='gsc_page_not_in_crawl', severity='warning',
            category=CATEGORY, issue='Google Knows Pages the Crawl Did Not Find',
            details=(f'{len(orphaned)} page(s) have Search Console impressions but were '
                     f'not reachable from any link on the site.'),
            offending_value=str(len(orphaned)),
            evidence={'found': f'{len(orphaned)} pages',
                      'related_urls': orphaned[:cfg['max_examples']]},
            how_to_fix=('Link to them if they should exist; they are earning impressions '
                        'with no internal support at all.')))

    # Crawled pages Google has no performance data for.
    silent = [url for url in crawled if url not in performance
              and url.rstrip('/') not in {k.rstrip('/') for k in performance}]
    if silent and performance:
        found.append(make_issue(
            url=base_url, check='gsc_no_impressions', severity='info',
            category=CATEGORY, issue='Pages With No Search Impressions',
            details=(f'{len(silent)} crawled page(s) were shown in Google search zero '
                     f'times in the last {cfg["days"]} days.'),
            offending_value=str(len(silent)),
            evidence={'found': f'{len(silent)} of {len(crawled)} pages',
                      'related_urls': sorted(silent)[:cfg['max_examples']]},
            how_to_fix=('Check they are indexed and that anything is searching for what '
                        'they cover; a page with no impressions is not competing.')))

    # Seen often, clicked rarely: a title and description problem.
    for url, row in sorted(performance.items()):
        impressions = row.get('impressions') or 0
        ctr = row.get('ctr') or 0.0
        if impressions < cfg['low_ctr_impressions'] or ctr >= cfg['low_ctr_threshold']:
            continue
        found.append(make_issue(
            url=url, check='gsc_low_ctr', severity='warning', category=CATEGORY,
            issue='Shown Often, Clicked Rarely',
            details=(f'{int(impressions)} impressions and {int(row.get("clicks") or 0)} '
                     f'clicks, a click-through rate of {ctr * 100:.2f}% at average '
                     f'position {row.get("position", 0):.1f}.'),
            offending_value=f'{int(impressions)}@{ctr:.4f}',
            evidence={'found': f'{ctr * 100:.2f}% CTR over {int(impressions)} impressions',
                      'expected': f'at least {cfg["low_ctr_threshold"] * 100:.0f}%'},
            how_to_fix=('Rewrite the title and meta description to match what the people '
                        'seeing it are actually searching for.')))

    # Two pages answering one query is usually one page too many.
    by_query = defaultdict(list)
    for row in queries or []:
        if (row.get('impressions') or 0) >= cfg['cannibalisation_min_impressions']:
            by_query[row['query']].append(row)
    competing = {query: rows for query, rows in by_query.items() if len(rows) > 1}
    for query, rows in sorted(competing.items())[:cfg['max_examples']]:
        urls = sorted({row['page'] for row in rows})
        if len(urls) < 2:
            continue
        found.append(make_issue(
            url=urls[0], check='gsc_query_cannibalisation', severity='warning',
            category=CATEGORY, issue='Two Pages Competing for One Query',
            details=(f'{len(urls)} pages are shown for "{query}", so they are splitting '
                     f'its impressions between them.'),
            offending_value=query,
            evidence={'found': query, 'related_urls': urls[:10]},
            how_to_fix=('Decide which page should own the query, and make the others '
                        'link to it rather than compete with it.')))

    # What the inspection API said, for the pages we spent budget on.
    for url, verdict in sorted(inspections.items()):
        coverage = (verdict or {}).get('coverage', '')

        # Google choosing a different canonical is most interesting when the
        # page IS indexed - under an address its author did not choose. So
        # this is judged before the indexed check returns early, not after.
        google_canonical = (verdict or {}).get('canonical', '')
        user_canonical = (verdict or {}).get('user_canonical', '')
        if google_canonical and user_canonical and google_canonical != user_canonical:
            found.append(make_issue(
                url=url, check='gsc_canonical_overridden', severity='warning',
                category=CATEGORY, issue='Google Chose a Different Canonical',
                details=(f'This page names {user_canonical} as canonical but Google '
                         f'chose {google_canonical}.'),
                offending_value=google_canonical,
                evidence={'found': google_canonical, 'expected': user_canonical},
                how_to_fix=('Google overrides a canonical when the two pages look alike. '
                            'Either make them genuinely different or consolidate them.')))

        # verdict is one of PASS, PARTIAL, FAIL, NEUTRAL or unspecified.
        # Only FAIL means Google is definitely not serving this page; NEUTRAL
        # usually means it was excluded on purpose, and reporting that as an
        # error would flag every deliberately noindexed page on the site.
        state = (verdict or {}).get('verdict') or ''
        if state == 'PASS':
            continue
        if state == 'FAIL':
            severity, headline = 'error', 'Not Indexed by Google'
        elif state in ('PARTIAL', 'NEUTRAL'):
            severity, headline = 'warning', f'Google Reports This Page as {state.title()}'
        else:
            continue  # unspecified tells us nothing worth reporting
        found.append(make_issue(
            url=url, check='gsc_not_indexed', severity=severity, category=CATEGORY,
            issue=headline,
            details=(f'Search Console returned {state} for this page'
                     + (f': {coverage}' if coverage else '') + '.'),
            offending_value=f'{state}:{coverage}' if coverage else state,
            evidence={'found': f'{state}' + (f' - {coverage}' if coverage else ''),
                      'expected': 'PASS, submitted and indexed'},
            how_to_fix=('Work from the reason Search Console gives; it is usually a '
                        'canonical pointing elsewhere, a noindex, or a page Google '
                        'judged too similar to another.')))

    return found


def collect(credentials, site_url, pages, config=None, opener=None, today=None):
    """Fetch everything and return issues, inspecting worst-first within budget.

    A failure to reach Google returns the error rather than raising: a crawl
    should not fail because a third-party API is down, and a missing
    integration must never look like a clean bill of health.
    """
    cfg = _config(config)
    try:
        performance = fetch_pages(credentials, site_url, cfg, opener, today)
        queries = fetch_queries(credentials, site_url, cfg, opener, today)
    except GoogleAuthError as exc:
        return {'issues': [], 'error': str(exc)}

    # Spend the inspection budget where it is most likely to find something:
    # crawled pages with no impressions at all.
    candidates = [page.get('url') for page in pages or []
                  if page.get('url') and page['url'] not in performance]
    inspections = {}
    for url in candidates[:cfg['inspect_budget']]:
        try:
            inspections[url] = inspect_url(credentials, site_url, url, opener)
        except GoogleAuthError:
            break  # quota or permission; stop rather than hammer

    return {'issues': issues(pages, performance, inspections, queries,
                             site_url or '', cfg),
            'performance': performance, 'inspections': inspections, 'error': ''}
