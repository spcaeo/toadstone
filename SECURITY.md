# Security

Toadstone fetches arbitrary URLs, renders them in a browser, stores page bodies,
and holds credentials for Google and several SEO vendors. That is a meaningful
attack surface, and the honest summary of its posture is below — including the
parts that are not finished.

## Reporting a vulnerability

Use **[private vulnerability reporting](https://github.com/spcaeo/toadstone/security/advisories/new)**
on this repository. It goes nowhere public, and it is the only channel that
should be used for anything exploitable.

Please include what you were able to do, the smallest reproduction you have, and
the commit you tested. If it needs a crawl of a hostile page to reproduce, the
page's HTML is more useful than a URL that may change.

Expect an acknowledgement within 72 hours. If a fix lands, the advisory names
you unless you ask otherwise.

**Do not** open a public issue for anything that lets one user read another
user's crawls, extract a stored token, reach the filesystem, or execute code.

## What this software does that deserves care

| Behaviour | Why it matters | What limits it |
|---|---|---|
| Renders crawled pages in Chromium | Crawled sites are untrusted input, and JavaScript on them runs | Rendering is in a Playwright browser, not the app process; no credentials are in that browser's context |
| Stores the body of every crawled page | A crawl of an authenticated area stores what it saw | Opt-in site login, local storage only, `Settings → Crawls to keep` bounds retention |
| Holds Google OAuth refresh tokens | A leaked token reads Search Console and Analytics | Fernet-encrypted at rest under a key derived from `SECRET_KEY`, in their own table; never written to the crawl config, which is snapshotted to the database and included in exports |
| Holds third-party API keys | Moz / Majestic / Ahrefs / CrUX keys are billable | Per-user settings or process environment; never logged |
| Multi-user with tiers | One user's data must not reach another | Per-session isolation with per-user memory accounting |
| Serves a reporting API and an MCP endpoint | Both read crawl data | API keys are per-user and revocable |

## Deployment expectations

Toadstone is built to be **self-hosted on a network you control**. It is not
hardened for the open internet, and these are not theoretical caveats:

- `--local` / `LOCAL_MODE=true` **disables authentication and grants admin to
  everyone**. Correct on a laptop, wrong on anything reachable.
- `--dangerously-skip-auth` lets anyone log in as any username with no
  password. It exists for development and the flag is named so you cannot use
  it by accident.
- Set `SECRET_KEY` in `.env` to a value you generated
  (`python -c "import secrets; print(secrets.token_hex(32))"`). Without it the
  app warns on startup and uses an ephemeral random key, so sessions die on
  restart and **connecting a Google account is refused outright** rather than
  stored unencrypted (`src/core/token_store.py`). Changing `SECRET_KEY` later
  makes already-stored tokens unreadable, by design.
- The default Docker binding is `127.0.0.1`. Changing `HOST_BINDING` to
  `0.0.0.0` puts it on your LAN; put a reverse proxy with TLS in front of it
  before you do.
- A crawler is a request amplifier. Crawl sites you own or have permission to
  crawl, and leave the rate limit on.

## Supported versions

The most recent release on `main` is the supported version. There is no
long-term support branch.

## What has and has not been reviewed

- The test suite includes adversarial cases for the credential store, the
  Claude bridge's command allow-list, site authentication, and the tier
  restrictions. 1,865 assertions run on every change.
- **No third-party security audit has been performed.** If you read this file
  looking for one, that is the answer.
