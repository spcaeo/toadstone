# Toadstone tests

No test framework, no runner, no extra dependencies. Every suite is a plain
script that prints PASS or FAIL per behaviour and exits non-zero if anything
failed.

**35 suites.** Two of them drive a real browser against a running
app; the rest need nothing but this repository.

```bash
# everything that needs no server
for t in tests/*_tests.py; do
  case "$t" in tests/palette_tests.py|tests/router_tests.py) continue ;; esac
  python "$t" || echo "FAILED: $t"
done

# the two that do, pointed at a running install
python main.py --local --port 5057 &
TOADSTONE_URL=http://127.0.0.1:5057 python tests/palette_tests.py
TOADSTONE_URL=http://127.0.0.1:5057 python tests/router_tests.py
```

`TOADSTONE_URL` defaults to `http://127.0.0.1:5000`. On macOS that port belongs
to AirPlay Receiver, so those two suites will time out against a page Apple is
serving unless you set it.

And the one that is not a test suite at all, but a crawl of a site built to be
wrong:

```bash
python tools/coverage_sweep.py        # 180 distinct checks fired, last run
```

## Suites

| Script | What it covers |
|---|---|
| `accessibility_tests.py` | axe-core rules becoming checks: one slug per rule, impact mapped to severity |
| `agent_tests.py` | The optional LLM analysts: the word floor, the stub runner, and what happens when the model cannot be reached |
| `amp_tests.py` | AMP validity, and the checks that only mean anything beside the canonical page |
| `api_tests.py` | The reporting API: filters, formats, diffs, accepting issues |
| `bridge_tests.py` | The Claude bridge: who may ask, what it will run, and what it refuses |
| `cms_tests.py` | The read-only WordPress connector |
| `coverage_gap_tests.py` | Signing in to the crawled site, microdata and RDFa, PDFs as documents |
| `crawl_checks_tests.py` | Checks that need the crawl as a whole rather than one page |
| `crawl_diff_tests.py` | What changed between two crawls, and regression reports |
| `date_tests.py` | Date parsing, mismatches, staleness, future dates |
| `detector_tests.py` | The issue detector: every fetch error has a label, enforced by reading the source |
| `duplicate_tests.py` | Duplicate detection on main text, aliases, shared passages |
| `export_tests.py` | Exports: twelve datasets, three formats, and the field selection |
| `false_positive_tests.py` | The false positives found by crawling ten real SEO sites: bot walls read as broken links, commas in URLs, tracking pixels, an unreadable robots.txt |
| `fixture_tests.py` | The crawler end to end against local fixture sites |
| `google_connect_tests.py` | Connecting a Google account: signed state, token exchange, the encrypted store |
| `google_tests.py` | Search Console and GA4 parsers, driven from Google's own Discovery documents |
| `header_tests.py` | Security headers, cookie flags, mixed content |
| `language_tests.py` | Spelling, grammar, punctuation, casing and style via LanguageTool |
| `link_graph_tests.py` | Orphans, click depth, anchor text, clusters, graph export |
| `link_manager_tests.py` | The URL queue: normalisation, scope, and what never gets queued |
| `link_metrics_tests.py` | Moz, Majestic and Ahrefs: mapping, batching, caps and refusals |
| `locale_tests.py` | hreflang alternates and locale prefixes across the other checks |
| `log_tests.py` | Server logs: formats, reverse DNS, bot spoofing, crawl budget |
| `mcp_tests.py` | The MCP server: every tool, the guardrails, and the legacy env prefix |
| `pagespeed_tests.py` | PageSpeed and CrUX, including the finding when the API could not be reached |
| `palette_tests.py` | **Needs a running server.** The command palette, and that every settings panel is reachable |
| `persistence_tests.py` | Saving and loading crawls, and what survives a restart |
| `rendered_tests.py` | Browser-only checks (needs Playwright) |
| `router_tests.py` | **Needs a running server.** URL routing in the browser |
| `rules_tests.py` | The user-defined consistency rules engine |
| `scheduler_tests.py` | Standing crawls: due times, overlap, and what a missed window does |
| `schema_tests.py` | Structured data: parse errors, FAQ markup, breadcrumbs, reviews |
| `sitemap_tests.py` | Sitemap discovery, parsing and the coverage audit |
| `technical_tests.py` | Canonicals, redirects, headings, images, hreflang, performance |

