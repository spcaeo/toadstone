"""One shape for every issue Toadstone reports.

Checks call `make_issue`, so an issue carries the same fields wherever it
surfaces (UI, API, export) and keeps a stable identity between crawls:

    id          sha1(url + check + offending value) - the same defect keeps
                the same id across crawls, so a fixed issue never comes back
                as "new" and an accepted one stays accepted
    check       machine-readable slug ('duplicate_content'), never free text
    type        'error' | 'warning' | 'info' (severity; named 'type' because
                that is what the UI and the database have always called it)
    category    'Content', 'Links', 'Sitemap', ...
    issue       the human name shown in the UI ('Duplicate Content Detected')
    details     one human sentence (kept for the existing UI and exports)
    evidence    what a developer needs to fix it without re-fetching the page:
                snippet, selector, line, expected, found, related_urls
    how_to_fix  one deterministic sentence
"""

import os
import hashlib

SEVERITIES = ('error', 'warning', 'info')

EVIDENCE_FIELDS = ('snippet', 'selector', 'line', 'expected', 'found', 'related_urls')


def issue_id(url, check, offending_value=''):
    """Stable identity for one defect on one page.

    The offending value keeps two instances of the same check on one page
    apart (two headings with stray spaces, two broken images).
    """
    raw = f'{url}\x1f{check}\x1f{offending_value}'
    return hashlib.sha1(raw.encode('utf-8', 'replace')).hexdigest()


# Set TOADSTONE_SLUG_LOG to a path and every check slug that actually fires
# is appended to it. Off by default and costs nothing when unset. It exists
# because "which of our checks have ever run against a real page?" could only
# be answered by grepping for string literals, and most slugs are built from
# tables rather than written out - so the grep undercounted and nobody could
# tell which checks were genuinely exercised.
_SLUG_LOG = (os.environ.get('TOADSTONE_SLUG_LOG')
             or os.environ.get('LIBRECRAWL_SLUG_LOG', ''))


def _record_slug(check):
    try:
        with open(_SLUG_LOG, 'a', encoding='utf-8') as handle:
            handle.write(f'{check}\n')
    except OSError:
        pass


def make_issue(url, check, severity, category, issue, details,
               offending_value='', evidence=None, how_to_fix='', docs_url=''):
    """Build one issue. See the module docstring for the field contract."""
    if _SLUG_LOG:
        _record_slug(check)
    if severity not in SEVERITIES:
        raise ValueError(f'severity must be one of {SEVERITIES}, got {severity!r}')

    clean_evidence = {}
    for key, value in (evidence or {}).items():
        if key not in EVIDENCE_FIELDS:
            raise ValueError(f'unknown evidence field {key!r}; use one of {EVIDENCE_FIELDS}')
        if value not in (None, '', []):
            clean_evidence[key] = value

    return {
        'id': issue_id(url, check, offending_value),
        'url': url,
        'check': check,
        # Every check explains itself at /checks; a caller may override with
        # its own documentation.
        'docs_url': docs_url or f'/checks#{check}',
        'type': severity,
        'category': category,
        'issue': issue,
        'details': details,
        'evidence': clean_evidence,
        'how_to_fix': how_to_fix,
    }
