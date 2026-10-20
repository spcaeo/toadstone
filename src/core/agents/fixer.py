"""Hand a repository's issues to a coding agent, and record what came back.

`docs/agent-handoff.md` already describes doing this by hand: point another
Claude Code session at a crawl's issues and let it work. This automates the
handoff and nothing more.

The boundary is deliberate and it is the whole design. **This proposes; it
never commits, pushes, or touches a working tree.** It builds a brief, runs
an agent in a directory someone named, and stores what the agent says it did.
Applying a change to a repository is a decision with consequences that a
crawler has no business making on its own, and an agent that quietly rewrote
source because a check fired would be the worst feature in this project.

What it is good for: turning forty findings into one ordered brief that
names the files, groups the issues by cause rather than by page, and tells
the agent how to verify its own work.
"""

import json
from collections import defaultdict

from src.core.agents.provider import ProviderError, ask, configured

# Issues whose fix is in a repository. A slow server or a missing Search
# Console property is not something a coding agent can do anything about, and
# including them wastes the brief.
FIXABLE_PREFIXES = (
    'missing_', 'duplicate_', 'canonical_', 'hreflang_', 'heading_', 'empty_',
    'image_', 'a11y_', 'amp_', 'jsonld_', 'breadcrumb_', 'title_', 'description_',
    'meta_', 'multiple_h1', 'skipped_heading_level', 'thin_content',
    'no_structured_data', 'noindex', 'nofollow', 'pagination_', 'mixed_content',
    'insecure_form_action', 'spelling_mistake', 'grammar_mistake',
)

NOT_FIXABLE_IN_CODE = (
    'slow_', 'moderate_response', 'gsc_', 'ga_', 'log_', 'crawl_budget',
    'bot_spoofing', 'requested_but_not_crawled', 'never_requested',
    'dns_not_found', 'connection_', 'request_timeout', 'ssl_tls_error',
    'no_response', 'agent_',
)

DEFAULTS = {
    'max_issues': 60,
    'max_per_check': 5,     # examples; the brief says how many more there are
}


def _config(config=None):
    cfg = dict(DEFAULTS)
    cfg.update(config or {})
    return cfg


def fixable(issues):
    """The issues a coding agent could actually do something about."""
    out = []
    for issue in issues or []:
        if not isinstance(issue, dict):
            continue
        check = issue.get('check') or ''
        if check.startswith(NOT_FIXABLE_IN_CODE):
            continue
        if check.startswith(FIXABLE_PREFIXES):
            out.append(issue)
    return out


def brief(issues, repo_path='', crawl_id=None, base_url='', config=None):
    """A brief a coding agent can work from, grouped by cause.

    Grouped by check rather than by page on purpose: forty pages missing a
    canonical is one template to fix, and a brief listing forty pages invites
    forty separate edits.
    """
    cfg = _config(config)
    workable = fixable(issues)[:cfg['max_issues']]
    if not workable:
        return ''

    grouped = defaultdict(list)
    for issue in workable:
        grouped[issue['check']].append(issue)

    order = {'error': 0, 'warning': 1, 'info': 2}
    ranked = sorted(grouped.items(),
                    key=lambda kv: (order.get(kv[1][0].get('type'), 3), -len(kv[1])))

    lines = [
        f'You are fixing SEO and accessibility defects in the site at {base_url}.',
        f'They were found by a crawl (id {crawl_id}) of that site.',
        '',
        'Work in this repository: ' + (repo_path or '(the current directory)'),
        '',
        'Rules:',
        '  - Group the work by cause. Most of these come from one template or',
        '    component, not from one page each.',
        '  - Change the source that generates the markup, never the rendered output.',
        '  - Do not change a check\'s symptom without understanding what produced it.',
        '  - If a finding looks wrong, say so rather than working around it.',
        '',
        f'{len(workable)} issue(s) in {len(ranked)} group(s), worst first:',
        '',
    ]
    for check, rows in ranked:
        first = rows[0]
        lines.append(f'## {check} ({first.get("type", "warning")}) x{len(rows)}')
        lines.append(f'   {first.get("issue", "")}: {first.get("details", "")}')
        if first.get('how_to_fix'):
            lines.append(f'   Fix: {first["how_to_fix"]}')
        for row in rows[:cfg['max_per_check']]:
            evidence = row.get('evidence') or {}
            where = evidence.get('selector') or evidence.get('found') or ''
            lines.append(f'   - {row.get("url", "")}' + (f'  [{where}]'[:120] if where else ''))
        if len(rows) > cfg['max_per_check']:
            lines.append(f'   - ...and {len(rows) - cfg["max_per_check"]} more')
        lines.append('')

    lines += [
        'When you are done, report for each group: what you changed, which file,',
        'and how you confirmed it. If you could not fix something, say why.',
    ]
    return '\n'.join(lines)


def run(issues, repo_path='', crawl_id=None, base_url='', config=None, runner=None):
    """Give a coding agent the brief and keep what it reports.

    Returns a record, never a commit. The caller decides what to do with it.
    """
    cfg = _config(config)
    text = brief(issues, repo_path, crawl_id, base_url, cfg)
    if not text:
        return {'ran': False, 'reason': 'nothing here is fixable in a repository',
                'brief': '', 'report': ''}
    if runner is None and not configured(cfg):
        return {'ran': False, 'reason': 'no model configured', 'brief': text,
                'report': ''}
    try:
        report = ask(text, cfg, runner)
    except ProviderError as exc:
        return {'ran': False, 'reason': str(exc), 'brief': text, 'report': ''}
    return {'ran': True, 'reason': '', 'brief': text, 'report': report}


def as_json(record):
    """The record, serialisable for storage next to the crawl."""
    return json.dumps({k: v for k, v in (record or {}).items()}, sort_keys=True)