Or run them in the Docker image:

```bash
docker run --rm -v "$PWD/src:/app/src:ro" -v "$PWD/tests:/app/tests:ro" \
  toadstone:latest python tests/fixture_tests.py
```

## fixture_tests.py

Deterministic tests. Starts tiny HTTP servers on localhost and runs the crawler
against them in-process, so nothing touches the internet.

```bash
python tests/fixture_tests.py
```

Prints a PASS/FAIL line per behaviour and exits non-zero if any fail. If ports
8911-8921 are taken locally, set `FIXTURE_BASE_PORT` to move the range.

Each test pins a bug that reached production once:

| Test | What breaks if it fails |
|---|---|
| `test_cross_domain_redirect` | A page redirecting off-site makes the crawler resolve that site's relative links against your domain, inventing internal 404s |
| `test_external_images` | Off-domain images get requested and listed even with external crawling off |
| `test_sitemap_discovery_is_async` | Sitemap probing blocks the start-crawl request, or the crawl finishes before discovery delivers its URLs |
| `test_max_urls_counts_pages_not_images` | Image rows consume the max-URL budget, so an image-heavy site crawls only a page or two |
| `test_event_ordering_under_polling` | The UI receives an update for a row it was never sent, or the same row twice |
| `test_duplicate_detection_is_linear` | A site of templated pages holds the crawl in "finishing up" for minutes and produces two duplicate issues per matching pair (700k rows for 1,000 pages), locking the database while they save |
| `test_export_formats_apply_to_every_data_type` | The format chosen in Settings is ignored for links/issues exports, or Excel silently produces CSV |
| `test_allow_cookies_off_ignores_set_cookie` | "Allow cookies" off still keeps cookies, so a locale cookie from one page changes how later pages render (false canonical/duplicate issues on multilingual sites) |

## router_tests.py

Browser tests for URL routing (`web/static/js/router.js`): every tab, filter,
search and popup is written to the address bar, deep links restore that view,
and Back/Forward move between views and crawls. Needs a running server in local
mode and Playwright; the crawls it creates are deleted afterwards. With Docker:

```bash
docker run --rm --network container:toadstone \
    -v "$PWD/tests:/app/tests:ro" toadstone:latest python tests/router_tests.py
```

## crawl_harness.py

End-to-end test against a real site. Start Toadstone first, then point the
harness at a URL. It polls `/api/crawl_status` using the same event protocol as
the browser and accumulates state the same way
`web/static/js/incremental_poller.js` does.

```bash
python main.py --local          # in one terminal
python tests/crawl_harness.py https://example.com/ --max-urls 150
```

Useful flags: `--js` (JavaScript rendering), `--images`, `--external`,
`--pause-test` (also verifies pause actually halts work and resume continues),
`--delay` and `--max-urls` to stay polite on sites you do not own, and `--base`
to drive an instance on another host or port.

It verifies, on every run:

- no duplicate `url`/`link` events, and no update for a row that was never sent
- the client's accumulated state matches the server's full snapshot exactly
- mutations reach the client, specifically link status backfills and the
  `linked_from` values filled in at the end of a crawl
- rows stay in scope: nothing off-domain unless `--external` is set
- the counter never reports fewer crawled URLs than the rows already sent
- streaming exports contain exactly one row per URL, link and issue
- reloading the crawl from SQLite flips the epoch, replays every row, and
  matches what was crawled

On failure it prints the poll history (sequence numbers, event counts, epoch
changes) so the violation can be explained rather than just reported.
