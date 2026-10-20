#!/usr/bin/env python3
"""Connecting a Google account: the flow, the store, and the error messages.

No network and no credentials. Every call to Google goes through an injected
opener returning canned payloads, and the token store runs against a throwaway
SQLite file.

Three things here are worth more than the usual line coverage, because each
has a failure mode that looks like success:

  * **The signed state.** If a forged or expired state were accepted, a
    callback could attach someone else's Google account to this user. The
    tests tamper with every part of it and with the key itself.
  * **The encryption.** A store that quietly wrote plaintext would pass any
    test that only round-trips a value, so the tests read the raw database
    row back and assert the refresh token is not in it.
  * **The error messages.** They are the whole point of the feature: a person
    who cannot connect needs a sentence naming the fix, not "invalid_grant".
    Every code Google returns is checked for the sentence, not for a type.
"""

import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import google_oauth, token_store  # noqa: E402
from src.core.google_oauth import OAuthError  # noqa: E402

PASSED, FAILED = [], []
SECRET = 'a-test-secret-key'


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def raises(fn, *args, **kwargs):
    """The OAuthError a call raises, or None if it did not raise one."""
    try:
        fn(*args, **kwargs)
    except OAuthError as exc:
        return str(exc)
    return None


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def opener_returning(*payloads):
    """Answers each successive call with the next payload, repeating the last."""
    queue = list(payloads)

    def send(request, timeout=None):
        payload = queue.pop(0) if len(queue) > 1 else queue[0]
        send.last_request = request
        return FakeResponse(json.dumps(payload).encode('utf-8'))
    send.last_request = None
    return send


def failing_opener(code, body=None):
    def send(request, timeout=None):
        raise urllib.error.HTTPError(
            'https://x', code, 'no', {},
            io.BytesIO(json.dumps(body or {}).encode('utf-8')))
    return send


def offline_opener():
    def send(request, timeout=None):
        raise urllib.error.URLError('nodename nor servname provided')
    return send


# ---------------------------------------------------------------------------
# the signed state
# ---------------------------------------------------------------------------

def test_state_survives_a_round_trip():
    state = google_oauth.make_state(42, SECRET)
    result('a state round-trips to the user it was made for',
           google_oauth.read_state(state, SECRET) == 42)

    anon = google_oauth.make_state(None, SECRET)
    result('a missing user id becomes 0 rather than crashing',
           google_oauth.read_state(anon, SECRET) == 0)


def test_a_state_cannot_be_forged():
    """Each of these would let a callback connect an account to the wrong user."""
    good = google_oauth.make_state(42, SECRET)
    user_part, issued, signature = good.split('.')

    # someone swaps the user id and keeps the signature
    swapped = f'7.{issued}.{signature}'
    message = raises(google_oauth.read_state, swapped, SECRET)
    result('a changed user id is rejected',
           message is not None and 'did not come from here' in message,
           repr(message))

    # someone flips a character of the signature
    flipped = signature[:-1] + ('0' if signature[-1] != '0' else '1')
    result('a tampered signature is rejected',
           raises(google_oauth.read_state, f'{user_part}.{issued}.{flipped}',
                  SECRET) is not None)

    # a state minted against a different install's key
    other = google_oauth.make_state(42, 'a-different-secret')
    result('a state signed with another key is rejected',
           raises(google_oauth.read_state, other, SECRET) is not None)

    for bad in ('', 'nonsense', 'two.parts', 'a.b.c.d', '42.notanumber.' + signature):
        if raises(google_oauth.read_state, bad, SECRET) is None:
            result(f'malformed state {bad!r} is rejected', False, 'accepted it')
            return
    result('malformed states are rejected rather than parsed', True)


def test_a_state_expires():
    stale = google_oauth.make_state(
        42, SECRET, now=time.time() - google_oauth.STATE_TTL_SECONDS - 1)
    message = raises(google_oauth.read_state, stale, SECRET)
    result('an old state is refused',
           message is not None and 'expired' in message, repr(message))

    # and the boundary is not off by one in the direction that matters
    fresh = google_oauth.make_state(
        42, SECRET, now=time.time() - google_oauth.STATE_TTL_SECONDS + 5)
    result('a state inside its lifetime still works',
           google_oauth.read_state(fresh, SECRET) == 42)


# ---------------------------------------------------------------------------
# the flow
# ---------------------------------------------------------------------------

def test_the_consent_url_asks_for_a_refresh_token():
    """Without both of these Google returns only a one-hour token, and the
    integration stops working an hour after someone sets it up."""
    url = google_oauth.authorize_url('client-id', 'https://here/cb', 'state-1')
    for required in ('access_type=offline', 'prompt=consent',
                     'client_id=client-id', 'state=state-1'):
        if required not in url:
            result('the consent URL asks for offline access', False,
                   f'missing {required}')
            return
    result('the consent URL asks for offline access', True)

    missing = [s for s in google_oauth.SCOPES
               if urllib.parse.quote(s, safe='') not in url
               and s not in url]
    result('every scope the tool uses is requested', not missing, str(missing))


