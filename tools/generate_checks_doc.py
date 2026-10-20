"""Write docs/checks.md from a running install's own catalogue.

    python tools/generate_checks_doc.py --app http://127.0.0.1:5000

The check reference has to be generated, because a hand-kept list drifts from
the code the first time a check changes its wording - and a reference that
disagrees with the tool is worse than no reference, since it gets quoted.

Two things are deliberately left out of the published file:

* **per-install counts** (how many pages a check hit here), because they are a
  fact about one machine's crawls and not about the check;
* **the example text**, in full. The catalogue builds each example from a real
  finding on whatever this install happens to have crawled, so it carries
  hostnames, cookie names, file paths and stack traces belonging to somebody's
  site. One run of this carried a cookie name from a client's homepage
  straight into the file. The fix text is the part worth publishing; the
  example is not.
"""

import argparse
import json
import os
import re
import sys
import urllib.request
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'docs', 'checks.md')
SEVERITY_ORDER = {'error': 0, 'warning': 1, 'info': 2}
BADGE = {'error': '🔴 error', 'warning': '🟠 warning', 'info': '🔵 info'}


def fetch(app, path):
    with urllib.request.urlopen(app.rstrip('/') + path, timeout=30) as response:
        return json.loads(response.read().decode('utf-8'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', default='http://127.0.0.1:5000')
    ap.add_argument('--proven', help='a file of slugs observed firing, one per line '
                                     '(the coverage harness writes one)')
    args = ap.parse_args()

    checks = fetch(args.app, '/api/checks')['checks']
    proven = set()
    if args.proven and os.path.exists(args.proven):
        with open(args.proven) as fh:
            proven = {line.strip() for line in fh if line.strip()}

    by_category = defaultdict(list)
    for check in checks:
        by_category[check.get('category') or 'Other'].append(check)

    lines = [
        '# The check reference',
        '',
        'Every check Toadstone can report, with its slug, its severity and what to',
        'do about it. **This file is generated** from a running install\'s own',
        'catalogue (`tools/generate_checks_doc.py`), so it cannot drift from the',
        'wording the code actually uses.',
        '',
        'The slug is the stable part. It is what the API filters on, what a finding',
        'carries in its `check` field, and what `/checks#<slug>` links to in the',
        'running app. Names and wording may be improved; slugs are not renamed',
        'without a reason.',
        '',
    ]
    if proven:
        lines += [
            f'**{len(proven)} of these have been observed firing in a real crawl** of the',
            'deliberately broken fixture site in `tests/coverage_site.py`, across its',
            'several modes. Those are marked ✅. A check with no mark is not a check',
            'that does not work - it is one this fixture has no page for yet, and',
            'that is a gap in the fixture, recorded rather than hidden.',
            '',
        ]
    lines += ['---', '']

    total = 0
    for category in sorted(by_category):
        rows = sorted(by_category[category],
                      key=lambda c: (SEVERITY_ORDER.get(c.get('severity'), 9), c['check']))
        total += len(rows)
        lines.append(f'## {category}  <sub>{len(rows)} checks</sub>')
        lines.append('')
        lines.append('| | Check | Severity | What to do |')
        lines.append('|---|---|---|---|')
        for c in rows:
            mark = '✅' if c['check'] in proven else ''
            fix = ' '.join((c.get('how_to_fix') or '').split()) or '—'
            name = c.get('name') or c['check']
            cell = f'**{name}**<br><code>{c["check"]}</code>'
            lines.append(f'| {mark} | {cell} | {BADGE.get(c.get("severity"), c.get("severity"))} | {fix} |')
        lines.append('')

    known = {c['check'] for c in checks}
    extra = sorted(proven - known)
    if extra:
        lines += [
            f'## Observed firing, not described above  <sub>{len(extra)} checks</sub>',
            '',
            'These fired in the coverage sweep but have never been reported through',
            'the install this reference was generated from, so there is no wording',
            'to quote for them yet. They are listed rather than dropped, because a',
            'reference that silently omits a third of the checks is how a tool ends',
            'up with checks nobody knows exist.',
            '',
        ]
        for i in range(0, len(extra), 3):
            lines.append('- ' + ' · '.join(f'`{slug}`' for slug in extra[i:i + 3]))
        lines.append('')

    lines += [
        '---',
        '',
        f'*{total} checks described, {len(extra)} more observed firing, '
        f'{total + len(extra)} in total. Generated by',
        '`tools/generate_checks_doc.py`. Per-install counts and example text are',
        'deliberately omitted: both are facts about one machine\'s crawls rather',
        'than about the check.*',
        '',
    ]

    with open(OUT, 'w') as fh:
        fh.write('\n'.join(lines))
    print(f'{OUT}: {total} checks in {len(by_category)} categories, '
          f'{len(proven & {c["check"] for c in checks})} marked as proven')
    return 0


if __name__ == '__main__':
    sys.exit(main())
