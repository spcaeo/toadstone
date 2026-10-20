"""The single-file summary a human can send on.

Self-contained: no remote CSS, no external asset, nothing to fetch. Every
value that came from a crawled page is escaped on the way in - the input is
somebody else's HTML, and the reader opens this in a browser.
"""

from datetime import datetime
from html import escape

from src import crawl_db
from src.api.exports.datasets import load_issues

SEVERITIES = ('error', 'warning', 'info')
TOP_CHECKS = 15
WORST_PAGES = 15


def summarise(crawl_id, crawl, baseline_id=None):
    """Everything both report formats render, gathered once."""
    issues = [row for row in load_issues(crawl_id, crawl)
              if row.get('status') != 'accepted']
    pages = crawl_db.load_crawled_urls(crawl_id)

    by_severity = {severity: 0 for severity in SEVERITIES}
    by_check = {}
    per_page = {}
    for issue in issues:
        severity = issue.get('type') or 'info'
        by_severity[severity] = by_severity.get(severity, 0) + 1

        key = issue.get('check') or issue.get('issue') or 'unknown'
        entry = by_check.setdefault(key, {'check': key, 'label': issue.get('issue') or key,
                                          'count': 0, 'severity': severity})
        entry['count'] += 1

        url = issue.get('url') or ''
        counts = per_page.setdefault(url, {'url': url, 'error': 0, 'warning': 0,
                                           'info': 0, 'total': 0})
        counts[severity] = counts.get(severity, 0) + 1
        counts['total'] += 1

    titles = {page.get('url'): page.get('title') for page in pages}
    worst = sorted(per_page.values(),
                   key=lambda c: (-(c['error'] * 3 + c['warning']), c['url']))[:WORST_PAGES]
    for entry in worst:
        entry['title'] = titles.get(entry['url']) or ''

    checks = sorted(by_check.values(), key=lambda c: (-c['count'], c['check']))[:TOP_CHECKS]

    coverage = crawl.get('sitemap_coverage') or {}
    coverage = coverage if isinstance(coverage, dict) else {}
    sitemap_files = crawl.get('sitemap_files') or []

    return {
        'crawl_id': crawl_id,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'base_url': crawl.get('base_url') or '',
        'base_domain': crawl.get('base_domain') or '',
        'status': crawl.get('status') or '',
        'started_at': crawl.get('started_at') or '',
        'completed_at': crawl.get('completed_at') or '',
        'urls_discovered': crawl.get('urls_discovered') or 0,
        'urls_crawled': crawl.get('urls_crawled') or len(pages),
        'max_depth': crawl.get('max_depth_reached') or 0,
        'pages': len(pages),
        'issue_total': len(issues),
        'by_severity': by_severity,
        'checks': checks,
        'worst_pages': worst,
        'sitemap': {
            'files': len(sitemap_files) if isinstance(sitemap_files, list) else 0,
            'in_both': len(coverage.get('in_both') or []),
            'crawled_only': len(coverage.get('crawled_only') or []),
            'sitemap_only': len(coverage.get('sitemap_only') or []),
        },
        'diff': _baseline_diff(crawl_id, crawl, baseline_id),
    }


def _baseline_diff(crawl_id, crawl, baseline_id=None):
    """What changed against the previous completed crawl, when there is one."""
    from src.core import crawl_diff

    if not baseline_id:
        previous = crawl_diff.previous_crawl(crawl.get('base_domain'), crawl_id,
                                             user_id=crawl.get('user_id'))
        baseline_id = previous.get('id') if previous else None
    if not baseline_id:
        return None

    report = crawl_diff.regression_report(baseline_id, crawl_id)
    return {
        'baseline': baseline_id,
        'counts': report.get('counts') or {},
        'new': [_diff_row(issue) for issue in report.get('new') or []][:TOP_CHECKS],
        'fixed': [_diff_row(issue) for issue in report.get('fixed') or []][:TOP_CHECKS],
    }


def _diff_row(issue):
    return {'url': issue.get('url') or '', 'check': issue.get('check') or '',
            'type': issue.get('type') or '', 'issue': issue.get('issue') or ''}


