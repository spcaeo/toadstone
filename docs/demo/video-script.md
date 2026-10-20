# Demo video — script and shot list

Ninety seconds, story-led, outcome first. Built to be cut in
[snapcn](https://snapcn.dev) (Remotion), so every beat below names the shot
type it needs.

The rule for this script: **no claim appears in it that the repository cannot
back.** Every number is one a viewer can reproduce with a command that is
on screen. Nothing is staged, no site is crawled that we do not own.

---

## The spine

| | |
|---|---|
| **Problem** | My crawler said the site was clean. The site was not clean. |
| **Why nothing else fixed it** | Every tool reports what it found. None of them tells you what it is incapable of finding. |
| **What I built** | A crawler that is measured against a site built to be wrong. |
| **The outcome** | 84 checks had ever fired. Now 180 do, and the command that proves it ships in the repo. |
| **The ask** | Clone it. Run one command. Watch it find things on your own site. |

---

## Scene 1 — the hook  ·  0:00–0:10

> **Caption, typed on:**
> "My SEO crawler gave this site a clean bill of health."
>
> *beat*
>
> "It had 225 checks. **84 of them had ever run.**"

**Shot:** `caption` over a dimmed still of a clean-looking report — green, no
findings, confident. Hold on the emptiness. The emptiness *is* the joke.

**Why it opens here:** the viewer has seen a clean report this week. The hook
is not "our tool is good", it is "the report you trusted may have been empty
for a reason you cannot see."

---

## Scene 2 — the thing nobody checks  ·  0:10–0:28

> **Voice / caption:**
> "A crawler is a detector. Its whole value rests on one assumption — that when
> it says nothing is wrong, nothing is wrong."
>
> "Nobody had ever measured that. So I did."

**Shot:** `terminal` session, typed live:

```bash
$ python tools/coverage_sweep.py
--- default                 77 pages crawled, 162 checks fired
--- sitemap-limits-1         3 pages crawled,  76 checks fired
--- sitemap-limits-2         3 pages crawled,  70 checks fired
--- sitemap-limits-3         3 pages crawled,  75 checks fired
--- silent-image            85 pages crawled, 166 checks fired
--- https                    4 pages crawled,  30 checks fired

180 distinct checks fired across 6 mode(s)
```

Let the lines land one at a time. The last line gets its own hold.

**Caption under it:** "Eight AMP checks read markup that was thrown away before
they ran. Every PDF check died the moment JavaScript rendering turned on. A
failed PageSpeed call reported as *perfect performance*."

---

## Scene 3 — the name  ·  0:28–0:40

> **Voice / caption:**
> "A toadstone was a gem people wore to detect poison. They were real objects.
> They were fossilised fish teeth. They never detected anything."
>
> "People wore them for centuries, felt safer, and were not."

**Shot:** the mark animating in — two toads, the stone between them — then the
`About` tab of the running app, scrolled slowly through that passage.

**Why this scene exists:** it is the one moment that is not about features, and
it is the reason anyone remembers the product afterwards.

---

## Scene 4 — it on a real site  ·  0:40–1:05

> **Voice / caption:**
> "Here it is on our own site. One hundred and forty-one pages. Zero errors."
>
> "And nine things worth fixing anyway."

**Shot:** `screen` recording, real: paste `https://freshlycited.com/`, press
Start, counters climb, rows stream in, switch to Issues, collapse the groups.
This is `assets/demo.gif` — reuse those frames at full resolution.

**Caption over the collapsed list, one line at a time, each pointing at its row:**

- `dates_from_one_machine_run` — "every article 'published' milliseconds apart.
  Two reads of one clock, not two events."
- `image_lazy_above_fold` — "four images lazy-loaded where the viewer will see
  them first."
- `csp_unsafe_inline` — "a security header that is present and not doing its
  job."

**The point to land:** these are not "missing meta description". They are the
findings a clean report hides.

---

## Scene 5 — what you get back  ·  1:05–1:20

> **Voice / caption:**
> "Every finding has a stable id. The same defect keeps the same identity
> between crawls — so 'what changed?' has an answer."

**Shot:** `code` block, the real API response, typed in:

```json
{
  "id": "sha1(url + check + offending value)",
  "check": "links_to_broken_urls",
  "evidence": { "found": "1 broken links", "related_urls": ["…"] },
  "how_to_fix": "Update or remove the link; a visitor following it hits an error.",
  "first_seen_crawl": 2
}
```

**Caption:** "An HTTP API, an MCP server, and your crawl data never leaving
your machine."

---

## Scene 6 — the ask  ·  1:20–1:30

> **Caption:**
> "222 checks. 180 of them watched to fire, on a site built to be wrong."
>
> "Self-hosted. No per-URL pricing. No account."

**Shot:** `terminal`, three lines, then the mark and the URL:

```bash
git clone https://github.com/spcaeo/toadstone
docker compose up -d
```

**End card:** the mark · `github.com/spcaeo/toadstone` · "Built by LabsMedia"

---

## Rules for the cut

1. **No stock footage, no fake dashboards.** Every frame is either the running
   app or a terminal that really printed that.
2. **Never claim a competitor lacks something.** The honest line is "no
   competitor documents equivalent behaviour", and it is too long for a caption
   — so the video simply does not go there. The comparison lives in the README
   where it can be qualified properly.
3. **The numbers in the captions must match the repository on the day it
   ships.** 180 and 222 come from `tools/coverage_sweep.py` and
   `docs/checks.md`. If a re-run changes them, the captions change.
4. **Zero errors on our own site is the honest result and stays in.** A demo
   that has to find a catastrophe to be interesting is a demo that will be
   staged next time.

## Assets this needs

| | |
|---|---|
| Mark, SVG | `web/static/favicon.svg` — animates well; the stone can land last |
| Crawl recording | `tools/screenshots.py --site <url>`, or the frames behind `assets/demo.gif` |
| Terminal output | real runs of `tools/coverage_sweep.py` |
| About / Licence panels | `assets/screens/about.png`, `assets/screens/licence.png` |
| Hero stills | `assets/screens/hero-light.png`, `hero-dark.png` |
