# Contributing

Toadstone has one unusual rule, and it is the reason the project is worth
contributing to: **a check is not finished until it has been seen firing in a
real crawl.** Everything below follows from that.

## The short version

```bash
git clone https://github.com/spcaeo/toadstone
cd toadstone
pip install -r requirements.txt
playwright install chromium
python main.py --local --port 5057       # http://localhost:5057
python tests/fixture_tests.py            # any suite is a plain script
```

No framework, no runner, no config. Every suite in `tests/` is a Python script
that prints `PASS` / `FAIL` per behaviour and exits non-zero if anything failed.

## What makes a change landable

1. **A test that fails before your change and passes after.** Not a test that
   agrees with the code — those are free and prove nothing. See
   `tests/google_tests.py` for the pattern: it drives its assertions from
   Google's published Discovery documents rather than from what we remember the
   API looking like, because code and its tests written from the same memory
   agree with each other and are both wrong.
2. **A new check must fire in `tests/coverage_site.py`.** That harness serves a
   deliberately broken site, crawls it with the real crawler and records which
   check slugs actually appear. A check that cannot be made to fire there is a
   check nobody can trust. If it genuinely cannot fire without a credential,
   stub the *transport only* — leave the parsers and the finding logic running
   as real code — and say so in the pull request.
3. **Prove a safeguard against something known to be broken.** If you add a
   guard, break the thing it guards and show it failing. A safeguard tested
   only against a healthy build is a safeguard nobody has tested.
4. **No new file over 1,000 lines**, Python or JavaScript — split it instead.
   Four files are over it today and are counted as debt rather than quietly
   exempted: `web/static/js/app.js` (4,548), `tests/coverage_site.py` (1,163),
   `tests/google_connect_tests.py` (1,041) and `src/core/rendered_checks.py`
   (1,001). Making one of them smaller is a welcome pull request; making one of
   them bigger needs an argument.
5. **The full suite passes.** 35 suites, 1,907 assertions:

   ```bash
   failed=""
   for t in tests/*_tests.py; do
     case "$t" in tests/palette_tests.py|tests/router_tests.py) continue ;; esac
     python "$t" || failed="$failed $t"
   done
   [ -z "$failed" ] || { echo "FAILED:$failed"; exit 1; }
   ```

   The two skipped suites drive a real browser and need the app running, so
   they get their own run — and the loop above exits non-zero rather than
   echoing a failure and returning success, which is what it used to do:

   ```bash
   python main.py --local --port 5057 &
   TOADSTONE_URL=http://127.0.0.1:5057 python tests/palette_tests.py
   TOADSTONE_URL=http://127.0.0.1:5057 python tests/router_tests.py
   ```

## Style

Match the file you are editing. Beyond that: name things after what they are
for rather than what they are made of, comment the *why* and never the *what*,
and prefer a plain function to a clever one. Error messages are read by people
having a bad day — say what happened and what to do next.

## Reporting a bug

The useful ones include the URL or page that triggered it (or the HTML, which
does not change under you), what you expected, what happened, and the crawl
settings. If it is a false positive from a check, the finding's `check` slug and
its `evidence` block is the whole report.

**Do not open a public issue for a security problem** — see
[SECURITY.md](SECURITY.md).

## Pull requests

One thing per pull request. Say what changed and what you did to convince
yourself it works; "the suite passes" is necessary and is not that. If you found
something you did not fix, write it down in the PR rather than leaving it for
someone to rediscover.

## Licensing of contributions

This section used to say "your contribution is licensed on the same terms",
which does not work and is worth explaining rather than quietly fixing. The
Elastic License 2.0 is granted *by a named licensor* — Space-O Technologies.
You cannot grant a licence under which somebody else is the licensor. You can
only license your own copyright **to** someone, and that is what is needed here,
because this project sells a commercial exception and cannot sell what it does
not hold.

So, plainly. **By opening a pull request you grant Space-O Technologies a
perpetual, worldwide, non-exclusive, royalty-free, irrevocable licence to use,
reproduce, modify, distribute and sublicense your contribution, under the
Elastic License 2.0 and under any other terms, including commercial terms.**

In return, and so this is not a one-way street:

- **You keep your copyright.** This is a licence, not an assignment. Nothing
  stops you using your own contribution anywhere else, under any terms.
- **Your contribution ships under ELv2 like everything else**, so every user of
  this project gets it on the same terms you got the rest.
- If the project is ever relicensed, the grant above is what makes that
  possible without hunting down every contributor — which is the honest reason
  it is worded this way.

You also confirm that the contribution is yours to give: that you wrote it, or
have the right to submit it, and that if you are contributing on behalf of an
employer you are permitted to. If you cannot agree to that, say so in the pull
request rather than not contributing — a patch we can only take as a bug report
is still worth having.

## Upstream

Toadstone is a fork of [LibreCrawl](https://librecrawl.com) by Phiality. If your
change is a general crawler improvement rather than something specific to what
this fork adds, upstream will serve more people — send it there first.
