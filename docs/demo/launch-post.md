# Launch post

Same shape as the Skald post: the problem first, why the obvious tools do not
solve it, what was built, what you know afterwards, then the ask. Written to be
posted as-is.

---

## Long version

I had a problem: my SEO crawler kept telling me a site was clean. The site was
not clean.

So I counted something nobody counts. The crawler had **225 checks. Eighty-four
of them had ever fired** — on any page, ever. The rest passed their unit tests
and sat there.

Eight AMP checks read markup that was thrown away before they ran. Every PDF
check died the moment JavaScript rendering was switched on. A raw-versus-rendered
comparison had been quietly comparing a document against itself. And a failed
PageSpeed call — the API returning a quota error — reported back as *a clean
bill of performance*, which is indistinguishable from a perfect site.

None of it looked broken. Every report came back green and confident, and green
was the wrong answer.

No other tool fixes this, because it is not a feature any of them ship. Every
crawler tells you what it found. **None of them tells you what it is incapable
of finding.** You cannot tell a silent check from a passing one, and a report
with nothing in it looks the same either way.

So I rebuilt it as Toadstone — named after a gem people wore for five hundred
years to detect poison. The stones were real. They were fossilised fish teeth.
They never detected anything. People wore them, felt safer, and were not.

The fix was not more checks. It was a site, in the repository, built to be
wrong in as many ways as possible — then crawled by the real crawler, recording
which checks actually appear.

Now, one command and anyone can see the number:

    python tools/coverage_sweep.py
    → 180 distinct checks fired across 6 mode(s)

That is what changed. Not "222 checks" on a feature page — **180 of them
watched to fire**, against a site that is wrong on purpose, with the command
printed in the README so you do not have to take my word for it.

On our own site it crawled 141 pages, found **zero errors** — and nine things
worth fixing anyway. Articles "published" milliseconds apart, because the dates
were two reads of one clock rather than two events. Images lazy-loaded where
the visitor sees them first. A security header present and not doing its job.
The findings a clean report hides.

It is self-hosted, so the crawl and the data never leave your machine. No
per-URL pricing, no seats, no account. It reads Search Console and Analytics if
you connect them, analyses your server's own access log, audits accessibility
with axe-core, and ships an MCP server so an agent can query your crawls
directly — unmetered, because they are your crawls.

Every finding carries a stable id — `sha1(url + check + offending value)` — so
the same defect keeps the same identity between crawls. "What changed since
last week" has an actual answer, and an issue you accepted stays accepted.

Free to self-host, Docker or Python:

https://github.com/spcaeo/toadstone

Built by LabsMedia. Source-available under the Elastic License 2.0 — read it,
run it, modify it, use it on your clients' sites; just do not resell it as a
hosted service.

---

## Short version, for X / Mastodon

My SEO crawler said the site was clean.

It had 225 checks. 84 of them had ever fired.

Eight AMP checks read markup discarded before they ran. Every PDF check died
under JS rendering. A failed PageSpeed call reported as perfect performance.

So I built a site that is wrong on purpose, and measured the crawler against
it. One command prints the number:

    python tools/coverage_sweep.py
    → 180 distinct checks fired

Self-hosted. No per-URL pricing. github.com/spcaeo/toadstone

---

## The one-liner

*A toadstone was a gem people wore to detect poison. It never detected
anything. This one is named after that, on purpose — and it ships the command
that proves it works.*

---

## What not to say

- **Not** "the most comprehensive SEO crawler". It is not, and Screaming Frog
  will still be better at most of what both do.
- **Not** "an MCP server for your SEO data" as a differentiator. Ten tools ship
  one, Screaming Frog's is deeper. The honest version is *unmetered, over your
  own crawls, on your own machine*.
- **Not** any claim that a competitor lacks a feature. The defensible phrasing
  is "no competitor documents equivalent behaviour", which is too long for a
  post — so the post does not go there at all.
- **Not** a number that is not reproducible from the repository that day.
