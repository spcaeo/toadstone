# The name

> For the complete documentation index, see [llms.txt](https://spcaeo.github.io/toadstone/llms.txt). Every page here is also served as Markdown at the same address with `.md` instead of `.html`.

A **toadstone** is a gem that does not exist.

For about five hundred years, educated people across Europe believed that a
jewel grew inside the head of an old toad. Pliny mentions it; so does
Shakespeare, in the line about adversity wearing a precious jewel in its
head. You were supposed to get one by laying a toad on a red cloth until it
gave the stone up. They were set into rings and worn, and they had one job.

**A toadstone was said to detect poison.** Held near something harmful, it
would grow warm, sweat, or change colour. You wore it so that you would know.

The stones were real objects. They were sold, inherited, listed in wills,
and they turn up in museum collections today. They are the fossilised tooth
plates of *Lepidotes*, a Jurassic fish — button-shaped, domed, faintly
polished, and nothing whatever to do with toads. They never detected
anything. People wore them for centuries, and felt safer, and were not.

That is why the name is on this tool.

## The thing this project spent its first days learning

A crawler is a detector. You point it at a site and it tells you what is
wrong. The entire value of it rests on one assumption: that when it says
nothing is wrong, nothing is wrong.

On the day this fork got its own name, somebody measured that assumption for
the first time. The project had 225 checks. **84 of them had ever been
observed to fire.** The rest passed their unit tests and sat inert on every
real crawl — eight AMP checks that could not fire on any site because the
markup they read was never kept; every PDF check, dead the moment JavaScript
rendering was switched on; a raw-versus-rendered comparison that had been
comparing a document against itself; a translation check that missed its
commonest case. A failed PageSpeed run reported as a clean bill of
performance. Three "checks" that were not checks at all, only sample data
left in a test file.

None of that looked broken. Every report came back clean and confident, and
clean was the wrong answer. The tool was a fossil fish tooth in a ring: worn
in good faith, detecting nothing, and all the more dangerous for how
reassuring it was.

Eleven of those faults were found by one method — build a page that *should*
make a check fire, then watch it stay silent. A twelfth was in the measuring
instrument itself, which gave each crawl two minutes and then reported
whatever had happened by then as a pass.

## So: Toadstone

The name is a reminder, pointed at ourselves.

Most tools in this field are named for what they do to a website — they
crawl it, spider it, audit it, scan it. The verb is always something done
*to* a site by something slightly predatory, and the reports read that way:
a wall of findings, scored and ranked, handed over as a verdict.

But a detector that has never been shown to detect is just jewellery. The
question that matters is not how many checks a tool advertises. It is how
many of them have been watched to fire, on a real crawl, against a page
built to be wrong — and what the tool does when it cannot answer, which must
never be to go quiet and let silence read as health.

We count that number in public, in `BUILD-LOG.md`, including the parts that
are stubbed, the parts that rest on someone else's API, and the one check
proven with a model's answer supplied rather than asked for. When a check
cannot honestly be reached, it says so rather than being quietly dropped
from the total.

A toadstone that works would be worth wearing. That is the whole ambition.

---

**Pronunciation.** TOHD-stohn. As it looks.

**Also known as:** *bufonite*, from *bufo*, the toad genus — the name used
when the fossils were finally identified for what they are.

---

## Where Toadstone comes from

Toadstone is a fork of [LibreCrawl](https://librecrawl.com), an open-source
crawler written by Phiality and released under the MIT licence. The crawling
engine, the multi-tenant model and the plugin system are theirs. We renamed
the fork because it has grown its own opinions, not because we think we
started it.

The MIT licence asks only that the original copyright notice travels with the
software. It does, and we go further than the licence requires, because a
single developer giving away work that replaces a £199-a-year product is the
reason any of this exists.

---

Last updated: 2026-10-20. Source: [docs/about-the-name.md on GitHub](https://github.com/spcaeo/toadstone/blob/main/docs/about-the-name.md).
