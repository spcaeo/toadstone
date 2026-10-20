# What this fork adds

This is LibreCrawl with a set of checks, APIs and UI work built on top. Upstream LibreCrawl is untouched beneath it, so its updates still merge.
Upstream is MIT and stays MIT; this fork is distributed under the Elastic
License 2.0, which MIT's sublicensing grant is what permits — see
[LICENSE](LICENSE) and [NOTICE](NOTICE).

Run it the same way (`docker compose up -d`, `.env` for the port and mode). The one
new requirement is that a crawl now stores the page body, so a crawl costs roughly
5–30 KB per page on disk — use **Settings → Crawls to keep** to bound it.

---

## Checks

Every finding, wherever it appears, carries the same fields: a **stable id**
(`sha1(url + check + offending value)`, so the same defect keeps its identity between
crawls), a machine-readable **check** slug, a severity, an **evidence** block a
developer can act on without re-fetching the page, and a one-line fix.

**Between crawls** — text removed since the last crawl, internal links lost, and a
new / fixed / still-open report keyed on those ids.

**Duplicate content** — scored on the page body with header, nav, footer and sidebar
stripped, using an exact hash or a 64-bit simhash, and reporting the shared passage
rather than a percentage. Two pages that share a layout no longer read as duplicates
because of their chrome. URLs that canonicalise to each other are never duplicates.

**Dates and freshness** — missing, unparseable or timezone-less dates; JSON-LD,
`article:modified_time`, HTTP `Last-Modified`, the sitemap's `lastmod` and the visible
"Updated on …" disagreeing; stale pages; future dates; modified before published.

**Sitemaps** — robots.txt → index → child sitemaps, then: URLs that 404, redirect, are
noindex, canonicalise elsewhere or are blocked; crawled pages missing from the sitemap;
lastmod that is missing, malformed, in the future or never bumped though the content
changed; file hygiene (50k URLs, 50 MB, duplicates across children, mixed http/https or
www, trailing-slash drift); and coverage lists.

**The technical checklist** — relative, cross-domain and chained canonicals, canonicals
pointing at a redirect or a noindex page; noindex pages that are linked or in the
sitemap; nofollow internal links; redirect chains and loops; internal links pointing at
a redirect; http/https and www mixes; broken targets reported against the page that
links them; duplicate titles, descriptions and H1s across pages; pixel width as well as
character count; multiple H1s, skipped levels, empty headings, stray spacing; images
without width and height, oversized, or lazy-loaded above the fold; hreflang codes,
self-references and return links; slow TTFB, uncacheable content pages, heavy pages.

**Structured data** — JSON-LD that does not parse, FAQ markup that is not in the visible
copy verbatim (numbering prefixes included), `@id` references that resolve to nothing,
self-serving Review/AggregateRating, BreadcrumbList sanity.

**Internal linking** — orphan, weakly linked and deep pages, missing parent links, one
anchor pointing at several URLs, many anchors for one URL, generic anchors, anchors that
cannibalise the page's own keyword, one-way pairs, click depth with and without
navigation links, per-section summaries, and a nodes + edges export.

**Your own rules** — a per-site file of plain regex and CSS selectors, checked on every
crawl: banned claims, figures that must agree across pages, required button labels, the
expected author. No AI, no new dependency; it reads a small YAML subset or JSON. Edit and
dry-run it in the **Rules** tab before saving.

```yaml
rules:
  - id: banned-claims
    severity: error
    forbid: ["500\\+ projects", "15\\+ years"]

  - id: cta-label
    severity: warning
    selector: "a.btn, button[type=submit]"
    must_equal: "Book a Discovery Call"
```

**WordPress, read-only** — structured data the CMS holds but the front end strips,
over-long SEO titles and descriptions, placeholder or whitespace-damaged fields,
unexpected authors, published items missing from the sitemap or never crawled. GET only;
a non-WordPress site costs one request.

**Rendered mode** (opt-in, needs JavaScript rendering) — images blown up by CSS, elements
that look like headings but are not, hidden navigation, text hidden by CSS, JSON-LD
injected or removed by JavaScript, console errors and layout overflow, at 1440/768/390.
It rides along with the existing render, so it costs no extra page loads.

