"""Build the static demo published at spcaeo.github.io/toadstone.

    python main.py --local --port 5058 &        # a clean install
    # crawl a site you own through the UI, then:
    python tools/build_demo_site.py --app http://127.0.0.1:5058 --crawl 1

It pulls a finished crawl out of the API and writes a self-contained page to
demo-site/ — one HTML file and one JSON file, no server, no build step. What a
visitor gets is the real findings from a real crawl, clickable, with no
sign-up and nothing to install.

What it deliberately does NOT export: page bodies, link graphs, anything that
would make the payload megabytes, and anything about an install's own
configuration. A demo is a sample of the output, not a copy of the database.
"""

import argparse
import html
import json
import os
import sys
import urllib.request
from collections import Counter, defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'demo-site')
PAGE_FIELDS = ('url', 'status_code', 'title', 'meta_description', 'h1',
               'word_count', 'depth', 'canonical_url')
ISSUE_FIELDS = ('id', 'check', 'type', 'category', 'issue', 'url', 'details',
                'evidence', 'how_to_fix')


def fetch(app, path):
    with urllib.request.urlopen(app.rstrip('/') + path, timeout=60) as r:
        return json.loads(r.read().decode('utf-8'))


def page_all(app, crawl, kind, key, limit=500):
    rows, offset = [], 0
    while True:
        body = fetch(app, f'/api/crawls/{crawl}/{kind}?limit={limit}&offset={offset}')
        batch = body.get(key) or []
        rows += batch
        offset += len(batch)
        if not batch or offset >= body.get('total', 0):
            return rows


def build(app, crawl, site_label):
    pages = page_all(app, crawl, 'pages', 'pages')
    issues = page_all(app, crawl, 'issues', 'issues')
    pages = [{k: p.get(k) for k in PAGE_FIELDS} for p in pages]
    issues = [{k: i.get(k) for k in ISSUE_FIELDS} for i in issues]

    by_check = defaultdict(list)
    for i in issues:
        by_check[i['check']].append(i)
    groups = sorted(by_check.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    severity = Counter(i['type'] for i in issues)
    codes = Counter(p['status_code'] for p in pages)

    data = {
        'site': site_label,
        'pages': pages,
        'issues': issues,
        'summary': {
            'pages': len(pages),
            'issues': len(issues),
            'checks_fired': len(by_check),
            'errors': severity.get('error', 0),
            'warnings': severity.get('warning', 0),
            'info': severity.get('info', 0),
            'status_codes': dict(sorted(codes.items())),
        },
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'data.json'), 'w') as fh:
        json.dump(data, fh, separators=(',', ':'))

    with open(os.path.join(OUT, 'index.html'), 'w') as fh:
        fh.write(render(data, groups))

    size = sum(os.path.getsize(os.path.join(OUT, f)) for f in os.listdir(OUT))
    print(f'{OUT}: {len(pages)} pages, {len(issues)} findings across '
          f'{len(by_check)} checks, {size // 1024}KB total')
    return 0


def esc(v):
    return html.escape(str(v if v is not None else ''), quote=True)