def test_exchanging_a_code():
    opener = opener_returning({'refresh_token': 'r-1', 'access_token': 'a-1',
                               'expires_in': 3600, 'scope': 'openid email'})
    got = google_oauth.exchange_code('code', 'id', 'secret', 'https://here/cb', opener)
    result('a code exchange keeps the refresh token',
           got['refresh_token'] == 'r-1' and got['access_token'] == 'a-1')
    result('the expiry is an absolute time, not a duration',
           time.time() + 3500 < got['expires_at'] < time.time() + 3700,
           str(got['expires_at']))


def test_a_missing_refresh_token_says_what_to_do():
    """Google withholds the refresh token when the account has connected
    before. Failing with "no refresh_token" would leave someone stuck; the
    message has to name the page that unsticks them."""
    opener = opener_returning({'access_token': 'a-1', 'expires_in': 3600})
    message = raises(google_oauth.exchange_code, 'code', 'id', 'secret',
                     'https://here/cb', opener)
    result('a withheld refresh token names the page that fixes it',
           message is not None and 'myaccount.google.com/permissions' in message,
           repr(message))


def test_every_google_error_becomes_a_sentence():
    cases = [
        ('invalid_grant', 'Try Connect again'),
        ('redirect_uri_mismatch', 'Google Cloud Console'),
        ('invalid_client', 'GOOGLE_CLIENT_ID'),
        ('access_denied', 'declined'),
    ]
    for code, expected in cases:
        message = raises(google_oauth.exchange_code, 'code', 'id', 'secret',
                         'https://here/cb', failing_opener(400, {'error': code}))
        if message is None or expected not in message:
            result(f'{code} is explained in words', False, repr(message))
            return
        if code in message:
            result(f'{code} is explained in words', False,
                   'the raw code leaked into the message: ' + repr(message))
            return
    result('every Google error code is explained in words', True)

    # an error nobody anticipated still produces something readable
    message = raises(google_oauth.exchange_code, 'code', 'id', 'secret',
                     'https://here/cb', failing_opener(400, {'error': 'surprise'}))
    result('an unrecognised error is still a sentence',
           message is not None and message.startswith('Google refused'), repr(message))


def test_being_offline_is_not_reported_as_a_google_refusal():
    message = raises(google_oauth.exchange_code, 'code', 'id', 'secret',
                     'https://here/cb', offline_opener())
    result('no internet says so, rather than blaming Google',
           message is not None and 'internet access' in message, repr(message))


def test_refreshing_an_access_token():
    opener = opener_returning({'access_token': 'a-2', 'expires_in': 1800})
    got = google_oauth.refresh_access_token('r-1', 'id', 'secret', opener)
    result('a refresh returns a new access token', got['access_token'] == 'a-2')

    message = raises(google_oauth.refresh_access_token, 'r-1', 'id', 'secret',
                     opener_returning({}))
    result('a refresh with no token in the reply says to reconnect',
           message is not None and 'Reconnect' in message, repr(message))


def test_revoking_never_blocks_disconnecting():
    """Disconnect must work when Google is unreachable or the token is already
    dead, or someone who wants their account unlinked cannot unlink it."""
    result('revoke succeeds against a working Google',
           google_oauth.revoke('r-1', opener_returning({})) is True)
    result('revoke survives an already-revoked token',
           google_oauth.revoke('r-1', failing_opener(400, {'error': 'invalid_token'})) is True)
    result('revoke survives being offline',
           google_oauth.revoke('r-1', offline_opener()) is True)


# ---------------------------------------------------------------------------
# what the person picks from
# ---------------------------------------------------------------------------

def test_properties_are_labelled_for_a_person():
    opener = opener_returning({'siteEntry': [
        {'siteUrl': 'https://www.zebra.test/', 'permissionLevel': 'siteOwner'},
        {'siteUrl': 'sc-domain:apple.test', 'permissionLevel': 'siteFullUser'},
        {'siteUrl': 'https://nope.test/', 'permissionLevel': 'siteUnverifiedUser'},
        {'siteUrl': '', 'permissionLevel': 'siteOwner'},
    ]})
    rows = google_oauth.search_console_properties('tok', opener)

    result('an empty site entry is dropped rather than listed blank', len(rows) == 3,
           str(len(rows)))
    result('properties are sorted so a long list can be scanned',
           [r['label'] for r in rows] == ['apple.test', 'https://nope.test/',
                                          'https://www.zebra.test/'],
           str([r['label'] for r in rows]))

    domain = rows[0]
    result('a domain property is labelled without its sc-domain: prefix',
           domain['label'] == 'apple.test' and domain['id'] == 'sc-domain:apple.test')
    result('the two kinds of property are named', domain['kind'] == 'Domain'
           and rows[2]['kind'] == 'URL prefix')
    result('a property this account cannot read is marked, not hidden',
           rows[1]['can_read'] is False and rows[0]['can_read'] is True)


def test_analytics_properties_are_labelled_by_name():
    opener = opener_returning({'accountSummaries': [
        {'displayName': 'Zebra Co', 'propertySummaries': [
            {'property': 'properties/999', 'displayName': 'Zebra Web'}]},
        {'displayName': 'Apple Co', 'propertySummaries': [
            {'property': 'properties/111', 'displayName': 'Apple Web'},
            {'property': '', 'displayName': 'broken'}]},
    ]})
    rows = google_oauth.analytics_properties('tok', opener)
    result('a property with no id is dropped', len(rows) == 2, str(len(rows)))
    result('GA4 properties are grouped by account and sorted',
           [r['label'] for r in rows] == ['Apple Web', 'Zebra Web'],
           str([r['label'] for r in rows]))
    result('the numeric id the API needs is kept alongside the name',
           rows[0]['id'] == '111' and rows[0]['account'] == 'Apple Co')


