"""Core Web Vitals as issues, from lab metrics and from field data.

This crawler already called PageSpeed Insights, stored the numbers and showed
them on a screen. Nothing ever judged them, so a page with a four-second
Largest Contentful Paint produced no finding at all - the data was fetched,
paid for in API quota, and left for a person to read. These checks close that.

Two sources, and the difference between them matters.

**Lab** metrics come from Lighthouse running the page once on Google's
hardware. They are reproducible and available for any URL, which makes them
good for catching a regression, and they are a simulation, which makes them a
poor description of what people actually experience.

**Field** data comes from CrUX: real Chrome users, 28-day rolling window. It
is the truth about a page's performance and it only exists for pages with
enough traffic to report on. A page with no field data is not a fast page and
not a slow one; it is a page nobody has measured, and the checks say so
rather than guessing from the lab number.

Thresholds are Google's published Core Web Vitals boundaries, not invented
here: https://web.dev/articles/defining-core-web-vitals-thresholds
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from src.utils.issue_factory import make_issue

CATEGORY = 'Performance'

CRUX_ENDPOINT = 'https://chromeuxreport.googleapis.com/v1/records:queryRecord'

# Google's thresholds: at or under `good` is good, at or under `poor` needs
# improvement, above `poor` is poor. Seconds, except CLS (unitless) and
# INP/FID (milliseconds).
THRESHOLDS = {
    'largest_contentful_paint': {'good': 2.5, 'poor': 4.0, 'unit': 's', 'name': 'Largest Contentful Paint'},
    'cumulative_layout_shift': {'good': 0.1, 'poor': 0.25, 'unit': '', 'name': 'Cumulative Layout Shift'},
    'first_input_delay': {'good': 200.0, 'poor': 500.0, 'unit': 'ms', 'name': 'Interaction to Next Paint'},
    'first_contentful_paint': {'good': 1.8, 'poor': 3.0, 'unit': 's', 'name': 'First Contentful Paint'},
    'speed_index': {'good': 3.4, 'poor': 5.8, 'unit': 's', 'name': 'Speed Index'},
    'time_to_interactive': {'good': 3.8, 'poor': 7.3, 'unit': 's', 'name': 'Time to Interactive'},
}

# Only the three Core Web Vitals get their own slug and an error severity.
# The rest are diagnostics: useful for explaining a bad vital, not worth
# failing a build over.
CORE_VITALS = ('largest_contentful_paint', 'cumulative_layout_shift', 'first_input_delay')

SLUGS = {
    'largest_contentful_paint': 'lcp',
    'cumulative_layout_shift': 'cls',
    'first_input_delay': 'inp',
}

DEFAULTS = {
    'min_performance_score': 50,   # below this the page is slow by any reading
    'strategy': 'mobile',          # what Google indexes with
    # A page can be fine on a laptop and unusable on a phone; that difference
    # is worth naming rather than averaging away.
    'divergence_score_points': 30,
    'crux_timeout': 20,
}


def _config(config=None):
    cfg = dict(DEFAULTS)
    cfg.update(config or {})
    return cfg


def _band(metric, value):
    """'good', 'needs-improvement' or 'poor' for one metric's value."""
    limits = THRESHOLDS.get(metric)
    if not limits or value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value <= limits['good']:
        return 'good'
    if value <= limits['poor']:
        return 'needs-improvement'
    return 'poor'


def _format(metric, value):
    limits = THRESHOLDS.get(metric, {})
    unit = limits.get('unit', '')
    if unit == 'ms':
        return f'{float(value):.0f}ms'
    if unit == 's':
        return f'{float(value):.2f}s'
    return f'{float(value):.3f}'