def render(data, groups):
    s = data['summary']
    rows = []
    for check, items in groups:
        first = items[0]
        badge = {'error': 'e', 'warning': 'w', 'info': 'i'}.get(first['type'], 'i')
        detail_rows = ''.join(
            f'''<tr class="d"><td colspan="4">
                 <div class="u"><a href="{esc(i['url'])}" target="_blank" rel="noopener nofollow">{esc(i['url'])}</a></div>
                 <div class="t">{esc(i['details'])}</div>
                 {evidence_html(i.get('evidence'))}
                 <div class="f"><b>Fix</b> {esc(i['how_to_fix'])}</div>
               </td></tr>'''
            for i in items)
        rows.append(f'''
          <tbody class="g" data-check="{esc(check)}" data-sev="{esc(first['type'])}">
            <tr class="h" tabindex="0">
              <td class="c"><span class="chev">›</span></td>
              <td><span class="b {badge}">{esc(first['type'])}</span>
                  <b>{esc(first['issue'])}</b> <code>{esc(check)}</code></td>
              <td class="cat">{esc(first['category'])}</td>
              <td class="n">{len(items)}</td>
            </tr>{detail_rows}
          </tbody>''')

    page_rows = ''.join(
        f'''<tr><td class="n">{esc(p['status_code'])}</td>
             <td class="url"><a href="{esc(p['url'])}" target="_blank" rel="noopener nofollow">{esc(p['url'])}</a></td>
             <td>{esc(p['title'])}</td><td class="n">{esc(p['word_count'])}</td>
             <td class="n">{esc(p['depth'])}</td></tr>'''
        for p in sorted(data['pages'], key=lambda p: p['url'])[:200])

    return TEMPLATE.format(
        site=esc(data['site']),
        pages=s['pages'], issues=s['issues'], checks=s['checks_fired'],
        errors=s['errors'], warnings=s['warnings'], info=s['info'],
        groups=''.join(rows), page_rows=page_rows,
        shown=min(200, s['pages']),
    )


def evidence_html(ev):
    if not isinstance(ev, dict):
        return ''
    bits = []
    for k in ('found', 'expected', 'selector', 'snippet', 'line'):
        if ev.get(k):
            bits.append(f'<span><b>{k}</b> <code>{esc(ev[k])}</code></span>')
    for u in (ev.get('related_urls') or [])[:4]:
        bits.append(f'<span><b>related</b> <code>{esc(u)}</code></span>')
    return f'<div class="ev">{"".join(bits)}</div>' if bits else ''


TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Toadstone — live demo, a real crawl of {site}</title>
<meta name="description" content="A real Toadstone crawl of {site}: {pages} pages, {issues} findings across {checks} checks, each with evidence and a fix. No sign-up.">
<meta property="og:title" content="Toadstone — a real crawl, no sign-up">
<meta property="og:description" content="{pages} pages, {issues} findings across {checks} checks. Every finding carries evidence and a fix.">
<meta property="og:image" content="https://raw.githubusercontent.com/spcaeo/toadstone/main/assets/brand/social-preview.png">
<link rel="icon" href="https://raw.githubusercontent.com/spcaeo/toadstone/main/web/static/favicon.svg">
<style>
:root {{ --bg:#fbfbfa; --sf:#fff; --bd:#e6e6e3; --tx:#17181c; --t2:#5c6069;
        --t3:#8a8f99; --ac:#4f46e5; --e:#c2410c; --w:#b45309; --i:#2563eb;
        --mono:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,monospace; }}
