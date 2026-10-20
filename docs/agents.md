# The agent layer, and how to give it a model

Toadstone can ask a language model to judge things a rule cannot: whether a
page's content actually answers the query it targets, whether two pages are
really competing or merely similar, whether a fix is worth making. Four kinds
of agent exist — analysts that judge pages, a fixer that proposes changes, a
monitor that watches a crawl, and the crawl-driving agents behind the MCP
server.

**It is switched off by default and that is deliberate.** Turning it on means
page text leaves the machine and calls cost money. A crawler that quietly did
either — quietly spent someone's money, quietly sent a client's unpublished
page text to a third party — would have broken a trust it was never given.
So `agentMode` defaults to `none`, and nothing in the agent layer runs until
somebody chooses otherwise.

## The three modes

| `agentMode` | What happens | What it costs |
|---|---|---|
| `none` | Agent findings are skipped. Everything else works. | nothing |
| `api` | Calls the Anthropic API directly with `ANTHROPIC_API_KEY`. | per token |
| `cli` | Runs a local `claude` command, which bills to that installation's own account. | a Claude subscription |

`agentMaxPages` (default 25) caps how many pages any one crawl will send,
because agents are slow and priced per page. `agentModel` defaults to
`claude-sonnet-5-5`: these are short judgements about one page at a time, and
Sonnet answers them well for a lot less than Opus.

Settings → Advanced shows, in words, where page text is going before you
commit to it.

## `cli` mode in Docker: the bridge

`cli` mode runs `claude -p <prompt>` as a subprocess. That works when
Toadstone runs natively on a machine with Claude Code installed. It cannot
work in the Docker image, and no amount of bind-mounting fixes it: the CLI
ships as a **native binary for the host OS**, so a macOS install will not
execute inside a Linux container.

The two ways out are to install the Linux build inside the container — which
means putting a long-lived credential into the image and its volume — or to
leave the CLI where it already is and let the container ask it a question.
`tools/claude_bridge.py` does the second.

```
container                              host
---------                              ----
agents ──▶ claude_bridge_client.py ──HTTP──▶ claude_bridge.py ──▶ claude -p …
           (stands in for the CLI)           127.0.0.1 only       keychain auth
```

Your credential never leaves the host, and never enters the image.

### Running it

On the host:

```bash
python tools/claude_bridge.py
```

It prints the two values to give the container. Put the token in `.env`:

```
CLAUDE_BRIDGE_TOKEN=<the token it printed>
```

and in Settings → Advanced set:

```
agentMode        cli
agentCliCommand  /app/tools/claude_bridge_client.py
```

`docker-compose.yml` already passes `CLAUDE_BRIDGE_URL` and
`CLAUDE_BRIDGE_TOKEN` through, defaulting the URL to
`http://host.docker.internal:14898/ask`. Leaving the token empty keeps the
whole thing off.

The bridge has to be running for `cli` mode to answer. If it is not, agent
findings fail with "could not reach the Claude bridge" rather than silently
producing nothing — an integration that could not run must never look like a
clean result.

### What the bridge will and will not do

It exists to run a program on behalf of something else, which deserves to be
narrow:

* **It binds `127.0.0.1` only.** Docker Desktop reaches a loopback service
  through `host.docker.internal`, verified on this machine, so nothing listens
  on the network. On Linux, `extra_hosts` in the compose file provides the
  same name.
* **It runs exactly one command**: the configured CLI, with `-p`, and a
  prompt. No shell, no argument passthrough, no second endpoint. A caller
  controls the prompt text and nothing else.
* **Every request carries a shared secret**, compared in constant time. The
  token is generated on first run into `~/.toadstone/claude-bridge.token`
  with `0600` permissions.
* **One call at a time**, a 200 KB cap on the prompt, and a timeout on the
  child, so a crawl that found forty pages worth judging cannot start forty
  CLIs at once.
* **Prompts are never logged.** They are someone's page text. The access log
  records the method, the path and the status.

The threat it is built against is a prompt that tries to run a command. The
bridge passes an argument list to `subprocess.run` with no shell, so the
metacharacters that would matter are just characters. `tests/bridge_tests.py`
proves it by trying: `; touch`, `&&`, `$( )`, backticks, a pipe and a newline,
each asserted to leave no file behind and to arrive at the CLI verbatim.

What it is *not* built against is someone who already has the token and can
reach the port — they can spend your Claude quota on arbitrary prompts. The
token file is owner-only and the socket is loopback-only; if neither of those
holds on your machine, do not run the bridge.

## Tests

```bash
python tests/bridge_tests.py     # 32 checks, no Claude needed
python tests/agent_tests.py      # 61 checks, the agent layer itself
```

The bridge tests start a real server on an ephemeral loopback port with a
stand-in for the CLI. Those stand-ins are written rather than borrowed from
`/bin`, because the obvious candidates are not portable — macOS has no
`/bin/false` or `/bin/true`, and `/bin/sleep` rejects `-p`. The first version
of this suite used them and reported a passing test for "a failing CLI" that
was really exercising "command not found".