_REPORT_CSS = """
:root { color-scheme: light dark; --ink:#16181d; --muted:#5b6270; --line:#e3e6ec;
        --bg:#ffffff; --panel:#f6f7f9; --error:#c0392b; --warning:#b7791f; --info:#2b6cb0; }
@media (prefers-color-scheme: dark) {
  :root { --ink:#e6e8ec; --muted:#9aa2b1; --line:#2c313a; --bg:#15171c;
          --panel:#1d2026; --error:#ff7b6b; --warning:#e3b341; --info:#79b8ff; } }
* { box-sizing: border-box; }
body { margin:0; padding:32px 16px 64px; background:var(--bg); color:var(--ink);
       font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
main { max-width: 960px; margin: 0 auto; }
h1 { font-size: 26px; margin: 0 0 4px; letter-spacing:-0.01em; }
h2 { font-size: 17px; margin: 36px 0 10px; letter-spacing:-0.01em; }
.sub { color: var(--muted); margin: 0 0 24px; font-size: 13px; }
.cards { display:flex; flex-wrap:wrap; gap:10px; margin: 0 0 8px; }
.card { flex:1 1 130px; background:var(--panel); border:1px solid var(--line);
        border-radius:10px; padding:12px 14px; }
.card .n { font-size:24px; font-weight:650; letter-spacing:-0.02em; }
.card .k { color:var(--muted); font-size:12px; text-transform:uppercase;
           letter-spacing:.06em; }
table { width:100%; border-collapse:collapse; font-size:13.5px; }
th, td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line);
         vertical-align:top; }
th { color:var(--muted); font-weight:600; font-size:11.5px; text-transform:uppercase;
     letter-spacing:.06em; }
td.n, th.n { text-align:right; font-variant-numeric: tabular-nums; }
.url { word-break:break-all; }
.tag { font-size:11px; font-weight:650; text-transform:uppercase; letter-spacing:.05em; }
.error { color:var(--error); } .warning { color:var(--warning); } .info { color:var(--info); }
.empty { color:var(--muted); font-style:italic; }
dl { display:grid; grid-template-columns:max-content 1fr; gap:4px 18px; margin:0; font-size:13.5px; }
dt { color:var(--muted); } dd { margin:0; word-break:break-all; }
@media (max-width:520px) { dl { grid-template-columns:1fr; } dd { margin-bottom:8px; } }
"""


def report_html(summary):
    """A self-contained page. Every crawled value goes through escape()."""
    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           f'<title>Crawl {summary["crawl_id"]} report - '
           f'{escape(summary["base_domain"] or summary["base_url"])}</title>',
           f'<style>{_REPORT_CSS}</style></head><body><main>']

    out.append(f'<h1>{escape(summary["base_domain"] or summary["base_url"])}</h1>')
    out.append(f'<p class="sub">Crawl {summary["crawl_id"]} &middot; report generated '
               f'{escape(summary["generated_at"])}</p>')

    out.append('<dl>')
    for label, value in (('Start URL', summary['base_url']),
                         ('Status', summary['status']),
                         ('Started', summary['started_at']),
                         ('Completed', summary['completed_at'])):
        out.append(f'<dt>{escape(label)}</dt><dd>{escape(str(value))}</dd>')
    out.append('</dl>')

    counts = summary['by_severity']
    out.append('<h2>At a glance</h2><div class="cards">')
    for label, number, css in (('Pages crawled', summary['urls_crawled'], ''),
                               ('URLs discovered', summary['urls_discovered'], ''),
                               ('Max depth', summary['max_depth'], ''),
                               ('Errors', counts.get('error', 0), 'error'),
                               ('Warnings', counts.get('warning', 0), 'warning'),
                               ('Info', counts.get('info', 0), 'info')):
        out.append(f'<div class="card"><div class="n {css}">{escape(str(number))}</div>'
                   f'<div class="k">{escape(label)}</div></div>')
    out.append('</div>')

    out.append('<h2>Most frequent checks</h2>')
    out.append(_html_table(('Check', 'Severity', 'Count'),
                           [(f'<span class="url">{escape(row["label"])}</span>',
                             f'<span class="tag {escape(row["severity"])}">'
                             f'{escape(row["severity"])}</span>',
                             ('n', escape(str(row['count']))))
                            for row in summary['checks']],
                           'No issues were reported.'))

    out.append('<h2>Worst pages</h2>')
    out.append(_html_table(('Page', 'Errors', 'Warnings', 'Total'),
                           [(f'<div class="url">{escape(row["url"])}</div>'
                             + (f'<div class="sub" style="margin:0">{escape(row["title"])}</div>'
                                if row['title'] else ''),
                             ('n', f'<span class="error">{row["error"]}</span>'),
                             ('n', f'<span class="warning">{row["warning"]}</span>'),
                             ('n', str(row['total'])))
                            for row in summary['worst_pages']],
                           'No page carried an issue.'))

    sitemap = summary['sitemap']
    out.append('<h2>Sitemap coverage</h2>')
    if sitemap['files']:
        out.append(_html_table(('Measure', 'URLs'),
                               [('Sitemap files read', ('n', str(sitemap['files']))),
                                ('Listed and crawled', ('n', str(sitemap['in_both']))),
                                ('Crawled but not listed', ('n', str(sitemap['crawled_only']))),
                                ('Listed but not crawled', ('n', str(sitemap['sitemap_only'])))],
                               ''))
    else:
        out.append('<p class="empty">No sitemap was audited for this crawl.</p>')

    diff = summary['diff']
    out.append('<h2>Since the previous crawl</h2>')
    if not diff:
        out.append('<p class="empty">No earlier completed crawl of this site to '
                   'compare against.</p>')
    else:
        counts = diff['counts']
        out.append(f'<p class="sub">Compared against crawl {diff["baseline"]}: '
                   f'{counts.get("new", 0)} new, {counts.get("fixed", 0)} fixed, '
                   f'{counts.get("still_open", 0)} still open.</p>')
        out.append('<h2>New issues</h2>')
        out.append(_html_table(('Page', 'Check', 'Severity'),
                               [(f'<span class="url">{escape(row["url"])}</span>',
                                 escape(row['issue'] or row['check']),
                                 f'<span class="tag {escape(row["type"])}">'
                                 f'{escape(row["type"])}</span>')
                                for row in diff['new']],
                               'Nothing new.'))
        out.append('<h2>Fixed since then</h2>')
        out.append(_html_table(('Page', 'Check', 'Severity'),
                               [(f'<span class="url">{escape(row["url"])}</span>',
                                 escape(row['issue'] or row['check']),
                                 f'<span class="tag {escape(row["type"])}">'
                                 f'{escape(row["type"])}</span>')
                                for row in diff['fixed']],
                               'Nothing was fixed.'))

    out.append('</main></body></html>')
    return ''.join(out)