@media (prefers-color-scheme:dark) {{ :root {{
  --bg:#0d0f14; --sf:#14161c; --bd:#262a33; --tx:#e8eaef; --t2:#9aa0ad;
  --t3:#6f7584; --ac:#8f92f5; --e:#fb923c; --w:#fbbf24; --i:#60a5fa; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--tx);
       font:15px/1.55 ui-sans-serif,system-ui,-apple-system,sans-serif; }}
a {{ color:var(--ac); }}
.wrap {{ max-width:1180px; margin:0 auto; padding:34px 22px 80px; }}
header {{ display:flex; align-items:center; gap:14px; flex-wrap:wrap; }}
header svg {{ width:34px; height:34px; }}
.bm-t {{ fill:var(--ac); }} .bm-e {{ fill:var(--bg); }} .bm-s {{ fill:#e0b254; }}
h1 {{ font-size:22px; margin:0; letter-spacing:-.01em; }}
.tag {{ color:var(--t2); font-size:14px; }}
.cta {{ margin-left:auto; display:flex; gap:9px; flex-wrap:wrap; }}
.cta a {{ border:1px solid var(--bd); border-radius:8px; padding:8px 13px;
         text-decoration:none; color:var(--tx); font-size:13px; background:var(--sf); }}
.cta a.p {{ background:var(--ac); color:#fff; border-color:var(--ac); }}
.lede {{ margin:26px 0 0; font-size:17px; line-height:1.6; color:var(--t2); max-width:74ch; }}
.lede b {{ color:var(--tx); }}
.stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(135px,1fr));
         gap:12px; margin:24px 0 8px; }}
.st {{ background:var(--sf); border:1px solid var(--bd); border-radius:11px; padding:14px 15px; }}
.st b {{ display:block; font:600 27px/1.1 var(--mono); letter-spacing:-.02em; }}
.st span {{ display:block; margin-top:4px; color:var(--t2); font-size:12.5px; }}
h2 {{ font-size:14px; text-transform:uppercase; letter-spacing:.05em;
     color:var(--t3); margin:38px 0 10px; }}
table {{ width:100%; border-collapse:collapse; background:var(--sf);
        border:1px solid var(--bd); border-radius:11px; overflow:hidden; font-size:14px; }}
thead th {{ text-align:left; padding:10px 13px; color:var(--t3);
           font-size:11.5px; text-transform:uppercase; letter-spacing:.05em;
           border-bottom:1px solid var(--bd); font-weight:600; }}
.g .h {{ cursor:pointer; border-top:1px solid var(--bd); }}
.g .h:hover {{ background:color-mix(in srgb,var(--ac) 6%,transparent); }}
.g .h td {{ padding:11px 13px; }}
.g .h code {{ color:var(--t3); font:500 12px var(--mono); }}
.g .c {{ width:26px; color:var(--t3); }}
.chev {{ display:inline-block; transition:transform .14s; }}
.g.open .chev {{ transform:rotate(90deg); }}
.cat {{ color:var(--t2); white-space:nowrap; }}
.n {{ text-align:right; font:500 13px var(--mono); color:var(--t2); white-space:nowrap; }}
.b {{ display:inline-block; font:500 11px var(--mono); padding:2px 7px;
     border-radius:999px; margin-right:8px; border:1px solid currentColor; }}
.b.e {{ color:var(--e); }} .b.w {{ color:var(--w); }} .b.i {{ color:var(--i); }}
.d {{ display:none; }} .g.open .d {{ display:table-row; }}
.d td {{ padding:0 13px 15px 39px; border-top:0; }}
.d .u a {{ font:500 12.5px var(--mono); word-break:break-all; }}
.d .t {{ color:var(--t2); margin:5px 0 8px; }}
.ev {{ display:flex; flex-wrap:wrap; gap:7px 16px; margin-bottom:8px; }}
.ev span {{ font-size:12.5px; color:var(--t2); }}
.ev b {{ color:var(--t3); font-weight:600; text-transform:uppercase;
        font-size:10.5px; letter-spacing:.05em; margin-right:4px; }}
.ev code, .d code {{ font:500 12px var(--mono); color:var(--tx);
                     background:color-mix(in srgb,var(--ac) 9%,transparent);
                     padding:1px 5px; border-radius:4px; }}
.f {{ font-size:13.5px; }} .f b {{ color:var(--t3); text-transform:uppercase;
     font-size:10.5px; letter-spacing:.05em; margin-right:6px; }}
.urls td {{ padding:8px 13px; border-top:1px solid var(--bd); }}
.urls .url a {{ font:500 12.5px var(--mono); word-break:break-all; text-decoration:none; }}
.note {{ margin-top:14px; color:var(--t3); font-size:13px; line-height:1.6; }}
footer {{ margin-top:48px; padding-top:22px; border-top:1px solid var(--bd);
         color:var(--t3); font-size:13px; line-height:1.7; }}
</style></head><body><div class="wrap">

<header>
  <svg viewBox="0 0 32 32" aria-hidden="true">
    <g transform="rotate(4 9 25)"><path class="bm-t" d="M2.6 25 q0 -8.2 6.4 -8.2 q6.4 0 6.4 8.2 z"/>
    <circle class="bm-t" cx="5.9" cy="16.6" r="2.5"/><circle class="bm-t" cx="11.5" cy="16.2" r="2.5"/>
    <circle class="bm-e" cx="5.9" cy="16.3" r=".95"/><circle class="bm-e" cx="11.5" cy="15.9" r=".95"/>
    <path class="bm-e" d="M4.3 21.3 q4.5 2.5 9 0 q-4.5 .9 -9 0 z"/></g>
    <g transform="rotate(-4 23 25)"><path class="bm-t" d="M16.6 25 q0 -8.2 6.4 -8.2 q6.4 0 6.4 8.2 z"/>
    <circle class="bm-t" cx="20.5" cy="16.2" r="2.5"/><circle class="bm-t" cx="26.1" cy="16.6" r="2.5"/>
    <circle class="bm-e" cx="20.5" cy="15.9" r=".95"/><circle class="bm-e" cx="26.1" cy="16.3" r=".95"/>
    <path class="bm-e" d="M18.7 21.3 q4.5 2.5 9 0 q-4.5 .9 -9 0 z"/></g>
    <path class="bm-s" d="M16 4.6 l3.1 3.6 l-3.1 3.6 l-3.1 -3.6 z"/>
  </svg>
  <div><h1>Toadstone</h1><div class="tag">a real crawl, nothing staged</div></div>
  <div class="cta">
    <a class="p" href="https://github.com/spcaeo/toadstone">Source on GitHub</a>
    <a href="https://github.com/spcaeo/toadstone#quick-start">Run it yourself</a>
  </div>
</header>

<p class="lede">This is the output of one real crawl of <b>{site}</b> — a site
we own and maintain — exported straight from the app. <b>Zero errors</b>, which
is what a maintained site looks like, and <b>{issues} findings worth fixing
anyway</b>. Open any row for the evidence and the fix.</p>

<div class="stats">
  <div class="st"><b>{pages}</b><span>pages crawled</span></div>
  <div class="st"><b>{issues}</b><span>findings</span></div>
  <div class="st"><b>{checks}</b><span>checks that fired</span></div>
  <div class="st"><b>{errors}</b><span>errors</span></div>
  <div class="st"><b>{warnings}</b><span>warnings</span></div>
  <div class="st"><b>{info}</b><span>info</span></div>
</div>

<h2>Findings, grouped by check</h2>
<table>
  <thead><tr><th></th><th>Check</th><th>Category</th><th>Pages</th></tr></thead>
  {groups}
</table>
<p class="note">Grouped because forty pages missing a canonical is one
template, not forty jobs. Every finding carries a stable id —
<code>sha1(url + check + offending value)</code> — so the same defect keeps its
identity between crawls and a fixed one never returns as "new".</p>

<h2>Pages ({shown} of {pages})</h2>
<table class="urls">
  <thead><tr><th>Status</th><th>URL</th><th>Title</th><th>Words</th><th>Depth</th></tr></thead>
  <tbody>{page_rows}</tbody>
</table>

<footer>
  Generated by <code>tools/build_demo_site.py</code> from a live crawl — no
  hand-editing, which is the point. Toadstone is self-hosted and
  source-available under the Elastic License 2.0, a fork of
  <a href="https://github.com/PhialsBasement/LibreCrawl">LibreCrawl</a> (MIT).
  Built by <a href="https://www.labsmedia.com/">LabsMedia</a>.
</footer>
</div>
<script>
document.querySelectorAll('.g .h').forEach(function (h) {{
  function toggle() {{ h.parentElement.classList.toggle('open'); }}
  h.addEventListener('click', toggle);
  h.addEventListener('keydown', function (e) {{
    if (e.key === 'Enter' || e.key === ' ') {{ e.preventDefault(); toggle(); }}
  }});
}});
</script>
</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', default='http://127.0.0.1:5058')
    ap.add_argument('--crawl', type=int, required=True)
    ap.add_argument('--label', default='freshlycited.com')
    args = ap.parse_args()
    return build(args.app, args.crawl, args.label)


if __name__ == '__main__':
    sys.exit(main())