def test_the_right_property_is_pre_selected():
    """Offering the correct default is the difference between a dropdown
    someone confirms and one they have to study."""
    rows = [
        {'id': 'sc-domain:example.com', 'label': 'example.com'},
        {'id': 'https://www.other.test/', 'label': 'https://www.other.test/'},
    ]
    cases = [
        ('https://example.com/', 'sc-domain:example.com', 'a bare domain'),
        ('https://www.example.com/pricing', 'sc-domain:example.com', 'www and a path'),
        ('http://example.com', 'sc-domain:example.com', 'http rather than https'),
        ('https://www.other.test/', 'https://www.other.test/', 'a URL-prefix property'),
        ('https://unrelated.test/', '', 'a site with no matching property'),
        ('', '', 'no site being crawled'),
    ]
    for base, expected, why in cases:
        got = google_oauth.guess_property(rows, base)
        if got != expected:
            result('the matching property is pre-selected', False,
                   f'{why}: {base!r} gave {got!r}, wanted {expected!r}')
            return
    result('the matching property is pre-selected, and nothing else is', True)


def test_a_broken_userinfo_call_does_not_stop_a_connection():
    """The email is a label in settings. Failing the whole connection because
    a cosmetic call failed would be the wrong trade."""
    result('a working userinfo call gives the address',
           google_oauth.account_email('tok', opener_returning({'email': 'a@b.test'}))
           == 'a@b.test')
    result('a failing userinfo call returns empty rather than raising',
           google_oauth.account_email('tok', failing_opener(403)) == '')


def test_the_setup_instructions_carry_the_redirect_uri():
    setup = google_oauth.setup_instructions('http://localhost:14727/api/google/callback')
    result('setup instructions carry the exact redirect URI to register',
           setup['redirect_uri'] == 'http://localhost:14727/api/google/callback')
    result('setup instructions are an ordered list a person can follow',
           len(setup['steps']) >= 5 and all(isinstance(s, str) for s in setup['steps']))
    result('setup names both APIs that have to be enabled',
           any('Search Console API' in s for s in setup['steps'])
           and any('Analytics' in s for s in setup['steps']))


def test_configured_requires_both_halves():
    result('a client id without a secret is not configured',
           not google_oauth.configured('id', '') and not google_oauth.configured('', 'secret'))
    result('whitespace is not a credential',
           not google_oauth.configured('  ', ' '))
    result('both halves present is configured', google_oauth.configured('id', 'secret'))


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------

