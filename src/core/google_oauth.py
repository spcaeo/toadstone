"""Connecting a Google account by clicking a button.

The alternative, and what this project did before, was: open Google Cloud
Console, create an OAuth client, run a consent flow by hand, copy a refresh
token, paste it into a settings form. That is a reasonable thing to ask of
the person who installed the software. It is not a reasonable thing to ask
of the person who uses it, and most people who would benefit from Search
Console data in a crawl report are the second kind.

So there are two separate jobs here and they belong to different people.

**The installer** registers one OAuth client, once, and puts its id and
secret in the environment. That cannot be avoided: an open-source project
cannot ship a shared client secret, because a secret in a public repository
is not a secret, and Google withdraws clients that are abused. This is the
same arrangement Plausible, Umami and most self-hosted analytics tools use.

**Everyone else** clicks Connect, chooses their site from a list, and is
done. They never see a client id, a scope, a token, or the words "OAuth".

Three details that exist for the second person's sake:

  * after connecting, their properties are **fetched and listed**, so they
    pick "example.com" from a menu instead of typing `sc-domain:example.com`
    and wondering why it returns nothing;
  * the `state` parameter is signed, so a stray callback cannot connect an
    account to the wrong person, and an expired one says so plainly;
  * every error Google can return is translated into a sentence naming what
    to do, because "invalid_grant" tells a person nothing.
"""

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

AUTH_ENDPOINT = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_ENDPOINT = 'https://oauth2.googleapis.com/token'
REVOKE_ENDPOINT = 'https://oauth2.googleapis.com/revoke'
USERINFO_ENDPOINT = 'https://www.googleapis.com/oauth2/v3/userinfo'
SITES_ENDPOINT = 'https://searchconsole.googleapis.com/webmasters/v3/sites'
GA_ADMIN_ENDPOINT = 'https://analyticsadmin.googleapis.com/v1beta/accountSummaries'

# Read-only, and the narrowest set that answers the questions this tool asks.
# Listed here rather than inline so the consent screen a person sees can be
# explained exactly, in the UI, before they click.
SCOPES = [
    'openid',
    'email',
    'https://www.googleapis.com/auth/webmasters.readonly',
    'https://www.googleapis.com/auth/analytics.readonly',
]

SCOPE_EXPLANATIONS = [
    ('Search Console, read only',
     'Which of your pages Google indexed, and how they perform in search.'),
    ('Analytics, read only',
     'Which pages people visit, and whether they stay.'),
    ('Your email address',
     'Shown in settings so you can tell which account is connected.'),
]

# A guard against a paging bug on Google's side turning into an endless
# loop here. Far more pages than any real account has.
MAX_PROPERTY_PAGES = 20

# How long a connect attempt stays valid. Long enough to read a consent
# screen, short enough that a stale link in someone's history is useless.
STATE_TTL_SECONDS = 600


class OAuthError(Exception):
    """Something went wrong, with a message written for a person."""


def configured(client_id, client_secret):
    return bool((client_id or '').strip() and (client_secret or '').strip())


def setup_instructions(redirect_uri):
    """What the installer has to do, once, in the order they must do it."""
    return {
        'why': ('An open-source project cannot ship a shared Google client, '
                'because its secret would be public. Each installation '
                'registers its own, once.'),
        'steps': [
            'Open console.cloud.google.com and pick or create a project.',
            'Under APIs & Services, enable "Google Search Console API" and '
            '"Google Analytics Data API".',
            'Under APIs & Services then OAuth consent screen, choose External, '
            'fill in the app name and your email, and add yourself as a test '
            'user. You do not need Google to review it for your own use.',
            'Under Credentials, create an OAuth client ID of type Web application.',
            'Under Authorised redirect URIs, add the address shown below. '
            'Copy it rather than typing it: it must match character for character.',
            'Copy the client ID and client secret into your .env file as '
            'GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET, then restart.',
        ],
        'redirect_uri': redirect_uri,
        'console_url': 'https://console.cloud.google.com/apis/credentials',
        'takes': 'about five minutes, once per installation',
    }


# ---------------------------------------------------------------------------
# the signed state, so a callback cannot be aimed at the wrong account
# ---------------------------------------------------------------------------

