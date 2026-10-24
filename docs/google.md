# Connecting Google Search Console and Analytics

> For the complete documentation index, see [llms.txt](https://spcaeo.github.io/toadstone/llms.txt). Every page here is also served as Markdown at the same address with `.md` instead of `.html`.

A crawl tells you what is on a site. Search Console tells you what Google did
with it, and Analytics tells you whether anyone came. Joined together they
answer questions neither half can: a page with traffic that nothing links to,
a page the crawler found that Google never indexed, two pages competing for
the same query.

This document covers how the connection works and how to set it up. If you
just want to use it, open **Settings → Google** and click **Connect Google**;
everything below is for whoever runs the installation.

## Two jobs, two people

**The installer** registers one OAuth client, once, and puts its id and secret
in the environment.

**Everyone else** clicks Connect, picks their site from a list, and is done.
They never see a client id, a scope, a token, or the word "OAuth".

The split is not an aesthetic choice. A source-available project cannot ship a
shared client secret, because a secret in a public repository is not a secret
and Google withdraws clients that are abused. Self-hosted analytics tools —
Plausible, Umami — make the same arrangement for the same reason.

## Setting up the client, once

Settings → Google prints these steps with the exact redirect URI for your
install filled in, which is the version to follow. Reproduced here:

1. Open [console.cloud.google.com](https://console.cloud.google.com) and pick
   or create a project.
2. **APIs & Services → Library**: enable **Google Search Console API** and
   **Google Analytics Data API**.
3. **APIs & Services → OAuth consent screen**: choose **External**, fill in
   the app name and your email, and add yourself as a test user. You do not
   need Google to review the app to use it yourself.
4. **APIs & Services → Credentials**: create an **OAuth client ID** of type
   **Web application**.
5. Under *Authorised redirect URIs*, add the address Settings → Google shows.
   Copy it rather than typing it — it must match character for character,
   including `http` vs `https` and the port.
6. Put the client id and secret in `.env`:

   ```
   GOOGLE_CLIENT_ID=...apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=...
   ```

7. Restart. Settings → Google now shows a Connect button.

The redirect URI is built from the request, so it is correct on localhost, on
a LAN address and behind a proxy without being configured anywhere. If you
reach Toadstone at more than one address, register each one.

## What is asked for, and why

Four scopes, all read-only:

| Scope | Why |
|---|---|
| `webmasters.readonly` | Which of your pages Google indexed, and how they perform in search |
| `analytics.readonly` | Which pages people visit, and whether they stay |
| `openid`, `email` | Shown in settings so you can tell which account is connected |

Nothing is ever written to a Google account. The consent screen a person sees
is explained in the panel before they click, scope by scope.

## Where the credential lives

A refresh token is not a password that expires in an hour. It is a standing
key to someone's Search Console and Analytics that keeps working until it is
revoked. So:

* It is kept in its own `google_accounts` table, not in the settings blob.
* It is encrypted with a key derived from the application's `SECRET_KEY`.
* It is stored **per user**. On a shared install, people never see each
  other's search data.
* It never reaches the browser. The settings API returns whether an account is
  connected and which one; the token itself stays on the server.
* Disconnecting revokes it at Google as well as deleting the local row.

Two consequences worth being plain about:

* **If `SECRET_KEY` changes, stored tokens become unreadable.** That is the
  point of tying them to it. The failure is clean — the account shows as not
  connected and the person reconnects with one click — rather than silent. If
  you have not set `SECRET_KEY`, a new one is generated at every restart, so
  connections will not survive a restart; set it.
* **This protects data at rest, not from the running application.** Anyone who
  can execute code as this app can decrypt them. The encryption is about the
  database file, the backup, and the copy someone attaches to a bug report.

## How the flow is kept honest

The `state` parameter is signed with an HMAC over the user id and a timestamp,
and expires after ten minutes. Without that, a callback could attach one
person's Google account to another person's Toadstone user. Forged, replayed
and expired states are each refused with a message saying what to do, and
`tests/google_connect_tests.py` tampers with every part of it.

The consent request uses `access_type=offline` with `prompt=consent`. Without
both, a second connection from the same account returns only a short-lived
token and the integration stops working an hour after someone sets it up.

The connect flow runs in a popup rather than a redirect, so a crawl in
progress is not disturbed, and the callback page tells the opener the moment
it finishes — the panel updates without anyone refreshing anything.

## When something goes wrong

Every error Google can return is translated into a sentence naming the fix,
because `invalid_grant` tells a person nothing:

| What Google says | What the panel says |
|---|---|
| `invalid_grant` | The link was already used or has expired. Try Connect again. |
| `redirect_uri_mismatch` | The redirect address does not match the one registered in Google Cloud Console — with the exact address to add. |
| `invalid_client` | Google did not recognise this installation's client ID or secret. Check `.env`. |
| `access_denied` | You declined the permission request, so nothing was connected. |
| no `refresh_token` in the reply | Remove this app at myaccount.google.com/permissions and connect again. |
| a network failure | Could not reach Google. Check this machine has internet access. |

Google uses two different error shapes and they are not interchangeable: the
OAuth token endpoint puts a string under `error`, the data APIs put an object.
Reading only the first made every API failure crash with `unhashable type:
dict` instead of explaining itself — found by making a real call with a bad
token, after sixty canned tests had missed it. Both shapes are handled, and
the captured bodies are in the test suite.

A 403 from the data APIs is split three ways, because each is a different
screen to go to: a missing scope (reconnect, approving both), an API nobody
enabled in Google Cloud Console (setup step 2), and an account without access
to the property (check Search Console). A 401 says the connection was revoked;
a 404 says the property is gone; a 429 says to wait.

## Analytics is optional

A person can connect, pick a Search Console property, and leave Analytics set
to None. A missing or refused Analytics permission never blocks choosing a
Search Console property; the panel says Analytics is unavailable and carries
on. Search Console is where most of the value is.

## Tests

### Checking a real account

```bash
python tools/verify_google.py          # the connected account
python tools/verify_google.py --adc    # a gcloud login instead
```

Everything about this integration can be checked without credentials except
one thing: whether a *successful* response carries the fields the code reads.
That one waits for an account — and waiting checks are checks nobody runs. So
it is a command. It makes the real calls, fetches Google's Discovery
documents, and compares what came back against what Google documents: every
field, every enum value, both spellings of `permissionLevel`. A credential
that cannot reach an API is a skip, not a failure, because that says nothing
about the code.

Its own branches are tested against canned payloads, so they do not execute
for the first time on someone's live data.

### Unit tests

`tests/google_connect_tests.py` — 96 checks, no network and no credentials.
Every call to Google goes through an injected opener returning canned
payloads, and the token store runs against a throwaway SQLite file.

```bash
python tests/google_connect_tests.py
```

Three things there are worth more than line coverage, because each has a
failure mode that looks like success: the signed state (forgery, expiry, a
different install's key), the encryption (the raw database row is read back
and asserted not to contain the token), and the error messages (checked for
the sentence, not the exception type).

The suite has been mutation-tested with twenty mutants, all caught: breaking
the signature check, the expiry, `prompt=consent`, either error translation,
the `sc-domain:` label stripping, the property pre-selection, the encryption,
the partial-save merge, the status redaction, the token expiry margin, the
crawl's lookup of the connected account, or the precedence of explicit
settings over it, each turns the suite red.

## What a crawl does with the connection

`from_config` resolves credentials in this order: explicit settings in the
crawl config, then the account connected in Settings → Google, then a gcloud
`application-default login`. Explicit settings win so an install that
deliberately configured its own client is never silently overridden.

The crawl thread has no request and no app context, so the store is told how
to find the application secret once at startup (`token_store.bind_secret`).
The secret is deliberately *not* carried in the crawl config: a config is
snapshotted into the database and exported with the crawl, which is the last
place an application secret should end up.

---

Last updated: 2026-10-20. Source: [docs/google.md on GitHub](https://github.com/spcaeo/toadstone/blob/main/docs/google.md).