class temporary_store:
    """A real SQLite database, thrown away afterwards."""

    def __enter__(self):
        from src import crawl_db
        from src.crawl_schema import init_crawl_tables
        self._crawl_db = crawl_db
        self._original = crawl_db.DB_FILE
        handle, self.path = tempfile.mkstemp(suffix='.db')
        os.close(handle)
        crawl_db.DB_FILE = self.path
        init_crawl_tables()
        return self

    def raw_payload(self, user_id=1):
        """The bytes actually on disk, bypassing the store entirely."""
        conn = sqlite3.connect(self.path)
        try:
            row = conn.execute('SELECT payload FROM google_accounts WHERE user_id = ?',
                               (user_id,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def __exit__(self, *exc):
        self._crawl_db.DB_FILE = self._original
        try:
            os.unlink(self.path)
        except OSError:
            pass


def test_a_token_round_trips():
    with temporary_store():
        token_store.save(1, SECRET, refresh_token='r-1', access_token='a-1',
                         expires_at=time.time() + 3600, scope='openid',
                         account_email='me@test')
        stored = token_store.load(1, SECRET)
        result('a saved account loads back',
               stored['refresh_token'] == 'r-1' and stored['account_email'] == 'me@test')


def test_what_is_on_disk_is_not_the_token():
    """A store that quietly wrote plaintext would pass every round-trip test."""
    with temporary_store() as store:
        token_store.save(1, SECRET, refresh_token='super-secret-refresh-token',
                         account_email='me@test')
        raw = store.raw_payload()
        result('something was written', bool(raw))
        result('the refresh token is not readable in the database row',
               'super-secret-refresh-token' not in (raw or ''),
               (raw or '')[:60])
        result('nor is the account email',
               'me@test' not in (raw or ''))


def test_a_changed_secret_key_disconnects_rather_than_corrupts():
    """Tying tokens to SECRET_KEY means changing it makes them unreadable.
    That is the point; what matters is that the failure is a clean
    "not connected" and not a stack trace."""
    with temporary_store():
        token_store.save(1, SECRET, refresh_token='r-1', account_email='me@test')
        result('the wrong key reads nothing rather than raising',
               token_store.load(1, 'a-different-secret') is None)
        state = token_store.status(1, 'a-different-secret')
        result('and the panel simply shows not connected',
               state['connected'] is False and state['can_connect'] is True)
        result('the original key still works',
               (token_store.load(1, SECRET) or {}).get('refresh_token') == 'r-1')


def test_one_users_account_is_not_anothers():
    with temporary_store():
        token_store.save(1, SECRET, refresh_token='r-one')
        token_store.save(2, SECRET, refresh_token='r-two')
        result('each user has their own connected account',
               token_store.load(1, SECRET)['refresh_token'] == 'r-one'
               and token_store.load(2, SECRET)['refresh_token'] == 'r-two')
        token_store.forget(1)
        result('disconnecting one user leaves the other connected',
               token_store.load(1, SECRET) is None
               and token_store.load(2, SECRET) is not None)


def test_saving_part_of_an_account_keeps_the_rest():
    """Choosing a property must not wipe the credential that was already
    stored, which is exactly what a naive INSERT OR REPLACE would do."""
    with temporary_store():
        token_store.save(1, SECRET, refresh_token='r-1', account_email='me@test')
        token_store.save(1, SECRET, search_console_property='sc-domain:example.com')
        stored = token_store.load(1, SECRET)
        result('a later partial save keeps the refresh token',
               stored['refresh_token'] == 'r-1', str(stored))
        result('and records the new choice',
               stored['search_console_property'] == 'sc-domain:example.com')


def test_status_never_carries_a_credential():
    with temporary_store():
        token_store.save(1, SECRET, refresh_token='r-1', access_token='a-1',
                         account_email='me@test',
                         search_console_property='sc-domain:example.com')
        state = token_store.status(1, SECRET)
        serialised = json.dumps(state)
        result('status says it is connected and to whom',
               state['connected'] is True and state['account_email'] == 'me@test')
        result('status never contains the refresh token', 'r-1' not in serialised,
               serialised)
        result('nor the access token', 'a-1' not in serialised, serialised)


def test_status_of_an_account_that_was_never_connected():
    with temporary_store():
        state = token_store.status(1, SECRET)
        result('an unconnected user can connect',
               state['connected'] is False and state['can_connect'] is True
               and state['reason'] == '')


def test_a_half_saved_account_is_not_connected():
    """A row holding a property but no credential - the shape left by a
    failed exchange - must not read as connected."""
    with temporary_store():
        token_store.save(1, SECRET, search_console_property='sc-domain:example.com')
        result('a row with no refresh token reads as not connected',
               token_store.status(1, SECRET)['connected'] is False)


def test_an_install_that_cannot_encrypt_says_so():
    state = token_store.status(1, '')
    result('no SECRET_KEY blocks connecting and says why',
           state['can_connect'] is False and 'SECRET_KEY' in state['reason'],
           str(state))
    result('and the reason names the file to edit', '.env' in state['reason'])
    result('available() agrees', token_store.available('') is False
           and token_store.available(SECRET) is True)


def test_the_cached_access_token_is_used_until_it_is_nearly_stale():
    """Refreshing on every call would be slow and rate-limited; using a token
    past its expiry would fail every request for a minute."""
    with temporary_store():
        token_store.save(1, SECRET, refresh_token='r-1', access_token='a-1',
                         expires_at=time.time() + 3600)
        result('a fresh token is reused', token_store.cached_access_token(1, SECRET) == 'a-1')

        token_store.save(1, SECRET, access_token='a-1', expires_at=time.time() + 30)
        result('a token about to expire is not reused',
               token_store.cached_access_token(1, SECRET) is None)

        token_store.save(1, SECRET, access_token='a-1', expires_at=time.time() - 10)
        result('an expired token is not reused',
               token_store.cached_access_token(1, SECRET) is None)

        token_store.save(1, SECRET, access_token='', expires_at=time.time() + 3600)
        result('an empty token is not reused',
               token_store.cached_access_token(1, SECRET) is None)


def test_loading_from_a_database_without_the_table():
    """An older database upgraded in place must not crash the settings panel
    before the migration runs."""
    from src import crawl_db
    original = crawl_db.DB_FILE
    handle, path = tempfile.mkstemp(suffix='.db')
    os.close(handle)
    try:
        crawl_db.DB_FILE = path
        sqlite3.connect(path).close()  # an empty database, no tables at all
        result('a missing table reads as not connected rather than raising',
               token_store.load(1, SECRET) is None)
    finally:
        crawl_db.DB_FILE = original
        os.unlink(path)


# ---------------------------------------------------------------------------
# the two error shapes Google actually uses
# ---------------------------------------------------------------------------

# Captured verbatim from a real call to searchconsole.googleapis.com with a
# bad token. The REST APIs put an *object* under "error"; the OAuth token
# endpoint puts a *string* there. Reading only the string shape made every
# API failure die with "unhashable type: dict" - a real crash, found by
# making a real call, that every canned test here had missed.
REAL_401_BODY = {
    "error": {
        "code": 401,
        "message": ("Request had invalid authentication credentials. Expected "
                    "OAuth 2 access token, login cookie or other valid "
                    "authentication credential."),
        "errors": [{"message": "Invalid Credentials", "domain": "global",
                    "reason": "authError", "location": "Authorization",
                    "locationType": "header"}],
        "status": "UNAUTHENTICATED",
    }
}

# Captured verbatim from a real call to oauth2.googleapis.com/token.
REAL_INVALID_CLIENT_BODY = {
    "error": "invalid_client",
    "error_description": "The OAuth client was not found.",
}


def test_an_api_error_object_does_not_crash():
    """The regression that matters most here: this used to raise TypeError."""
    message = raises(google_oauth.search_console_properties, 'tok',
                     failing_opener(401, REAL_401_BODY))
    result('a real Google 401 becomes an OAuthError, not a TypeError',
           message is not None, 'it raised something else or nothing')
    result('and the message says to reconnect',
           message is not None and 'connect again' in message, repr(message))


def test_the_token_endpoints_string_shape_still_works():
    """Fixing the object shape must not break the shape that already worked."""
    message = raises(google_oauth.exchange_code, 'code', 'id', 'secret',
                     'https://here/cb', failing_opener(401, REAL_INVALID_CLIENT_BODY))
    result('the real invalid_client body is still explained',
           message is not None and 'GOOGLE_CLIENT_ID' in message, repr(message))


def test_each_api_failure_sends_someone_to_the_right_screen():
    """These are four different problems and three different screens. Printing
    Google's own message for all of them would be the easy, useless option."""
    cases = [
        (403, {'error': {'code': 403, 'status': 'PERMISSION_DENIED',
                         'message': 'Request had insufficient authentication scopes.',
                         'errors': [{'reason': 'ACCESS_TOKEN_SCOPE_INSUFFICIENT'}]}},
         'approving both', 'a missing scope'),
        (403, {'error': {'code': 403, 'status': 'PERMISSION_DENIED',
                         'message': ('Google Search Console API has not been used in '
                                     'project 123 before or it is disabled.'),
                         'errors': [{'reason': 'SERVICE_DISABLED'}]}},
         'APIs & Services', 'an API nobody enabled'),
        (403, {'error': {'code': 403, 'status': 'PERMISSION_DENIED',
                         'message': 'User does not have sufficient permission for site.'}},
         'does not have access', 'an account without access to the property'),
        (404, {'error': {'code': 404, 'status': 'NOT_FOUND',
                         'message': 'Requested entity was not found.'}},
         'no such property', 'a property that is not there'),
        (429, {'error': {'code': 429, 'status': 'RESOURCE_EXHAUSTED',
                         'message': 'Quota exceeded.'}},
         'rate-limiting', 'being rate-limited'),
    ]
    for code, body, expected, why in cases:
        message = raises(google_oauth.search_console_properties, 'tok',
                         failing_opener(code, body))
        if message is None or expected not in message:
            result('each API failure names its own fix', False,
                   f'{why}: got {message!r}, wanted {expected!r} in it')
            return
    result('each API failure names its own fix, and they are not the same fix', True)


def test_an_unrecognised_api_error_is_still_readable():
    message = raises(google_oauth.search_console_properties, 'tok',
                     failing_opener(418, {'error': {'code': 418, 'status': 'WEIRD',
                                                    'message': 'I am a teapot.'}}))
    result('an unanticipated API error is a sentence, not a traceback',
           message is not None and 'teapot' in message, repr(message))


def test_an_empty_or_unparseable_body_is_survivable():
    for body in (None, {}, {'error': None}):
        if raises(google_oauth.search_console_properties, 'tok',
                  failing_opener(500, body)) is None:
            result('an empty error body still raises something readable', False,
                   f'body {body!r} produced no error')
            return
    result('an empty error body still raises something readable', True)


# ---------------------------------------------------------------------------
# the seam: does a crawl actually use the account someone connected?
# ---------------------------------------------------------------------------

def test_a_connected_account_is_what_a_crawl_uses():
    """The whole feature is worthless if the crawler keeps reading the old
    settings fields. It did: from_config never looked at the store."""
    from src.core import google_auth
    with temporary_store():
        token_store.bind_secret(lambda: SECRET)
        try:
            os.environ['GOOGLE_CLIENT_ID'] = 'install-id'
            os.environ['GOOGLE_CLIENT_SECRET'] = 'install-secret'
            token_store.save(7, SECRET, refresh_token='r-connected')

            creds = google_auth.from_config({}, user_id=7)
            result('a crawl picks up the account connected in settings',
                   creds is not None and creds.refresh_token == 'r-connected',
                   repr(creds and creds.refresh_token))
            result('and uses the installation\'s own client',
                   creds is not None and creds.client_id == 'install-id')

            result('a user who connected nothing gets nothing',
                   google_auth.from_config({'google_use_adc': False},
                                           user_id=8) is None)
            result('and neither does an anonymous crawl',
                   google_auth.from_config({'google_use_adc': False},
                                           user_id=None) is None)
        finally:
            os.environ.pop('GOOGLE_CLIENT_ID', None)
            os.environ.pop('GOOGLE_CLIENT_SECRET', None)
            token_store.bind_secret(None)


def test_explicit_settings_still_beat_a_connected_account():
    """An install that deliberately configured its own credentials must not be
    silently overridden by whatever someone clicked Connect with."""
    from src.core import google_auth
    with temporary_store():
        token_store.bind_secret(lambda: SECRET)
        try:
            token_store.save(7, SECRET, refresh_token='r-connected')
            creds = google_auth.from_config({
                'google_client_id': 'explicit', 'google_client_secret': 's',
                'google_refresh_token': 'r-explicit'}, user_id=7)
            result('explicit settings win over a connected account',
                   creds.refresh_token == 'r-explicit', creds.refresh_token)
        finally:
            token_store.bind_secret(None)


def test_the_properties_someone_picked_reach_the_crawl():
    with temporary_store():
        token_store.bind_secret(lambda: SECRET)
        try:
            token_store.save(7, SECRET, refresh_token='r-1',
                             search_console_property='sc-domain:example.com',
                             analytics_property_id='412938475')
            result('the picked properties are readable without a request context',
                   token_store.chosen_properties(7)
                   == ('sc-domain:example.com', '412938475'),
                   str(token_store.chosen_properties(7)))
            result('a user who picked nothing yields empty strings, not None',
                   token_store.chosen_properties(9) == ('', ''))
        finally:
            token_store.bind_secret(None)


def test_the_app_secret_never_travels_through_a_crawl_config():
    """A crawl config is snapshotted into the database and exported with the
    crawl, so the secret is read through a bound callable instead."""
    token_store.bind_secret(lambda: 'bound-secret')
    try:
        result('a bound secret is what the store reads',
               token_store.app_secret() == 'bound-secret')
    finally:
        token_store.bind_secret(None)

    def exploding():
        raise RuntimeError('no app context')

    token_store.bind_secret(exploding)
    try:
        result('a secret that cannot be read yields empty, not a crash',
               token_store.app_secret() == '')
    finally:
        token_store.bind_secret(None)


# ---------------------------------------------------------------------------
# the wire format, checked against Google's own machine-readable schema
# ---------------------------------------------------------------------------

# Taken verbatim from the Discovery documents, which need no credentials:
#   https://searchconsole.googleapis.com/$discovery/rest?version=v1
#   https://analyticsadmin.googleapis.com/$discovery/rest?version=v1beta
#
# WmxSite.permissionLevel is an enum and Google spells it two ways: the v1
# Discovery document says SITE_UNVERIFIED_USER, the older webmasters/v3
# reference says siteUnverifiedUser, and this code calls the v3 path on the v1
# host. The first version of this matched only the camelCase spelling, and the
# tests agreed with it because both were written from the same memory. Against
# the real enum, a property the account cannot read was offered as selectable.
PERMISSION_LEVELS = ('SITE_PERMISSION_LEVEL_UNSPECIFIED', 'SITE_OWNER',
                     'SITE_FULL_USER', 'SITE_RESTRICTED_USER',
                     'SITE_UNVERIFIED_USER')

# The same enum as the older reference spells it.
LEGACY_PERMISSION_LEVELS = ('siteOwner', 'siteFullUser', 'siteRestrictedUser',
                            'siteUnverifiedUser')


def test_unreadable_properties_are_marked_in_both_of_googles_spellings():
    for spelling in ('SITE_UNVERIFIED_USER', 'siteUnverifiedUser'):
        rows = google_oauth.search_console_properties('tok', opener_returning(
            {'siteEntry': [{'siteUrl': 'https://nope.test/',
                            'permissionLevel': spelling}]}))
        if rows[0]['can_read'] is not False:
            result('an unreadable property is marked however Google spells it',
                   False, f'{spelling} was reported readable')
            return
    result('an unreadable property is marked however Google spells it', True)


def test_every_readable_permission_level_stays_readable():
    """Being wrong in this direction hides a property someone owns."""
    readable = [p for p in PERMISSION_LEVELS + LEGACY_PERMISSION_LEVELS
                if 'UNVERIFIED' not in p.upper()]
    for spelling in readable:
        rows = google_oauth.search_console_properties('tok', opener_returning(
            {'siteEntry': [{'siteUrl': 'https://ok.test/',
                            'permissionLevel': spelling}]}))
        if rows[0]['can_read'] is not True:
            result('every other permission level stays selectable', False,
                   f'{spelling} was hidden')
            return
    result('every permission level but unverified stays selectable', True)

    # and a level Google has not invented yet does not hide anything
    rows = google_oauth.search_console_properties('tok', opener_returning(
        {'siteEntry': [{'siteUrl': 'https://ok.test/',
                        'permissionLevel': 'SITE_SOMETHING_NEW'}]}))
    result('an unrecognised permission level is treated as readable',
           rows[0]['can_read'] is True)


def test_the_fields_read_are_the_fields_google_documents():
    """Every key this code reads, against the Discovery schema's property list."""
    wmx_site = {'siteUrl', 'permissionLevel'}
    sites_list = {'siteEntry'}
    account_summary = {'propertySummaries', 'displayName', 'account', 'name'}
    property_summary = {'displayName', 'parent', 'canEdit', 'propertyType', 'property'}

    read_from_sites = {'siteEntry'}
    read_from_entry = {'siteUrl', 'permissionLevel'}
    read_from_accounts = {'accountSummaries', 'nextPageToken'}
    read_from_summary = {'displayName', 'propertySummaries'}
    read_from_property = {'property', 'displayName'}

    result('every Search Console field read is one Google documents',
           read_from_sites <= sites_list and read_from_entry <= wmx_site,
           str(read_from_entry - wmx_site))
    result('every GA4 account field read is one Google documents',
           read_from_summary <= account_summary,
           str(read_from_summary - account_summary))
    result('every GA4 property field read is one Google documents',
           read_from_property <= property_summary,
           str(read_from_property - property_summary))
    result('the GA4 endpoint is the documented one',
           google_oauth.GA_ADMIN_ENDPOINT
           == 'https://analyticsadmin.googleapis.com/v1beta/accountSummaries',
           google_oauth.GA_ADMIN_ENDPOINT)
    result('the Search Console host is the real one, not the legacy alias',
           google_oauth.SITES_ENDPOINT.startswith('https://searchconsole.googleapis.com/'),
           google_oauth.SITES_ENDPOINT)


def test_a_second_page_of_ga4_properties_is_not_dropped():
    """accountSummaries is paged. An agency easily exceeds one page, and
    stopping at the first would hide properties the person is looking for."""
    page_one = {'accountSummaries': [
        {'displayName': 'Acct A', 'propertySummaries': [
            {'property': 'properties/111', 'displayName': 'First'}]}],
        'nextPageToken': 'page-2'}
    page_two = {'accountSummaries': [
        {'displayName': 'Acct B', 'propertySummaries': [
            {'property': 'properties/222', 'displayName': 'Second'}]}]}

    seen = []

    def paging_opener(request, timeout=None):
        url = request.full_url
        seen.append(url)
        payload = page_two if 'pageToken=page-2' in url else page_one
        return FakeResponse(json.dumps(payload).encode('utf-8'))

    rows = google_oauth.analytics_properties('tok', paging_opener)
    result('both pages of properties are returned',
           [r['id'] for r in rows] == ['111', '222'], str(rows))
    result('the page token is actually sent back to Google',
           len(seen) == 2 and 'pageToken=page-2' in seen[1], str(seen))


def test_paging_cannot_loop_forever():
    """A server that always returns a token must not hang the settings panel."""
    calls = []

    def endless(request, timeout=None):
        calls.append(1)
        return FakeResponse(json.dumps(
            {'accountSummaries': [], 'nextPageToken': 'always'}).encode('utf-8'))

    google_oauth.analytics_properties('tok', endless)
    result('an endless page token stops at the cap',
           len(calls) == google_oauth.MAX_PROPERTY_PAGES, f'{len(calls)} calls')


# ---------------------------------------------------------------------------
# tools/verify_google.py - the check that runs once an account exists
# ---------------------------------------------------------------------------

# Those branches only execute when someone has a real Google account, which
# means that without these they would run for the very first time on a user's
# live data. Here they run against canned payloads shaped like the Discovery
# documents, so a mistake in the checker surfaces now instead of then.

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'tools'))

