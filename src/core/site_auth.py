"""Signing in to the site being crawled.

Plenty of what is worth crawling sits behind a login: a staging site, an
intranet, a members' area, a shop's account pages. Without this, crawling one
produces a tidy report about a login page repeated four hundred times.

Three ways in, because sites differ:

  * **HTTP auth** — Basic or Digest, the `WWW-Authenticate` kind. One line of
    configuration, handled by `requests` itself.
  * **A login form** — POST a username and password to a URL, keep the
    cookies that come back. What most real sites use.
  * **Cookies you already have** — paste them from a browser session. The
    escape hatch for a login this cannot drive (SSO, a captcha, 2FA).

Two things this deliberately does not do. It does not try to *find* the login
form: guessing field names across arbitrary sites fails quietly and leaves
someone debugging a crawler instead of their site, so the field names are
configured. And it does not store a password in the crawl config, because a
crawl config is snapshotted into the database and exported with the crawl.
Credentials are passed in at session-setup time and the snapshot gets a
redacted copy.

After signing in, the session is **verified** before the crawl starts. A login
that silently failed is worse than no login at all: the crawl succeeds, every
page comes back 200, and every one of them is the login page.
"""

import re
import urllib.parse

# Fields whose values must never reach a crawl record, an export or a log.
SECRET_FIELDS = ('password', 'auth_password', 'login_password', 'secret',
                 'token', 'auth_token', 'cookie', 'cookies', 'auth_cookies')

REDACTED = '<redacted>'


class AuthError(Exception):
    """Signing in failed, with a message written for a person."""


def redacted(config):
    """A copy of a config safe to store, export or print.

    Called before a config is snapshotted into a crawl record. Anything whose
    key looks like a credential keeps its shape - so the UI can still show
    that a password is set - without keeping its value.
    """
    safe = {}
    for key, value in (config or {}).items():
        if isinstance(value, dict):
            safe[key] = redacted(value)
        elif any(secret in key.lower() for secret in SECRET_FIELDS) and value:
            safe[key] = REDACTED
        else:
            safe[key] = value
    return safe


def configured(config):
    """Is any form of site authentication set up?"""
    config = config or {}
    mode = (config.get('auth_mode') or 'none').strip().lower()
    return mode in ('basic', 'digest', 'form', 'cookies')


def parse_cookie_header(raw):
    """`a=1; b=2` as a dict, the way a browser's copy-as-cURL gives it."""
    jar = {}
    for part in (raw or '').split(';'):
        if '=' not in part:
            continue
        name, value = part.split('=', 1)
        name = name.strip()
        if name:
            jar[name] = value.strip()
    return jar


def apply(session, config, opener=None):
    """Sign `session` in to the site. Returns a short description of how.

    Raises AuthError with an actionable message if it cannot. The caller must
    surface that rather than crawling anyway: a crawl of a login page is not
    a crawl of the site.
    """
    config = config or {}
    mode = (config.get('auth_mode') or 'none').strip().lower()
    if mode in ('', 'none'):
        return ''

    if mode in ('basic', 'digest'):
        return _apply_http_auth(session, config, mode)
    if mode == 'cookies':
        return _apply_cookies(session, config)
    if mode == 'form':
        return _apply_form(session, config)
    raise AuthError(f'Unknown authentication mode {mode!r}. Use basic, digest, '
                    f'form or cookies.')


def _apply_http_auth(session, config, mode):
    user = (config.get('auth_username') or '').strip()
    password = config.get('auth_password') or ''
    if not user or not password:
        raise AuthError('HTTP authentication needs both a username and a password.')
    if mode == 'digest':
        from requests.auth import HTTPDigestAuth
        session.auth = HTTPDigestAuth(user, password)
    else:
        from requests.auth import HTTPBasicAuth
        session.auth = HTTPBasicAuth(user, password)
    return f'HTTP {mode} auth as {user}'


