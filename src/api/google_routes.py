"""The Connect Google button, and everything behind it.

    GET  /api/google/status        what settings should show right now
    GET  /api/google/connect       -> Google's consent screen
    GET  /api/google/callback      <- Google, after the person decides
    GET  /api/google/properties    their sites and GA4 properties, for the pickers
    POST /api/google/property      remember which one they picked
    POST /api/google/disconnect    forget it, and tell Google too

Every response is written to be read by a person. A route that fails says
what to do next, not what went wrong internally, and nothing here ever
returns a token to the browser.
"""

import os

from flask import Blueprint, jsonify, redirect, request, session

from src.core import google_oauth, token_store
from src.core.google_oauth import OAuthError


def create_blueprint(login_required, secret_key_fn, base_url_fn=None):
    """Build the blueprint.

    `secret_key_fn()` returns the app secret, read live so a restart with a
    new one is noticed rather than cached. `base_url_fn()` optionally returns
    the site currently being crawled, used only to pre-select the matching
    property.
    """
    api = Blueprint('google_routes', __name__)

    def client():
        return (os.environ.get('GOOGLE_CLIENT_ID', '').strip(),
                os.environ.get('GOOGLE_CLIENT_SECRET', '').strip())

    def redirect_uri():
        # Built from the request so it is right on localhost, on a LAN
        # address and behind a proxy, without anyone configuring it twice.
        return request.url_root.rstrip('/') + '/api/google/callback'

    def me():
        return session.get('user_id') or 0

    def fresh_access_token(stored):
        """A usable access token, refreshing only when the cached one is stale."""
        cached = token_store.cached_access_token(me(), secret_key_fn())
        if cached:
            return cached
        client_id, client_secret = client()
        got = google_oauth.refresh_access_token(
            stored['refresh_token'], client_id, client_secret)
        token_store.save(me(), secret_key_fn(), **got)
        return got['access_token']

    @api.route('/api/google/status')
    @login_required
    def status():
        """Everything the settings panel needs, in one call."""
        client_id, client_secret = client()
        state = token_store.status(me(), secret_key_fn())
        state['installation_ready'] = google_oauth.configured(client_id, client_secret)
        state['redirect_uri'] = redirect_uri()
        state['scopes_explained'] = [
            {'name': name, 'why': why} for name, why in google_oauth.SCOPE_EXPLANATIONS]
        if not state['installation_ready']:
            state['setup'] = google_oauth.setup_instructions(redirect_uri())
        return jsonify({'success': True, 'google': state})

    @api.route('/api/google/connect')
    @login_required
    def connect():
        client_id, client_secret = client()
        if not google_oauth.configured(client_id, client_secret):
            return jsonify({
                'success': False,
                'error': 'This installation has no Google client configured yet.',
                'setup': google_oauth.setup_instructions(redirect_uri()),
            }), 400
        reason = token_store.unavailable_reason(secret_key_fn())
        if reason:
            return jsonify({'success': False, 'error': reason}), 400
        state = google_oauth.make_state(me(), secret_key_fn())
        return redirect(google_oauth.authorize_url(client_id, redirect_uri(), state))

    @api.route('/api/google/callback')
    def callback():
        """Google sends the person back here. Always ends on a readable page."""
        if request.args.get('error'):
            return _done(False, 'You declined the permission request, so nothing '
                                'was connected.')
        code, state = request.args.get('code'), request.args.get('state')
        if not code or not state:
            return _done(False, 'Google did not send back what was expected. '
                                'Try Connect again.')
        try:
            user_id = google_oauth.read_state(state, secret_key_fn())
        except OAuthError as exc:
            return _done(False, str(exc))

        client_id, client_secret = client()
        try:
            tokens = google_oauth.exchange_code(code, client_id, client_secret,
                                                redirect_uri())
        except OAuthError as exc:
            return _done(False, str(exc))

        email = google_oauth.account_email(tokens['access_token'])
        try:
            token_store.save(user_id, secret_key_fn(), account_email=email, **tokens)
        except token_store.TokenStoreError as exc:
            return _done(False, str(exc))
        return _done(True, f'Connected as {email}.' if email else 'Connected.')

    @api.route('/api/google/properties')
    @login_required
    def properties():
        """Their sites and GA4 properties, with a sensible default pre-chosen."""
        stored = token_store.load(me(), secret_key_fn())
        if not stored or not stored.get('refresh_token'):
            return jsonify({'success': False,
                            'error': 'No Google account is connected yet.'}), 400
        try:
            token = fresh_access_token(stored)
            sites = google_oauth.search_console_properties(token)
        except OAuthError as exc:
            return jsonify({'success': False, 'error': str(exc)}), 400

        try:
            ga = google_oauth.analytics_properties(token)
            ga_error = ''
        except OAuthError as exc:
            # Analytics is optional; a missing permission there must not stop
            # someone choosing a Search Console property.
            ga, ga_error = [], str(exc)

        base = base_url_fn() if base_url_fn else ''
        return jsonify({
            'success': True,
            'search_console': sites,
            'analytics': ga,
            'analytics_error': ga_error,
            'suggested_search_console': (stored.get('search_console_property')
                                         or google_oauth.guess_property(sites, base)),
            'suggested_analytics': stored.get('analytics_property_id') or '',
            'selected_search_console': stored.get('search_console_property') or '',
            'selected_analytics': stored.get('analytics_property_id') or '',
        })

    @api.route('/api/google/property', methods=['POST'])
    @login_required
    def choose_property():
        stored = token_store.load(me(), secret_key_fn())
        if not stored or not stored.get('refresh_token'):
            return jsonify({'success': False,
                            'error': 'No Google account is connected yet.'}), 400
        payload = request.get_json(silent=True) or {}
        token_store.save(
            me(), secret_key_fn(),
            search_console_property=(payload.get('search_console_property') or '').strip(),
            analytics_property_id=(payload.get('analytics_property_id') or '').strip())
        return jsonify({'success': True,
                        'google': token_store.status(me(), secret_key_fn())})

    @api.route('/api/google/disconnect', methods=['POST'])
    @login_required
    def disconnect():
        """Forget it here, and ask Google to forget us as well."""
        stored = token_store.load(me(), secret_key_fn())
        if stored and stored.get('refresh_token'):
            google_oauth.revoke(stored['refresh_token'])
        token_store.forget(me())
        return jsonify({'success': True,
                        'google': token_store.status(me(), secret_key_fn())})

    return api