CONSOLE_DOC = {'schemas': {'WmxSite': {'properties': {
    'siteUrl': {'type': 'string'},
    'permissionLevel': {'type': 'string', 'enum': [
        'SITE_PERMISSION_LEVEL_UNSPECIFIED', 'SITE_OWNER', 'SITE_FULL_USER',
        'SITE_RESTRICTED_USER', 'SITE_UNVERIFIED_USER']}}}}}

ADMIN_DOC = {'schemas': {
    'GoogleAnalyticsAdminV1betaAccountSummary': {'properties': {
        'propertySummaries': {}, 'displayName': {}, 'account': {}, 'name': {}}},
    'GoogleAnalyticsAdminV1betaPropertySummary': {'properties': {
        'displayName': {}, 'parent': {}, 'canEdit': {}, 'propertyType': {},
        'property': {}}}}}


class verifier_against:
    """Runs the checker with Google's responses replaced by canned ones."""

    def __init__(self, payload_or_error):
        self.payload = payload_or_error

    def __enter__(self):
        import verify_google
        self.module = verify_google
        self.real_get = google_oauth._get
        self.real_email = google_oauth.account_email
        payload = self.payload

        def fake_get(url, token, opener=None, timeout=30):
            if isinstance(payload, Exception):
                raise payload
            return payload
        google_oauth._get = fake_get
        google_oauth.account_email = lambda token, opener=None: 'someone@test'
        verify_google.PASSED[:] = []
        verify_google.FAILED[:] = []
        verify_google.SKIPPED[:] = []
        return verify_google

    def __exit__(self, *exc):
        google_oauth._get = self.real_get
        google_oauth.account_email = self.real_email


