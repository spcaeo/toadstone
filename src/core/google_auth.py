"""OAuth2 for the Google APIs, with no Google client library.

This project has very few dependencies on purpose, and Google's own Python
client pulls in a large tree to do what is, for a server-side refresh-token
flow, two HTTP calls. So this implements the parts actually needed: swap an
authorisation code for a refresh token once, then swap the refresh token for
an access token whenever one expires.

What this deliberately does not do is run the interactive consent flow. That
needs a browser and a person, and a crawler is neither. The documented setup
is: create an OAuth client in Google Cloud, authorise it once by hand, and
give Toadstone the refresh token. After that it runs unattended forever,
which is what a scheduled crawl needs.

Tokens are held in memory for the life of the process and never written to
the crawl database, because a refresh token is a long-lived credential and
the crawl database is exported, copied and shared.
"""

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN_ENDPOINT = 'https://oauth2.googleapis.com/token'
AUTH_ENDPOINT = 'https://accounts.google.com/o/oauth2/v2/auth'

SCOPES = {
    'search_console': 'https://www.googleapis.com/auth/webmasters.readonly',
    'analytics': 'https://www.googleapis.com/auth/analytics.readonly',
}

# Refresh a little before expiry rather than after a failure.
EXPIRY_MARGIN_SECONDS = 120


class GoogleAuthError(Exception):
    """Something about the credentials is wrong, and a retry will not help."""


def consent_url(client_id, redirect_uri, scopes=None):
    """The URL a person visits once to authorise this client.

    Printed by the setup documentation; nothing calls it during a crawl.
    `access_type=offline` with `prompt=consent` is what makes Google return a
    refresh token rather than only an access token.
    """
    wanted = [SCOPES[s] for s in (scopes or SCOPES) if s in SCOPES]
    query = urllib.parse.urlencode({
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': ' '.join(wanted),
        'access_type': 'offline',
        'prompt': 'consent',
    })
    return f'{AUTH_ENDPOINT}?{query}'


class Credentials:
    """A client id, a client secret and a refresh token, plus caching.

    Thread-safe because several crawl workers may ask for a token at once and
    refreshing three times in parallel would be both wasteful and, on Google's
    side, rate-limited.
    """

    def __init__(self, client_id, client_secret, refresh_token, opener=None):
        self.client_id = (client_id or '').strip()
        self.client_secret = (client_secret or '').strip()
        self.refresh_token = (refresh_token or '').strip()
        self._opener = opener
        self._access_token = ''
        self._expires_at = 0.0
        self._lock = threading.Lock()

    @property
    def configured(self):
        return bool(self.client_id and self.client_secret and self.refresh_token)

    def access_token(self, now=None):
        """A valid access token, refreshing only when the cached one is stale."""
        if not self.configured:
            raise GoogleAuthError(
                'Google credentials are incomplete: a client id, client secret and '
                'refresh token are all required.')
        now = now if now is not None else time.time()
        with self._lock:
            if self._access_token and now < self._expires_at - EXPIRY_MARGIN_SECONDS:
                return self._access_token
            body = urllib.parse.urlencode({
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'refresh_token': self.refresh_token,
                'grant_type': 'refresh_token',
            }).encode('utf-8')
            request = urllib.request.Request(
                TOKEN_ENDPOINT, data=body,
                headers={'Content-Type': 'application/x-www-form-urlencoded'})
            send = self._opener or urllib.request.urlopen
            try:
                with send(request, timeout=30) as response:
                    payload = json.loads(response.read().decode('utf-8'))
            except urllib.error.HTTPError as exc:
                detail = ''
                try:
                    detail = json.loads(exc.read().decode('utf-8')).get('error', '')
                except Exception:
                    pass
                if exc.code in (400, 401):
                    # Google returns 400 for a revoked or wrong refresh token,
                    # which is a credential problem a retry will never fix.
                    raise GoogleAuthError(
                        f'Google rejected the refresh token ({detail or exc.code}). '
                        f'Re-authorise the client and store the new token.')
                raise GoogleAuthError(f'Token request failed: HTTP {exc.code}')
            except Exception as exc:
                raise GoogleAuthError(f'Token request failed: {type(exc).__name__}')

            token = payload.get('access_token')
            if not token:
                raise GoogleAuthError('Google returned no access token')
            self._access_token = token
            self._expires_at = now + float(payload.get('expires_in') or 3600)
            return token


