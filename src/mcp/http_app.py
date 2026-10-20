"""The HTTP transport: the same server, mounted inside Toadstone itself.

    POST /api/mcp     one JSON-RPC request, one JSON response
    GET  /api/mcp     what this endpoint is and how it is configured

This is MCP's Streamable HTTP transport with the streaming left out: every
method this server implements answers in one message, so there is nothing to
stream and no session to keep. A client that insists on SSE will not work
here; stdio is the supported path for Claude Code and Cursor, and this
exists for scripts, for remote agents, and for checking the server is alive
without starting a subprocess.

Requests go through main.py's own `login_required`, and the tools then reach
the API as that same user through `FlaskClient`, carrying the request's
cookies. So the HTTP transport can see exactly the crawls its caller could
already see - and nothing else.

main.py mounts it with one line:

    app.register_blueprint(_create_mcp_blueprint(login_required))
"""

from flask import Blueprint, current_app, jsonify, request

from src.mcp import config
from src.mcp.client import FlaskClient
from src.mcp.server import PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION, build


def create_blueprint(login_required, settings=None):
    """Build the blueprint. `login_required` is main.py's own decorator."""
    api = Blueprint('mcp', __name__)
    base = settings or config.from_env()

    @api.route('/api/mcp', methods=['GET'])
    @login_required
    def mcp_info():
        return jsonify({
            'success': True,
            'server': {'name': SERVER_NAME, 'version': SERVER_VERSION,
                       'protocolVersion': PROTOCOL_VERSION},
            'transport': 'POST a JSON-RPC 2.0 request to this URL.',
            'guardrails': base.describe(),
        })

    @api.route('/api/mcp', methods=['POST'])
    @login_required
    def mcp_rpc():
        raw = request.get_data(as_text=True) or ''
        client = FlaskClient(current_app, request.cookies)
        server = build(base, client)

        reply = server.handle_text(raw)
        if reply is None:
            # A notification. 202 with no body is what the transport expects.
            return '', 202
        return current_app.response_class(reply + '\n',
                                          mimetype='application/json')

    return api


def settings_for_http(app_base_url=''):
    """Settings for in-process use: whatever the environment says, plus a URL.

    The base URL matters only for the messages that tell a human where to
    download an xlsx export; nothing in HTTP mode dials out over the network.
    """
    settings = config.from_env()
    if app_base_url:
        settings.base_url = app_base_url.rstrip('/')
    return settings


__all__ = ['create_blueprint', 'settings_for_http']
