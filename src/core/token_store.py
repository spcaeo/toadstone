"""Where a connected Google account is kept, and why it is encrypted.

A refresh token is not a password that expires in an hour. It is a standing
key to someone's Search Console and Analytics that keeps working until it is
revoked, and on a shared install it belongs to a person rather than to the
machine. Keeping one as plaintext JSON in a settings blob - which is where
this project put every other setting - would mean anyone who could read the
database, or a backup of it, or a copy of it attached to a bug report, could
read the whole of that person's search data.

So tokens live in their own table, encrypted with a key derived from the
application's SECRET_KEY, and they never travel to the browser. The settings
API returns whether an account is connected and which one, never the token.

Two consequences worth being plain about:

  * **If SECRET_KEY changes, stored tokens become unreadable.** That is the
    point of tying them to it. The failure is clean - the account shows as
    disconnected and the person reconnects with one click - rather than
    silent.
  * **This protects data at rest, not from the running application.** Anyone
    who can execute code as this app can decrypt them. Encryption here is
    about the database file, the backup and the copy someone emails.
"""

import base64
import hashlib
import json
import os
import time

from src import crawl_db

# Grows if Google ever returns something else worth keeping.
FIELDS = ('refresh_token', 'access_token', 'expires_at', 'scope', 'account_email',
          'search_console_property', 'analytics_property_id')


class TokenStoreError(Exception):
    """The store could not read or write a token, and the caller must say so."""


# A crawl runs on a background thread with no request and no app context, but
# still has to decrypt the account the person connected. The secret is read
# through a bound callable rather than carried in the crawl config, because
# a crawl's config is snapshotted into the database and exported with the
# crawl - which is the last place an application secret should end up.
_secret_source = None


def bind_secret(source):
    """Tell the store how to find the application secret. Called once at startup."""
    global _secret_source
    _secret_source = source


def app_secret():
    """The application secret, or '' if there is none to be had."""
    if _secret_source is not None:
        try:
            return _secret_source() or ''
        except Exception:
            return ''
    return os.environ.get('SECRET_KEY', '')


def chosen_properties(user_id):
    """The Search Console and GA4 properties this user picked in settings."""
    stored = load(user_id, app_secret()) or {}
    return (stored.get('search_console_property') or '',
            stored.get('analytics_property_id') or '')


def _fernet(secret_key):
    """A Fernet built from the app's secret, or None when unavailable.

    Returns None rather than raising when `cryptography` is missing so an
    install without it degrades to a clear "cannot store credentials"
    message instead of a stack trace at import time.
    """
    if not secret_key:
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    digest = hashlib.sha256(str(secret_key).encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def available(secret_key):
    """Can this install store a connected account at all?"""
    return _fernet(secret_key) is not None


def unavailable_reason(secret_key):
    """Why not, in words a person can act on."""
    if not secret_key:
        return ('SECRET_KEY is not set, so credentials cannot be encrypted. '
                'Set it in .env and restart.')
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return ('The cryptography package is not installed, so credentials '
                'cannot be encrypted. Run: pip install cryptography')
    return ''


def save(user_id, secret_key, **fields):
    """Store or update one user's connected Google account."""
    box = _fernet(secret_key)
    if box is None:
        raise TokenStoreError(unavailable_reason(secret_key))
    existing = load(user_id, secret_key) or {}
    merged = {key: fields.get(key, existing.get(key)) for key in FIELDS}
    blob = box.encrypt(json.dumps(merged).encode('utf-8')).decode('ascii')
    with crawl_db.get_db() as conn:
        conn.execute(
            'INSERT INTO google_accounts (user_id, payload, updated_at) '
            'VALUES (?, ?, CURRENT_TIMESTAMP) '
            'ON CONFLICT(user_id) DO UPDATE SET payload = excluded.payload, '
            'updated_at = CURRENT_TIMESTAMP',
            (user_id or 0, blob))
    return merged


def load(user_id, secret_key):
    """One user's connected account, or None.

    A token that cannot be decrypted - almost always because SECRET_KEY
    changed - is treated as absent rather than as an error, so the person
    simply sees "not connected" and reconnects.
    """
    box = _fernet(secret_key)
    if box is None:
        return None
    try:
        with crawl_db.get_db() as conn:
            row = conn.execute('SELECT payload FROM google_accounts WHERE user_id = ?',
                               (user_id or 0,)).fetchone()
    except Exception:
        return None
    if not row or not row['payload']:
        return None
    try:
        return json.loads(box.decrypt(row['payload'].encode('ascii')).decode('utf-8'))
    except Exception:
        return None


def forget(user_id):
    """Disconnect: remove the stored account entirely."""
    with crawl_db.get_db() as conn:
        conn.execute('DELETE FROM google_accounts WHERE user_id = ?', (user_id or 0,))
    return True


def status(user_id, secret_key):
    """What the UI shows. Never includes a token."""
    reason = unavailable_reason(secret_key)
    if reason:
        return {'connected': False, 'can_connect': False, 'reason': reason}
    stored = load(user_id, secret_key)
    if not stored or not stored.get('refresh_token'):
        return {'connected': False, 'can_connect': True, 'reason': ''}
    return {
        'connected': True,
        'can_connect': True,
        'reason': '',
        'account_email': stored.get('account_email') or '',
        'scope': stored.get('scope') or '',
        'search_console_property': stored.get('search_console_property') or '',
        'analytics_property_id': stored.get('analytics_property_id') or '',
    }


def cached_access_token(user_id, secret_key, margin=120):
    """A still-valid access token, or None when one must be fetched."""
    stored = load(user_id, secret_key) or {}
    token, expires = stored.get('access_token'), stored.get('expires_at')
    if token and expires and time.time() < float(expires) - margin:
        return token
    return None
