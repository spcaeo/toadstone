"""Third-party authority metrics: Moz, Majestic and Ahrefs.

A crawl knows a site's internal shape perfectly and knows nothing about how
the rest of the web regards it. These three vendors sell that missing half,
and Screaming Frog's integration with them is one of the few capabilities it
has that nothing here matched.

The useful question is not "what is this page's score" — a number with no
context is a number nobody acts on. It is **which of your pages have earned
links and which have not**, set against the crawl: a page with real external
authority buried four clicks deep, or a page everything links to internally
that nothing links to externally. Those are findings. A raw Domain Authority
is trivia.

Each vendor needs a paid subscription, so this is configuration-driven in
exactly the way the Google integration is: no key, no calls, no pretending.
What the code *can* guarantee without a key is that the request shapes are
right and that the failure modes say something useful, and that is what the
tests cover.

One deliberate limitation stated plainly: metrics are fetched for the pages a
crawl already found, in batches, capped. Pulling authority data for every URL
of a 50,000-page crawl would cost more in API credits than most people want
to spend without being asked.
"""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

from src.utils.issue_factory import make_issue

CATEGORY = 'Authority'

MOZ_ENDPOINT = 'https://lsapi.seomoz.com/v2/url_metrics'
MAJESTIC_ENDPOINT = 'https://api.majestic.com/api/json'
AHREFS_ENDPOINT = 'https://api.ahrefs.com/v3/site-explorer/metrics'

# Vendors rate-limit by request, not by URL, so batching is the difference
# between one call and four hundred.
MOZ_BATCH = 50
MAJESTIC_BATCH = 100

# Default ceiling on how many of a crawl's pages get looked up at all.
DEFAULT_MAX_URLS = 200

# A page linked to this often internally ought to have some external interest.
WELL_LINKED_INTERNALLY = 10


class MetricsError(Exception):
    """A vendor could not be reached or refused, with a message for a person."""


def configured(config):
    """Which vendors this install can actually call."""
    config = config or {}
    ready = []
    if (config.get('moz_access_id') or '').strip() and \
            (config.get('moz_secret_key') or '').strip():
        ready.append('moz')
    if (config.get('majestic_api_key') or '').strip():
        ready.append('majestic')
    if (config.get('ahrefs_api_token') or '').strip():
        ready.append('ahrefs')
    return ready


def describe(config):
    """What a person should be told before any of this runs."""
    ready = configured(config)
    if not ready:
        return ('No link-metrics provider is configured, so authority checks '
                'are skipped. Moz, Majestic and Ahrefs each need a paid '
                'subscription.')
    return (f'Page URLs will be sent to {", ".join(ready)} to look up external '
            f'link metrics.')


