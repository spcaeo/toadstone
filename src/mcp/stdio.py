"""The stdio transport: newline-delimited JSON-RPC on stdin and stdout.

This is how Claude Code, Claude Desktop and Cursor start an MCP server, and
the rules are strict in one way that is easy to get wrong: **stdout belongs to
the protocol**. Anything else printed there - a warning, a stray `print`, a
traceback - corrupts the stream and the client drops the connection. So every
diagnostic in this module goes to stderr, and a message that cannot be encoded
is answered with a JSON-RPC error rather than allowed to escape.
"""

import sys

from src.mcp.server import build


def serve(settings, client, stdin=None, stdout=None, stderr=None):
    """Read frames until stdin closes. Returns the number of frames handled."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    server = build(settings, client)
    guards = settings.describe()
    print(f'toadstone MCP server on stdio: {guards["mode"]}, '
          f'hosts: {guards["allowed_hosts"]}, max {guards["max_urls_per_crawl"]} '
          f'URLs/crawl, Toadstone at {guards["toadstone_url"]}', file=stderr,
          flush=True)

    handled = 0
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        handled += 1
        try:
            reply = server.handle_text(line)
        except Exception as error:  # the loop outlives any single bad frame
            print(f'MCP frame failed: {type(error).__name__}: {error}', file=stderr,
                  flush=True)
            continue
        if reply is None:
            continue
        stdout.write(reply + '\n')
        stdout.flush()
    return handled
