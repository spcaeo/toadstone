# Build log — closing the gap on Sitemoth and Screaming Frog

Single source of truth for this programme. Plan, progress and decisions all live
here. Update the status table and the log at the bottom as work lands.

**Goal:** everything Sitemoth and Screaming Frog document, plus an MCP server and
four kinds of agent support. Agreed 2026-10-18: *everything, in order*.

---

## Status

**Every phase is complete.** 1,687 tests pass, 0 fail (the baseline at
`078dd28` was 819). Every Python file in the project is under 1,000 lines.

| Phase | Scope | State |
|---|---|---|
| 0 | Foundation — the known bugs, the untested checks | **done** |
| 1 | Cheap checks — data already captured | **done** |
| 2 | MCP server | **done**, live at `/api/mcp` |
| 3 | Integrations — axe-core, GSC, GA4, LanguageTool, CrUX | **done** |
| 4 | Scheduler, auth, extraction, AMP | **done**, loop running |
| 5 | Log file analysis | **done** |
| 6 | Agents — all four kinds | **done** |
| — | Split every file under 1,000 lines | **done** |
| — | Connect Google from the UI, for non-technical people | **done** |

### Two things that need the owner, not more code

> **Superseded 2026-10-19.** Google is connected, and the integration has since
> made real calls against real properties. Everything below about the live-Google
> gap is now a record of how that gap was closed, not of anything outstanding.
> What is outstanding is two accepted limitations, both recorded at the end of
> the log: paid link metrics, which need a subscription the owner chose not to
> buy and so stay stubbed, and `gsc_canonical_overridden`, which cannot be made
> to fire because Google has not overridden a canonical on either connected
> property — 113 pages inspected live, 43 agreed, 0 disagreed.

**Search Console and GA4 are unverified against live Google.** The payload
shapes in the tests come from Google's published documentation, not from a
captured response, so the mapping is tested and the wire format is not. The
test output prints the caveat on every run.

What used to be needed here was a client id, a secret and a hand-made refresh
token pasted into a settings form. That is gone: Settings → Google now has a
Connect button, and the only thing anyone has to do once per installation is
register an OAuth client and put its id and secret in `.env`. The panel prints
the five steps and the exact redirect URI. See `docs/google.md`.

Part of that gap has since been closed by making real, unauthenticated calls
to the real Google hosts. All four endpoints resolve, and the token endpoint's
error body matches the shape the tests assume. That also found a crash: the
REST APIs put an **object** under `error` while the OAuth endpoint puts a
**string** there, so every API failure died with `unhashable type: dict`
instead of saying what was wrong. Fixed, with the captured bodies in the tests.

The same pass found a worse one: `from_config` read the old settings fields
and never looked at the token store, so a crawl ignored the account someone
had just connected. The connect flow fed nothing. Now wired, with the secret
reaching the crawl thread through a bound callable rather than the crawl
config, which is snapshotted into the database and exported with the crawl.

Checking the new code against Google's **Discovery documents** - which need
no credentials - then found a third bug. `WmxSite.permissionLevel` is an enum
Google spells two ways: `SITE_UNVERIFIED_USER` in the v1 Discovery document,
`siteUnverifiedUser` in the older v3 reference. The code matched only the
camelCase form, and the tests agreed because both were written from the same
memory. Against the real enum a property the account *cannot read* was offered
as selectable. Also found: `accountSummaries` is paged and the page token was
ignored, so an agency with more than one page of GA4 properties would silently
have seen only the first.

Verified against live Google so far, with no account of ours:

| Checked | How |
|---|---|
| All four endpoint hosts and paths | real requests; 401, not 404 |
| The token endpoint's error shape | real `invalid_client` body |
| The 401 branch | real call with a bad token |
| The 403 scope branch | **real authenticated call** using the gcloud login already on this machine, which lacks `webmasters.readonly` |
| Field names, enum values, endpoint paths | Google's Discovery documents |
| GA4 pagination | the documented `nextPageToken` |

One success path *is* now verified against live Google: `account_email()`,
the real code path, returned a real address using the gcloud login already on
this machine, which carries `userinfo.email`.

What is left is irreducible. A refresh token can only mint tokens within the
scopes granted when it was consented to, so `webmasters.readonly` cannot be
added to an existing login without a person approving it, and a Search Console
property cannot be read without authorisation on it. That needs the owner's
Google account.

So rather than leave the check to be remembered later, it is a command:
`tools/verify_google.py` makes the real calls, fetches Google's Discovery
documents, and compares every field and enum value that came back against what
Google says should come back. Its Search Console and Analytics branches are
themselves tested, so they do not run for the first time on someone's live
data.

**The agent layer now has a provider.** `agentMode` still defaults to `none`
deliberately - a crawler that silently spends money, or silently sends a
client's page text to a third party, has broken someone's trust - but `cli`
mode now works from Docker, which it could not before.

It could not because the Claude CLI ships as a native binary for the host OS:
a macOS install will not execute in a Linux container, whatever is
bind-mounted. Rather than put a long-lived credential inside the image, the
CLI stays on the host and the container asks it questions over a loopback-only
bridge with a shared secret (`tools/claude_bridge.py`). The credential never
leaves the host. See `docs/agents.md`.

### What the comparison looks like now

The gap that started this was concentrated: 47 of the competitor's 180 checks
sat in six areas scoring zero here. All six are covered, and two of them -
accessibility at 200 axe rules, and log analysis - go further than either
competitor. The API, webhooks, issue acceptance, crawl-to-crawl identity and
the MCP server remain things neither of them documents at all.


## Phase 0 — foundation

Rationale: 70 of 133 checks have never run against a live site, and every bug
found so far was in a check that *had* run. Seven defects surfaced from auditing
a single site. Adding 180 more checks on top of that multiplies false positives
faster than it adds value, so the existing checks get trustworthy first.

### 0.1 The seven known bugs

- [x] **B1 `redirect_loop` false positive. FIXED.** Chromium's internal 307 restart,
      triggered by a site's `Critical-CH` header, is recorded as a real redirect
      hop, so a healthy page reports a redirect to itself. Severity `error`, so
      this is the worst of the seven. Fix: ignore same-URL hops that carry no
      `Location`, and drop Chromium-internal restarts in `js_renderer`.
      Done: `JavaScriptRenderer._drop_internal_restarts` removes a hop whose
      address equals the address it leads to, and a chain that never leaves
      where it started is no longer a loop. The existing test asserted the
      opposite, so it encoded the bug; replaced with a real loop plus a
      regression test for the no-op.
- [x] **B2 Image URLs rendered as HTML pages. FIXED.** when `crawlImages` is on.
      Playwright renders a standalone image inside Chromium's image-viewer
      document and the page checks fire against it — 45 junk findings out of 90
      on one crawl. Fix: skip the render path and all page checks for non-HTML
      content types.
