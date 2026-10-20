"""GA4: which pages people actually use, joined to what the crawl found.

Search Console says what Google did with a page. Analytics says what happened
after someone arrived on it, and the two answer different questions. A page
can be indexed, ranking and still useless.

The findings here are the ones a crawl genuinely cannot reach:

  * a page nobody visited at all in the window
  * a page people land on and immediately leave
  * a page getting real traffic that the crawl could not find, which usually
    means it is linked from somewhere external and from nowhere internal

Deliberately few. Analytics will happily produce a hundred metrics, and most
of them are not defects - a low session count on a privacy policy is correct
behaviour, not a problem. Only the patterns that imply something is wrong
with the page or the site structure are reported.
"""

import urllib.parse
from datetime import date, timedelta

from src.core.google_auth import GoogleAuthError, call
from src.utils.issue_factory import make_issue

CATEGORY = 'Analytics'

RUN_REPORT = 'https://analyticsdata.googleapis.com/v1beta/properties/{property}:runReport'

DEFAULTS = {
    'days': 28,
    'row_limit': 5000,
    'min_sessions_for_bounce': 30,   # below this a bounce rate is noise
    'high_bounce_rate': 0.85,
    'max_examples': 20,
}


def _config(config=None):
    cfg = dict(DEFAULTS)
    cfg.update(config or {})
    return cfg


def fetch_pages(credentials, property_id, config=None, opener=None, today=None,
                origin=''):
    """Sessions, engagement and bounce rate per page path."""
    cfg = _config(config)
    today = today or date.today()
    body = {
        'dateRanges': [{'startDate': str(today - timedelta(days=cfg['days'])),
                        'endDate': str(today)}],
        'dimensions': [{'name': 'pagePath'}],
        'metrics': [{'name': 'sessions'}, {'name': 'engagedSessions'},
                    {'name': 'bounceRate'}, {'name': 'averageSessionDuration'}],
        'limit': cfg['row_limit'],
    }
    payload = call(credentials,
                   RUN_REPORT.format(property=urllib.parse.quote(str(property_id))),
                   method='POST', body=body, opener=opener)

    out = {}
    for row in (payload or {}).get('rows') or []:
        dimensions = row.get('dimensionValues') or []
        metrics = row.get('metricValues') or []
        if not dimensions or len(metrics) < 3:
            continue
        path = (dimensions[0] or {}).get('value') or ''
        if not path:
            continue

        def number(index, default=0.0):
            try:
                return float((metrics[index] or {}).get('value') or default)
            except (TypeError, ValueError, IndexError):
                return default

        out[origin.rstrip('/') + path if origin else path] = {
            'sessions': int(number(0)),
            'engaged_sessions': int(number(1)),
            'bounce_rate': number(2),
            'average_duration': number(3),
        }
    return out


def issues(pages, traffic, base_url='', config=None):
    """What Analytics knows that the crawl could not."""
    cfg = _config(config)
    traffic = traffic or {}
    found = []

    crawled = {}
    for page in pages or []:
        url = (page or {}).get('url')
        if url:
            crawled[url.rstrip('/') or url] = page

    def seen(url):
        return (url or '').rstrip('/') in crawled

    # Pages with real traffic that nothing on the site links to.
    unlinked = [url for url, row in traffic.items()
                if (row.get('sessions') or 0) > 0 and not seen(url)]
    if unlinked:
        found.append(make_issue(
            url=base_url, check='ga_page_not_in_crawl', severity='warning',
            category=CATEGORY, issue='Pages With Traffic the Crawl Did Not Find',
            details=(f'{len(unlinked)} page(s) received sessions but were not reachable '
                     f'from any link on the site.'),
            offending_value=str(len(unlinked)),
            evidence={'found': f'{len(unlinked)} pages',
                      'related_urls': sorted(unlinked)[:cfg['max_examples']]},
            how_to_fix=('Link to them, or confirm they are meant to be reachable only '
                        'from outside the site.')))

    # Pages nobody visited at all.
    if traffic:
        unvisited = [url for url in crawled
                     if not any(key.rstrip('/') == url for key in traffic)]
        if unvisited and len(unvisited) < len(crawled):
            found.append(make_issue(
                url=base_url, check='ga_no_sessions', severity='info',
                category=CATEGORY, issue='Pages Nobody Visited',
                details=(f'{len(unvisited)} crawled page(s) had no sessions in the last '
                         f'{cfg["days"]} days.'),
                offending_value=str(len(unvisited)),
                evidence={'found': f'{len(unvisited)} of {len(crawled)} pages',
                          'related_urls': sorted(unvisited)[:cfg['max_examples']]},
                how_to_fix=('Decide whether each still earns its place; an unvisited page '
                            'still costs crawl budget and dilutes internal linking.')))

    # Pages people arrive on and immediately leave.
    for url, row in sorted(traffic.items()):
        sessions = row.get('sessions') or 0
        bounce = row.get('bounce_rate') or 0.0
        if sessions < cfg['min_sessions_for_bounce'] or bounce < cfg['high_bounce_rate']:
            continue
        if not seen(url):
            continue  # already reported as not in the crawl
        found.append(make_issue(
            url=url, check='ga_high_bounce', severity='info', category=CATEGORY,
            issue='Visitors Leave Immediately',
            details=(f'{bounce * 100:.0f}% of {sessions} sessions bounced, against a '
                     f'threshold of {cfg["high_bounce_rate"] * 100:.0f}%.'),
            offending_value=f'{sessions}@{bounce:.3f}',
            evidence={'found': f'{bounce * 100:.0f}% over {sessions} sessions',
                      'expected': f'under {cfg["high_bounce_rate"] * 100:.0f}%'},
            how_to_fix=('Check the page delivers what the link promised; a high bounce '
                        'on an answer page can be fine, on a landing page it is not.')))
    return found


def collect(credentials, property_id, pages, base_url='', config=None, opener=None,
            today=None):
    """Fetch and map, returning the error rather than raising on failure."""
    cfg = _config(config)
    try:
        traffic = fetch_pages(credentials, property_id, cfg, opener, today,
                              origin=base_url)
    except GoogleAuthError as exc:
        return {'issues': [], 'error': str(exc)}
    return {'issues': issues(pages, traffic, base_url, cfg),
            'traffic': traffic, 'error': ''}