def make_state(user_id, secret_key, now=None):
    now = int(now if now is not None else time.time())
    body = f'{user_id or 0}.{now}'
    signature = hmac.new(str(secret_key).encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f'{body}.{signature}'


def read_state(state, secret_key, now=None):
    """The user id a state belongs to, or an OAuthError saying why not."""
    parts = (state or '').split('.')
    if len(parts) != 3:
        raise OAuthError('That sign-in link was malformed. Start again from settings.')
    user_part, issued, signature = parts
    expected = hmac.new(str(secret_key).encode(), f'{user_part}.{issued}'.encode(),
                        hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(signature, expected):
        raise OAuthError('That sign-in link did not come from here. Start again '
                         'from settings.')
    now = int(now if now is not None else time.time())
    try:
        if now - int(issued) > STATE_TTL_SECONDS:
            raise OAuthError('That sign-in link expired. Start again from settings.')
        return int(user_part)
    except ValueError:
        raise OAuthError('That sign-in link was malformed. Start again from settings.')


# ---------------------------------------------------------------------------
# the flow
# ---------------------------------------------------------------------------

def authorize_url(client_id, redirect_uri, state):
    """Where to send someone's browser.

    `prompt=consent` with `access_type=offline` is what makes Google return a
    refresh token. Without both, a second connection from the same account
    returns only a short-lived token and the integration quietly stops
    working an hour later.
    """
    return AUTH_ENDPOINT + '?' + urllib.parse.urlencode({
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': ' '.join(SCOPES),
        'access_type': 'offline',
        'prompt': 'consent',
        'include_granted_scopes': 'true',
        'state': state,
    })


def _post(url, fields, opener=None, timeout=30):
    request = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode('utf-8'),
        headers={'Content-Type': 'application/x-www-form-urlencoded'})
    send = opener or urllib.request.urlopen
    try:
        with send(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise OAuthError(_explain(exc))
    except Exception as exc:
        raise OAuthError(f'Could not reach Google ({type(exc).__name__}). '
                         f'Check this machine has internet access and try again.')


def _explain(exc):
    """Google's error codes, in words that name the fix.

    Two different shapes arrive here and they are not interchangeable. The
    OAuth token endpoint returns `{"error": "invalid_grant"}` - a string. The
    REST APIs return `{"error": {"code": 401, "status": "UNAUTHENTICATED"}}` -
    an object. Handling only the first made every API failure die with
    "unhashable type: dict" rather than say what was wrong, which is the one
    moment this function exists for. Found by making a real call with a bad
    token; the canned payloads in the tests had all been the string shape.
    """
    body = {}
    try:
        body = json.loads(exc.read().decode('utf-8')) or {}
    except Exception:
        pass
    code = getattr(exc, 'code', 0)
    error = body.get('error')
    if isinstance(error, dict):
        return _explain_api(error, code)
    return _explain_oauth(str(error or ''), code)


OAUTH_ERRORS = {
    'invalid_grant': ('Google would not accept that sign-in. It usually means '
                      'the link was already used or has expired. Try Connect again.'),
    'redirect_uri_mismatch': ('The redirect address does not match the one '
                              'registered in Google Cloud Console. The exact '
                              'address to add is shown in settings.'),
    'invalid_client': ('Google did not recognise this installation\'s client ID '
                       'or secret. Check GOOGLE_CLIENT_ID and '
                       'GOOGLE_CLIENT_SECRET in .env.'),
    'access_denied': 'You declined the permission request, so nothing was connected.',
    'invalid_scope': ('Google rejected the permissions this asked for. The '
                      'installation may need the Search Console and Analytics '
                      'APIs enabled in Google Cloud Console.'),
}


def _explain_oauth(detail, code):
    """The sign-in endpoint, which names its errors in a string."""
    if detail in OAUTH_ERRORS:
        return OAUTH_ERRORS[detail]
    return f'Google refused the request ({detail or code}).'


def _disabled_service(error):
    """The API Google says is off, and where to switch it on.

    A SERVICE_DISABLED error carries an ErrorInfo with the service's title
    and an activation URL. Reading them beats guessing which API a call
    needed, because a guess that names the wrong one costs somebody a trip
    to a console screen that was already correct.
    """
    for item in (error.get('details') or []):
        if not isinstance(item, dict):
            continue
        if str(item.get('reason') or '') != 'SERVICE_DISABLED':
            continue
        meta = item.get('metadata') or {}
        if not isinstance(meta, dict):
            continue
        return (str(meta.get('serviceTitle') or '').strip(),
                str(meta.get('activationUrl') or '').strip())
    return '', ''


def _explain_api(error, code):
    """The data APIs, which name their errors in an object.

    Each branch is a different screen for the person to go to, which is the
    whole reason for not printing Google's message and leaving it there.
    """
    status = str(error.get('status') or '')
    message = str(error.get('message') or '')
    reasons = ' '.join(str(item.get('reason') or '')
                       for item in (error.get('errors') or [])
                       if isinstance(item, dict))
    code = error.get('code') or code
    haystack = f'{status} {message} {reasons}'.lower()

    if code == 401 or status == 'UNAUTHENTICATED':
        return ('Google no longer accepts this connection - it was probably '
                'revoked. Disconnect and connect again in settings.')

    if code == 403 or status == 'PERMISSION_DENIED':
        if 'scope' in haystack:
            return ('This connection does not carry the permission that part of '
                    'Google needs. Disconnect and connect again, approving both '
                    'Search Console and Analytics.')
        if 'disabled' in haystack or 'has not been used' in haystack:
            # Name the API Google named, not the one we assumed. This used to
            # say "enable the Search Console API and the Analytics Data API",
            # which sent people to switch on an API that was already on: the
            # one actually missing was the Analytics *Admin* API, and nothing
            # in the message mentioned it. Google puts the service's title and
            # a direct activation link in the error details, so use them.
            service, link = _disabled_service(error)
            if service:
                return (f'{service} is not switched on for this installation. '
                        + (f'Switch it on at {link} ' if link else
                           'Switch it on in Google Cloud Console, under APIs '
                           '& Services, ')
                        + 'and try again; it can take a few minutes to take '
                          'effect.')
            return ('That Google API is not switched on for this installation. '
                    'In Google Cloud Console, under APIs & Services, switch on '
                    'the API named in Google\'s message, then try again.')
        return ('This Google account does not have access to that property. '
                'Check it is listed for this account in Search Console.')

    if code == 404:
        return ('Google has no such property. Check it is still listed in '
                'Search Console for this account.')

    if code == 429 or status == 'RESOURCE_EXHAUSTED':
        return ('Google is rate-limiting this installation. Wait a few minutes '
                'and try again.')

    return (f'Google refused the request ({status or code}).'
            + (f' Google said: {message}' if message else ''))


def exchange_code(code, client_id, client_secret, redirect_uri, opener=None):
    """Swap the one-time code for a refresh token."""
    payload = _post(TOKEN_ENDPOINT, {
        'code': code, 'client_id': client_id, 'client_secret': client_secret,
        'redirect_uri': redirect_uri, 'grant_type': 'authorization_code',
    }, opener)
    refresh = payload.get('refresh_token')
    if not refresh:
        # Google withholds it when the account has connected before and
        # prompt=consent was not honoured; say what to do rather than fail.
        raise OAuthError('Google did not return a long-lived token. Remove this '
                         'app at myaccount.google.com/permissions and connect again.')
    return {
        'refresh_token': refresh,
        'access_token': payload.get('access_token', ''),
        'expires_at': time.time() + float(payload.get('expires_in') or 3600),
        'scope': payload.get('scope', ''),
    }


def refresh_access_token(refresh_token, client_id, client_secret, opener=None):
    payload = _post(TOKEN_ENDPOINT, {
        'refresh_token': refresh_token, 'client_id': client_id,
        'client_secret': client_secret, 'grant_type': 'refresh_token',
    }, opener)
    token = payload.get('access_token')
    if not token:
        raise OAuthError('Google did not return an access token. Reconnect the account.')
    return {'access_token': token,
            'expires_at': time.time() + float(payload.get('expires_in') or 3600)}


def revoke(refresh_token, opener=None):
    """Tell Google to forget us too, not just our own database."""
    try:
        _post(REVOKE_ENDPOINT, {'token': refresh_token}, opener)
    except OAuthError:
        pass  # already revoked, or offline; the local record goes either way
    return True


# ---------------------------------------------------------------------------
# what the person picks from
# ---------------------------------------------------------------------------

def _get(url, access_token, opener=None, timeout=30):
    request = urllib.request.Request(url, headers={'Authorization': f'Bearer {access_token}'})
    send = opener or urllib.request.urlopen
    try:
        with send(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        raise OAuthError(_explain(exc))
    except Exception as exc:
        raise OAuthError(f'Could not reach Google ({type(exc).__name__}).')


def account_email(access_token, opener=None):
    """Which account this is, so settings can show it."""
    try:
        return (_get(USERINFO_ENDPOINT, access_token, opener) or {}).get('email', '')
    except OAuthError:
        return ''


def search_console_properties(access_token, opener=None):
    """Every property this account can read, ready for a dropdown.

    Google returns `sc-domain:example.com` for a domain property and a URL
    for a prefix one. Nobody should have to know that, so each entry carries
    a label a person recognises alongside the id the API needs.
    """
    payload = _get(SITES_ENDPOINT, access_token, opener) or {}
    out = []
    for entry in payload.get('siteEntry') or []:
        site = entry.get('siteUrl') or ''
        if not site:
            continue
        out.append({
            'id': site,
            'label': site[len('sc-domain:'):] if site.startswith('sc-domain:') else site,
            'kind': 'Domain' if site.startswith('sc-domain:') else 'URL prefix',
            'can_read': _can_read(entry.get('permissionLevel')),
            'permission': entry.get('permissionLevel') or '',
        })
    out.sort(key=lambda row: row['label'])
    return out


# The one permission level that cannot read anything. Google spells this enum
# two ways depending on which document you read: the v1 Discovery document
# says SITE_UNVERIFIED_USER, the older webmasters/v3 reference says
# siteUnverifiedUser, and this code calls the v3 path on the v1 host. Matching
# only one spelling silently offered unreadable properties as selectable -
# someone would pick one and get nothing back, with nothing to explain why.
UNREADABLE_PERMISSIONS = {'SITEUNVERIFIEDUSER'}


def _can_read(permission):
    """Whether this permission level can read a property's data at all.

    Normalised rather than compared literally, so both of Google's spellings
    land on the same answer. Anything unrecognised counts as readable: being
    wrong in that direction shows someone a property that returns nothing,
    while being wrong the other way hides one they actually own.
    """
    normalised = ''.join(c for c in str(permission or '').upper() if c.isalnum())
    return normalised not in UNREADABLE_PERMISSIONS


def analytics_properties(access_token, opener=None):
    """GA4 properties, labelled by name rather than by number."""
    out, token, seen_pages = [], '', 0
    while True:
        url = GA_ADMIN_ENDPOINT + (f'?pageToken={urllib.parse.quote(token)}' if token else '')
        payload = _get(url, access_token, opener) or {}
        for summary in payload.get('accountSummaries') or []:
            account = summary.get('displayName') or ''
            for prop in summary.get('propertySummaries') or []:
                name = prop.get('property') or ''      # "properties/123456"
                number = name.split('/')[-1]
                if not number:
                    continue
                out.append({
                    'id': number,
                    'label': prop.get('displayName') or number,
                    'account': account,
                })
        # Google pages this response and an agency account easily exceeds one
        # page. Stopping at the first would silently hide the rest, and the
        # person would conclude their property was not there.
        token = payload.get('nextPageToken') or ''
        seen_pages += 1
        if not token or seen_pages >= MAX_PROPERTY_PAGES:
            break
    out.sort(key=lambda row: (row['account'], row['label']))
    return out


def guess_property(properties, base_url):
    """The property that matches the site being crawled, if one obviously does.

    Offering a correct default is the difference between a dropdown someone
    confirms and a dropdown someone has to study.
    """
    host = urllib.parse.urlsplit(base_url or '').netloc.lower().removeprefix('www.')
    if not host:
        return ''
    for row in properties:
        label = (row.get('label') or '').lower().rstrip('/')
        if label.removeprefix('https://').removeprefix('http://').removeprefix('www.') == host:
            return row['id']
    return ''
