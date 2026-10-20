"""Accessibility findings from axe-core, turned into ordinary crawl issues.

Accessibility was the second-largest gap against the commercial crawlers: they
ship around ten accessibility checks each, and this one shipped none. Rather
than hand-write ten, this runs the engine the industry already agrees on and
reports whatever it finds, so the coverage is WCAG's rather than ours.

Two decisions worth stating.

axe is **vendored, not fetched**. A crawler that pulled it from a CDN would
fail on an install with no outbound network, and injecting a remote script
into a page under audit is the exact thing a strict Content-Security-Policy on
that page exists to prevent. Playwright adds the file's contents directly, so
neither problem arises.

Each axe rule becomes **its own check slug**, `a11y_<rule-id>`, rather than all
violations collapsing into one "accessibility" issue. A slug per rule is what
makes an issue addressable: it can be filtered, accepted with a reason, and
tracked across crawls by stable id like everything else here. It also means
the catalogue grows as axe's does, instead of freezing at whatever ten rules
seemed important the day this was written.

Running axe costs roughly 100-300ms per page, so it is off unless asked for.
"""

import json
import os

from src.utils.issue_factory import make_issue

CATEGORY = 'Accessibility'

AXE_VERSION = '4.10.2'
AXE_PATH = os.path.join(os.path.dirname(__file__), 'vendor', 'axe.min.js')

# WCAG 2.1 levels A and AA: what accessibility regulation generally cites, and
# what the other crawlers test. AAA is deliberately excluded - it is not the
# conformance target for most sites and reporting it would be noise.
DEFAULT_TAGS = ('wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa')

# axe grades every violation; this is how that grade becomes our severity.
IMPACT_SEVERITY = {
    'critical': 'error',
    'serious': 'error',
    'moderate': 'warning',
    'minor': 'info',
}

# How many offending elements travel with one issue. Enough to fix from,
# not so many that one bad template floods the evidence.
MAX_NODES = 5

DEFAULTS = {
    'enabled': False,          # costs 100-300ms a page, so it is opt-in
    'tags': list(DEFAULT_TAGS),
    'max_violations': 40,      # per page; a broken template can hit every rule
    'timeout_ms': 15000,
}


def _config(config=None):
    cfg = dict(DEFAULTS)
    cfg.update(config or {})
    return cfg


def axe_source():
    """The vendored engine, read once per call site.

    Returns an empty string when the file is missing rather than raising: a
    crawl should lose accessibility findings, not fall over, if an install is
    incomplete.
    """
    try:
        with open(AXE_PATH, encoding='utf-8') as handle:
            return handle.read()
    except OSError:
        return ''


async def collect(page, config=None):
    """Run axe against an already-navigated page. Returns plain data.

    Mirrors `rendered_checks.collect`: it observes, it never navigates or
    closes the page, and it returns data rather than issues so the result can
    be stored and re-checked later without a browser.
    """
    cfg = _config(config)
    if not cfg['enabled']:
        return {}
    source = axe_source()
    if not source:
        return {'error': f'axe-core is not installed at {AXE_PATH}'}

    try:
        await page.add_script_tag(content=source)
        raw = await page.evaluate(
            """async (tags) => {
                 if (!window.axe) return {error: 'axe did not load'};
                 const run = await window.axe.run(document, {
                   runOnly: {type: 'tag', values: tags},
                   resultTypes: ['violations'],
                   // The page under audit is not ours to mutate.
                   elementRef: false,
                 });
                 return {violations: (run.violations || []).map((v) => ({
                   id: v.id,
                   impact: v.impact,
                   help: v.help,
                   description: v.description,
                   help_url: v.helpUrl,
                   tags: v.tags,
                   nodes: (v.nodes || []).slice(0, 20).map((n) => ({
                     target: (n.target || []).join(' '),
                     html: (n.html || '').slice(0, 300),
                     summary: n.failureSummary || '',
                   })),
                 }))};
               }""",
            list(cfg['tags']),
        )
    except Exception as exc:
        return {'error': f'{type(exc).__name__}: {exc}'}

    if not isinstance(raw, dict):
        return {'error': 'axe returned something unreadable'}
    violations = raw.get('violations') or []
    return {
        'axe_version': AXE_VERSION,
        'tags': list(cfg['tags']),
        'violations': violations[:cfg['max_violations']],
        'error': raw.get('error', ''),
    }


def check(observations, url, config=None):
    """Turn stored axe output into issues. No browser needed."""
    cfg = _config(config)
    data = (observations or {}).get('accessibility') or {}
    if not isinstance(data, dict):
        return []

    if data.get('error'):
        return [make_issue(
            url=url, check='a11y_check_failed', severity='info', category=CATEGORY,
            issue='Accessibility Check Did Not Run',
            details=f'The accessibility engine could not run on this page: {data["error"]}.',
            offending_value=str(data['error'])[:80],
            evidence={'found': str(data['error'])[:300]},
            how_to_fix='Report this as a Toadstone bug if it happens on every page.',
        )]

    issues = []
    for violation in data.get('violations') or []:
        if not isinstance(violation, dict) or not violation.get('id'):
            continue
        nodes = [n for n in (violation.get('nodes') or []) if isinstance(n, dict)]
        count = len(nodes)
        where = nodes[0].get('target', '') if nodes else ''
        evidence = {
            'found': f'{count} element(s)' if count else 'reported by axe-core',
            'selector': '; '.join(n.get('target', '') for n in nodes[:MAX_NODES]),
            'snippet': nodes[0].get('html', '') if nodes else '',
        }
        issues.append(make_issue(
            url=url,
            check=f'a11y_{violation["id"]}',
            severity=IMPACT_SEVERITY.get(violation.get('impact'), 'warning'),
            category=CATEGORY,
            issue=violation.get('help') or violation['id'],
            details=(f'{violation.get("description") or violation.get("help") or violation["id"]} '
                     f'({count} element(s) on this page'
                     + (f', first at {where}' if where else '') + ').'),
            offending_value=where or violation['id'],
            evidence={k: v for k, v in evidence.items() if v},
            how_to_fix=(violation.get('help') or 'See the axe-core rule documentation.'),
            docs_url=violation.get('help_url') or '',
        ))
    return issues


def rule_catalogue():
    """Every rule axe would run, for the check reference page.

    Read out of the vendored file so the catalogue cannot drift from the
    engine actually in use.
    """
    source = axe_source()
    if not source:
        return []
    rules, seen = [], set()
    needle = '{id:"'
    index = source.find(needle)
    while index != -1 and len(rules) < 200:
        start = index + len(needle)
        end = source.find('"', start)
        if end == -1:
            break
        rule_id = source[start:end]
        if rule_id and rule_id not in seen and rule_id.replace('-', '').isalnum():
            seen.add(rule_id)
            rules.append(rule_id)
        index = source.find(needle, end)
    return sorted(rules)


def as_json(observations):
    """The axe block, serialisable for storage alongside other observations."""
    return json.dumps((observations or {}).get('accessibility') or {}, sort_keys=True)
