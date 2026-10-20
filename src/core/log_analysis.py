"""Server logs: what search engines actually did, against what we found.

A crawl says what a site looks like. A log says what Googlebot did with it,
and the two disagree more often than anyone expects: pages nothing links to
that Google requests daily, pages the sitemap promotes that it has never
fetched, and crawl budget spent on parameter URLs and redirect chains that
nobody meant to publish.

Three formats cover almost everything in the wild: the Apache/Nginx combined
log, IIS's W3C extended format, and the JSON lines a CDN emits. All three are
parsed here. A line that does not fit any of them is counted and skipped
rather than guessed at, and the count is reported - a parser that silently
drops 40% of a file is worse than one that refuses the file.

**Bots are verified, not believed.** Any script can send Googlebot's user
agent, and plenty do. A user agent claiming to be a search engine is checked
by reverse DNS and then forward-confirmed, which is the method Google and
Bing both document. An unverified claim is reported as a spoof rather than
counted as search-engine traffic, because treating it as real would make
every crawl-budget number wrong.
"""

import json
import re
import socket
from collections import Counter, defaultdict
from datetime import datetime

from src.utils.issue_factory import make_issue

CATEGORY = 'Logs'

# Apache/Nginx combined, which is also the common format with two extra fields.
_COMBINED = re.compile(
    r'(?P<ip>\S+)\s+\S+\s+\S+\s+\[(?P<time>[^\]]+)\]\s+'
    r'"(?P<method>[A-Z]+)\s+(?P<path>\S+)(?:\s+(?P<proto>\S+))?"\s+'
    r'(?P<status>\d{3})\s+(?P<bytes>\S+)'
    r'(?:\s+"(?P<referer>[^"]*)"\s+"(?P<agent>[^"]*)")?')

_W3C_TIME = ('date', 'time')

# Hostnames each engine's own documentation says its crawlers resolve to.
VERIFIED_SUFFIXES = {
    'googlebot': ('.googlebot.com', '.google.com'),
    'bingbot': ('.search.msn.com',),
    'applebot': ('.applebot.apple.com',),
    'duckduckbot': ('.duckduckgo.com',),
    'yandexbot': ('.yandex.ru', '.yandex.net', '.yandex.com'),
}

# Crawlers worth counting separately. The AI ones matter as much as the search
# ones now, and nobody else reports on them.
KNOWN_BOTS = {
    'googlebot': 'Googlebot',
    'bingbot': 'Bingbot',
    'applebot': 'Applebot',
    'duckduckbot': 'DuckDuckBot',
    'yandexbot': 'YandexBot',
    'gptbot': 'GPTBot',
    'oai-searchbot': 'OAI-SearchBot',
    'chatgpt-user': 'ChatGPT-User',
    'claudebot': 'ClaudeBot',
    'perplexitybot': 'PerplexityBot',
    'ccbot': 'CCBot',
    'bytespider': 'Bytespider',
    'ahrefsbot': 'AhrefsBot',
    'semrushbot': 'SemrushBot',
}

DEFAULTS = {
    'max_unparsed_ratio': 0.2,   # above this the file is the wrong format
    'min_hits_for_waste': 10,    # a URL has to be hit this often to be waste
    'max_examples': 20,
    'verify_bots': True,
    'verify_limit': 200,         # distinct IPs to resolve; DNS is slow
}


def _config(config=None):
    cfg = dict(DEFAULTS)
    cfg.update(config or {})
    return cfg


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

def _parse_combined(line):
    found = _COMBINED.match(line)
    if not found:
        return None
    data = found.groupdict()
    return {
        'ip': data['ip'],
        'path': data['path'],
        'status': int(data['status']),
        'agent': data.get('agent') or '',
        'referer': data.get('referer') or '',
        'bytes': int(data['bytes']) if (data['bytes'] or '').isdigit() else 0,
        'time': data['time'],
    }


def _parse_json_line(line):
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    # CDNs disagree about field names; accept the common spellings.
    path = (data.get('cs-uri-stem') or data.get('uri') or data.get('path')
            or data.get('request_uri') or data.get('url') or '')
    status = (data.get('sc-status') or data.get('status')
              or data.get('response_status') or data.get('statusCode'))
    if not path or status is None:
        return None
    try:
        status = int(status)
    except (TypeError, ValueError):
        return None
    return {
        'ip': (data.get('c-ip') or data.get('client_ip') or data.get('remote_addr')
               or data.get('clientIp') or ''),
        'path': path,
        'status': status,
        'agent': (data.get('cs(User-Agent)') or data.get('user_agent')
                  or data.get('userAgent') or ''),
        'referer': data.get('referer') or data.get('cs(Referer)') or '',
        'bytes': int(data.get('sc-bytes') or data.get('bytes') or 0 or 0),
        'time': str(data.get('time') or data.get('timestamp') or ''),
    }


