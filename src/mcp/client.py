"""Two ways to reach the Toadstone API, behind one interface.

The MCP server is a wrapper over the HTTP API, not a second implementation of
it, so every tool goes through a client with three methods: `get_json`,
`post_json` and `get_text`. There are two clients because there are two
transports:

    HttpClient    the stdio server, running as its own process, talking to a
                  Toadstone instance over the network
    FlaskClient   the HTTP blueprint mounted inside main.py, and the tests,
                  talking to the same Flask app in-process through its test
                  client

`FlaskClient` forwards the caller's own cookies, so a request arriving on the
HTTP transport is answered as that user by the same `login_required` and the
same crawl-ownership check as every other route. Neither client can see a
crawl its caller could not already fetch itself.
"""

import json as _json

TEXT_LIMIT_NOTE = '\n[truncated by the MCP server]\n'


class ApiError(Exception):
    """The API answered, and the answer was a refusal.

    Carries the status so a tool can tell "no such crawl" (404) from "not
    yours" (403) from "the server fell over" (500).
    """

    def __init__(self, message, status=None, path=''):
        super().__init__(message)
        self.message = message
        self.status = status
        self.path = path

    def as_dict(self):
        return {'error': self.message, 'status': self.status, 'path': self.path}


class _Base:
    """Shared decoding. Subclasses supply `_request`."""

    def get_json(self, path, params=None):
        return self._decode(path, *self._request('GET', path, params=params))

    def post_json(self, path, payload=None, params=None):
        return self._decode(path, *self._request('POST', path, params=params,
                                                 payload=payload or {}))

    def get_text(self, path, params=None, limit=None):
        """A raw body (CSV, NDJSON, markdown), truncated to `limit` characters."""
        status, body, content_type = self._request('GET', path, params=params)
        text = body if isinstance(body, str) else body.decode('utf-8', 'replace')
        if status >= 400:
            raise ApiError(_error_of(text) or f'HTTP {status}', status, path)
        truncated = False
        if limit and len(text) > limit:
            text, truncated = text[:limit] + TEXT_LIMIT_NOTE, True
        return {'content_type': content_type, 'text': text, 'truncated': truncated}

    def _decode(self, path, status, body, content_type):
        text = body if isinstance(body, str) else body.decode('utf-8', 'replace')
        try:
            data = _json.loads(text or '{}')
        except ValueError:
            if status >= 400:
                raise ApiError(f'HTTP {status}', status, path)
            raise ApiError(f'Expected JSON from {path}, got {content_type or "no type"}',
                           status, path)
        if status >= 400 or (isinstance(data, dict) and data.get('success') is False):
            message = _error_of(text) or f'HTTP {status}'
            raise ApiError(message, status, path)
        return data


class HttpClient(_Base):
    """Talks to a Toadstone over the network, keeping one logged-in session."""

    def __init__(self, settings):
        import requests  # already a dependency; imported late to keep tests light

        self.settings = settings
        self.session = requests.Session()
        if settings.cookie:
            for pair in settings.cookie.split(';'):
                name, _, value = pair.partition('=')
                if name.strip():
                    self.session.cookies.set(name.strip(), value.strip())
        self._logged_in = not (settings.username or settings.password)

    def _login(self):
        if self._logged_in:
            return
        # Marked done before the attempt: a failed login must not be retried on
        # every tool call, and the API's own 401 is the clearer error anyway.
        self._logged_in = True
        try:
            self.session.post(f'{self.settings.base_url}/api/login',
                              json={'username': self.settings.username,
                                    'password': self.settings.password},
                              timeout=self.settings.timeout)
        except Exception:
            pass

    def _request(self, method, path, params=None, payload=None):
        import requests

        self._login()
        url = f'{self.settings.base_url}{path}'
        try:
            response = self.session.request(method, url, params=params, json=payload,
                                            timeout=self.settings.timeout)
        except requests.RequestException as error:
            raise ApiError(f'Could not reach Toadstone at {self.settings.base_url}: '
                           f'{type(error).__name__}: {error}', None, path)
        return response.status_code, response.text, response.headers.get('Content-Type', '')


class FlaskClient(_Base):
    """Talks to a Flask app in-process, as the caller who owns `cookies`."""

    def __init__(self, app, cookies=None):
        self.app = app
        self.cookies = dict(cookies or {})

    def _client(self):
        client = self.app.test_client()
        for name, value in self.cookies.items():
            try:
                client.set_cookie(name, value, domain='localhost')
            except TypeError:  # Werkzeug < 2.3 wanted the server name first
                client.set_cookie('localhost', name, value)
        return client

    def _request(self, method, path, params=None, payload=None):
        client = self._client()
        kwargs = {'query_string': {k: v for k, v in (params or {}).items()
                                   if v is not None}}
        if method == 'POST':
            kwargs['json'] = payload or {}
        response = client.open(path, method=method, **kwargs)
        return (response.status_code, response.get_data(as_text=True),
                response.headers.get('Content-Type', ''))


def _error_of(text):
    """The API's own error sentence, when the body carries one."""
    try:
        body = _json.loads(text or '{}')
    except ValueError:
        return ''
    if not isinstance(body, dict):
        return ''
    if body.get('error'):
        return str(body['error'])
    if body.get('errors'):
        return '; '.join(str(e) for e in body['errors'])
    if body.get('message'):
        return str(body['message'])
    return ''
