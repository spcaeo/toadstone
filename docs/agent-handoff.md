# Agent handoff: fix the SEO issues from a Toadstone crawl

> For the complete documentation index, see [llms.txt](https://spcaeo.github.io/toadstone/llms.txt). Every page here is also served as Markdown at the same address with `.md` instead of `.html`.

Paste this into another Claude Code session (or tell it to read this file and
follow it) after filling in the placeholders:

- `<PATH TO THE SITE'S REPO>`: where the website's code lives
- `<SITE URL>`: the site that was crawled
- `<CRAWL ID>`: the crawl to work from (`/api/crawls/list` shows them)
- `<DEV PORT>`: the port the site runs on locally

---

Fix the SEO issues Toadstone found in crawl `<CRAWL ID>` of `<SITE URL>`,
then re-run the crawl to prove they are fixed.
The website code is in: `<PATH TO THE SITE'S REPO>`

## 1. Connect to Toadstone

Toadstone runs at http://localhost:5000 in local mode (whatever you set
`EXTERNAL_PORT` to; 5000 is the default): no API key, just a
session cookie that you get by opening the home page.

```bash
B=http://localhost:5000; J=/tmp/toadstone-cookies.txt
curl -s -c $J -o /dev/null $B/          # auto-login, saves the session cookie
```

Use `-b $J -c $J` on every call after this.

## 2. Read the crawl

Every crawl is saved by ID. This returns one crawl as JSON: the crawl record
plus all its `issues`, `urls` (page data) and `links`:

```bash
curl -s -b $J -c $J $B/api/crawls/1 > crawl-1.json
```

- `crawl`: `id`, `base_url`, `status`, `urls_crawled`, `started_at`, `completed_at`
- `issues[]`: `url`, `type` (`error` / `warning` / `info`), `category`, `issue`, `details`
- `urls[]`: `url`, `status_code`, `title`, `meta_description`, `h1`, `h2`,
  `canonical_url`, `word_count`, `robots`, `images`, `linked_from`, `response_time`, ...

Quick summary of the issues:

```bash
python3 -c '
import json, collections
d = json.load(open("crawl-1.json"))
for (t, i), n in collections.Counter((x["type"], x["issue"]) for x in d["issues"]).most_common():
    print(f"{n:5}  {t:8} {i}")'
```

A human can look at the same crawl at http://localhost:5000/crawl/1/issues?severity=error

## 3. Rules

- Use only the HTTP API. Never open `data/users.db` directly: reading it from
  outside the container while the app writes has corrupted it before.
- Never restart or rebuild the `toadstone` Docker container, and never delete
  crawls you did not create. The user may have a crawl running.
- Don't change Toadstone settings. They are shared with the user's account.

## 4. What to fix

Read the findings rather than being told them: `/api/crawls/<id>/issues`
returns every one, with a `check` slug, an `evidence` block and a `how_to_fix`
line. Group by `check` before you start — forty pages missing a canonical is
one template, not forty jobs.

A worked example, from a crawl of the fixture site that ships in this repo
(`python tests/coverage_site.py` serves it), so you can reproduce the shape
before pointing this at anything real:

```
 60 x no_structured_data        error    one layout, one fix
  4 x broken_image_404          error    3 files, referenced from the shared header
  4 x links_to_broken_urls      error    evidence.related_urls names each one
  2 x a11y_image-alt            error    axe-core rule, same component twice
  1 x canonical_cross_domain    error    a copied template kept the old domain
  1 x modified_before_published error    a date field written in the wrong order
 64 x meta_description_too_short warning  a generator truncating at 50 chars
```

Seven groups, not 136 jobs. The counts come from the `check` field; the
`evidence.related_urls` and `evidence.found` fields tell you which file to open
without re-fetching the page.

Work through the issues by priority (errors first), grouping fixes by page
template rather than editing page by page. Show me the plan before large
changes.

## 5. Re-run the crawl after fixing

### 5a. Pick what to crawl

- **Before deploying**: run the site locally and crawl the dev server.
  Toadstone runs in Docker, so from its point of view the host machine is
  `host.docker.internal`, not `localhost`:
  `http://host.docker.internal:<DEV PORT>/`