def _html_table(headers, rows, empty_text):
    """Rows hold already-escaped cells; a ('n', cell) tuple right-aligns one."""
    if not rows:
        return f'<p class="empty">{escape(empty_text)}</p>' if empty_text else ''
    head = ''.join(f'<th>{escape(header)}</th>' for header in headers)
    body = []
    for row in rows:
        cells = []
        for cell in row:
            if isinstance(cell, tuple):
                cells.append(f'<td class="{cell[0]}">{cell[1]}</td>')
            else:
                cells.append(f'<td>{cell}</td>')
        body.append('<tr>' + ''.join(cells) + '</tr>')
    return f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def _md_cell(value):
    """Keep a crawled value inside its table cell: no pipes, no newlines."""
    text = str(value if value is not None else '')
    return text.replace('|', '\\|').replace('\r', ' ').replace('\n', ' ').strip()


def _md_table(headers, rows, empty_text):
    if not rows:
        return f'_{empty_text}_\n' if empty_text else ''
    lines = ['| ' + ' | '.join(headers) + ' |',
             '| ' + ' | '.join('---' for _ in headers) + ' |']
    for row in rows:
        lines.append('| ' + ' | '.join(_md_cell(cell) for cell in row) + ' |')
    return '\n'.join(lines) + '\n'


def report_markdown(summary):
    counts = summary['by_severity']
    sitemap = summary['sitemap']
    out = [f'# {_md_cell(summary["base_domain"] or summary["base_url"])}',
           '',
           f'Crawl {summary["crawl_id"]} - report generated {summary["generated_at"]}',
           '',
           _md_table(('Field', 'Value'),
                     [('Start URL', summary['base_url']),
                      ('Status', summary['status']),
                      ('Started', summary['started_at']),
                      ('Completed', summary['completed_at']),
                      ('Pages crawled', summary['urls_crawled']),
                      ('URLs discovered', summary['urls_discovered']),
                      ('Max depth', summary['max_depth'])], ''),
           '',
           '## Issues by severity',
           '',
           _md_table(('Severity', 'Count'),
                     [('Errors', counts.get('error', 0)),
                      ('Warnings', counts.get('warning', 0)),
                      ('Info', counts.get('info', 0)),
                      ('Total', summary['issue_total'])], ''),
           '',
           '## Most frequent checks',
           '',
           _md_table(('Check', 'Severity', 'Count'),
                     [(row['label'], row['severity'], row['count'])
                      for row in summary['checks']], 'No issues were reported.'),
           '',
           '## Worst pages',
           '',
           _md_table(('Page', 'Title', 'Errors', 'Warnings', 'Total'),
                     [(row['url'], row['title'], row['error'], row['warning'], row['total'])
                      for row in summary['worst_pages']], 'No page carried an issue.'),
           '',
           '## Sitemap coverage',
           '']

    if sitemap['files']:
        out.append(_md_table(('Measure', 'URLs'),
                             [('Sitemap files read', sitemap['files']),
                              ('Listed and crawled', sitemap['in_both']),
                              ('Crawled but not listed', sitemap['crawled_only']),
                              ('Listed but not crawled', sitemap['sitemap_only'])], ''))
    else:
        out.append('_No sitemap was audited for this crawl._')

    out += ['', '## Since the previous crawl', '']
    diff = summary['diff']
    if not diff:
        out.append('_No earlier completed crawl of this site to compare against._')
    else:
        counts = diff['counts']
        out += [f'Compared against crawl {diff["baseline"]}: {counts.get("new", 0)} new, '
                f'{counts.get("fixed", 0)} fixed, {counts.get("still_open", 0)} still open.',
                '', '### New issues', '',
                _md_table(('Page', 'Check', 'Severity'),
                          [(row['url'], row['issue'] or row['check'], row['type'])
                           for row in diff['new']], 'Nothing new.'),
                '', '### Fixed since then', '',
                _md_table(('Page', 'Check', 'Severity'),
                          [(row['url'], row['issue'] or row['check'], row['type'])
                           for row in diff['fixed']], 'Nothing was fixed.')]

    return '\n'.join(out) + '\n'