def vitals_issues(pagespeed_results, config=None):
    """Issues from the lab metrics this crawler already stores.

    `pagespeed_results` is the list the crawler keeps in
    `stats['pagespeed_results']`: one entry per page with a 'mobile' and a
    'desktop' block, each carrying 'metrics' and 'performance_score'.
    """
    cfg = _config(config)
    issues = []

    # A run where PageSpeed answered for nothing used to produce no issues
    # and no notice, so a quota-exhausted or keyless install read as "your
    # Core Web Vitals are fine". Every other integration here says when it
    # could not run - link metrics, the agents, Search Console - and this one
    # is the loudest candidate for a false clean bill, because performance is
    # exactly what someone switches it on to check.
    # "Answered" means numbers came back, not that a particular flag is set:
    # the success flag belongs to one caller's shape, and keying on it made
    # every fixture in the test suite look like a failed run.
    def _answered(entry):
        block = entry.get(cfg['strategy'])
        if not isinstance(block, dict):
            return False
        return bool(block.get('metrics')) or block.get('performance_score') is not None

    # Only when a call was actually made and came back empty-handed. An
    # entry with no strategy block at all is malformed input, not a refusal,
    # and saying "PageSpeed could not be fetched" about it would be inventing
    # a story about a request nobody sent.
    attempted = [e for e in pagespeed_results or []
                 if isinstance(e, dict) and isinstance(e.get(cfg['strategy']), dict)]
    answered = [e for e in attempted if _answered(e)]
    if attempted and not answered:
        first = attempted[0]
        reason = (first[cfg['strategy']].get('error')
                  or 'PageSpeed returned nothing usable')
        issues.append(make_issue(
            url=first.get('url', ''), check='pagespeed_unavailable',
            severity='info', category=CATEGORY,
            issue='PageSpeed Data Could Not Be Fetched',
            details=(f'PageSpeed Insights answered for none of the '
                     f'{len(pagespeed_results)} page(s) asked about: {reason}. '
                     f'No judgement was made about performance either way.'),
            offending_value=str(reason)[:80],
            evidence={'found': str(reason)[:200]},
            how_to_fix=('Add a Google API key in Settings, which raises the '
                        'daily quota well above what a crawl uses, or switch '
                        'PageSpeed analysis off.')))
        return issues

    for entry in pagespeed_results or []:
        if not isinstance(entry, dict):
            continue
        url = entry.get('url') or ''
        if not url:
            continue
        primary = entry.get(cfg['strategy']) or {}
        if not isinstance(primary, dict):
            continue
        metrics = primary.get('metrics') or {}
        if not isinstance(metrics, dict):
            continue

        for metric, value in metrics.items():
            band = _band(metric, value)
            if band in (None, 'good'):
                continue
            limits = THRESHOLDS[metric]
            is_core = metric in CORE_VITALS
            if not is_core and band != 'poor':
                continue  # diagnostics only speak up when they are bad
            slug = SLUGS.get(metric, metric)
            issues.append(make_issue(
                url=url,
                check=f'{slug}_poor' if band == 'poor' else f'{slug}_needs_improvement',
                severity='error' if (band == 'poor' and is_core) else 'warning',
                category=CATEGORY,
                issue=f'{limits["name"]} Is {"Poor" if band == "poor" else "Slow"}',
                details=(f'{limits["name"]} is {_format(metric, value)} on '
                         f'{cfg["strategy"]}, against a good threshold of '
                         f'{_format(metric, limits["good"])}.'),
                offending_value=f'{metric}={value}',
                evidence={'found': _format(metric, value),
                          'expected': f'at or under {_format(metric, limits["good"])}'},
                how_to_fix=_ADVICE.get(metric, 'Profile the page in Lighthouse and '
                                               'address the largest opportunity it lists.'),
            ))

        score = primary.get('performance_score')
        if isinstance(score, (int, float)) and score < cfg['min_performance_score']:
            issues.append(make_issue(
                url=url, check='performance_score_low', severity='warning',
                category=CATEGORY, issue='Low Performance Score',
                details=(f'Lighthouse scores this page {int(score)} out of 100 on '
                         f'{cfg["strategy"]}.'),
                offending_value=str(int(score)),
                evidence={'found': str(int(score)),
                          'expected': f'at least {cfg["min_performance_score"]}'},
                how_to_fix='Work through the opportunities Lighthouse lists, largest first.',
            ))

        other = entry.get('desktop' if cfg['strategy'] == 'mobile' else 'mobile') or {}
        issues.extend(_divergence(url, primary, other, cfg))
    return issues


def _divergence(url, primary, other, cfg):
    """A page that is fine on a laptop and unusable on a phone."""
    a, b = primary.get('performance_score'), other.get('performance_score')
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return []
    if b - a < cfg['divergence_score_points']:
        return []
    return [make_issue(
        url=url, check='mobile_desktop_divergence', severity='warning',
        category=CATEGORY, issue='Much Slower on Mobile Than Desktop',
        details=(f'This page scores {int(a)} on {cfg["strategy"]} but {int(b)} on the '
                 f'other form factor, a gap of {int(b - a)} points.'),
        offending_value=f'{int(a)}v{int(b)}',
        evidence={'found': f'{int(a)} vs {int(b)}',
                  'expected': f'within {cfg["divergence_score_points"]} points'},
        how_to_fix=('Test on a throttled mobile connection: the gap is usually '
                    'unoptimised images or JavaScript that a laptop absorbs.'),
    )]