def _done(ok, message):
    """The page Google returns someone to.

    Deliberately a whole page rather than a redirect with a query string: it
    is the last step of a flow that left this app entirely, and landing on
    something that plainly says what happened beats landing on a dashboard
    and having to look for a changed label.
    """
    tone = '#1c7a4a' if ok else '#b3341f'
    heading = 'Google connected' if ok else 'Not connected'
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{heading}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center;
         font:16px/1.6 ui-monospace,"SF Mono",Menlo,Consolas,monospace;
         background:#fbfbfa; color:#16181d; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background:#0d0f14; color:#e8eaed; }}
    .card {{ background:#1a1e27 !important; border-color:#272c37 !important; }} }}
  .card {{ background:#fff; border:1px solid #dcdcd7; border-radius:4px;
          padding:32px 36px; max-width:460px; margin:16px; }}
  h1 {{ font-size:18px; margin:0 0 10px; color:{tone}; }}
  p {{ margin:0 0 20px; }}
  a {{ display:inline-block; padding:9px 16px; border:1px solid currentColor;
      border-radius:3px; text-decoration:none; color:inherit; }}
</style></head>
<body><div class="card">
  <h1>{heading}</h1>
  <p>{message}</p>
  <a href="/">Back to Toadstone</a>
</div>
<script>
  // The settings panel that opened this listens for the result, so the
  // person does not have to refresh anything to see it update.
  try {{ if (window.opener) {{
    window.opener.postMessage({{source:'toadstone-google', ok:{str(ok).lower()}}},
                              window.location.origin);
    setTimeout(function () {{ window.close(); }}, 1200);
  }} }} catch (e) {{}}
</script>
</body></html>'''