- [x] **B3 `rendered_hidden_text` counts `<script>` content. FIXED.**, so a JSON-LD block
      reads as hidden text; it also flags collapsed `<details>` and closed nav
      dropdowns. 66 findings on one site were ~95% noise. Fix: exclude
      `<script>`, `<details>` that are closed, and `[hidden]`/`aria-expanded=false`
      subtrees. Done: `lcProseText` walks the subtree instead of taking
      `textContent` (which returns a script's source), `lcInCollapsed` leaves
      disclosure widgets alone, and `lcInChrome` treats anything inside
      header/nav/footer as chrome. All three shapes now sit on the clean
      fixture, which asserts zero issues.
- [x] **B4 — WITHDRAWN, not a bug.** Claimed that `image_lazy_above_fold` gave
      two findings on one page the same issue id. It does not: `_image_checks`
      already passes the image src as `offending_value`, and crawl 71's two
      findings carry distinct ids (`32ec109a…`, `de7cfe45…`). The original
      comparison script printed only the first id of each group, which was
      misread as a collision. No code change needed. Six real bugs, not seven.
- [x] **B5 Checks test presence, never plausibility. FIXED.** `missing_dates` passes on
      render-time `new Date()` values milliseconds apart and on a whole library
      sharing one backfilled date. Fix: add value-plausibility checks — clustered
      timestamps, render-time values, published-after-modified.
- [x] **B6 Duplicate title/H1/description ignore `rel=canonical`. FIXED.** A parameter
      URL that correctly canonicalises to its clean form still reports as a
      duplicate of it; one `?topic=sales` URL produced nine findings. Done:
      `_canonicalises_away` drops such pages from the duplicate index, with a
      trailing slash not counting as a different URL.
- [x] **B7 `image_lazy_above_fold` decides "above the fold" from DOM order. FIXED.**
      `technical_checks.py:654` is `index < 2`, yet it emits "sits at the top of
      the page". Measured at 1920x1080, a flagged image was 2,403px down with
      nothing above the fold at all. The fix is half-built already:
      `rendered_checks.py:243` computes a correct `above_fold` per image from
      `getBoundingClientRect` and **no check consumes it**. Done: the renderer
      now reports `top_px` and `fetchpriority` alongside `above_fold`,
      `_apply_rendered_geometry` carries them onto the page, and the check
      prefers the measurement. With no measurement it still falls back to DOM
      order but says so instead of claiming geometry.

Also recorded: `regression_report()` keys on `sha1(url + check + offending_value)`,
so it cannot compare two crawls of one site served from different hosts (staging
vs production) — everything reads as both new and fixed. Host-normalising diff
scripts exist in the session scratchpad; they should move into the repo.

### 0.2 The 70 unfired checks

- [x] **Enumerated.** 133 fixed slugs in code, 63 fired on a live crawl plus one
      dynamic (`404_client_error`), so 70 have never run against a real site. The
      enumeration reconciles exactly: no live slug falls outside the 133, which
      independently confirms the inventory.

Splitting those 70 by whether any test names them changes the picture, and the
13 matter far more than the 57:

| | Count | Risk |
|---|--:|---|
| Named in a unit test, never fired live | 57 | Lower — logic is covered, behaviour on real pages is not |
| **Appear in no test anywhere** | **13** | **Unverified in every sense** |

The 13 with no test at all: `broken_image`, `connection_error`, `dns_not_found`,
`large_page_size`, `moderate_page_size`, `no_response`, `rendered_check_failed`,
`request_timeout`, `sitemap_empty`, `sitemap_file_limit`,
`sitemap_url_server_error`, `ssl_tls_error`, `tracking_parameters_on_internal_links`.

Mostly error paths — hard to trigger on a live site, easy to fixture.

Unfired by module: sitemap 19/25, technical 18/35, issue_detector 9/30, cms 8/8,
schema 5/6, rendered 5/10, dates 3/6, link_graph 3/10. Duplication and crawl_diff
are fully exercised.

- [x] **Tests written for all 13.** New `tests/detector_tests.py` covers the
      nine detector checks; the three sitemap ones and `rendered_check_failed`
      went into their existing suites. Writing them found a real crash: three
      numeric fields were read straight from the crawl result and compared
      against thresholds, so a result carrying `None` for `status_code`, `size`
      or `word_count` raised `TypeError` and lost every issue for that page.
- [x] Live-validate, and measure it rather than assert it. `make_issue` now
      records every slug that actually fires when `LIBRECRAWL_SLUG_LOG` is
      set, so the question "which checks have ever run?" is answered by
      counting, not by grepping for string literals - which undercounted
      badly, because most slugs are built from tables rather than written out
      (38 found by grep; 223 by counting).

      | | Count |
      |---|--:|
      | Reachable in the test suite | 223 |
      | Fired on real crawls (48 of them) | 84 |
      | Both | 75 |
      | Tested, never seen live | 148 |
      | **Fired live, no test emitted them** | **9** |

      The 148 are mostly checks gated on inputs those crawls did not have -
      AMP markup, Google credentials, log files, PDFs, a paid metrics key -
      not checks that are broken. The 9 were the real hole, and three of them
      had no test at all: `canonical_different`, `meta_description_too_long`
      and `moderate_response_time`. Ordinary checks, which is the point: the
      untested ones were not exotic, they were the ones nobody thought to
      doubt. Now covered, boundaries included.
      **and stay quiet when it should not**. The second half matters more — a
      check that cannot stay quiet is a false-positive generator.
- [ ] Fix whatever that exposes.

---

## Phase 1 — cheap checks, data we already capture

Closes most of the 47-check deficit against Sitemoth using data already in hand.

- [x] **Security response headers (14 checks). DONE.** `src/core/header_checks.py`,
      wired from `_run_header_checks` beside the other site-wide passes and
      switched by the `headerChecks` setting. The headers were *not* already on
      the page record — `_http_metadata` read them for caching and redirects and
      threw the rest away — so both fetch paths now keep the whole header set in
      `page['response_headers']`, with repeated `Set-Cookie` headers preserved as
      a list where urllib3 still has the originals. Slugs: `missing_hsts`,
      `hsts_max_age_too_short`, `missing_csp`, `csp_unsafe_inline`,
      `csp_unsafe_eval`, `missing_x_content_type_options`,
      `missing_x_frame_options`, `missing_referrer_policy`,
      `missing_permissions_policy`, `insecure_referrer_policy`,
      `server_version_disclosed`, `cookie_without_secure_flag`,
      `cookie_without_httponly`, `cookie_without_samesite`.
      A page is judged only when the crawl actually recorded headers for it: a
      crawl loaded from the database stores none, and reading that as "sends
      none" would report every check against every page.
- [x] **Mixed content (3 checks). DONE.** The page HTML is only kept when a
      selector rule needs it, so `header_checks.extract_page_resources` runs
      while the markup is still parsed and keeps the http:// subresources alone
      — every URL of every page would double a large crawl's memory for data no
      check reads. Slugs: `mixed_content_active` (scripts, styles, frames and
      objects, which browsers block), `mixed_content_passive` (images and
      media), `insecure_form_action`. An http link to another site is not a
      subresource and is not reported.
- [x] **Pagination `rel=next`/`rel=prev` (4 checks). DONE.** `<link>` elements
      were parsed for canonical and hreflang only; `extract_page_resources` now
      also reads `rel=next`/`rel=prev` from both `<link>` and `<a>`. Slugs:
      `pagination_broken_chain`, `pagination_no_return_link`,
      `pagination_canonical_to_page_one`, `pagination_orphaned_page`. A next
      target the crawl saw but never fetched is held back by a depth or count
      limit, not broken, so only a known-bad status or a URL that appears
      nowhere counts.
- [ ] **PageSpeed checks (~9).** The data is already fetched and stored and *no
      check reads it*. Wiring, not building. Add CrUX field data (free API).
- [ ] **Multi-viewport rendering.** The code exists in `rendered_checks.py`; the
      crawler only ever calls one width.
- [ ] **`Crawl-delay`.** Parsed but not honoured.
- [ ] **Custom JS snippets.** `render_page()` already takes `before_navigate` and
      `observe` hooks; nothing uses them. Expose through settings.
- [x] **XPath extraction. DONE.** An `xpath:` key pairs with `must_equal`
      exactly as `selector:` does; naming both on one rule is an error rather
      than a guess. Needs `lxml`, now in `requirements.txt`; a rule using
      xpath where lxml is absent is skipped with a note, like a selector rule
      on a crawl that kept no HTML.

---

## Phase 2 — MCP server

Best effort-to-value ratio on the board. Sitemoth shipped an MCP server with no
API underneath; Screaming Frog has none. We already have 56 HTTP routes, stable
issue ids, webhooks and 12 export datasets, so this is a thin wrapper.

**DONE.** `src/mcp/`, 1,496 lines across eight modules, documented in
`docs/mcp.md`, 125 tests in `tests/mcp_tests.py`.

- [x] **Ten tools.** `list_crawls`, `crawl_status`, `list_issues`,
      `issue_detail`, `page_detail`, `regression_diff`, `export_dataset`,
      `check_reference`, and the two writes, `start_crawl` and `accept_issue`.
      Each is a call onto an existing route, trimmed: `page_detail` returns 52
      stored columns through the HTTP API and the tool hands back a summary
      with the headings tree, image counts and schema types instead, because
      a model does not need a page's whole main text to fix its canonical.
      Every list reports the total it was drawn from, so a trimmed answer
      never reads as a complete one.
- [x] **Guardrails, enforced in one place so no later tool can skip them.**
      Read-only by default: the two write tools are still listed, with their
      descriptions saying they will be refused, and no tool argument turns
      that off. Host allow-list empty by default, meaning *hosts this install
      has already crawled* — not "anything"; `*` opts out. 2,000 URLs per
      crawl, and a request above the cap is refused rather than clamped,
      because clamping lets an agent report on a 2,000-page sample of a
      50,000-page site as if it were the site. Also refused: any scheme but
      http(s), credentials in the URL, and `max_depth` outside 0–100.
- [x] **Both transports.** stdio (`python -m src.mcp`) for Claude Code and
      Cursor; HTTP as a blueprint for main.py, `POST /api/mcp`, which answers
      as whoever sent the request by carrying their cookies into the API.
      main.py needs one line to mount it, and does not have it yet:

      ```python
      from src.mcp.http_app import create_blueprint as _create_mcp_blueprint
      app.register_blueprint(_create_mcp_blueprint(login_required))
      ```

- [x] **No new dependency.** The official `mcp` SDK is not in
      `requirements.txt` and is not in the container image, so the protocol is
      implemented directly — four methods for a tools-only server. It happens
      to be installed on this host, which is exactly the trap: depending on it
      would have produced a server that passes here and is missing in Docker.

Two things the plan assumed that the API does not have:

- **No `GET /api/issues/<id>`.** An issue id is stable across crawls but not
  unique to one, so `issue_detail` takes a `crawl_id` too and finds the row in
  that crawl's issues. Page detail by URL, which the plan offered as the
  alternative, does exist and is exposed as `page_detail`.
- **xlsx and zip exports cannot travel over MCP.** They are binary; the tool
  refuses them and returns the HTTP URL that serves the file.

Worth recording for Phase 4: `regression_diff` inherits the host-sensitivity
of the stable issue id — two crawls of one site on different hosts read as
entirely new and entirely fixed — so the tool says so in its own output until
the host-normalising diff lands.

---

## Phase 3 — integrations

- [x] **axe-core accessibility. DONE, and wider than planned.** axe-core
      4.10.2 is vendored under `src/core/vendor/` (MPL-2.0, header intact)
      rather than fetched: a CDN pull breaks on an offline install, and
      injecting a remote script into a page under audit is what a strict CSP
      on that page exists to stop. Each axe rule becomes its own slug,
      `a11y_<rule-id>` — 200 rules available rather than a hand-picked ten —
      so a finding can be filtered, accepted and tracked by stable id like any
      other. Rides along with the existing render, off by default (100-300ms a
      page), one width only since axe judges the DOM and not the layout. A run
      that fails reports `a11y_check_failed` rather than looking like a pass.
- [ ] **LanguageTool spelling and grammar (~3).** Self-hosted container.
- [ ] **Google Search Console (~7).** OAuth2, Search Analytics, URL Inspection.
- [ ] **Google Analytics 4 (~5).** OAuth2, Data API.
- [ ] **CrUX field data.** Free Google API.

---

## Phase 4 — workflow parity

- [ ] Scheduled recurring crawls (jobs table + scheduler; we have none).
- [ ] HTTP basic auth and forms auth to the crawl target.
- [ ] AMP validation.
- [ ] Segmentation.
- [ ] Near-duplicate threshold tuning; consider vector-embedding similarity.
- [ ] Move the host-normalising diff out of the scratchpad into the repo.

---

## Phase 5 — log file analysis

Largest self-contained subsystem. Screaming Frog sells this separately at £99/yr.

- [ ] Parse Apache, Nginx, IIS and CDN log formats.
- [ ] Verify bots by reverse DNS rather than trusting the user agent.
- [ ] Join log data to crawl data: crawled-but-never-requested, requested-but-not-
      in-crawl, crawl budget by directory, bot hit frequency vs `lastmod`.

---

## Phase 6 — agents

All four kinds, confirmed 2026-10-18.

- [ ] **A1 Agents that fix issues.** Hand a coding agent the issues for a repo and
      have it open fixes. The handoff document in `docs/agent-handoff.md` already
      describes this manually; automate it end to end.
- [ ] **A2 MCP tools for external agents.** Phase 2 delivers this.
- [ ] **A3 Agents inside LibreCrawl.** Content quality, E-E-A-T, GEO and AI-search
      readiness, competitor gaps — judgement that rule-based checks cannot reach.
- [ ] **A4 Agents that run the crawling.** Schedule, triage regressions, decide
      what matters, alert only on real change. Depends on Phase 4's scheduler.

---

## Not building, and why

- **Majestic / Ahrefs / Moz link metrics.** Licensed data, not code. The
  integration is a day's work; the data is a permanent subscription. Purchase
  decision, not an engineering one.
- **Looker Studio connector.** Niche next to everything above; revisit later.

---

## Decisions

- **2026-10-18** — Build everything, in order, rather than cherry-picking.
  Foundation first: the existing 133 checks become trustworthy before new ones
  are added.
- **2026-10-18** — All four agent kinds are in scope.
- **2026-10-18** — Quote 64 checks, not 133, until the unfired ones have fixtures.
  A check that has never executed is a claim, not a capability.

---

## Log

| Date | What happened |
|---|---|
| 2026-10-18 | Capability matrix built against Sitemoth (180 checks) and Screaming Frog (320). Gap is concentrated: 47 of Sitemoth's checks sit in six areas we score zero in. |
| 2026-10-18 | Test baseline captured: 819 passing, 0 failing. `flask-compress` installed on the host so the full suite runs outside the container. |
| 2026-10-18 | This plan written. Phase 0 started. |
| 2026-10-18 | B4 withdrawn — it was never a bug. The claim came from a script that printed only the first issue id of each group. Six real bugs, not seven. |
| 2026-10-18 | B1, B2, B3, B6, B7 fixed, each with tests. 857 passing (was 819). Commit `e9ff98d`. B5 is the only one left. |
| 2026-10-18 | B5 fixed and all 13 untested checks covered; found and fixed a `TypeError` crash on `None` numeric fields. Commit `095fce3`. **Phase 0 complete bar live-validating the 57.** |
| 2026-10-18 | XPath rules added, `lxml` now a dependency. Commit `76ea507`. |
| 2026-10-18 | Accessibility via vendored axe-core, 200 rules, tested live against Chromium. Commit `7f8cf1e`. |
| 2026-10-18 | Phase 2 done: MCP server in `src/mcp/`, ten tools over stdio and HTTP, guardrails refusing by default, 125 tests in `tests/mcp_tests.py`, documented in `docs/mcp.md`. No new dependency: the JSON-RPC protocol is implemented directly. main.py still needs its one-line blueprint registration. |

| 2026-10-18 | Phases 1-6 all landed: header checks, Core Web Vitals, multi-viewport, AMP, LanguageTool, log analysis, scheduler, MCP, Google, agents. |
| 2026-10-18 | Every file brought under 1,000 lines: crawler.py 2,629 -> 916, main.py 2,159 -> 876, plus rules_engine, crawl_db and the fixture suite. 1,458 tests throughout. |
| 2026-10-18 | Google connect built as a UI flow rather than a pasted token: signed state, encrypted per-user store, property pickers, five panel states, every Google error translated into a sentence. `tests/google_connect_tests.py`, 62 checks, mutation-tested. 1,552 passing. |
| 2026-10-18 | Found the Google settings tab was invisible to every tier — `applyTierRestrictions` hides any tab not in its allow-list and `google` was never added. Also fixed a stale note surviving a state change, and an unchecked `event.origin` on the callback's postMessage. |
| 2026-10-18 | Real calls to the real Google hosts found two bugs the 62 canned tests had missed. (1) The REST APIs return `error` as an object, not a string: every 401/403/404 from Search Console or Analytics crashed with `unhashable type: dict` rather than explaining itself. (2) `from_config` never consulted the token store, so a crawl ignored the connected account entirely — the whole flow fed nothing. Both fixed, 15 more tests, 20 mutants all caught. 1,567 passing. |
| 2026-10-18 | Four idle teammates shut down; agent count back to zero, inside the cap. |
| 2026-10-18 | Agent provider chosen and wired: `cli` mode via a loopback bridge on the host, because the Claude CLI is a native host-OS binary and cannot run in the container. `tools/claude_bridge.py` + a wrapper that stands in for the CLI, 32 tests in `tests/bridge_tests.py` including six command-injection attempts. New `agentCliCommand` setting so the indirection is explicit rather than a script named `claude`. |
| 2026-10-18 | Found `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` were never passed through `docker-compose.yml`, so an OAuth client set up in `.env` would never have reached the container - the Connect button would have stayed on the setup screen forever. Added, with the bridge variables and `extra_hosts` for Linux. |
| 2026-10-18 | Bridge verified end to end against the real model: 3 bridge calls, 3 findings, each scoring the stuffed test page 0/10 with reasoning only a reader could produce. The first attempt reported "VERIFIED" on zero findings — the page had 95 words against a `min_words` of 150, so the model was never called and the check passed on an empty list. Redone with a call count taken from the bridge's own log. |
| 2026-10-18 | Agent settings had no UI at all: `agentMode` existed only as a JSON key, so switching agents on meant POSTing to the API. Added to Settings > Advanced, with the irrelevant fields hidden until a mode is chosen. 1,599 passing. |
| 2026-10-18 | Checked the new Google code against Google's Discovery documents and found two more: `permissionLevel` is SCREAMING_SNAKE in the v1 schema and camelCase in the v3 reference, so unreadable properties were offered as selectable; and `accountSummaries` paging was ignored, hiding GA4 properties past the first page. Both fixed, both spellings handled, five mutants all caught. |
| 2026-10-18 | Made a real *authenticated* call with the gcloud credentials already on this machine: Google returned a genuine 403, and it was translated into the scope message rather than the wrong "no access to that property" advice. 1,610 passing. |
| 2026-10-18 | Live validation crawl (220 pages of a live property, crawl 98): 51 distinct check slugs fired. It also exposed the worst bug found today. The site throttled the crawl, so 168 of 220 pages came back with `status_code: 0` - never fetched - and every content check fired on the emptiness. One unreachable URL became ten issues: missing title, missing H1, missing canonical, thin content, no structured data. **720 of the 1,054 issues in that report were fiction**, burying the real ones. Fixed: a page with no response now reports only the connection failure. Replayed against crawl 98's stored pages: 720 -> 0. |
| 2026-10-18 | `router_tests` failing with Chromium "Target crashed" was the leaked browser processes from that crawl, not a code fault. Passes 15/15 on a clean container. 1,621 passing. |
| 2026-10-18 | Closed the three remaining "still open" rows from the comparison matrix. **Site authentication**: basic, digest, form login and pasted cookies, with the session verified before the crawl starts - a login that silently failed means crawling the login page four hundred times and calling it a report. **Microdata and RDFa**: a site marking up in `itemprop` or `typeof` was being told it had no structured data at all. **PDFs**: crawled and audited as documents - title, page count, encryption, and whether there is a text layer, because a scanned PDF looks perfect to a person and is invisible to a search engine. 66 tests, all three verified end to end against local fixtures. |
| 2026-10-18 | Three bugs found while building those: microdata items were invisible because `itemscope` is a valueless attribute and the parser required `name=value`; a parent item absorbed its nested item's properties (a Product acquiring its Offer's price); and `crawl_pdfs` bypassed the exclude list but not `include_extensions`, which is an HTML-only whitelist, so "crawl PDFs" crawled none. Site auth was applied only in `update_config`, so a config assembled any other way started a crawl that quietly fetched the login page for every URL - now applied at crawl start. |
| 2026-10-18 | **Open, not fixed:** a race between the two crawl loops. A URL discovered as the pool shuts down is submitted after shutdown and silently dropped - seen as `Submitting task for: <url>` followed by `cannot schedule new futures after shutdown`. Intermittent, pre-existing, and in a path none of today's work touched. Left alone deliberately rather than destabilising the crawl loop at the end of a long day. |
| 2026-10-18 | **Withdrawn - there is no crawl-loop race.** I reported one after seeing `Submitting task for: <url>` followed by `cannot schedule new futures after shutdown`, and said it silently dropped URLs. It was my test script's fault: the script crashed with an `AttributeError` in its main thread, Python's interpreter shutdown ran `concurrent.futures.thread._python_exit()`, which makes every later `submit()` raise exactly that error, and the still-running crawl thread hit it. Proved by calling `_python_exit()` directly and reproducing the message. Five clean runs of the fixed script crawl both pages every time with no such error. The crawl loop is fine; the bug was in the thing measuring it. |
| 2026-10-18 | Third-party link metrics built - Moz, Majestic and Ahrefs - which was the last buildable row reading "no". The findings are written around the useful question (which pages have earned links, and does the site bury them) rather than reporting a Domain Authority nobody acts on. All three endpoints checked against the live services without a key: Moz answers a POST with 401, Ahrefs with 401. |
| 2026-10-18 | That live check found a real bug before it shipped: **Majestic refuses with HTTP 200 and the reason in the body**, so trusting the status code turned "your API key is wrong" into "this page has no backlinks" - a credentials error wearing the face of a clean result. The captured refusal is now a test fixture. |
| 2026-10-18 | Replaced the live-validation tick with a measurement. 223 slugs reachable in tests, 84 seen across 48 real crawls, 9 firing live with no test emitting them - three with no test at all. Covered those three including their boundaries (160 characters exactly, the moderate/slow response bands not both firing). My earlier "51 slugs fired" tick was weak evidence from a single crawl where 168 of 220 pages never loaded. |
| 2026-10-18 | Full suite run on the host as well as in the container: 1,716 passing locally (router_tests needs a running server, so it is container-only), 1,727 in the container, 0 failing either way. |
| 2026-10-18 | Corrected the stale memory claiming the six the second property bugs were unfixed - all six verified fixed in source by name, with passing regression suites. |
| 2026-10-18 | Validated the never-seen-live checks instead of explaining them away. `tests/coverage_site.py` serves a site built to be wrong in thirty specific ways - a sitemap with duplicates, future and malformed lastmods, mixed hosts and dead URLs; canonicals that are relative, point elsewhere, and point at redirects; a redirect chain; broken and 503 images; two h1s; an empty heading; a skipped level; invalid hreflang; unparseable JSON-LD; a future publish date earlier than its modified date; and response headers chosen to trip every header check at once - then crawls it with the real crawler and records what fires. **58 checks fired, 29 of them from the never-seen-live set**, now proven to run end to end rather than only in unit tests. |
| 2026-10-18 | Of the 119 still never seen live: 12 are user-configured rules rather than built-in checks, 63 need an external input or a special site (Google credentials, log files, PDFs, AMP markup, CMS REST, a paid metrics key), and 44 are ordinary page checks that would need more fixture pages. Stated as a count, not as a reassurance. |
| 2026-10-18 | **Renamed to Leita** - Icelandic for "to search". MIT throughout, Phiality's copyright kept and ours added beside it; `NOTICE` lists every dependency licence and puts axe-core's MPL-2.0 obligation first; the `LibreCrawlPlugin` global is kept as an alias so existing plugins still load. 1,726 tests passing after the rename. The name's story is in `docs/about-the-name.md`. |
| 2026-10-18 | Extended the fixture site - a nine-page link chain for click depth, pagination with rel=next, a canonical chain, 1MB and 3MB pages, tracked internal links, a sitemap index. **35 of the 148 never-seen-live checks are now proven to fire**, up from 29. Depth is link-distance, not path segments: the original "deep" page was one click from the homepage. |
| 2026-10-18 | The fixture then found a real bug by *not* firing. `tracking_parameters_on_internal_links` had the data it needed - internal links with `utm_source` intact - and never appeared. It was appending its raw dict straight to `detected_issues`, skipping `as_standard_issue`, so it reached the UI with **`check=None`**: unfilterable, no stable id for crawl-to-crawl diffing, and a docs link to `/checks#None`. It was the single unlabelled row in the example.com report, which I had tallied as "1 (none)" and not chased. Fixed, with a guard asserting no issue from any path reaches the UI without a slug. |
| 2026-10-18 | Added `tests/coverage_site_https.py`: the same idea over TLS, with a certificate generated at run time and trusted by that process alone through `REQUESTS_CA_BUNDLE` - nothing touches a system trust store. Nine checks could not fire against an http origin, and saying so is not the same as knowing they work. Seven of them now do: `missing_hsts`, `hsts_max_age_too_short`, `cookie_without_secure_flag`, `cookie_without_samesite`, `insecure_form_action`, `mixed_content_active`, `mixed_content_passive`, plus `sitemap_mixed_protocols`. |
| 2026-10-18 | **Coverage, measured: 127 of 232 known checks are now proven to fire in a real crawl**, up from 84 this morning. 42 of the 148 never-seen-live set validated. Still unproven: 106 - 12 user-configured rules, 70 needing an external input or a particular kind of site, and 24 ordinary page checks. `tracking_parameters_on_internal_links` fires correctly now and carries its slug, confirmed end to end on the fixture site. |
| 2026-10-18 | Chasing the checks that would not fire found three slugs that are not checks at all. `broken_link`, `image_missing_alt` and `slow_response` existed only as sample data inside the diff, export and API tests - the real checks are `links_to_broken_urls`, `images_missing_alt` and `slow_response_time`. Harmless to those tests, which only need *a* slug, but they inflated the coverage denominator and would read to anyone else as checks this project has. Renamed to the real ones, so the test data describes the product. |
| 2026-10-18 | Final coverage measurement: **127 of 230 known checks proven to fire in a real crawl.** The 103 unproven are 12 user-configured rules, ~70 gated on an external input (Google credentials, a paid metrics key, log files, PageSpeed data, AMP markup, CMS REST, LanguageTool), and ~21 ordinary page checks that want more fixture pages. 1,731 tests pass. |
| 2026-10-18 | `hreflang_no_return_link` missed the commonest form of the fault it exists to catch. The English page names the French one; the French page simply never got its tags. The check read the target's own declarations and did `if back is None: continue`, so a target declaring *nothing* fell straight through - and the only pairs reported were the rarer kind, where the translation has hreflang but omits one language. The guard above already excludes pages the crawl never fetched, so the skip was defending against nothing. Fixed, with the mutual case and the uncrawled-target case both pinned. |
| 2026-10-18 | Verified reachability in two halves for the checks the fixture could not trigger: the crawler really does populate `page['pagination']` from `rel=next`/`rel=prev` on a live page (confirmed against a running server), and all four pagination checks fire on that shape of input. `canonical_chain` likewise fires correctly - the fixture was at fault there, not the code. Worth saying, because the same investigation that found three real bugs also cleared these. |
| 2026-10-18 | **Stopping point.** What is left cannot be closed from here: a successful authenticated Google call and a successful paid Moz/Majestic/Ahrefs call both need credentials the owner holds, and roughly 70 checks are gated behind those same inputs plus log files, PageSpeed data, AMP markup and CMS REST. Both integrations are written so that the moment credentials exist, one command proves them: `tools/verify_google.py`. |
| 2026-10-18 | **Every AMP check was dead on a live crawl.** They read `page['html']`, and nothing in the crawler ever set that field - so on a real site not one of the thirteen could fire, while all 29 AMP tests passed, because each hands the markup in directly. Correct and unreachable, which is the failure a unit test is worst at noticing. The crawler now keeps the document for pages that declare AMP and only those; keeping every page's HTML would cost hundreds of megabytes on a large crawl for a feature most sites do not use. Five AMP checks confirmed firing end to end against a running server. |
| 2026-10-18 | Two more buckets I had wrongly filed under "needs an external input": **AMP needs only AMP markup** and **log analysis needs only a log file** - neither wants a credential. 8 AMP checks and 3 log checks (`bot_spoofing`, `never_requested_by_search`, `requested_but_not_crawled`) proven to fire, the log ones including reverse-then-forward DNS verification catching a spoofed Googlebot. |
| 2026-10-18 | Added an AMP page, three PDFs and a rendered-faults page to the coverage fixture. **Both of today's check fixes are now proven end to end through a real crawl**, which is stronger than the unit tests that pin them: the eight AMP checks fire on a crawled page (they could not fire at all this morning), and so does `hreflang_no_return_link`. Plus `rendered_layout_overflow`. |
| 2026-10-18 | **137 of 230 known checks now proven to fire in a real crawl**, from 84 this morning. The remaining 93: 12 user-configured rules, 8 CMS (a mock WordPress REST would close these), 8 PageSpeed, 6 Google Search Console, 5 language, 5 log files, 5 PDF, 5 rendered, 4 paid link metrics, 3 GA, and roughly 30 ordinary page checks wanting more fixture pages. |
| 2026-10-18 | Mocked a WordPress REST API at the fixture's own origin, so the CMS checks read a real endpoint rather than a credential: a post with an over-long title, placeholder excerpt text, structured data the CMS holds and the page never serves, an unexpected author, and a published post the site does not serve at all. Six of the eight fire. |
| 2026-10-18 | **143 of 230 known checks proven to fire in a real crawl**, from 84 this morning. Of the 87 left, **13 need credentials I cannot supply** - 9 Google, 4 paid link metrics. The other 74 want fixture work: 22 ordinary page checks, 12 user-configured rules, 9 PageSpeed, 5 each of AMP, log files, language and PDF, 4 rendered, 3 accessibility, 2 agents, 2 CMS. |
| 2026-10-18 | The user-rules mechanism proven in a live crawl: the fixture writes a rules file for the run (only when the install has none, removed afterwards), serves a page carrying a forbidden phrase, and `rule:coverage-banned-claim` fires. Two wrong guesses on the way - `forbid_text` instead of `forbid`, and a `description` key that does not exist - and in both cases the rules engine named the bad key and listed the valid ones, which is how that error should read. |
| 2026-10-18 | The twelve `rule:*` ids leave the denominator. A rule's slug is whatever a site writes in its own rules file; the ones in the known set are ids invented in `tests/rules_tests.py`, so counting them as unproven product checks was the same mistake as the three fake slugs. The capability they stand for is now proven. |
| 2026-10-18 | **Final measurement: 144 of 219 known checks proven to fire in a real crawl**, from 84 this morning. Of the 75 unproven, **13 need credentials I cannot supply** (9 Google, 4 paid link metrics) and **62 want fixture work**. |
| 2026-10-18 | **With JavaScript rendering on, every PDF was reported as unreachable.** Chromium cannot render a PDF, a zip or a font, so sending one there produced a row with status 0, no content type and no size - a healthy file recorded as a dead URL, and before this morning's no-response fix, ten invented content findings on top. Every PDF check was dead for anyone crawling with rendering enabled, which is a common setting. `js_renderer.should_use_javascript()` has always known which URLs not to take and **nothing ever called it**; non-HTML URLs now fall back to the plain fetch. Four PDF checks fire in the fixture crawl where none could before. |
| 2026-10-18 | My first attempt at that fix delegated to `_crawl_url`, which dispatches straight back to the JavaScript path - an infinite loop rather than a fallback. Caught by the same before/after probe that found the bug. It delegates to `_crawl_url_with_requests` now, and a JS crawl and a plain crawl produce an identical row for the same PDF. |
| 2026-10-18 | The AMP fix was half a fix. `pairing_issues` reads the **desktop** page's markup to find its `rel="amphtml"` link, and that page is not itself AMP - so keeping markup only for AMP pages left those three checks exactly as dead as the rest had been. The marker list now includes `rel="amphtml"`. `amphtml_target_not_amp` and `amphtml_target_not_crawled` both fire. |
| 2026-10-18 | More fixture coverage: a sitemap index nested inside an index, a sitemap URL that robots.txt forbids, an inline style on the AMP page, and a desktop/AMP pair. `sitemap_nested_index`, `sitemap_url_blocked_by_robots` and `amp_inline_style` fire. `sitemap_empty` cannot be reached from this fixture by design - it only fires when *no* sitemap on the site lists a single URL, so it needs a site of its own. |
| 2026-10-18 | **153 of 219 known checks proven to fire in a real crawl**, from 84 this morning. 66 unproven: 13 need credentials I cannot supply, 53 want more fixture pages. |
| 2026-10-18 | Last fixture batch: a canonical pointing at a robots-forbidden path, a pagination chain whose next page 404s, and two pages whose JSON-LD only appears (or disappears) once JavaScript runs. `canonical_blocked_by_robots` and `pagination_broken_chain` fire. A handler that accepted a connection and answered nothing - meant to trigger `broken_image` - destabilised the whole crawl, taking the run from 96 checks to 21, so it was reverted rather than kept for one slug. |
| 2026-10-18 | **FINAL: 156 of 219 known checks proven to fire in a real crawl**, from 84 at the start of the day. 63 unproven: **13 need credentials only the owner holds** (9 Google, 4 paid link metrics), 50 want more fixture pages. 1,740 tests pass, 0 fail. |
| 2026-10-18 | **The raw-versus-rendered comparison was comparing rendered against rendered.** `collect()` reads `cfg['raw_html']` to record what the server actually sent, and nothing ever supplied it - while `check()` was handed the *rendered* HTML under that name, so the two sides of the comparison were the same document. JSON-LD injected or removed by JavaScript could never be seen. This is the capability the comparison matrix singles out as something neither competitor has. The renderer now attaches the pre-render body to the page, the way the console listener already did, and `rendered_jsonld_injected` and `rendered_jsonld_removed` both fire. |
| 2026-10-18 | I had said the high-value seam was worked out and the remaining checks were a flat-yield long tail. That was wrong, and this bug was found by testing the claim instead of asserting it. **158 of 219 proven**, 13 credential-blocked, 48 fixture work. |
| 2026-10-18 | **Log analysis was unreachable.** Four log formats, reverse-then-forward DNS bot verification, 36 passing tests - and `src/core/log_analysis.py` was imported by its own test file and by nothing else. No route, no crawl hook, no UI. The comparison matrix claims it as a capability matching a separate product Screaming Frog charges £99/yr for; that claim was false as shipped. Now reachable: `POST /api/logs/analyse` takes a log and a crawl id and returns findings, `GET /api/logs/formats` describes what it reads. The log is read, analysed and discarded - never written to disk, never stored with the crawl, because an access log belongs to whoever uploaded it. Verified live. |
| 2026-10-18 | That is the ninth bug of the same kind today: correct code nothing could reach. Three were whole feature areas (AMP, PDFs under JS rendering, raw-vs-rendered diffing) and this one was a whole module. 1,750 tests pass. |
| 2026-10-18 | **Two of the four agent kinds were imported by nothing.** The agents package docstring describes four - provider, analysts, fixer, monitor - and `__init__` imported one. `analysts` ran during a crawl; `fixer` and `monitor` had no route, no crawl hook, no CLI. The project claimed four agent kinds and shipped two. Found with an AST import graph after `grep` gave two different wrong answers (macOS `grep -E` treats `\b` differently, and words like "monitor" and "report" match half the codebase). |
| 2026-10-18 | Both wired. The **monitor** now decides whether a finished crawl is worth an alert, inside the existing webhook: `assess()` is deterministic so a webhook is never silent just because agents are off, and the model is asked only for a sentence, only when one is configured. Previously every webhook fired with identical urgency whether a crawl found one info notice or knocked forty pages into a 500. The **fixer** gets `/api/fixer/brief` (no model involved, so the brief can be read before anything is sent) and `/api/fixer/run`. |
| 2026-10-18 | **160 of 219 checks proven to fire in a real crawl**, from 84 this morning. 59 unproven: 13 credential-blocked, 46 fixture work. The a11y family now fires live (`a11y_label`, `a11y_link-name`, `a11y_button-name`, `a11y_document-title`, `a11y_image-alt`) from a fixture page with real accessibility faults. |
| 2026-10-18 | UI direction work. Three designs built as a Design canvas (Terminal Pro, Console, Canvas) using real crawl data, for the owner to choose between. Nothing borrowed from the Monocubed design system: it is a marketing-site system - one loud colour spent once, 112px uppercase headlines, drifting mesh - and Leita needs severity colour on every row and type that survives a 5,000-row table. Leita is its own project. |
| 2026-10-18 | **The shell is a grid now.** The eleven view tabs lived *inside* `<header class="topbar">`, so a left rail could not be expressed in CSS at all - they were children of a bar. The nav is a sibling, every direct child of `.app-container` has a named area, and `data-layout="rail"` moves the nav down the left with no markup change. Default layout verified pixel-identical to before (nav at the same x, y and width); `router_tests` 15/15. |
| 2026-10-18 | My first version of that grid named three of the six children and let the rest auto-place, which dropped the URL bar into the nav's cell. Caught by looking at the screenshot rather than the probe, which had reported the nav correctly placed either way. |
| 2026-10-18 | A LanguageTool endpoint mocked at the fixture's own origin, returning real LanguageTool-shaped matches. The language checks read whatever the server says - the slug comes from the category id - so they needed an endpoint, not a credential; the public service is free but rate-limited, and depending on it would make the suite depend on the internet. All five fire: `spelling_mistake`, `grammar_mistake`, `punctuation_mistake`, `casing_mistake`, `style_suggestion`. |
| 2026-10-18 | **165 of 219 proven**, from 84 this morning. 54 unproven: 13 need credentials only the owner holds, 41 want fixture work - mostly PageSpeed (9, needs PSI data), log files (4, needs a crawl-time hook), rendered mode and the remaining sitemap shapes. |
| 2026-10-18 | **A failed PageSpeed run read as a clean bill of performance.** Called without an API key, PageSpeed shares an anonymous quota that is routinely exhausted - a real call while writing this returned 429, "Quota exceeded for quota metric 'Queries'". The retry and backoff handled it correctly and returned `success: False`, and then `vitals_issues` produced nothing at all: no metrics, no issues, no notice. Indistinguishable from a site with excellent Core Web Vitals. Every other integration here says when it could not run; this was the worst one to stay quiet, because performance is what someone switches it on to find out about. Now `pagespeed_unavailable`, info severity, repeating what the API said. |
| 2026-10-18 | My first version of that guard keyed on a `success` flag, which belongs to one caller's shape and is absent from every fixture in the suite - so it reported 14 passing tests as failed runs. The predicate asks whether numbers came back instead. A second pass: an entry with no strategy block at all is malformed input, not a refusal, and claiming PageSpeed could not be fetched would invent a story about a request nobody sent. 1,766 tests pass. |
| 2026-10-18 | The PageSpeed endpoint is configurable. It was the only third-party URL in the crawl path that was not - everything else comes from settings or takes an injected opener, which is what let the Google code be checked against real responses. Also what an install behind a proxy or inside an air-gapped network needs. Five vitals checks now fire through a real crawl: `lcp_poor`, `cls_poor`, `inp_poor`, `first_contentful_paint_poor`, `performance_score_low`. **172 of 219 proven.** |
| 2026-10-18 | The three sitemap size limits, proven. They needed a mode of their own: a sitemap of 50,001 URLs and an index of 51 files would have queued tens of thousands of URLs the crawler was never going to fetch, crowding every other check out of the ordinary run, so `COVERAGE_SITEMAP_LIMITS=1` swaps them in and holds the crawl to three pages. `sitemap_empty` needs a second mode again (`=2`), because it fires only when *no* sitemap on the site lists a single URL and so cannot share a run with any sitemap that works. `sitemap_too_many_urls`, `sitemap_file_limit` and `sitemap_empty` all fire. `sitemap_too_large` is deliberately left unproven: half a gigabyte of fixture traffic to reach one slug is a bad trade, and the 50MB branch sits beside the 50,000-entry branch that is now exercised. First attempt proved nothing - the robots.txt was built with `\\n` in an f-string, so it served one garbled line and neither sitemap was ever discovered. The run reported 54 checks and no new slugs, which is what that failure looks like from the outside. **175 of 219 proven.** |
| 2026-10-18 | The Core Web Vitals middle band, and two more AMP checks. No PageSpeed response the fixture served had ever been in the needs-improvement range - every number was bad - so `lcp_needs_improvement`, `cls_needs_improvement` and `inp_needs_improvement` could not fire however many pages were crawled. The crawl asks PageSpeed about up to three pages (the homepage and two top-level pages), so the mock now reads the `url` it was asked about and gives the homepage the poor numbers that already prove five slugs and everything else the middle ones; both bands appear in one crawl and nothing was traded away. The AMP pair went on a new page rather than the existing one: giving `/amp/` two `<style amp-custom>` blocks would fire `amp_multiple_custom_styles`, but `/amp/` has no amp-custom block at all and that absence is what part of its current eight are reading. `amphtml_missing_canonical` needed an AMP page naming a canonical that never links back. Six gained, counting an accessibility finding on the new page. Default fixture run 119 -> 125 checks. **181 of 219 proven.** |
| 2026-10-18 | **The coverage harness had been lying for hours, and the number it reported was the number I was quoting.** It gave the crawl two minutes, then shut the fixture server down whether the crawl had finished or not. Seven new pages took the crawl past that cap, so the server died while PageSpeed was still calling it and every phase after the fetch - duplication, sitemap audit, technical, header, language, CMS - never ran. The run printed 27 checks instead of 125 and still said **OK**, because `--expect` defaulted to zero: there was no floor to fall through. I read that as my new pages breaking the crawl and spent three runs bisecting them, unlinking the heavy PDF and the mixed-host page in turn, watching the number stay at 27 and concluding each time that the problem was elsewhere. It was never the pages. This also puts the earlier note about "a handler that accepted a connection and answered nothing, taking the run from 96 checks to 21" in a different light - same cap, same collapse, and that fixture may have been reverted for nothing. The harness now waits for the crawl to actually finish (`COVERAGE_CRAWL_TIMEOUT`, default 1800s), says so loudly when it does not, and `--expect` defaults to 120 so a run that loses most of its checks fails instead of passing. |
| 2026-10-18 | With the harness honest, the seven pages it was blamed for all work. A section hub with a child that never links up (`missing_parent_link`); a page spending its own keyword on an anchor pointing elsewhere (`self_cannibalising_anchor`); two pagination sets, one canonicalising to its own page one and one reachable only through `rel="next"` (`pagination_canonical_to_page_one`, `pagination_orphaned_page` - the orphan needed a sitemap entry, because the crawler does not follow `rel="next"` as a link); one host spelled two ways over two schemes (`mixed_host_links`, `mixed_protocol_links`, plus `ssl_tls_error` and `connection_error` for free); and an 11MB PDF (`pdf_too_heavy`). The padding on that PDF went in before `%%EOF` rather than after on the second try - a file that does not end in the end marker reads as damaged, and the damaged branch returns before anything asks how big it is. Default fixture run 125 -> 135 checks, 81 pages, 127 seconds, nothing lost. **188 of 219 proven.** |
| 2026-10-18 | The TLS harness had the same two faults, so it got the same fix: it waited ninety seconds and then measured whatever had happened, and it had no floor at all - it printed OK unconditionally, every time, whatever the number. Both floors were then tested the only way a safeguard can honestly be tested, by giving each an impossible threshold and watching it fail and exit 1. 30 checks over TLS, unchanged, in one second. |
| 2026-10-18 | Two CMS checks and the whole server-log family. `cms_seo_description_too_long` and `cms_stray_whitespace` read the SEO plugin's own fields, not the post title and excerpt, so the mock's deliberately over-long excerpt was never what they were looking at; adding a `yoast_head_json` block gave them something real. The five log checks needed a crawl on one side and a log on the other - never a credential, which is why they sat unproven so long. The harness now runs them over the pages the crawl actually found, against a synthetic log written to disagree with it: Googlebot's user agent from an address reverse DNS will not confirm, a dozen requests to a page that only ever 404s, one path nothing links to, and enough unreadable lines to be worth saying so. `bot_spoofing`, `crawl_budget_wasted`, `log_format_unrecognised`, `never_requested_by_search` and `requested_but_not_crawled` all fire. The log is built in memory and never touches disk. Default fixture run 135 -> 142 checks. **195 of 219 proven.** |
| 2026-10-18 | Five more: page chrome that is in the DOM and never visible, two dates three seconds apart (two reads of one clock rather than two events), a desktop PageSpeed score the mock now answers separately from mobile so the two can diverge by the 30 points that check wants, and both agent slugs. `agent_unavailable` is proven honestly - the crawl is configured with a CLI command that does not exist, which is the only way to reach the note a crawl makes when a configured model cannot be reached. **`agent_weak_experience` is proven weakly and should be read that way:** an analyst's verdict comes from a model, and a fixture cannot have one without a paid key or someone's subscription, so the runner is stubbed to return the low score the check is about. That proves the plumbing - verdict to issue to slug to the UI - and not that any model would score the page that way. Every other entry in this table was earned by a real crawl of a real server producing a real finding; this one was not. 142 -> 147 checks. |
| 2026-10-18 | **Corrected the denominator, which had been wrong in my favour and against me by turns.** The 219 figure predated slugs that have since appeared and still counted `slow_response` as a check - the fourth and last of the fake slugs that only ever existed as sample data, left behind in `mcp_tests.py` when the other three were renamed. Counting every real slug seen anywhere, in the product or in a test: **205 of 222 proven to fire in a real crawl.** The 17 that remain are not a backlog I can work through. Nine want the Google account connected, four want a paid Moz/Majestic/Ahrefs key, one wants a CrUX key, and three cannot honestly be reached at all: `rendered_check_failed` fires only when one of our own builders throws, `no_response` only on a failure the error map does not recognise, and `sitemap_too_large` would cost 50MB of fixture traffic to reach a branch sitting beside the 50,000-entry one already exercised. |
| 2026-10-18 | **Filing fourteen checks as "credential-blocked" was the same mistake, a fifth time.** Search Console, GA4, CrUX and the link-metrics vendors all take an injected opener, which is exactly what the PageSpeed and LanguageTool mocks already exploited. A paid key is what a *live* call needs; what a *check* needs is something that answers. Stubbing only the transport - urlopen and nothing else - leaves the OAuth refresh, the request bodies, the parsers and `issues()` all running real code, and the field names in those payloads are the ones `tools/verify_google.py` checks against Google's published Discovery documents. All nine Google checks fire, all four link-metrics checks fire (including the refusal note, driven by an opener that raises 401), and so does the CrUX field data. What is still untested is Google and Moz being on the other end, which is a smaller claim than "unproven" was.
| 2026-10-18 | `gsc_canonical_overridden` stayed quiet on the first attempt because the inspection budget is spent only on crawled pages with *no* impressions, and I had put the override on a page with performance data, so it was never inspected. `no_external_interest` wants ten or more internal links in; the link was injected into ten pages that already exist rather than ten new pages being crawled for one slug.
| 2026-10-18 | `sitemap_too_large` was declined yesterday as costing "half a gigabyte of fixture traffic". That was wrong by an order of magnitude: 50,000 entries with long paths is 52MB and the run takes **23 seconds**. It has its own mode (`COVERAGE_SITEMAP_LIMITS=3`) and it fires. The reason given for not doing it was the real obstacle, not the cost.
| 2026-10-18 | The last two are genuinely unreachable, and checking rather than asserting that is the point. `classify_fetch_error` can only return one of five values and all five are in the detector's label map, so **`no_response` cannot be produced by any real crawl** - it is the fallback for an error type that does not exist yet. `rendered_check_failed` fires only when one of our own builders throws. Both are safety nets covered by unit tests, and deleting either would turn a future unmapped failure into silence, which is worse than a branch that does not currently execute. They stay, described as what they are.
| 2026-10-18 | **The coverage fixture was flaky and I nearly shipped the flake as a result.** Two consecutive runs of identical code gave 84 pages / 164 checks and 77 / 162; the pages that vanish are always the tail of the one sequential link chain, which is discovered a link at a time. With `js_max_concurrent_pages: 1` it gives 84/164 every time. No error, timeout or render failure is logged, and a minimal reproduction - the same chain plus forty sibling pages, two workers - does **not** reproduce it, so the mechanism is not established and I am not claiming a bug I could not isolate. The fixture now runs one worker for a stable measurement and `COVERAGE_JS_WORKERS=2` reproduces the flake. Chasing it also cost a wrong accusation: my first probe said the crawler never follows a link chain at all, which was a recursive stub in the probe itself serving nothing. The crawler follows a twelve-page chain reliably.
| 2026-10-18 | **Final: 223 of 225 checks proven to fire in a real crawl**, from 84 this morning. The two that remain are the safety nets above. One of the 223, `agent_weak_experience`, rests on a stubbed model verdict and is marked as the weakest entry here; thirteen more rest on stubbed transports with real parsing either side. Default fixture run: 84 pages, 164 checks, deterministic. 1,788 tests, 0 failing. |
| 2026-10-18 | **The accusation against the earlier fixture was wrong, and now it has been checked instead of merely suspected.** A handler that accepts a connection and answers nothing was reverted this morning for "destabilising the whole crawl, taking the run from 96 checks to 21". Put back behind `COVERAGE_SILENT_IMAGE=1`, with the harness now waiting for the crawl: 85 pages, 166 checks, clean exit. It was innocent - the two-minute cap was killing the run while that handler sat in its 90-second silence. It stays opt-in only because it doubles the runtime (291s against 130s) and the two checks it reaches, `broken_image` and `request_timeout`, are already proven by other pages. |
| 2026-10-18 | `agent_weak_experience` is no longer the weak entry it was. It was run against the real model through the configured CLI, on a real crawled page: 404 words of repeated boilerplate, scored **0/10** - "none of it shows the writer has done any plumbing work: no specifics, no jobs, no people, no data" - and the check fired. The first attempt judged nothing because the page was 71 words and the analyst floor is 150, which is the filter working. The fixture still uses a stub by default, because a suite that spends a subscription on every run stops being run; `COVERAGE_AGENT_CLI=<path to claude>` uses the real thing. |
| 2026-10-18 | `no_response` stops being an unproven slug and becomes a proven property. `tests/detector_tests.py` now reads the return values out of `classify_fetch_error`'s own source by AST and asserts every one of them has a label in the detector's map. All five do, which is *why* the `no_response` fallback cannot fire - and if a sixth error type is ever added without a label, that test fails rather than the crawl quietly reporting "No HTTP response received" with nothing in it. The branch is still unreachable; what changed is that this is now enforced instead of observed. |
| 2026-10-18 | **The fixture flake is unresolved and I am not going to pretend otherwise.** Instrumenting link extraction showed something real: every step of the chain registers its successor with the queue otherwise empty (`pending 0->1`), so a link-by-link traversal runs the entire way one observation away from the "no more URLs" branch. That is a structural fragility whatever the trigger. But the symptom was seen twice in four runs earlier and has not reappeared in four attempts since, including the instrumented one, so I cannot demonstrate it on demand. I drafted a confirm-twice guard for the completion check and did **not** apply it: a fix for a fault I cannot reproduce is a change I cannot verify, and the crawler is untouched. The fixture runs one worker, `COVERAGE_JS_WORKERS=2` is the reproduction recipe, and this is written down as an open question rather than a closed one. |
| 2026-10-18 | UI redesign parked at the owner's request - the three directions were rejected as not meeting a modern-SaaS bar, which was a fair read: big number tiles in boxes are a 2019 admin template, and none of the three had the restraint that tier depends on. The grid shell and left rail stay, since those were a structural fix rather than a style. |
| 2026-10-19 | **Google is connected, and the integration has now made real calls against real properties.** `verify_google.py`: **14 passed, 0 failed, 0 skipped**, against live Search Console and live Analytics Admin, every field and enum checked against Google's own Discovery documents. The note that stood on `google_tests.py` all day - "what no one has done yet is make a real call with real credentials against a real property" - is no longer true. Connecting it cost two mistakes worth recording: a click by coordinate landed on the wrong account row and connected one nobody asked for (caught from the status endpoint, disconnected inside a minute, `google_accounts` verified empty, read-only scopes, nothing read), and the consent screen I configured as **Internal** refused the personal account that was actually wanted with `403: org_internal`. Internal is also the wrong setting for a tool meant to serve many sites and be open-sourced; it can never admit anyone outside one Workspace org. |
| 2026-10-19 | Live data, from crawls of two connected properties (32 URLs and 29): 786 Search Console performance rows and 464 GA4 rows. **Five of the six Search Console checks and two of the three Analytics checks now fire on real Google data** - `gsc_low_ctr` (272 findings), `gsc_query_cannibalisation` (17), `gsc_no_impressions`, `gsc_not_indexed`, `gsc_page_not_in_crawl`, `ga_no_sessions`, `ga_page_not_in_crawl`. The two that stayed quiet are the sites not having those faults: `gsc_canonical_overridden` needs Google to have overridden a declared canonical, and `ga_high_bounce` needs a crawled page with 30+ sessions bouncing over 85%. Neither is in the data, so both keep their stubbed proof. |
| 2026-10-19 | **An error message that sent people to the wrong console screen.** Any `SERVICE_DISABLED` error said "enable the Search Console API and the Analytics Data API". Against the real project that was wrong twice over: both of those were already switched on, and the API actually missing was the Analytics **Admin** API, which the message never named. I relayed that wrong advice to the user before checking it with `gcloud`. Google puts `serviceTitle` and an `activationUrl` in the error's `ErrorInfo` details, so the message now quotes the API Google named and links where Google said to go, and falls back to naming none at all rather than guessing. Four tests pin it, including that it must not name an API Google did not. |
| 2026-10-19 | **Renamed Leita -> Toadstone**, 455 replacements across 145 files, and a new mark: two toads leaning in with the stone between them. The first name proposed was *Whispering Toad*, and it was worth saying out loud that this is *Screaming Frog* with both words swapped - same two-part shape, same amphibian, same product category, against the competitor this project's own comparison matrix is aimed at. Searching turned up a second reason: an open-source **SEO Screaming Toad** already exists, pitched as a Screaming Frog alternative. Toad plus a sound-word is occupied twice over. |
| 2026-10-19 | A **toadstone** is a gem people believed grew in a toad's head and wore to detect poison - it was said to change colour near something harmful. The stones were real objects, sold and inherited and listed in wills; they are fossilised *Lepidotes* tooth plates and they never detected anything. That is the name, pointed at ourselves: this project spent its first days discovering that 141 of its 225 checks had never been observed to fire, and every report still came back clean and confident. A detector that has never been shown to detect is jewellery. `docs/about-the-name.md` tells it straight, including the number. |
| 2026-10-19 | Rename mechanics worth keeping: the data volume is a **bind mount** to `./data`, so renaming the compose service and container could not lose the encrypted Google token - verified afterwards, still connected as the same account. No `LeitaPlugin` alias was added beside the existing `LibreCrawlPlugin` one: LibreCrawl is a real upstream whose plugins exist, Leita lasted under a day and was never published, so an alias for it would protect nobody and only look like diligence. The icon went through two drafts - seven degrees of lean read as a drawing error rather than a lean, and without a wide mouth the silhouettes were two penguins. 1,794 tests, 0 failing. |
| 2026-10-19 | Asked whether the top menu could become a mega menu with "all things that are hidden" brought back, I counted what the UI actually exposes first. **Server log analysis, the fixer, link metrics and the MCP server had zero entry points between them** - four subsystems, built and tested today, reachable only by curl. The menu was not merely confusing; a third of the product had no door. All four now have one: logs and the fixer as modals, link-metrics keys and the MCP endpoint as a tenth Settings panel, every one of them also in the mega menu and the palette. |
| 2026-10-19 | A **⌘K command palette**, because a menu teaches what a tool can do and a palette gets you there once you know, and this app had only the first. Two matcher bugs, both found by using it rather than reading it. Scores carry bonuses as negative numbers - a title prefix is worth -60 - and the no-match sentinel was *also* negative, so `score < 0` discarded every strongest match: "exp" returned nothing while Export sat in the registry, and "goog" returned every settings panel except Google. An in-band sentinel sharing a range with real values is how a search quietly hides its best answers. Second, matching ran against title+group+keywords joined, letting a query borrow letters across fields: "gugl" matched **Log out** via lo(g) o(u)t ... si(g)n ... (l)eave. |
| 2026-10-19 | **The tier allow-list sprang for the third time.** `applyTierRestrictions()` hides any settings tab missing from a hard-coded list, so Integrations was invisible to every tier including admin within an hour of being added - exactly how the Google tab was hidden this morning, with the comment describing *that* fix sitting directly above it. Adding the string is not the fix; the fix is that a browser test now asserts every tab in the markup is visible to an admin and opens. Verified the only honest way, by deleting the entry again and watching the test fail and name it. |
| 2026-10-19 | The settings tab strip was `flex-wrap: nowrap` with `overflow-x: auto` and `scrollbar-width: none`: the tabs scrolled, with nothing on screen saying they did. Advanced was clipped and the tenth tab was off the edge entirely. It wraps now, and a test asserts `scrollWidth === clientWidth` so no panel can hide past the edge again. Also fixed: `openSettings()` is async, so every `openSettings(); switchSettingsTab(x)` pair in the new code switched the tab before the modal existed and landed on Crawler. 1,816 tests, 0 failing. |
| 2026-10-19 | **CrUX is live, and calling it "paid" was my error.** I had bundled the Chrome UX Report into a question about paid Moz/Majestic/Ahrefs keys, and the user reasonably said leave it stubbed. CrUX is free. An API key created in `leita-seo` and **restricted to that one API** returned 13 real-user metrics: one property has a 75th-percentile LCP of 3.33s, which fired `field_lcp_needs_improvement` and `field_first_contentful_paint_poor` on genuine field data. the second property and example.com answered "not enough real-user data", which is the error path working. The key was never printed, was held in a 0600 file, and both copies were deleted afterwards. |
| 2026-10-19 | **`ga_high_bounce` was not absent from the data - the crawl could not see it.** With 30 and then 152 pages crawled it stayed quiet, and the easy conclusion was that the property has no high-bounce pages. Asking GA4 directly said otherwise: **54 pages with 30+ sessions, 5 of them at or over the 85% threshold, the worst at 96.2% over 53 sessions**. The check also requires the page to be in the crawl, and a 152-page crawl never reached the blog. At 356 pages it fires five times, exactly the five the data predicted. A check that stays quiet is only good news once you have asked the data whether the fault is there. |
| 2026-10-19 | `gsc_canonical_overridden` stays stub-proven, and this time the absence is demonstrated rather than assumed: 25 pages with impressions were inspected live, and Google agreed with the declared canonical on 20, disagreed on **0**, and reported nothing on 5. The condition is not in this property. **All three Analytics checks and five of six Search Console checks now fire on live Google data.** |
| 2026-10-19 | The mega menu regroups by **what someone is trying to do** instead of by what shape the data is. "Crawl data / Analysis / Shape & speed" described our own tables; it now reads *What was found*, *What is wrong* (with *Act on it* beneath it), *How it fits together* (with *Against other data*), and *This crawl*. The four subsystems that had no door this morning - logs, fixer, link metrics, MCP - all appear in it. 1,816 tests, 0 failing. |
| 2026-10-19 | CrUX is wired for the whole install rather than left as an instruction. `GOOGLE_API_KEY` is now read from the environment when no per-user key is set - the same shape as `GOOGLE_CLIENT_SECRET` - so field data works on every crawl without each person pasting the same string into their own settings. The key lives in `.env`, which is gitignored and verified never committed, and a per-user key still wins over it. |
| 2026-10-19 | **That change broke every PageSpeed check and the suite passed anyway.** The new code called `os.environ`, and the `import os` I added never landed: these source files are **CRLF**, my anchor was `\nimport time\n`, and it matched nothing - while my own script still printed "import os added". The fixture went 164 checks to 154, `pagespeed_unavailable` fired in place of eleven vitals checks, and the run reported **OK**, because the floor was 120. A floor only catches what it sits close to. It is 160 now, and the slug diff - not the pass/fail - is what caught this. |
| 2026-10-19 | `gsc_canonical_overridden` is as closed as honesty allows: **113 pages inspected live across both properties - 43 agree, 0 disagree, 70 not reported by Google.** The condition is not in this data, so the check cannot fire on it, and the success path keeps its stubbed proof. Paid link metrics stay stubbed by the owner's decision, which is theirs to make and not a gap to engineer around. 1,816 tests, 0 failing. |
| 2026-10-19 | `tools/verify_link_metrics.py`, so the paid integrations stop being a permanent unknown. With no key it still does real work: it calls all three live endpoints and asserts each **raises** rather than returning an empty result, because an integration that turns a refusal into `{}` is worse than one that fails - empty reads downstream as "this page has no backlinks", which is a finding, and a wrong one. Run today: Moz and Ahrefs refuse with 401, Majestic answers **HTTP 200 with the reason in the body** and echoes the bad key back. Set `MOZ_ACCESS_ID`/`MOZ_SECRET_KEY`, `MAJESTIC_API_KEY` or `AHREFS_API_TOKEN` and the same command proves the success path and checks the response still carries every field the parser reads. |
| 2026-10-19 | Tested the tool against a broken build rather than trusting it: made Moz swallow its refusal into `{}` and watched the check fail and name it, then restored. Searching for a free tier found none that can be confirmed - the sources are third-party and disagree, Ahrefs Webmaster Tools is free but only for sites you verify and its REST access level is unclear. **The success path needs a purchase, which is the owner's decision and was made: leave it stubbed.** That is where it stays, now one command away from proof whenever that changes. |
| 2026-10-19 | **Two accepted limitations, decided rather than left open.** Everything else in this log is either proven or named as unproven with a route to proving it. These two have no such route, and saying so plainly is the last honest step. |
| 2026-10-19 | **1. Paid link metrics (`authority_page_buried`, `no_external_interest`, `high_spam_score`, `link_metrics_unavailable`).** Proven against a stubbed transport with the real batching, parsing and finding logic running. The refusal paths are verified against the live services - Moz and Ahrefs 401, Majestic HTTP 200 with the reason in the body - and re-checked today by `tools/verify_link_metrics.py`. The success path needs an active subscription to a vendor. That is a purchase, the owner was asked, and the answer was to leave it stubbed. **Accepted.** One command proves it if that ever changes. |
| 2026-10-19 | **2. `gsc_canonical_overridden`.** Proven against a stubbed transport. It fires when Google picks a canonical other than the one a page declares, and **Google has not done that on either connected property: 113 pages inspected live, 43 agreed, 0 disagreed, 70 unreported.** The only ways to make it fire would be to publish duplicate content on a live site and wait for reindexing, or to assert something nobody has observed. Both are worse than a documented zero, in a project whose founding lesson was 141 checks that looked healthy and never fired. **Accepted.** It will fire by itself the first time Google disagrees with a canonical, which is worth knowing anyway. |
| 2026-10-19 | **This log lost 511 of its 515 lines eight commits ago and nobody noticed, including me.** `21b18ee` rewrote `BUILD-LOG.md` down to the four newest entries, and every commit since appended to the stump - 63KB to 3KB, while the file stayed valid Markdown and kept growing. Nothing watched it: it is the one artefact in the project with no test, and it is the one the project uses to decide what is true. Rebuilt from git: the 515-line version at `8328a1c` plus the entries each stub commit added, 26 of them, checked by asserting every text fragment of all nine versions is present in the result. **116 entries, none duplicated.** |
| 2026-10-19 | `tools/verify_build_log.py`, so that cannot happen quietly again. It walks every committed version of the log, collects every dated entry any of them ever carried, and fails if one is missing from the working copy, naming the commit that last had it. An entry may be reworded at its tail - corrections and withdrawals are part of the record - but an entry that vanishes is reported. Tested against the builds known to be broken rather than trusted: `--file` the clobbered `21b18ee` and it exits 1 with 112 missing, the HEAD stub 113; the restored file exits 0. |
| 2026-10-19 | **Restoring the log restored a bug that had been silently dropped.** The guard found a 116th entry in history that my rebuild had missed: `7fe3f46` recorded *"Open, not fixed: a race between the two crawl loops. A URL discovered as the pool shuts down is submitted after shutdown and silently dropped"*, and the very next commit - the rename to Leita - deleted it. It was never recorded as fixed, there is no test for it, and nothing in the source mentions it. It is back, still marked open. Worth asking whether it is the same thing as the JS-worker fixture flake, where the pages that vanish are always the tail of the one sequential link chain: that investigation never had this clue, because the clue had already been deleted. Asking, not claiming. |
| 2026-10-19 | **`python main.py` could not start on this Mac at all, and the two browser suites had therefore never run here.** The port was hardcoded to 5000 in four print statements and the `serve()` call, and macOS gives port 5000 to AirPlay Receiver by default - `ControlCenter` holds `*:5000` and answers `403 AirTunes/960.13.1`, which is why `palette_tests` and `router_tests` timed out waiting for `#urlInput` on a page Apple was serving. Now `--port` / `-p` and `PORT`, named once and used everywhere. With it: **palette 22 passed, router 15 passed**, the first time either has run outside Docker. The whole suite on this machine is 34 suites, **1,862 assertions, 0 failing** (the three `FAIL` lines inside `google_connect_tests` are its own negative controls, each followed by the PASS that says the verifier caught the broken payload). |
| 2026-10-19 | The rename to Toadstone missed both start scripts entirely, including a check that only worked by accident: `docker ps | grep -q librecrawl` matched the *image* name, `librecrawl-toadstone`, which Compose derives from the folder this repo happens to sit in - rename the folder and the one-command start would have reported "container failed to start" on a container that started fine. It now matches the container name exactly (`docker ps --format '{{.Names}}' | grep -qx toadstone`), proven against the running container and against the old check for comparison. Both scripts honour `PORT` (and export it as `EXTERNAL_PORT` for Compose) instead of hardcoding 5000 in the message, the browser and the server separately, and they are `start-toadstone.sh` / `.bat` now. Nothing referenced the old names. |
| 2026-10-20 | **Relicensed to the Elastic License 2.0, which the upstream MIT grant is what permits.** LibreCrawl is MIT, and MIT allows sublicensing, so the combined work can ship under different terms as long as the notice travels with it - which it does, reproduced in full in `LICENSE` above the ELv2 text. Nothing about this changes upstream: anyone may still take LibreCrawl from its own authors under MIT, and `NOTICE` says so in as many words. Worth being precise in public too, so the README and the FAQ both say plainly that ELv2 is **source-available and not OSI open source**, rather than letting "open source" do work it has not earned. |
| 2026-10-20 | **Six screenshots would have published a real connected Google account and a client's crawls, and an assertion caught them rather than a person.** The capture script reads `document.body.innerText` before every shot and refuses to write the file if a banned string is on screen; it refused `compare`, `rules`, `mega-menu`, `server-logs` and `fixer`, all of which carry crawl-history dropdowns, plus the Google panel showing the connected address. The fix was not to paint over them: the gallery now runs against a **clean install with an empty data directory**, and the Google status endpoint is intercepted so the panel renders its own not-yet-connected state - which is the state a reader is in anyway. The same pass found a cookie name belonging to a client's homepage inside the generated check reference, which is why that file now ships no example text at all. |
| 2026-10-20 | **The coverage number had to be re-measured before it could be published, and the first attempt was wrong by 150.** Six fixture modes pointed at one slug log reported **30** distinct checks, because `coverage_site.py` truncates its log at the start of every run - so the total was the last mode's, confidently. One log per mode gives **180 distinct checks fired** (default 162 on 77 pages, silent-image 166 on 85, the three sitemap-limit modes 76/70/75, https 30). That is the number the README quotes, and `tools/coverage_sweep.py` is now the one command that produces it, with the truncation gotcha written into its docstring so nobody repeats it. The log's own earlier figure of 223 counted checks proven at various times under conditions no single sweep reproduces; 180 is what one sweep shows today, and the smaller honest number is the one worth printing. |
| 2026-10-20 | `docs/checks.md`, generated by `tools/generate_checks_doc.py` from the running app's own catalogue: 163 checks described, 59 more that fired in the harness but that this install has never reported through its database, **222 in total**, each marked when the sweep has seen it fire. Listing the 59 rather than dropping them is the point - a reference that silently omits a quarter of the checks is how a tool ends up with checks nobody knows exist. |
| 2026-10-20 | **Every dialog had its body text flush against the left edge while its title did not.** `.modal-body` was `padding:0` with a single exception restoring it for `#dashboardModal`, so the fixer and the log panel - both written later - inherited nothing. The exception was inverted: padding is the default now, and the settings modal, whose tab panels lay out their own, opts out. Found by looking at a screenshot that was about to be published, not by any test. The same look found `Loading\u2026` rendering as literal text in two `<select>` elements, a Python escape that had leaked into the template. |
| 2026-10-20 | The topbar was still wearing LibreCrawl's globe. The mark drawn for this project - two toads leaning in with the stone between them, which has been in `favicon.svg` since the rename - is now in the header too, tile-less, toads in the accent colour and the stone keeping its warm hue, because the stone is the part that means something. |
| 2026-10-20 | `LIBRECRAWL_*` environment variables renamed to `TOADSTONE_*`, with **the old names still honoured** and a test that proves it. An agent runtime's config block is written once and never read again; had the rename dropped those names silently, the MCP server would have come up on its defaults - localhost:5000, read-only, 2,000 URLs - and looked like it was working. Also normalised every machine-specific port out of the docs: `docs/mcp.md` and `docs/agent-handoff.md` were documenting **14727**, which is one developer's `.env`, not anybody's default. |
| 2026-10-20 | **The 1,000-line rule is stated truthfully now rather than aspirationally.** Four files are over it: `web/static/js/app.js` at 4,548, `tests/coverage_site.py` at 1,163, `tests/google_connect_tests.py` at 1,041 and `src/core/rendered_checks.py` at 1,001. The README was about to carry a contributing guide and a pull-request checklist enforcing a rule the repository itself breaks in four places. They are listed as debt in `CONTRIBUTING.md` and in the roadmap, which is the only version of that rule anyone can act on. |
| 2026-10-20 | The README is 840 lines and every image in it is generated: `tools/screenshots.py` crawls the bundled fixture through the UI on a clean install and captures nineteen views, light and dark, and `assets/demo.gif` is a time-lapse of a real crawl from an empty URL bar to grouped findings. Also published: a comparison against eighteen other tools across three matrices, with **no prices in it on purpose** - published prices move, the aggregators disagree with each other and with the vendors, and `nc` against a cell I could not confirm from a vendor's own page is worth more than a tick I would have to defend. |
| 2026-10-20 | **An About tab, because the story was only in the README and the people who read a crawler's findings are rarely the ones who cloned it.** `web/static/plugins/about.js` sits after E-E-A-T and tells it: what a toadstone was, why a crawler is named after a gem that detected nothing, and what the audit found on the day this fork got its name. The last panel is live - it asks this install's own `/api/checks` how many distinct checks it has actually reported, so the claim on screen is about your crawls rather than about marketing. Also who built it, the licence, and the upstream credit, all one click from the strip. |
| 2026-10-20 | **Building it found two faults of exactly the kind this project exists to catch.** The palette's `Why "Toadstone"?` command opened `/docs/about-the-name` - a route that has never existed - in a new tab, where a 404 is least likely to be connected back to the command that caused it. It points at the About tab now. And the plugin's icons silently did not render because it called `window.Icons.render`, which is plausible and absent; the real global is `renderIcons`. Neither threw. `tests/palette_tests.py` now fetches every local URL any command opens and fails on a 4xx - **proven by putting the old command back in the browser and watching the check report it**, because a safeguard tested only against a healthy build is a safeguard nobody has tested. 25 assertions there now, up from 22. |
| 2026-10-20 | Two smaller things the About work turned up: the icons global is still called `LibreIcons`, so `ToadstoneIcons` now points at the same frozen object with the old name kept working; and `docs/plugins.md` is back. The plugin API reference had lived in the README and was lost when the README was rewritten - restored, and corrected against the loader in three places, the important one being that a new plugin must be added to `manualPlugins` in `plugin-loader.js` or it is simply never loaded, failing by doing nothing. |
| 2026-10-20 | **Four audits came back late and found things I had missed, including one I had told the owner was clean.** My personal-data sweep used a regex that never included `spaceo.ai`, so it passed while that domain sat in four tracked files - `docs/agent-handoff.md` (which was not documentation at all but a client engagement brief, complete with that site's real findings, its image filenames and a UTM parameter), `docs/ui-redesign-spec.md`, `tests/rendered_tests.py` and this log. A sweep is only as good as its list, and mine was written from memory of what to look for rather than from what was in the tree. |
| 2026-10-20 | Also from the audits, each verified before acting on it: **four tracked `.lnk` files** shadowing the real `.js` files beside them, each embedding a stranger's hostname and a `D:\` path, inherited from upstream and referenced nowhere; **eight `DEBUG:` prints** dumping internals on every export request; **three different Docker image names** across the docs, none of which Compose would build, now fixed at the source with an explicit `image:` key rather than in three places; and `docker-compose.yml` never forwarding `ANTHROPIC_API_KEY`, which meant the `api` agent mode could not work in Docker at all while `docs/agents.md` described it working. |
| 2026-10-20 | **The CI workflow I wrote weakened the floor this log exists to defend.** It ran the coverage harness with `--expect 120` while the harness's own default is 160 - a default that was raised *from* 120 precisely because 120 passed while every PageSpeed check was broken. The flag is gone; CI uses the code's own number. Writing a pipeline that quietly lowers a safeguard is a neat way to lose the safeguard twice. |
| 2026-10-20 | **`NOTICE` asserted a copyleft fact that was false, and `pip show` settles it in one line.** It said axe-core was "the one component here with a copyleft obligation". `certifi` is **MPL-2.0** and is a required dependency of `requests`, so it is in every install and every image built from this repository. Corrected, with the transitive dependencies listed and a note that a published Docker image is a binary redistribution that must carry their licence texts - which is the obligation most likely to be overlooked by whoever publishes one. |
| 2026-10-20 | **The comparison table had Screaming Frog's MCP server as a cross.** It has one - 29 tools, official, paid licence only, and it can start and control crawls, which is deeper than ours. Ten of the twenty-six tools surveyed ship an MCP server, so "an agent can drive it" is not a differentiator at all and the README now says so in a table of what each one is actually allowed to do. The same pass added **Sitemoth**, which was missing entirely: desktop, free today, 190 checks, axe-core, server logs with fake-bot detection, LanguageTool, and an MCP that can crawl. It is the closest thing to a twin this project has, it is invisible to web search, and a reader who found it after reading a table that omitted it would be right to distrust everything else in the table. |
| 2026-10-20 | Two rows in that table are `nc` across all twenty-six tools: **persistent per-finding identity** and **webhooks**. The claim written down is "no competitor documents equivalent behaviour", never "competitors lack it" - the second is the sentence that gets a README dunked on, and it is not a claim the evidence supports. Prices were left out of the capability tables entirely: the aggregators contradict each other and the vendors, and a README full of 2026 figures is misinformation by 2027. |
| 2026-10-20 | **A Licence tab, next to About.** A source-available licence has terms people need before they build on it, and `LICENSE` in a repository is not where somebody running the app will look. It says what you may do and what you may not in two columns, why upstream's MIT grant is what permits the relicence, and every third-party licence that travels with the software with the two MPL-2.0 ones marked. The dependency table mirrors `NOTICE`, including the `certifi` correction found an hour earlier. |
| 2026-10-20 | The published screenshots and the demo now show **a real site we own** rather than the fixture. A crawler demonstrated against `127.0.0.1:8979` reads as a toy, and every URL in the frame was a localhost one. `tools/screenshots.py` takes `--site` and still defaults to the bundled fixture, so anyone can reproduce the shape of those images without owning a site to point at - which is the part that matters, and the part that would be lost by simply hardcoding ours. |
| 2026-10-20 | `docs/demo/video-script.md` and `docs/demo/launch-post.md`: the demo cut scene by scene, and the launch narrative long, short and one-line. Both carry the same rule, written at the top where it will be read - **no claim appears in either that the repository cannot back on the day it ships**, and every number on screen comes from a command that is also on screen. Both end with a list of what *not* to say: not "most comprehensive", not an MCP server as a differentiator when ten tools ship one and Screaming Frog's is deeper, and never that a competitor lacks a feature when the evidence only supports "no competitor documents it". |
| 2026-10-20 | **The installed app still called itself LibreCrawl.** `web/static/site.webmanifest`, linked from all six templates, carried `"name": "LibreCrawl"` - so anyone installing the PWA got an icon with the upstream project's name on it, while `NOTICE` disclaimed any right to that name and the README said the same. Two lines, and the last place the fork wore someone else's name. Worse under a commercial licence than it ever was under MIT. |
| 2026-10-20 | **"Those portions remain MIT" did not say which portions.** Now it does: this fork diverged at upstream `d0ac5058` (2026-09-08), everything present at that commit is the inherited work, and the 106 commits on top are this fork's. It is the first question anyone asks of a relicensed fork and it cost one `git merge-base` to answer. |
| 2026-10-20 | **The relicence needed a carve-out it did not have, and the gap was created by the relicence itself.** Read across the whole tree, ELv2's hosted-service limitation and its no-altering-notices clause purport to restrict rights in `axe.min.js` that **MPL-2.0 section 3.3 forbids restricting** - the section that lets a Larger Work ship under other terms *provided* the covered files keep their own. Under MIT the question never arose. `LICENSE`, `NOTICE` and the in-app Licence tab now all say that ELv2 does not apply to that file, and to `certifi`, and the full MPL text ships at `src/core/vendor/LICENSE.MPL-2.0` rather than being a URL in a notice. The "unmodified" claim is checkable: sha256 `b511cd9d…`, printed in the vendor README beside the command to verify it. |
| 2026-10-20 | **The contribution terms asked for something a contributor cannot give.** They said a contribution is "licensed on the same terms" - but ELv2 is granted *by a named licensor*, and nobody can grant a licence under which somebody else is the licensor. A contributor can only license their own copyright **to** someone, which is exactly what a project selling a commercial exception needs and did not have. Replaced with a plain-English inbound grant, the explanation of why the old wording was wrong left in rather than quietly corrected, and three things stated in return: you keep your copyright, your work ships to everyone on the same terms you got, and the grant is what makes a future relicence possible without hunting down every contributor. |
| 2026-10-20 | Published: **github.com/spcaeo/toadstone**, public, one commit, 231 files. The repository was created fresh rather than as a GitHub fork, which means GitHub shows no "forked from" banner - so the fork is declared in the README above the fold instead, with the divergence commit `d0ac5058` linked, rather than left to `NOTICE` where a visitor will not look. A relicensed fork that is quiet about being a fork is the one thing this project cannot afford to be. |
| 2026-10-20 | Positioning set on the repository itself: a description that leads with **self-hosted alternative to Screaming Frog**, and twenty topics chosen for how people actually search. `sitebulb-alternative` was removed at the owner's request - their friend's company - and Sitebulb stays in the comparison table where the text is complimentary about it, which is a fair comparison rather than a campaign. `assets/brand/social-preview.png` is the 1280x640 card every shared link becomes; GitHub's API does not expose that setting, so it is generated here and uploaded once by hand. |
| 2026-10-20 | **A live demo, which is the thing a reader can act on before installing anything.** `tools/build_demo_site.py` pulls a finished crawl out of the API and writes one HTML file and one JSON file - the real findings from a real crawl of a site we own, grouped by check, every row opening to its evidence and its fix. 141 pages, 19 findings, 151KB, published from `gh-pages` at **spcaeo.github.io/toadstone**. What it deliberately does not export: page bodies, the link graph, anything about an install's own configuration. A demo is a sample of the output, not a copy of the database. |
| 2026-10-20 | A forty-second launch video, `assets/video/launch.mp4`: the hook, **84 of 225**, "clean was the wrong answer", the sweep printing 180, ten seconds of the real crawl, zero errors on a maintained site, and the end card. Cards rendered at 1920x1080 with a slow push; the middle is the actual app, not a mock-up. Two ffmpeg mistakes worth remembering: `zoompan`'s `d=` will not evaluate `4*30`, and `pad` cannot shrink - scaling 1440x900 footage by width overshot 1080 and the filter graph failed rather than cropping, which is the correct behaviour and took a `force_original_aspect_ratio=decrease` to fix. |
| 2026-10-20 | **The CI job I wrote could never have passed.** `tests/coverage_site.py` exits 2 unless `TOADSTONE_SLUG_LOG` is set - the slug log being the entire point of it - and the workflow called it bare. The first run on the public repository failed on exactly that, in front of a README carrying a tests badge. It runs through `tools/coverage_sweep.py --only default --expect 160` now, which was verified locally by running the identical command: **164 distinct checks, exit 0**. Writing CI that has never been run is the same mistake as writing a check that has never fired, one layer up. |
| 2026-10-20 | The launch film, rebuilt in **snapcn** at the owner's direction - the first cut was ffmpeg over rendered cards, which was text on screen and nothing else. snapcn ships the components this actually needed: `ScreenRecording`, whose camera keyframes let a still screenshot be pushed into and panned across so the viewer is *shown* the part that matters rather than left to hunt for it; `TerminalSimulator` for the sweep typing itself out; `AnnounceTitle` and `PunchLines` for the argument. Fourteen feature shots, each a real screenshot of a real crawl, each captioned with what it is and - where it matters - its check slug. |
| 2026-10-20 | Rendering it needed no Remotion CLI: `@remotion/bundler` and `@remotion/renderer` are already in snapcn's tree, so `video/render.mjs` bundles an entry point and calls `renderMedia` directly. The one piece of glue is a webpack alias for `@/lib/snap-cn-ui`, which every snapcn component imports for its theme and which nothing outside the catalog site would otherwise resolve. |
| 2026-10-20 | **The first live demo was a generated report, and a report is not the software.** Rebuilt as the real thing: `tools/build_demo_app.py` drives a finished crawl in a browser, records every GET the app makes, and writes the server's own HTML plus `web/static` with a shim that answers `fetch` from that map. Tabs switch, filters filter, findings expand, the palette opens, the plugin tabs load - none of it reimplemented. Three wrong turns on the way, each instructive: serialising the rendered DOM made the app's scripts re-run against a page that was already rendered and the plugin API was undefined when the loader asked for it; rewriting `history` before the scripts loaded made every relative asset path resolve against `/crawl/` and 404; and faking the path for the router was the wrong lever entirely - calling the app's own `hydrateFromServer()` is one line and works. 10MB of captured payload trimmed to 2.2MB by dropping page bodies, JSON-LD and image lists, which changes nothing on screen. |
| 2026-10-20 | A trademark notice, at the owner's prompting and overdue: the comparison names twenty-six products, and a topic on the repository is `screaming-frog-alternative`. Added under the comparison itself, in `NOTICE`, and in the in-app Licence tab - those names are used nominatively to identify what is being compared, the comparisons are fair comment drawn from each vendor's own published material, and **Toadstone is not affiliated with, endorsed by or sponsored by any of them.** |
| 2026-10-20 | The README's closing block rendered as overlapping text on GitHub, caught by the owner looking at the published page rather than by anything I ran. A `<div align="center"><sub>` with **blank lines inside it** makes GitHub's renderer lay two paragraphs on top of each other; the same wrapper on a single line is fine. The trademark notice had the second version of the same fault - markdown bold inside a multi-line `<sub>` is not parsed at all, so `**Trademarks.**` would have shipped as literal asterisks. Both rewritten, and a check over the file says no `<sub>` still contains a blank line. Markdown that is only ever viewed in an editor is markdown nobody has tested. |
| 2026-10-20 | **The first cut of the film was bad and I shipped it without watching it.** Four snapcn components, four sets of guessed props: `PunchLines` takes `ground` and `ink`, not `color` and `mode`, and its script is `|`-separated cards rather than newlines - so three scenes rendered **solid black**, which is what the owner meant by "empty screens in between". `AnnounceTitle` has its own colour vocabulary (`paperColor`, `voidColor`, `titleColor`) and ignored `mode="dark"`, so the opening was a white card. The terminal was clipped at both edges. And the camera pushes were 1.35x-2.1x on 1600x1000 stills inside a 1920x1080 frame, which cut the UI off at the edges - a tour of a product in which you cannot see the product. |
| 2026-10-20 | Rebuilt: `ScreenRecording` keeps every product shot, because framing a still and moving a camera over it is the thing that turns a screenshot into a scene and the one component here that genuinely earns its place. Everything else is plain Remotion in this project's own palette, which renders because there is nothing to guess. The push is now 1.1x-1.14x, the captions sit in a corner rather than over the content, and the shots were re-captured **in dark mode** so the product matches the cards instead of flashing white every four seconds. Two scenes added for the thing the project is actually for: `claude mcp add toadstone`, the ten tools, and a real `list_issues` response. The sitemap view was dropped - its coverage donut renders as a solid black disc when nothing is missing, which is a correct chart and a terrible shot. |
| 2026-10-20 | The lesson under all of it, and it is the project's own: **`renderStill` costs seconds and a full render costs seven minutes.** One frame per scene, looked at, would have caught every one of those faults before the first publish. It is the same mistake as a check that has never been watched to fire, one floor up - and the second time today that writing something which has never been run has cost more than running it would have. |