_ADVICE = {
    'largest_contentful_paint': (
        'Find what paints largest - usually a hero image or a heading - and make it '
        'load first: no lazy-loading, a preload, and no render-blocking CSS above it.'),
    'cumulative_layout_shift': (
        'Give images, ads and embeds explicit width and height so the browser can '
        'reserve their space before they arrive.'),
    'first_input_delay': (
        'Break up long JavaScript tasks so the main thread is free to answer the '
        'first tap or click.'),
    'first_contentful_paint': (
        'Reduce render-blocking CSS and JavaScript in the document head.'),
    'speed_index': 'Get visible content painting sooner; defer what is below the fold.',
    'time_to_interactive': 'Ship less JavaScript, and defer what is not needed to render.',
}


# ---------------------------------------------------------------------------
# field data
# ---------------------------------------------------------------------------

def fetch_crux(url, api_key, timeout=20, opener=None):
    """Real-user Core Web Vitals for one URL, or a reason there are none.

    Returns {'metrics': {...}} on success, or {'error': str}. A URL with too
    little traffic returns a 404 from the API, which is not a failure: it
    means nobody has measured this page, and the caller must not treat that
    as a pass.
    """
    if not api_key:
        return {'error': 'no API key configured for field data'}
    body = json.dumps({'url': url, 'formFactor': 'PHONE'}).encode('utf-8')
    request = urllib.request.Request(
        f'{CRUX_ENDPOINT}?key={urllib.parse.quote(api_key)}',
        data=body, headers={'Content-Type': 'application/json'})
    try:
        send = opener or urllib.request.urlopen
        with send(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {'error': 'not enough real-user data for this URL'}
        return {'error': f'field data request failed: HTTP {exc.code}'}
    except Exception as exc:
        return {'error': f'field data request failed: {type(exc).__name__}'}

    record = (payload or {}).get('record') or {}
    out = {}
    for key, metric in (record.get('metrics') or {}).items():
        percentiles = (metric or {}).get('percentiles') or {}
        value = percentiles.get('p75')
        if value is not None:
            out[key] = value
    return {'metrics': out, 'period': (record.get('collectionPeriod') or {})}


# CrUX names its metrics differently from Lighthouse.
CRUX_METRICS = {
    'largest_contentful_paint': ('largest_contentful_paint', 1000.0),
    'cumulative_layout_shift': ('cumulative_layout_shift', 1.0),
    'interaction_to_next_paint': ('first_input_delay', 1.0),
    'first_contentful_paint': ('first_contentful_paint', 1000.0),
}


def field_issues(url, crux, config=None):
    """Issues from real-user data, which outranks the lab when both exist."""
    cfg = _config(config)
    crux = crux or {}
    if crux.get('error'):
        return []  # absence of field data is not a finding about the page
    issues = []
    for crux_name, (metric, divisor) in CRUX_METRICS.items():
        raw = (crux.get('metrics') or {}).get(crux_name)
        if raw is None:
            continue
        try:
            value = float(raw) / divisor
        except (TypeError, ValueError):
            continue
        band = _band(metric, value)
        if band in (None, 'good'):
            continue
        if metric not in CORE_VITALS and band != 'poor':
            continue
        limits = THRESHOLDS[metric]
        slug = SLUGS.get(metric, metric)
        issues.append(make_issue(
            url=url,
            check=f'field_{slug}_poor' if band == 'poor' else f'field_{slug}_needs_improvement',
            severity='error' if (band == 'poor' and metric in CORE_VITALS) else 'warning',
            category=CATEGORY,
            issue=f'{limits["name"]} Is {"Poor" if band == "poor" else "Slow"} For Real Users',
            details=(f'Real Chrome users see a 75th-percentile {limits["name"]} of '
                     f'{_format(metric, value)} on this page, against a good threshold '
                     f'of {_format(metric, limits["good"])}.'),
            offending_value=f'field:{metric}={value}',
            evidence={'found': _format(metric, value),
                      'expected': f'at or under {_format(metric, limits["good"])}'},
            how_to_fix=_ADVICE.get(metric, 'Profile the page and address the slowest step.'),
        ))
    return issues