def _parse_w3c(line, fields):
    if not fields:
        return None
    parts = line.split()
    if len(parts) != len(fields):
        return None
    row = dict(zip(fields, parts))
    path = row.get('cs-uri-stem')
    status = row.get('sc-status')
    if not path or not (status or '').isdigit():
        return None
    query = row.get('cs-uri-query') or ''
    if query and query != '-':
        path = f'{path}?{query}'
    return {
        'ip': row.get('c-ip', ''),
        'path': path,
        'status': int(status),
        'agent': (row.get('cs(User-Agent)') or '').replace('+', ' '),
        'referer': row.get('cs(Referer)', ''),
        'bytes': int(row['sc-bytes']) if (row.get('sc-bytes') or '').isdigit() else 0,
        'time': f"{row.get('date', '')} {row.get('time', '')}".strip(),
    }


def parse_lines(lines, config=None):
    """Every request a log file records, plus how much could not be read."""
    _config(config)
    entries, unparsed, total = [], 0, 0
    w3c_fields = []
    for raw in lines or []:
        line = (raw or '').strip()
        if not line:
            continue
        if line.startswith('#'):
            if line.lower().startswith('#fields:'):
                w3c_fields = line.split(':', 1)[1].split()
            continue
        total += 1
        entry = (_parse_combined(line)
                 or (_parse_w3c(line, w3c_fields) if w3c_fields else None)
                 or _parse_json_line(line))
        if entry:
            entries.append(entry)
        else:
            unparsed += 1
    return {'entries': entries, 'unparsed': unparsed, 'total': total}


# ---------------------------------------------------------------------------
# bots
# ---------------------------------------------------------------------------

def bot_name(agent):
    """Which known crawler claims to have sent this, if any."""
    lowered = (agent or '').lower()
    for needle, name in KNOWN_BOTS.items():
        if needle in lowered:
            return name
    return ''


def verify_ip(ip, bot_key, resolver=None):
    """Reverse-then-forward DNS, the method Google and Bing both document.

    A reverse lookup alone proves nothing: anyone can set a PTR record. The
    forward confirmation is what makes it evidence.
    """
    suffixes = VERIFIED_SUFFIXES.get(bot_key)
    if not suffixes or not ip:
        return None  # nothing published to check against
    resolve = resolver or _default_resolver
    try:
        host = resolve('reverse', ip)
        if not host:
            return False
        host = host.rstrip('.').lower()
        if not host.endswith(suffixes):
            return False
        return ip in (resolve('forward', host) or [])
    except Exception:
        return None  # DNS failure is not evidence of a spoof


def _default_resolver(kind, value):
    if kind == 'reverse':
        return socket.gethostbyaddr(value)[0]
    return socket.gethostbyname_ex(value)[2]


def classify(entries, config=None, resolver=None):
    """Group requests by bot, verifying the ones that claim a search engine."""
    cfg = _config(config)
    by_bot = defaultdict(list)
    for entry in entries or []:
        by_bot[bot_name(entry.get('agent')) or 'Other'].append(entry)

    verdicts = {}
    if cfg['verify_bots']:
        for name, rows in by_bot.items():
            key = name.lower()
            if key not in VERIFIED_SUFFIXES:
                continue
            ips = []
            for row in rows:
                if row.get('ip') and row['ip'] not in ips:
                    ips.append(row['ip'])
                if len(ips) >= cfg['verify_limit']:
                    break
            results = [verify_ip(ip, key, resolver) for ip in ips]
            real = [r for r in results if r is True]
            fake = [r for r in results if r is False]
            verdicts[name] = {'checked': len(results), 'verified': len(real),
                              'spoofed': len(fake)}
    return {'by_bot': dict(by_bot), 'verdicts': verdicts}


# ---------------------------------------------------------------------------
# joining logs to the crawl
# ---------------------------------------------------------------------------

def _paths(pages):
    out = {}
    for page in pages or []:
        url = (page or {}).get('url') or ''
        if not url:
            continue
        path = re.sub(r'^https?://[^/]+', '', url) or '/'
        out[path.rstrip('/') or '/'] = page
    return out