def _apply_cookies(session, config):
    raw = config.get('auth_cookies') or ''
    jar = raw if isinstance(raw, dict) else parse_cookie_header(raw)
    if not jar:
        raise AuthError('No cookies were given. Paste them as name=value pairs '
                        'separated by semicolons, as a browser shows them.')
    for name, value in jar.items():
        session.cookies.set(name, value)
    return f'{len(jar)} cookie{"s" if len(jar) != 1 else ""} supplied'


def _apply_form(session, config):
    """POST a login form and keep whatever cookies come back."""
    url = (config.get('auth_login_url') or '').strip()
    user = (config.get('auth_username') or '').strip()
    password = config.get('auth_password') or ''
    if not url:
        raise AuthError('Form login needs the URL the login form posts to.')
    if not user or not password:
        raise AuthError('Form login needs both a username and a password.')

    # Named rather than guessed: field names differ per site and per CMS, and
    # a wrong guess fails in a way that looks like wrong credentials.
    user_field = (config.get('auth_username_field') or 'username').strip()
    password_field = (config.get('auth_password_field') or 'password').strip()

    payload = {user_field: user, password_field: password}
    for pair in (config.get('auth_extra_fields') or '').split('&'):
        if '=' in pair:
            name, value = pair.split('=', 1)
            if name.strip():
                payload[name.strip()] = value.strip()

    try:
        response = session.post(url, data=payload, timeout=30,
                                allow_redirects=True)
    except Exception as exc:
        raise AuthError(f'Could not reach the login form at {url} '
                        f'({type(exc).__name__}).')

    if response.status_code >= 500:
        raise AuthError(f'The login form returned HTTP {response.status_code}. '
                        f'The site may be down.')
    if response.status_code in (401, 403):
        raise AuthError('The site rejected those credentials '
                        f'(HTTP {response.status_code}).')
    if not session.cookies:
        raise AuthError('The login POST succeeded but set no cookies, so the '
                        'crawl would not stay signed in. Check the login URL '
                        'and the field names.')
    return f'form login as {user}, {len(session.cookies)} cookie(s) held'


# ---------------------------------------------------------------------------
# proving it worked
# ---------------------------------------------------------------------------

# Signs that a page is the login screen rather than the content behind it.
LOGIN_MARKERS = (
    re.compile(r'<input[^>]+type=["\']password["\']', re.I),
    re.compile(r'\bname=["\'](?:pwd|passwd|password)["\']', re.I),
)


def looks_like_a_login_page(html):
    """A password field is the one reliable tell across CMSes and languages."""
    text = html or ''
    return any(marker.search(text) for marker in LOGIN_MARKERS)


def verify(session, url, config=None, opener=None):
    """Fetch `url` as the signed-in session and say whether it worked.

    Returns a description on success and raises AuthError on failure, because
    the alternative - crawling four hundred copies of a login page and
    reporting them as the site - wastes far more of someone's time than
    stopping here does.
    """
    config = config or {}
    if not configured(config):
        return ''
    try:
        getter = opener or session.get
        response = getter(url, timeout=30, allow_redirects=True)
    except Exception as exc:
        raise AuthError(f'Signed in, but could not fetch {url} to check it '
                        f'({type(exc).__name__}).')

    status = getattr(response, 'status_code', 0)
    if status in (401, 403):
        raise AuthError(f'Still refused after signing in (HTTP {status}). '
                        f'The credentials may be wrong, or the account may not '
                        f'have access to {url}.')

    body = getattr(response, 'text', '') or ''
    if looks_like_a_login_page(body):
        raise AuthError(
            'Signing in appeared to work, but the first page still looks like '
            'a login screen. The crawl would have reported the login page as '
            'the whole site. Check the login URL and the field names.')

    expected = (config.get('auth_expect_text') or '').strip()
    if expected and expected not in body:
        raise AuthError(f'Signed in, but {url} does not contain '
                        f'{expected!r}. That text is what this was told to '
                        f'look for to prove the session works.')
    return f'verified against {url}'