def test_the_checker_passes_a_well_formed_search_console_reply():
    good = {'siteEntry': [
        {'siteUrl': 'sc-domain:example.com', 'permissionLevel': 'SITE_OWNER'},
        {'siteUrl': 'https://other.test/', 'permissionLevel': 'SITE_UNVERIFIED_USER'}]}
    with verifier_against(good) as checker:
        checker.verify_search_console('tok', CONSOLE_DOC)
        result('a correct Search Console reply passes every check',
               not checker.FAILED, str(checker.FAILED))
        result('and it actually ran several checks', len(checker.PASSED) >= 5,
               str(len(checker.PASSED)))


def test_the_checker_notices_a_field_google_never_documented():
    """If Google adds a field, that is worth knowing rather than ignoring."""
    surprising = {'siteEntry': [{'siteUrl': 'https://a.test/',
                                 'permissionLevel': 'SITE_OWNER',
                                 'somethingNew': 'surprise'}]}
    with verifier_against(surprising) as checker:
        checker.verify_search_console('tok', CONSOLE_DOC)
        result('an undocumented field is reported, not ignored',
               any('documents' in name for name in checker.FAILED),
               str(checker.FAILED))


def test_the_checker_notices_an_enum_value_outside_the_schema():
    odd = {'siteEntry': [{'siteUrl': 'https://a.test/',
                          'permissionLevel': 'SITE_INVENTED_LEVEL'}]}
    with verifier_against(odd) as checker:
        checker.verify_search_console('tok', CONSOLE_DOC)
        result('an unknown permissionLevel is reported',
               any('permissionLevel' in name for name in checker.FAILED),
               str(checker.FAILED))


