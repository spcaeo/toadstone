# Launch post

The shape is the one that works: the problem first, why the obvious tools do
not solve it, what was built, what you know afterwards, then the ask. Written
to be posted as-is.

---

## LinkedIn

Post the video **natively** — the feed suppresses posts whose payload is an
outbound link, and native video reaches several times further. Upload the 1080p
master, not the 720p copy. One link in the body (the demo); GitHub goes in the
first comment.

> I run an SEO team of 22 people. Screaming Frog is licensed per machine.
>
> That's not a complaint about the price. It's a good tool and worth its
> licence. The shape is wrong for how we work.
>
> We're in Ahrefs every day. We need a full technical crawl maybe five or six
> times a month. Paying a seat for every executive so each of them can run a
> crawl a handful of times a month is paying for the wrong thing.
>
> What I wanted was one instance I host myself, on my own Mac, that the whole
> team can reach — and that Claude Code can talk to directly over MCP, so a
> crawl comes back as a list of issues to fix rather than a CSV someone has to
> open, read and turn into tickets.
>
> Nothing available had that shape. So I forked an open-source crawler and
> started building.
>
> Then I found something I wasn't looking for.
>
> I counted how many of its checks had ever actually run on a real page. It had
> 225 checks. Eighty-four of them had ever fired.
>
> Eight AMP checks read markup that was thrown away before they ran. Every PDF
> check died the moment JavaScript rendering was switched on. And a failed
> PageSpeed call — the API returning a quota error — reported back as a clean
> bill of performance, which looks exactly like a perfect site.
>
> That's the real problem, and it isn't specific to one tool. Every crawler
> tells you what it found. None tells you what it is incapable of finding. You
> can't tell a silent check from a passing one.
>
> So Toadstone now ships a site that is wrong on purpose, crawls it with the
> real crawler, and records which checks actually appear. 180 of them, printed
> by one command anyone can run.
>
> What my team has now:
>
> → one self-hosted instance, no per-seat licence, no per-URL pricing
> → Claude Code connected over MCP: "crawl this and tell me what to fix" comes
> back as grouped findings with evidence and a fix line — unmetered, because
> they're our crawls
> → every finding keeps the same id between crawls, so "what changed since last
> week" has an answer
> → Search Console, Analytics, server logs and axe-core accessibility in the
> same report
>
> On a site we maintain: 141 pages, zero errors, and nine things worth fixing
> anyway.
>
> Live demo, no sign-up — it's the actual interface, not a screenshot:
> https://spcaeo.github.io/toadstone/
>
> Free to self-host with Docker or one command. The crawl never leaves your
> machine.

### Where the files are

| | |
|---|---|
| Native upload (1080p, 1:46) | the Remotion render — `video/toadstone-launch.mp4` in the snapcn project |
| Web copy (720p, 5.5MB) | [`assets/video/launch.mp4`](../../assets/video/launch.mp4) |
| Shareable page | https://spcaeo.github.io/toadstone/watch.html — for Slack, email, X, anywhere video cannot be uploaded |
| The demo itself | https://spcaeo.github.io/toadstone/ |

## Long version, for a blog or a README

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