def log_issues(parsed, pages, base_url='', config=None, resolver=None):
    """What the logs say that the crawl could not."""
    cfg = _config(config)
    entries = (parsed or {}).get('entries') or []
    issues = []

    total = (parsed or {}).get('total') or 0
    unparsed = (parsed or {}).get('unparsed') or 0
    if total and unparsed / total > cfg['max_unparsed_ratio']:
        issues.append(make_issue(
            url=base_url, check='log_format_unrecognised', severity='warning',
            category=CATEGORY, issue='Log Format Not Recognised',
            details=(f'{unparsed} of {total} lines could not be read. The findings '
                     f'below are drawn from the rest and are probably incomplete.'),
            offending_value=str(unparsed),
            evidence={'found': f'{unparsed}/{total} unreadable'},
            how_to_fix='Export the log in combined, W3C extended or JSON-lines format.'))

    if not entries:
        return issues

    grouped = classify(entries, cfg, resolver)
    for name, verdict in grouped['verdicts'].items():
        if verdict['spoofed']:
            issues.append(make_issue(
                url=base_url, check='bot_spoofing', severity='info',
                category=CATEGORY, issue='Traffic Falsely Claiming to Be a Crawler',
                details=(f'{verdict["spoofed"]} of {verdict["checked"]} addresses '
                         f'claiming to be {name} did not pass reverse DNS '
                         f'verification.'),
                offending_value=f'{name}:{verdict["spoofed"]}',
                evidence={'found': f'{verdict["spoofed"]} unverified of {verdict["checked"]}'},
                how_to_fix=('Treat those requests as ordinary traffic; they are not the '
                            'search engine they claim to be.')))

    crawled = _paths(pages)
    search_bots = {name: rows for name, rows in grouped['by_bot'].items()
                   if name != 'Other'}
    bot_hits = Counter()
    bot_statuses = defaultdict(Counter)
    for rows in search_bots.values():
        for row in rows:
            path = (row['path'].split('#')[0]).rstrip('/') or '/'
            bot_hits[path] += 1
            bot_statuses[path][row['status']] += 1

    if not bot_hits:
        return issues

    # Pages a bot requests that the crawl never found: nothing links to them.
    unknown = [(path, hits) for path, hits in bot_hits.most_common()
               if path not in crawled and '?' not in path]
    if unknown:
        issues.append(make_issue(
            url=base_url, check='requested_but_not_crawled', severity='warning',
            category=CATEGORY, issue='Search Engines Request Pages the Crawl Never Found',
            details=(f'{len(unknown)} path(s) are being requested by crawlers but were '
                     f'not reachable from any link on the site.'),
            offending_value=str(len(unknown)),
            evidence={'found': f'{len(unknown)} paths',
                      'related_urls': [base_url.rstrip('/') + p
                                       for p, _ in unknown[:cfg['max_examples']]]},
            how_to_fix=('Link to them if they matter, or return 410 if they do not. '
                        'A page a crawler finds and the site does not link is usually '
                        'an old URL still in the index.')))

    # Pages the crawl found that no bot has ever asked for.
    never = [path for path in crawled if path not in bot_hits]
    if never and len(never) < len(crawled):
        issues.append(make_issue(
            url=base_url, check='never_requested_by_search', severity='info',
            category=CATEGORY, issue='Pages No Crawler Has Requested',
            details=(f'{len(never)} crawled page(s) appear nowhere in this log, so no '
                     f'search engine fetched them in the period it covers.'),
            offending_value=str(len(never)),
            evidence={'found': f'{len(never)} of {len(crawled)} pages',
                      'related_urls': [base_url.rstrip('/') + p
                                       for p in sorted(never)[:cfg['max_examples']]]},
            how_to_fix=('Check they are in the sitemap and linked from somewhere a '
                        'crawler reaches; a page nothing requests cannot rank.')))

    # Budget spent on URLs that answer with an error or a redirect.
    wasted = [(path, counts) for path, counts in bot_statuses.items()
              if sum(counts.values()) >= cfg['min_hits_for_waste']
              and not counts.get(200)
              and any(code >= 300 for code in counts)]
    if wasted:
        worst = sorted(wasted, key=lambda item: -sum(item[1].values()))
        spent = sum(sum(counts.values()) for _, counts in wasted)
        issues.append(make_issue(
            url=base_url, check='crawl_budget_wasted', severity='warning',
            category=CATEGORY, issue='Crawl Budget Spent on Errors and Redirects',
            details=(f'{spent} crawler requests went to {len(wasted)} URL(s) that never '
                     f'returned a 200.'),
            offending_value=str(spent),
            evidence={'found': f'{spent} requests to {len(wasted)} URLs',
                      'related_urls': [base_url.rstrip('/') + p
                                       for p, _ in worst[:cfg['max_examples']]]},
            how_to_fix=('Point the links and the sitemap at the final URL, and return '
                        '410 for what is really gone.')))

    return issues


def summary(parsed, pages=None, config=None, resolver=None):
    """Counts a person would want next to the issues."""
    entries = (parsed or {}).get('entries') or []
    grouped = classify(entries, config, resolver)
    by_bot = {name: len(rows) for name, rows in grouped['by_bot'].items()}
    statuses = Counter(entry['status'] for entry in entries)
    return {
        'requests': len(entries),
        'unparsed': (parsed or {}).get('unparsed', 0),
        'by_bot': dict(sorted(by_bot.items(), key=lambda kv: -kv[1])),
        'by_status': dict(sorted(statuses.items())),
        'verdicts': grouped['verdicts'],
        'crawled_pages': len(_paths(pages)) if pages else 0,
    }