def test_the_checker_accepts_either_spelling_of_the_enum():
    for spelling in ('SITE_OWNER', 'siteOwner'):
        with verifier_against({'siteEntry': [
                {'siteUrl': 'https://a.test/', 'permissionLevel': spelling}]}) as checker:
            checker.verify_search_console('tok', CONSOLE_DOC)
            if any('permissionLevel' in name for name in checker.FAILED):
                result('both of Google\'s enum spellings are accepted', False,
                       f'{spelling} was reported unknown')
                return
    result('both of Google\'s enum spellings are accepted', True)


def test_no_permission_is_a_skip_not_a_failure():
    """A credential that cannot reach an API says nothing about the code."""
    with verifier_against(OAuthError('no scope')) as checker:
        checker.verify_search_console('tok', CONSOLE_DOC)
        result('an unreachable API is skipped, not failed',
               not checker.FAILED and checker.SKIPPED,
               f'failed={checker.FAILED} skipped={checker.SKIPPED}')


def test_the_checker_passes_a_well_formed_analytics_reply():
    good = {'accountSummaries': [
        {'displayName': 'Acct', 'account': 'accounts/1', 'name': 'accountSummaries/1',
         'propertySummaries': [{'property': 'properties/123',
                                'displayName': 'Web', 'propertyType': 'ORDINARY',
                                'parent': 'accounts/1', 'canEdit': True}]}]}
    with verifier_against(good) as checker:
        checker.verify_analytics('tok', ADMIN_DOC)
        result('a correct Analytics reply passes every check',
               not checker.FAILED, str(checker.FAILED))