def _post_json(url, payload, headers, opener=None, timeout=60):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', **headers})
    send = opener or urllib.request.urlopen
    try:
        with send(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise MetricsError(_explain(exc))
    except Exception as exc:
        raise MetricsError(f'Could not reach the provider ({type(exc).__name__}).')


def _get_json(url, headers=None, opener=None, timeout=60):
    request = urllib.request.Request(url, headers=headers or {})
    send = opener or urllib.request.urlopen
    try:
        with send(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise MetricsError(_explain(exc))
    except Exception as exc:
        raise MetricsError(f'Could not reach the provider ({type(exc).__name__}).')


def _explain(exc):
    """Vendor HTTP codes, in words that name the fix."""
    code = getattr(exc, 'code', 0)
    if code in (401, 403):
        return ('The provider rejected these credentials. Check the key and '
                'that the subscription is active.')
    if code == 429:
        return ('The provider is rate-limiting this account. Wait, or lower '
                'how many pages each crawl looks up.')
    if code == 402:
        return ('The provider says this account is out of quota for the '
                'period.')
    detail = ''
    try:
        detail = (exc.read() or b'').decode('utf-8', 'replace')[:160]
    except Exception:
        pass
    return f'The provider returned HTTP {code}.' + (f' {detail}' if detail else '')


# ---------------------------------------------------------------------------
# the vendors
# ---------------------------------------------------------------------------

def moz_metrics(urls, access_id, secret_key, opener=None):
    """Moz URL Metrics. Returns {url: {...}} for whatever came back."""
    out = {}
    token = base64.b64encode(f'{access_id}:{secret_key}'.encode()).decode('ascii')
    headers = {'Authorization': f'Basic {token}'}
    for start in range(0, len(urls), MOZ_BATCH):
        batch = urls[start:start + MOZ_BATCH]
        payload = _post_json(MOZ_ENDPOINT, {'targets': batch}, headers, opener)
        for row in (payload or {}).get('results') or []:
            page = row.get('page') or row.get('target') or ''
            if not page:
                continue
            out[page] = {
                'provider': 'moz',
                'page_authority': row.get('page_authority'),
                'domain_authority': row.get('domain_authority'),
                'external_links': row.get('external_pages_to_page'),
                'linking_domains': row.get('root_domains_to_page'),
                'spam_score': row.get('spam_score'),
            }
    return out


def majestic_metrics(urls, api_key, opener=None):
    """Majestic's bulk endpoint, which takes items as numbered parameters."""
    out = {}
    for start in range(0, len(urls), MAJESTIC_BATCH):
        batch = urls[start:start + MAJESTIC_BATCH]
        params = {'app_api_key': api_key, 'cmd': 'GetIndexItemInfo',
                  'items': str(len(batch)), 'datasource': 'fresh'}
        for index, url in enumerate(batch):
            params[f'item{index}'] = url
        payload = _get_json(MAJESTIC_ENDPOINT + '?' + urllib.parse.urlencode(params),
                            opener=opener)
        # Majestic answers HTTP 200 even when it refuses, putting the reason
        # in the body. Trusting the status code meant a wrong API key came
        # back as "no metrics found" - an integration that could not run,
        # looking exactly like a clean result. Confirmed against the live API:
        # a bad key returns 200 with Code=FailedRequestViaAPI.
        code = (payload or {}).get('Code')
        if code and code != 'OK':
            raise MetricsError(
                (payload.get('ErrorMessage') or f'Majestic refused the request ({code}).')
                + ' Check the Majestic API key.')
        tables = ((payload or {}).get('DataTables') or {})
        for row in (tables.get('Results') or {}).get('Data') or []:
            item = row.get('Item') or ''
            if not item:
                continue
            out[item] = {
                'provider': 'majestic',
                'trust_flow': row.get('TrustFlow'),
                'citation_flow': row.get('CitationFlow'),
                'external_links': row.get('ExtBackLinks'),
                'linking_domains': row.get('RefDomains'),
            }
    return out


def ahrefs_metrics(url, api_token, opener=None):
    """Ahrefs v3 metrics. One target per call, so callers pick their targets."""
    query = urllib.parse.urlencode({'target': url, 'mode': 'exact'})
    payload = _get_json(f'{AHREFS_ENDPOINT}?{query}',
                        headers={'Authorization': f'Bearer {api_token}',
                                 'Accept': 'application/json'},
                        opener=opener)
    metrics = (payload or {}).get('metrics') or {}
    if not metrics:
        return {}
    return {url: {
        'provider': 'ahrefs',
        'domain_rating': metrics.get('domain_rating'),
        'url_rating': metrics.get('url_rating'),
        'external_links': metrics.get('backlinks'),
        'linking_domains': metrics.get('refdomains'),
        'organic_traffic': metrics.get('org_traffic'),
    }}


def collect(pages, config, opener=None, max_urls=None):
    """Metrics for a crawl's pages, from whichever vendor is configured.

    Returns {'metrics': {...}, 'provider': name, 'error': ''}. A failure is
    reported rather than swallowed: an integration that could not run must
    never look like a clean result.
    """
    ready = configured(config)
    if not ready:
        return {'metrics': {}, 'provider': '', 'error': ''}

    cap = max_urls or int(config.get('link_metrics_max_urls') or DEFAULT_MAX_URLS)
    targets = [p.get('url') for p in (pages or [])
               if p.get('url') and str(p.get('status_code') or '') == '200'][:cap]
    if not targets:
        return {'metrics': {}, 'provider': ready[0], 'error': ''}

    provider = ready[0]
    try:
        if provider == 'moz':
            found = moz_metrics(targets, config['moz_access_id'],
                                config['moz_secret_key'], opener)
        elif provider == 'majestic':
            found = majestic_metrics(targets, config['majestic_api_key'], opener)
        else:
            found = {}
            for url in targets:
                found.update(ahrefs_metrics(url, config['ahrefs_api_token'], opener))
    except MetricsError as exc:
        return {'metrics': {}, 'provider': provider, 'error': str(exc)}
    return {'metrics': found, 'provider': provider, 'error': ''}


# ---------------------------------------------------------------------------
# the findings, which is the point of fetching any of this
# ---------------------------------------------------------------------------

def metric_issues(pages, outcome, emit=None):
    """Findings from joining authority data onto what the crawl found."""
    emit = emit or make_issue
    found = []
    metrics = (outcome or {}).get('metrics') or {}
    provider = (outcome or {}).get('provider') or 'the provider'

    if (outcome or {}).get('error'):
        found.append(emit(
            url='', check='link_metrics_unavailable', severity='info',
            category=CATEGORY, issue='Link Metrics Could Not Be Fetched',
            details=f'{provider} did not answer: {outcome["error"]} No authority '
                    f'judgements were made either way.',
            offending_value=str(outcome['error'])[:80],
            evidence={'found': str(outcome['error'])[:200]},
            how_to_fix='Check the provider credentials, or switch link metrics off.'))
        return found

    if not metrics:
        return found

    by_url = {p.get('url'): p for p in (pages or []) if p.get('url')}
    for url, row in metrics.items():
        page = by_url.get(url)
        if not page:
            continue
        external = _number(row.get('external_links'))
        domains = _number(row.get('linking_domains'))
        internal = _number(page.get('linked_from_count')
                           or len(page.get('linked_from') or []))
        depth = _number(page.get('depth'))

        # The finding that pays for the subscription: something out there
        # rates this page, and the site buries it.
        if domains >= 5 and depth >= 4:
            found.append(emit(
                url=url, check='authority_page_buried', severity='warning',
                category=CATEGORY, issue='Page With External Links Is Buried',
                details=f'{domains} other sites link to this page, but it is '
                        f'{depth} clicks from the homepage. Authority earned '
                        f'from outside is being spent on a page nobody inside '
                        f'the site points at.',
                offending_value=f'{domains} domains, depth {depth}',
                evidence={'found': f'{domains} linking domains at depth {depth}',
                          'expected': 'a shorter path from the homepage'},
                how_to_fix='Link to it from a section page or the homepage.'))

        # And its mirror: the site shouts about a page the web ignores.
        if internal >= WELL_LINKED_INTERNALLY and domains == 0 and external == 0:
            found.append(emit(
                url=url, check='no_external_interest', severity='info',
                category=CATEGORY, issue='Heavily Linked Internally, Ignored Outside',
                details=f'{internal} of your own pages link here and no other '
                        f'site does. That is not a fault in itself - it is what '
                        f'a pricing page looks like - but if this page was '
                        f'meant to attract links, it has not.',
                offending_value=f'{internal} internal, 0 external',
                evidence={'found': f'{internal} internal links, no external ones'},
                how_to_fix='Worth checking whether this page was meant to earn '
                           'links, or is purely navigational.'))

        spam = _number(row.get('spam_score'))
        if spam >= 30:
            found.append(emit(
                url=url, check='high_spam_score', severity='warning',
                category=CATEGORY, issue='Provider Reports A High Spam Score',
                details=f'{provider} scores this page {spam:.0f} for spam '
                        f'signals. That is the provider\'s own model, not a '
                        f'Google signal, and it is worth a look rather than '
                        f'a panic.',
                offending_value=f'spam score {spam:.0f}',
                evidence={'found': f'{spam:.0f}', 'expected': 'under 30'},
                how_to_fix='Review the page\'s backlink profile with the provider.'))
    return found


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
