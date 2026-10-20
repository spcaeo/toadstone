# Changelog

Dated entries, newest first. The day-by-day engineering record — including the
wrong turns, which this file leaves out — is [BUILD-LOG.md](BUILD-LOG.md).

## 2026-10-20 — first public release

**Licence.** Relicensed to the [Elastic License 2.0](LICENSE): source-available,
not OSI open source. Self-host it, modify it, use it on your own and your
clients' sites; you may not offer it to third parties as a hosted service
without a commercial licence. Portions inherited from LibreCrawl remain MIT,
and MIT's sublicensing grant is what permits the relicence.

**Added**
- An **About** tab telling the project's story, with a live panel that reports
  how many distinct checks *your* install has actually seen fire.
- `--port` / `PORT`. The server port was hardcoded to 5000, which on macOS
  belongs to AirPlay Receiver — so `python main.py` could not start on a Mac at
  all, and the two browser test suites had never run outside Docker.
- `tools/coverage_sweep.py` — runs every fixture mode and prints how many
  distinct checks fired. **180**, last run.
- `tools/screenshots.py` — regenerates every image in the README, and refuses
  to write a screenshot whose page contains a personal identifier.
- `tools/generate_checks_doc.py` and [docs/checks.md](docs/checks.md) — 222
  checks, generated from the running app's own catalogue.
- `tools/verify_build_log.py` — fails if any entry the build log ever carried
  has gone missing. It found one that had been deleted eight commits earlier.
- [docs/plugins.md](docs/plugins.md), [SECURITY.md](SECURITY.md),
  [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), issue and PR templates, and a CI
  workflow covering the suites, the browser tests, the coverage floor and the
  build log.

**Changed**
- `LIBRECRAWL_*` environment variables are now `TOADSTONE_*`. **The old names
  still work**, with a test that proves it: an agent runtime's config block is
  written once and never read again.
- One Docker image name (`toadstone:latest`), pinned in `docker-compose.yml`
  rather than derived from whatever the containing folder is called.
- `docker-compose.yml` forwards `ANTHROPIC_API_KEY`, without which the `api`
  agent mode could not work in Docker at all.
- The topbar carries this project's own mark instead of the inherited globe.

**Fixed**
- Every dialog had its body text flush against the left edge while its title
  did not: `.modal-body` was `padding: 0` with a single exception restoring it.
- The command palette's *Why "Toadstone"?* opened `/docs/about-the-name`, a
  route that has never existed. There is now a test that fetches every local
  URL any command opens.
- `Loading\u2026` rendered as literal text in two dropdowns.
- The CI coverage floor was 120 while the harness's own default is 160 — the
  default was raised *from* 120 because 120 passed while every PageSpeed check
  was broken.
- `NOTICE` claimed axe-core was the only copyleft component. `certifi` is
  MPL-2.0 and ships in every install and every image.
- Eight `DEBUG:` prints dumping internals on every export request.
- Four tracked Windows shortcut files, each embedding a stranger's hostname and
  a `D:\` path, shadowing the real `.js` files beside them.

**Known**
- `web/static/js/app.js` is 4,548 lines against the project's own 1,000-line
  rule. Three other files sit just over it. Listed as debt rather than
  quietly exempted.
- A race between the two crawl loops, where a URL discovered as the pool shuts
  down is submitted after shutdown and lost. Recorded as open, not fixed.
- `gsc_canonical_overridden` and the paid link-metrics checks are proven
  against stubbed transports; see BUILD-LOG.md for exactly what that does and
  does not establish.