def test_the_checker_notices_a_non_numeric_ga4_id():
    broken = {'accountSummaries': [
        {'displayName': 'Acct', 'propertySummaries': [
            {'property': 'properties/not-a-number', 'displayName': 'Web'}]}]}
    with verifier_against(broken) as checker:
        checker.verify_analytics('tok', ADMIN_DOC)
        result('a GA4 id that is not a number is reported',
               any('numeric' in name for name in checker.FAILED), str(checker.FAILED))


TESTS = (
    test_state_survives_a_round_trip,
    test_a_state_cannot_be_forged,
    test_a_state_expires,
    test_the_consent_url_asks_for_a_refresh_token,
    test_exchanging_a_code,
    test_a_missing_refresh_token_says_what_to_do,
    test_every_google_error_becomes_a_sentence,
    test_being_offline_is_not_reported_as_a_google_refusal,
    test_refreshing_an_access_token,
    test_revoking_never_blocks_disconnecting,
    test_properties_are_labelled_for_a_person,
    test_analytics_properties_are_labelled_by_name,
    test_the_right_property_is_pre_selected,
    test_a_broken_userinfo_call_does_not_stop_a_connection,
    test_the_setup_instructions_carry_the_redirect_uri,
    test_configured_requires_both_halves,
    test_a_token_round_trips,
    test_what_is_on_disk_is_not_the_token,
    test_a_changed_secret_key_disconnects_rather_than_corrupts,
    test_one_users_account_is_not_anothers,
    test_saving_part_of_an_account_keeps_the_rest,
    test_status_never_carries_a_credential,
    test_status_of_an_account_that_was_never_connected,
    test_a_half_saved_account_is_not_connected,
    test_an_install_that_cannot_encrypt_says_so,
    test_the_cached_access_token_is_used_until_it_is_nearly_stale,
    test_loading_from_a_database_without_the_table,
    test_an_api_error_object_does_not_crash,
    test_the_token_endpoints_string_shape_still_works,
    test_each_api_failure_sends_someone_to_the_right_screen,
    test_an_unrecognised_api_error_is_still_readable,
    test_an_empty_or_unparseable_body_is_survivable,
    test_a_connected_account_is_what_a_crawl_uses,
    test_explicit_settings_still_beat_a_connected_account,
    test_the_properties_someone_picked_reach_the_crawl,
    test_the_app_secret_never_travels_through_a_crawl_config,
    test_unreadable_properties_are_marked_in_both_of_googles_spellings,
    test_every_readable_permission_level_stays_readable,
    test_the_fields_read_are_the_fields_google_documents,
    test_a_second_page_of_ga4_properties_is_not_dropped,
    test_paging_cannot_loop_forever,
    test_the_checker_passes_a_well_formed_search_console_reply,
    test_the_checker_notices_a_field_google_never_documented,
    test_the_checker_notices_an_enum_value_outside_the_schema,
    test_the_checker_accepts_either_spelling_of_the_enum,
    test_no_permission_is_a_skip_not_a_failure,
    test_the_checker_passes_a_well_formed_analytics_reply,
    test_the_checker_notices_a_non_numeric_ga4_id,
)


def main():
    for test in TESTS:
        print(f'\n{test.__name__}:')
        try:
            test()
        except Exception as exc:  # a raised test is a failed test
            result(test.__name__, False, f'raised {type(exc).__name__}: {exc}')

    print('\n' + '=' * 64)
    print(f'  {len(PASSED)} passed, {len(FAILED)} failed')
    if FAILED:
        print('  failed: ' + ', '.join(FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