# Where the gcloud CLI leaves a user's application-default credentials.
ADC_PATH = os.path.join(os.path.expanduser('~'), '.config', 'gcloud',
                        'application_default_credentials.json')


def from_adc(path=None, opener=None):
    """Credentials from a gcloud application-default login, or None.

    Anyone with gcloud installed already has somewhere to put these, and
    asking them to create a second OAuth client and paste three secrets into
    a settings form is worse in every way: more steps, more copies of a
    long-lived credential, and nothing to revoke cleanly afterwards.

    `gcloud auth application-default login` writes an authorized_user file
    holding exactly the three fields this needs. It is read, never written,
    and never copied into the crawl database.

    The scopes still have to be right. gcloud's default login does not
    include webmasters.readonly, so the documentation asks for them
    explicitly; a token without them fails with a 403 that says so.
    """
    path = path or ADC_PATH
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if data.get('type') != 'authorized_user':
        return None  # a service account cannot reach Search Console this way
    creds = Credentials(data.get('client_id'), data.get('client_secret'),
                        data.get('refresh_token'), opener=opener)
    return creds if creds.configured else None


def from_connected_account(user_id, opener=None):
    """Credentials for an account connected through Settings > Google.

    This is the path almost everyone takes. The refresh token comes from the
    encrypted per-user store; the client id and secret are the installation's,
    from the environment, because they are the same for everyone here.
    """
    if not user_id:
        return None
    from src.core import token_store
    stored = token_store.load(user_id, token_store.app_secret())
    if not stored or not stored.get('refresh_token'):
        return None
    creds = Credentials(os.environ.get('GOOGLE_CLIENT_ID', ''),
                        os.environ.get('GOOGLE_CLIENT_SECRET', ''),
                        stored['refresh_token'], opener=opener)
    return creds if creds.configured else None


def from_config(config, opener=None, user_id=None):
    """Credentials for a crawl, in the order an install would expect.

    Explicit settings win, so an install that has deliberately set up its own
    OAuth client is never silently overridden. Then the account the person
    connected in settings - the ordinary case. Then a gcloud login, for a
    machine already signed in.
    """
    config = config or {}
    creds = Credentials(config.get('google_client_id'),
                        config.get('google_client_secret'),
                        config.get('google_refresh_token'), opener=opener)
    if creds.configured:
        return creds
    connected = from_connected_account(user_id, opener=opener)
    if connected:
        return connected
    if config.get('google_use_adc', True):
        return from_adc(opener=opener)
    return None


def _reason(exc):
    """What Google said went wrong, from the error body it returns."""
    try:
        payload = json.loads(exc.read().decode('utf-8'))
    except Exception:
        return ''
    error = (payload or {}).get('error') or {}
    for item in error.get('details') or []:
        if isinstance(item, dict) and item.get('reason'):
            return str(item['reason'])
    return str(error.get('message') or '')


def call(credentials, url, method='GET', body=None, opener=None, timeout=60):
    """One authenticated request. Returns parsed JSON or raises GoogleAuthError."""
    token = credentials.access_token()
    data = json.dumps(body).encode('utf-8') if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    })
    send = opener or urllib.request.urlopen
    try:
        with send(request, timeout=timeout) as response:
            raw = response.read().decode('utf-8')
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = _reason(exc)
        if exc.code == 403:
            # Two very different problems arrive as 403, and telling someone
            # to check property access when the real fault is a missing scope
            # sends them to the wrong screen. Google says which; this repeats
            # it rather than guessing. Found by making a real call with a
            # token that had cloud-platform but not webmasters.readonly.
            if 'SCOPE' in detail.upper() or 'scope' in detail:
                raise GoogleAuthError(
                    'Google refused the request: the token does not carry the '
                    'scope this API needs. Re-authorise asking for '
                    'webmasters.readonly and analytics.readonly.')
            raise GoogleAuthError(
                'Google refused the request (403). The authorised account does '
                'not have access to this property.'
                + (f' Google said: {detail}' if detail else ''))
        if exc.code == 429:
            raise GoogleAuthError('Google rate-limited the request (429).')
        if exc.code == 404:
            raise GoogleAuthError(
                'Google has no such property (404). Check the property id is '
                'exactly as Search Console shows it, including sc-domain: form.')
        raise GoogleAuthError(f'Google returned HTTP {exc.code}'
                              + (f': {detail}' if detail else ''))
    except GoogleAuthError:
        raise
    except Exception as exc:
        raise GoogleAuthError(f'Request failed: {type(exc).__name__}')
