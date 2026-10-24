# Toadstone MCP server

> For the complete documentation index, see [llms.txt](https://spcaeo.github.io/toadstone/llms.txt). Every page here is also served as Markdown at the same address with `.md` instead of `.html`.

Ten tools that let a coding agent read a crawl, find what is wrong with a
site, and — if you let it — start a crawl and mute an issue. It speaks the
Model Context Protocol, so Claude Code, Claude Desktop and Cursor can use it
directly.

It is a wrapper over Toadstone's own HTTP API, not a second implementation of
it. Every answer comes from a route you could call with `curl`
(`docs/agent-handoff.md` shows how), trimmed to what fits in a model's context
and bounded so a 20,000-issue crawl answers in a readable page.

It is **read-only by default**, it will **only crawl hosts you have already
crawled**, and it caps a crawl at **2,000 URLs**. All three are configurable
and all three are explained below.

---

## Quick start

Toadstone must be running. In local mode there is no key to set up:

```bash
./start-toadstone.sh          # or: python main.py --local
python -m src.mcp --print-config
```

`--print-config` prints the guardrails and exits, which is the fastest way to
check that a config block does what you meant.

### Claude Code

```bash
claude mcp add toadstone -- python -m src.mcp
```

or add it to `.mcp.json` in your project (so your team gets it too):

```json
{
  "mcpServers": {
    "toadstone": {
      "command": "python",
      "args": ["-m", "src.mcp"],
      "cwd": "/absolute/path/to/Toadstone",
      "env": {
        "TOADSTONE_MCP_URL": "http://localhost:5000"
      }
    }
  }
}
```

### Cursor

`~/.cursor/mcp.json` for every project, or `.cursor/mcp.json` for one:

```json
{
  "mcpServers": {
    "toadstone": {
      "command": "python",
      "args": ["-m", "src.mcp"],
      "cwd": "/absolute/path/to/Toadstone",
      "env": {
        "TOADSTONE_MCP_URL": "http://localhost:5000"
      }
    }
  }
}
```

### Letting the agent start crawls

Writes are off until you say otherwise, and the host allow-list is separate
from the write switch, so turning writes on does not open the crawler on the
internet:

```json
{
  "mcpServers": {
    "toadstone": {
      "command": "python",
      "args": ["-m", "src.mcp", "--allow-writes", "--allow-host", "staging.example.com"],
      "cwd": "/absolute/path/to/Toadstone",
      "env": { "TOADSTONE_MCP_URL": "http://localhost:5000" }
    }
  }
}
```

### A Toadstone that is not in local mode

Give the server an account. It logs in once and keeps the session:

```json
"env": {
  "TOADSTONE_MCP_URL": "https://toadstone.internal",
  "TOADSTONE_MCP_USERNAME": "agent",
  "TOADSTONE_MCP_PASSWORD": "..."
}
```

`TOADSTONE_MCP_COOKIE` takes a raw `name=value; name=value` cookie string
instead, if you would rather not put a password in a config file.

---

## The tools

Every tool returns one JSON object in a text block. Lists report the total
they were drawn from, so a trimmed answer never looks like a complete one.

| Tool | Arguments | What you get |
|---|---|---|
| `list_crawls` | `limit`, `status` | Recent crawls: id, URL, status, page counts. Start here. |
| `crawl_status` | `crawl_id` | Status, pages done, pages found, ETA. Poll after `start_crawl`. |
| `list_issues` | `crawl_id`, `severity`, `check`, `category`, `url_prefix`, `status`, `since_crawl`, `include_accepted`, `limit`, `offset` | The issues, with a tally by severity and by check. The workhorse. |
| `issue_detail` | `crawl_id`, `issue_id` | One issue by its stable id, plus a summary of the page it sits on. |
| `page_detail` | `crawl_id`, `url`, `baseline`, `include_links`, `link_limit` | One URL: the stored row, its headings tree, its issues, links both ways, and how it changed since the previous crawl. |
| `regression_diff` | `baseline_crawl_id`, `candidate_crawl_id`, `limit` | New, fixed and still-open issues between two crawls, matched by stable id. |
| `export_dataset` | `crawl_id`, `dataset`, `format`, `limit`, `severity`, `check`, `url_prefix`, `status_code` | A whole dataset as csv, json or ndjson. |
| `check_reference` | `check` | What a check means, how severe it is, how to fix it. |
| `start_crawl` **(write)** | `url`, `render`, `max_urls`, `max_depth` | Starts a crawl, returns its id. |
| `accept_issue` **(write)** | `issue_id`, `reason`, `base_domain` | Mutes one issue with a reason. |

### Datasets

`export_dataset` serves the same twelve datasets as
`GET /api/crawls/<id>/export/<dataset>`:

`issues`, `pages`, `links`, `graph-nodes`, `graph-edges`, `sitemap-files`,
`sitemap-coverage`, `duplicates`, `redirects`, `broken-links`, `headings`,
`images`.

`csv`, `json` and `ndjson` come back as text. `xlsx` and `zip` are binary
downloads and cannot travel through MCP; the tool refuses them and hands you
the HTTP URL that will serve the file.

### Issue ids

An issue id is `sha1(url + check + offending value)`, so the same defect keeps
the same id from crawl to crawl: that is what makes `regression_diff` and
`accept_issue` meaningful. Two consequences worth knowing:

- An id is **stable across crawls but not unique to one**, so `issue_detail`
  takes a `crawl_id` as well. There is no `GET /api/issues/<id>` to wrap.
- Because the URL is part of the hash, two crawls of one site served from
  **different hosts** (staging against production) read as entirely new and
  entirely fixed. `regression_diff` says so in its own output.

### A typical session

```
list_crawls                              -> crawl 86, completed
list_issues  crawl_id=86 severity=error  -> 12 errors, tallied by check
check_reference check=missing_canonical  -> what it means, how to fix it
page_detail  crawl_id=86 url=...         -> the page, its links, its diff
<fix the site>
start_crawl  url=https://site.test       -> crawl 87
crawl_status crawl_id=87                 -> poll until completed
regression_diff baseline=86 candidate=87 -> what you fixed, and what you broke
```

---

## Guardrails

An MCP server is a crawler with its trigger wired to a language model. The
failure that matters is not a wrong answer; it is a thousand requests to
somebody else's site because a model read a URL on a page and decided to look
into it. Three rules stop that, and none of them can be turned off from a tool
call — only at startup, by you.

### Read-only by default

`start_crawl` and `accept_issue` refuse until the server is started with
`--allow-writes` (or `TOADSTONE_MCP_ALLOW_WRITES=true`). On a read-only
server both tools are still listed, with a note in the description saying they
will be refused, so the agent knows the capability exists and why it cannot
use it.

### Host allow-list

With no allow-list, the server permits **only hosts this Toadstone install
has already crawled** — the set a human has already pointed the crawler at.
An empty list does not mean "anything".

- `--allow-host example.com` permits `example.com` and its subdomains.
  Repeatable.
- `--allow-host '*'` turns the check off. Do this only when the crawler is
  pointed at infrastructure you own.
- The list of known hosts is read once at startup, so a host that becomes
  known because of a crawl started in this session stays refused until the
  server restarts.

### URL cap

A crawl may not be asked for more URLs than the cap, which defaults to 2,000.
A request above it is **refused, not quietly clamped**: silently trimming would
let an agent crawl 2,000 pages of a 50,000-page site and report on the sample
as if it were the site. A crawl that asks for nothing gets the cap.

`max_depth` is not capped by size, only sanity-checked (0–100).

### What else is refused

- Any scheme but `http` and `https`.
- Credentials in the URL (`https://user:pass@host/`) — they would end up in
  logs. Configure crawl authentication in Toadstone instead.
- Another account's crawls. Every tool reaches the API through the same
  session and the same `login_required` and ownership checks as a browser
  would, so the server can see exactly what its caller could already see.

### Settings

| Environment | Flag | Default |
|---|---|---|
| `TOADSTONE_MCP_URL` | `--url` | `http://localhost:5000` |
| `TOADSTONE_MCP_ALLOW_HOSTS` | `--allow-host` (repeatable) | empty — hosts already crawled |
| `TOADSTONE_MCP_MAX_URLS` | `--max-urls` | `2000` |
| `TOADSTONE_MCP_ALLOW_WRITES` | `--allow-writes` / `--read-only` | `false` |
| `TOADSTONE_MCP_USERNAME` | `--username` | none (local mode auto-logs in) |
| `TOADSTONE_MCP_PASSWORD` | `--password` | none |
| `TOADSTONE_MCP_COOKIE` | — | none |
| `TOADSTONE_MCP_TIMEOUT` | `--timeout` | `120` seconds |
| `TOADSTONE_MCP_MAX_BYTES` | — | `200000` characters per export |

A flag beats an environment variable, so a config block can set the policy and
you can still override one setting for a single run.

---

## Transports

### stdio (what agent runtimes use)

`python -m src.mcp`. Newline-delimited JSON-RPC on stdin and stdout; every
diagnostic goes to stderr, because anything else on stdout corrupts the
protocol stream.

You can drive it by hand, which is the quickest way to see whether a problem
is the server or the client:

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | TOADSTONE_MCP_URL=http://localhost:5000 python -m src.mcp
```

### HTTP (mounted inside Toadstone)

```
POST /api/mcp     one JSON-RPC request, one JSON response
GET  /api/mcp     what the server is and how it is configured
```

This is Streamable HTTP with the streaming left out: every method this server
implements answers in one message, so there is nothing to stream and no
session to keep. A client that requires SSE will not work against it; stdio is
the supported path for Claude Code and Cursor.

It is behind the app's own `login_required`, and a tool call is answered as
whoever sent the request, using their cookies.

```bash
B=http://localhost:5000; J=/tmp/toadstone-cookies.txt
curl -s -c $J -o /dev/null $B/
curl -s -b $J $B/api/mcp
curl -s -b $J -X POST $B/api/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"list_crawls","arguments":{"limit":5}}}'
```

### Why no `mcp` SDK

The protocol is implemented directly, in `src/mcp/server.py`. The official
Python SDK is not in `requirements.txt` and is not installed in the image this
app ships in, and a server that only runs where somebody happened to `pip
install mcp` is not a feature of Toadstone. A tools-only server needs four
methods — `initialize`, `tools/list`, `tools/call`, `ping` — and no new
dependency was worth them.

Protocol version `2025-06-18`, with `2025-03-26` and `2024-11-05` accepted on
the handshake.

---

## Tests

```bash
python tests/mcp_tests.py
```

No network, no server, no crawl: the tests drive the real protocol against the
real Flask app over a throwaway database. The one call that would reach the
network — `POST /api/crawls` — is intercepted, including in the tests that
check the guardrails refuse, so a regression in a guardrail fails the suite
instead of crawling something.

---

## Troubleshooting

**"Could not reach Toadstone"** — the app is not running, or
`TOADSTONE_MCP_URL` points at the wrong port. It is whatever you set
`EXTERNAL_PORT` to in `.env` - 5000 unless you changed it, and on a Mac you
probably had to, because AirPlay Receiver owns 5000.

**Every tool returns "Authentication required"** — Toadstone is not in local
mode and the server has no credentials. Set `TOADSTONE_MCP_USERNAME` and
`TOADSTONE_MCP_PASSWORD`.

**`start_crawl` is refused and you did pass `--allow-writes`** — read the
refusal. The `rule` field says which guardrail: `read_only`, `host_allow_list`
or `max_urls`. Writes and the host allow-list are separate switches.

**The client shows no tools** — run the server by hand with the `printf` block
above. If that prints a tools list, the problem is the client's config: nearly
always a `cwd` that is not the Toadstone checkout.

---

Last updated: 2026-10-20. Source: [docs/mcp.md on GitHub](https://github.com/spcaeo/toadstone/blob/main/docs/mcp.md).
