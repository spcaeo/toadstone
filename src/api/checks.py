"""The check reference: what every check means, and how to fix it.

Each issue carries `docs_url` pointing at `/checks#<slug>`, so this page has to
exist for that link to be worth anything. It is built from what this install
has actually reported - name, severity, category, how many pages it has hit and
the fix text the check itself supplies - rather than from a hand-kept list that
would drift from the code the first time a check changed.

    GET /checks            the page
    GET /api/checks        the same as JSON
"""

import html
from collections import defaultdict

from flask import Blueprint, Response, jsonify, request

from src import crawl_db

SEVERITY_ORDER = {'error': 0, 'warning': 1, 'info': 2}


def create_blueprint(login_required):
    """Build the blueprint. `login_required` is main.py's own decorator."""
    api = Blueprint('checks', __name__)

    @api.route('/api/checks')
    @login_required
    def checks_json():
        return jsonify({'success': True, 'checks': catalogue(request.args.get('crawl_id', type=int))})

    @api.route('/checks')
    @login_required
    def checks_page():
        return Response(render(catalogue(request.args.get('crawl_id', type=int))),
                        mimetype='text/html')

    return api


def catalogue(crawl_id=None):
    """Every check this install has reported, newest wording wins.

    One pass over the issues table: a site with a million issues still only
    groups them, and the numbers are what make the page useful (you can see
    which checks actually fire here, and how often).
    """
    rows = []
    try:
        with crawl_db.get_db() as conn:
            query = ('SELECT check_slug, issue, type, category, details, how_to_fix, '
                     'url, crawl_id FROM crawl_issues WHERE check_slug IS NOT NULL')
            params = []
            if crawl_id:
                query += ' AND crawl_id = ?'
                params.append(crawl_id)
            rows = conn.execute(query, params).fetchall()
    except Exception as e:
        print(f"Could not read the check catalogue: {e}")
        return []

    grouped = defaultdict(lambda: {'pages': set(), 'crawls': set(), 'count': 0})
    for row in rows:
        entry = grouped[row['check_slug']]
        entry['count'] += 1
        entry['pages'].add(row['url'])
        entry['crawls'].add(row['crawl_id'])
        # Later rows win: the current wording of a check, not its first
        entry['name'] = row['issue'] or row['check_slug']
        entry['severity'] = row['type'] or 'info'
        entry['category'] = row['category'] or 'Other'
        entry['how_to_fix'] = row['how_to_fix'] or entry.get('how_to_fix', '')
        entry['example'] = row['details'] or entry.get('example', '')

    catalogue_rows = []
    for slug, entry in grouped.items():
        catalogue_rows.append({
            'check': slug,
            'name': entry.get('name', slug),
            'severity': entry.get('severity', 'info'),
            'category': entry.get('category', 'Other'),
            'how_to_fix': entry.get('how_to_fix', ''),
            'example': entry.get('example', ''),
            'reported': entry['count'],
            'pages': len(entry['pages']),
            'crawls': len(entry['crawls']),
        })

    catalogue_rows.sort(key=lambda row: (row['category'],
                                         SEVERITY_ORDER.get(row['severity'], 3),
                                         row['name']))
    return catalogue_rows


def render(rows):
    """A self-contained page: no stylesheet, no script, works from a saved copy."""
    by_category = defaultdict(list)
    for row in rows:
        by_category[row['category']].append(row)

    esc = html.escape
    sections = []
    for category in sorted(by_category):
        items = []
        for row in by_category[category]:
            items.append(f"""
            <article id="{esc(row['check'])}" class="check">
              <h3>{esc(row['name'])} <span class="sev sev-{esc(row['severity'])}">{esc(row['severity'])}</span></h3>
              <p class="slug"><code>{esc(row['check'])}</code></p>
              {f'<p class="fix">{esc(row["how_to_fix"])}</p>' if row['how_to_fix'] else ''}
              {f'<p class="example">Example: {esc(row["example"][:300])}</p>' if row['example'] else ''}
              <p class="stats">Reported {row['reported']} time(s) across {row['pages']} page(s)
                 in {row['crawls']} crawl(s).</p>
            </article>""")
        sections.append(f'<section><h2>{esc(category)}</h2>{"".join(items)}</section>')

    contents = ''.join(
        f'<li><a href="#{esc(row["check"])}">{esc(row["name"])}</a></li>' for row in rows)

    empty = ('<p class="empty">No checks have reported yet. Run a crawl and this page will '
             'list every check it used, what it means and how to fix it.</p>')

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Toadstone checks</title>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<style>
  :root {{ color-scheme: light dark; --bg:#fbfbfa; --fg:#16181d; --muted:#5c6370;
           --line:#dcdcd7; --card:#fff; --error:#b42318; --warning:#95450a; --info:#175cd3; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0d0f14; --fg:#e8eaed; --muted:#8f98a8; --line:#272c37; --card:#1a1e27;
             --error:#f97066; --warning:#fdb022; --info:#84caff; }} }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:14px/1.6 ui-monospace,
          "SF Mono",Menlo,Consolas,monospace; }}
  .wrap {{ max-width:900px; margin:0 auto; padding:32px 20px 80px; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .lede {{ color:var(--muted); margin:0 0 28px; }}
  h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.1em; color:var(--muted);
        border-bottom:1px solid var(--line); padding-bottom:6px; margin:36px 0 12px; }}
  .toc {{ columns:2; gap:24px; padding:0; margin:0 0 20px; list-style:none; }}
  .toc a {{ color:inherit; text-decoration:none; border-bottom:1px solid var(--line); }}
  .check {{ background:var(--card); border:1px solid var(--line); border-radius:2px;
            padding:14px 16px; margin:0 0 10px; }}
  .check h3 {{ font-size:14px; margin:0 0 4px; display:flex; gap:8px; align-items:center; }}
  .slug {{ margin:0 0 8px; color:var(--muted); }}
  .fix {{ margin:0 0 6px; }}
  .example, .stats {{ margin:0; color:var(--muted); font-size:12px; }}
  .sev {{ font-size:11px; text-transform:uppercase; letter-spacing:.08em;
          border:1px solid currentColor; border-radius:2px; padding:0 5px; }}
  .sev-error {{ color:var(--error); }} .sev-warning {{ color:var(--warning); }}
  .sev-info {{ color:var(--info); }}
  .empty {{ color:var(--muted); }}
  a.back {{ color:var(--muted); }}
</style></head>
<body><div class="wrap">
  <h1>Checks</h1>
  <p class="lede">What each check means, how to fix it, and how often it has fired here.
     <a class="back" href="/">Back to the crawler</a></p>
  {f'<ul class="toc">{contents}</ul>' if rows else empty}
  {''.join(sections)}
</div></body></html>"""