- **After deploying**: crawl production, `https://example.com/`

Make sure the dev server is reachable first:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:<DEV PORT>/   # expect 200
```

### 5b. Start the crawl and note its ID

```bash
TARGET="http://host.docker.internal:<DEV PORT>/"
NEW=$(curl -s -b $J -c $J -H 'Content-Type: application/json' \
        -d "{\"url\": \"$TARGET\"}" $B/api/start_crawl \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["crawl_id"])')
echo "New crawl: $NEW   watch it at $B/crawl/$NEW"
```

It uses the user's saved settings (JavaScript rendering, depth, robots.txt,
and so on), so results are comparable with crawl 24.

### 5c. Wait for it to finish

The crawl runs in your session; `crawl_status` reports on it:

```bash
while :; do
  STATUS=$(curl -s -b $J -c $J "$B/api/crawl_status?stats_only=1" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d["stats"]; print(d["status"], s["crawled"], "/", s["discovered"])')
  echo "$STATUS"
  case "$STATUS" in running*) sleep 10 ;; *) break ;; esac
done
```

It ends on `completed`. The site has about 90 pages; with JavaScript rendering
this takes 2-3 minutes. `POST $B/api/stop_crawl` stops it early.

### 5d. Read the new run

Exactly like step 2, with the new ID:

```bash
curl -s -b $J -c $J $B/api/crawls/$NEW > crawl-$NEW.json
```

### 5e. Compare with crawl 1

Issues are matched by page path and issue name, so a dev-server crawl can be
compared with the production crawl:

```bash
python3 - 1 $NEW <<'EOF'
import json, sys
from urllib.parse import urlsplit

def issues(crawl_id):
    data = json.load(open(f'crawl-{crawl_id}.json'))
    keyed = {}
    for i in data['issues']:
        u = urlsplit(i['url'])
        path = (u.path.rstrip('/') or '/') + (f'?{u.query}' if u.query else '')
        keyed[(path, i['issue'])] = i['type']
    return keyed

old_id, new_id = sys.argv[1], sys.argv[2]
old, new = issues(old_id), issues(new_id)
fixed = sorted(old.keys() - new.keys())
still = sorted(old.keys() & new.keys())
added = sorted(new.keys() - old.keys())
print(f'crawl {old_id} -> {new_id}: {len(fixed)} fixed, {len(still)} remaining, {len(added)} new')
for title, rows, src in (('NEW (regressions)', added, new), ('REMAINING', still, new), ('FIXED', fixed, old)):
    print(f'\n{title}:')
    for path, issue in rows:
        print(f'  {src[(path, issue)]:8} {issue:32} {path}')
EOF
```

Anything under **NEW** is a regression introduced by the changes; fix it before
reporting back.

### 5f. Report back

Give the user:

- the new crawl ID and its links:
  `http://localhost:5000/crawl/<NEW>/issues` (all issues) and
  `http://localhost:5000/crawl/<NEW>/issues?severity=error` (errors only)
- the fixed / remaining / new counts from 5e, and what is left and why

## 6. Finding runs later

```bash
# Latest crawls, newest first: id, site, status, pages
curl -s -b $J -c $J "$B/api/crawls/list?limit=10" \
  | python3 -c 'import json,sys; [print(c["id"], c["base_url"], c["status"], c["urls_crawled"], c["started_at"]) for c in json.load(sys.stdin)["crawls"]]'

# One crawl's status only
curl -s -b $J -c $J $B/api/crawls/<ID> | python3 -c 'import json,sys; c=json.load(sys.stdin)["crawl"]; print(c["status"], c["urls_crawled"], c["completed_at"])'
```

In the browser, every crawl has a page at `http://localhost:5000/crawl/<ID>`,
and the dashboard (`http://localhost:5000/?dashboard=1`) lists them all with
their IDs.

## Reusing this for another crawl

Everything except section 4 works for any crawl: replace `1` with the crawl ID
from the dashboard's **ID** column (or the number in `/crawl/<id>/...`), and
let the agent build its own fix list from the issues in step 2.

---

Last updated: 2026-10-20. Source: [docs/agent-handoff.md on GitHub](https://github.com/spcaeo/toadstone/blob/main/docs/agent-handoff.md).
