"""MCP server for Toadstone: the HTTP API, as tools an agent can call.

Toadstone already answers every question this server answers - 56 HTTP routes,
twelve export datasets, stable issue ids, a check reference. What it did not
have was a way for a coding agent to ask. This package is that way, and it is
deliberately thin: no tool here knows anything the API does not already tell
it, and none of them reach past the API into the database.

    config.py      the guardrails and where they come from
    guardrails.py  what gets refused: host allow-list, URL cap, read-only
    client.py      two ways to reach the API (network, or in-process Flask)
    tools.py       the ten tools and the shapes they hand back
    server.py      JSON-RPC 2.0 and the MCP methods, transport-agnostic
    stdio.py       the stdio transport (Claude Code, Cursor)
    http_app.py    the HTTP transport, as a blueprint main.py can mount
    __main__.py    `python -m src.mcp`

See docs/mcp.md for the config blocks to paste into a client.
"""

from src.mcp.config import Settings, from_env

__all__ = ['Settings', 'from_env', 'build_server', 'create_blueprint']


def build_server(settings, client):
    """A `Server` for these settings over this client. Imported lazily."""
    from src.mcp.server import build
    return build(settings, client)


def create_blueprint(login_required, settings=None):
    """main.py's hook: the HTTP transport as a Flask blueprint."""
    from src.mcp.http_app import create_blueprint as _create
    return _create(login_required, settings)