---

## API

Everything a crawl found, without re-fetching the site. In local mode authentication is
a session cookie you get by opening `/`.

| | |
|---|---|
| `GET /api/crawls/<id>` | one crawl: metadata, issues, pages, links |
| `GET /api/crawls/<id>/issues` | `?severity=&check=&category=&url_prefix=&since_crawl=&status=&format=json\|csv\|ndjson` |
| `GET /api/crawls/<id>/pages` | `?fields=&url_prefix=&status_code=&format=` |
| `GET /api/crawls/<id>/links` | `?src=&dst=&anchor=&internal=` |
| `GET /api/crawls/<id>/graph` | nodes + edges + clusters, or `?format=csv` |
| `GET /api/crawls/<id>/sitemaps` | per-file summaries and coverage |
| `GET /api/crawls/<id>/diff/<other>` | `{new, fixed, still_open}` by stable id |
| `GET /api/crawls/<id>/export/<dataset>` | 12 datasets as `csv\|json\|ndjson\|xlsx` |
| `GET /api/crawls/<id>/export/all` | one workbook, or a zip of CSVs |
| `GET /api/crawls/<id>/report` | a self-contained `html` or `md` summary |
| `GET /api/export/datasets` | the catalogue: every dataset and its columns |
| `POST /api/issues/<id>/accept` | mute a known limitation, with a reason |
| `GET /api/rules`, `PUT /api/rules`, `POST /api/rules/preview` | the rules file |

Datasets: `issues`, `pages`, `links`, `graph-nodes`, `graph-edges`, `sitemap-files`,
`sitemap-coverage`, `duplicates`, `redirects`, `broken-links`, `headings`, `images`.

**For CI**, a crawl can run without the server and fail the build:

```bash
python main.py --crawl https://example.com --fail-on error --report issues.json
```

**A webhook** can be set in Settings; it receives the crawl id, counts by severity and
what changed since the previous crawl.

---

## Interface

- **Every view is a URL.** The crawl, tab, filters, searches and open popups all live in
  the address bar (`/crawl/24/issues?severity=error&filter=html`), so a view survives a
  reload, can be bookmarked, and can be sent to whoever is fixing it. Back and Forward
  move between views and between crawls.
- **Sitemap** tab: coverage at a glance, every sitemap file, downloadable lists.
- **Compare** tab: two crawls side by side — fixed, new, unchanged.
- **Rules** tab: edit the rules file, dry-run it against the open crawl, and see what is
  currently muted.
- Crawl IDs are shown on both dashboards and link straight to the crawl.

---

## Fixes to upstream behaviour

- **"Allow cookies" was never read**, so a cookie set by one page changed how later pages
  rendered. On a multilingual site this made every English URL report a foreign canonical
  and a duplicate.
- **Tracking parameters made a second page.** A link to `/contact-us/?utm_source=…` was
  crawled as its own page and reported as duplicate content. Those URLs are now one page,
  and the UTM links are reported once as a link-hygiene item — which is the real defect.
- **Deleting a crawl left its rows behind.** The schema says `ON DELETE CASCADE`, but
  SQLite only enforces that when `PRAGMA foreign_keys` is on, which it never was.
- **Response time included our own parsing**, so heavier analysis made every page look
  slower. It now times the fetch; our work is recorded separately.
- **The issues table ignored the chosen severity** whenever it redrew.
- `alt=""` on a decorative image, `FAQPage` markup treated as an article, and a
  `BreadcrumbList` step written as `{"item": {"@id": …}}` were all mis-reported.

---

## Tests

Thirty-four suites, no framework, no network, no runner — each is a plain
script that prints PASS or FAIL per behaviour and exits non-zero if anything
failed:

```bash
for t in tests/*_tests.py; do python "$t" || echo "FAILED: $t"; done
```

`tests/palette_tests.py` and `tests/router_tests.py` drive the real UI with
Playwright and need a running server — point them at one with
`TOADSTONE_URL=http://127.0.0.1:5057`.

And the one that matters most, which is not a test suite at all but a crawl of
a site built to be wrong:

```bash
python tools/coverage_sweep.py        # 180 distinct checks fired, last run
```
See `tests/README.md` for what each suite pins down.
