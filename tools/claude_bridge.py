#!/usr/bin/env python3
"""Run the Claude CLI on this machine, for a Toadstone container that cannot.

Toadstone's agent layer shells out to a `claude` command. That works when
Toadstone runs natively. It cannot work in the Docker image: the CLI ships as
a native binary for the host OS, so a macOS install cannot be bind-mounted into
a Linux container whatever you do to the path.

The alternative - installing the Linux build inside the container - means
putting a long-lived credential in the image and its volume. This instead
keeps the CLI and its credential where they already are, on the host and in
the keychain, and lets the container ask for an answer.

    container                          host
    ---------                          ----
    agents -> claude-bridge  --HTTP->  this  --subprocess->  claude -p ...
              (wrapper)                                      (keychain auth)

What it will and will not do, because it exists to run a program on behalf of
something else and that deserves to be narrow:

  * It binds **127.0.0.1 only**. Docker Desktop reaches a loopback service
    through `host.docker.internal`, verified on this machine, so nothing needs
    to listen on the network.
  * It runs exactly one command - the configured CLI with `-p` and a prompt.
    There is no shell, no argument passthrough and no second endpoint. A
    caller controls the prompt text and nothing else.
  * Every request carries a shared secret, compared in constant time. The
    token is generated on first run into a 0600 file.
  * One call at a time, a size cap on the prompt and a timeout on the child,
    so a runaway crawl cannot fork a hundred CLIs.

Usage:

    python tools/claude_bridge.py                 # prints the token to use
    python tools/claude_bridge.py --port 14898

Then give the container that token as CLAUDE_BRIDGE_TOKEN and point
`agentCliCommand` at the wrapper, `tools/claude_bridge_client.py`.
"""

import argparse
import hmac
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 14898
MAX_PROMPT_BYTES = 200_000
DEFAULT_TIMEOUT = 180

TOKEN_PATH = os.path.join(os.path.expanduser('~'), '.toadstone', 'claude-bridge.token')

# One call at a time. The CLI is slow and heavy, and a crawl that found forty
# pages worth judging would otherwise start forty of them at once.
_one_at_a_time = threading.Lock()


def load_or_create_token(path=TOKEN_PATH):
    """The shared secret, created on first run with owner-only permissions."""
    existing = os.environ.get('CLAUDE_BRIDGE_TOKEN', '').strip()
    if existing:
        return existing
    try:
        with open(path, encoding='utf-8') as handle:
            token = handle.read().strip()
        if token:
            return token
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write(token)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return token


def ask_claude(prompt, command, timeout):
    """One question to the CLI. Returns (ok, text)."""
    with _one_at_a_time:
        try:
            finished = subprocess.run(
                [command, '-p', prompt],
                capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError:
            return False, f'{command} is not installed on the host'
        except subprocess.TimeoutExpired:
            return False, f'{command} did not answer within {timeout}s'
        except Exception as exc:
            return False, f'could not run {command}: {type(exc).__name__}'
    if finished.returncode != 0:
        return False, (finished.stderr or '').strip()[:400] or f'exit {finished.returncode}'
    text = (finished.stdout or '').strip()
    if not text:
        return False, f'{command} returned nothing'
    return True, text


def make_handler(token, command, timeout):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'ToadstoneClaudeBridge'

        def _send(self, status, payload):
            body = json.dumps(payload).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorised(self):
            offered = (self.headers.get('Authorization') or '')
            if offered.startswith('Bearer '):
                offered = offered[len('Bearer '):]
            return hmac.compare_digest(offered.strip(), token)

        def do_GET(self):
            # Liveness only. Says nothing a caller does not already know.
            if self.path == '/health':
                self._send(200, {'ok': True})
            else:
                self._send(404, {'error': 'not found'})

        def do_POST(self):
            if self.path != '/ask':
                self._send(404, {'error': 'not found'})
                return
            if not self._authorised():
                self._send(401, {'error': 'bad or missing token'})
                return
            try:
                length = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_PROMPT_BYTES:
                self._send(413, {'error': f'prompt must be 1..{MAX_PROMPT_BYTES} bytes'})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode('utf-8'))
            except Exception:
                self._send(400, {'error': 'body must be JSON'})
                return
            prompt = (payload or {}).get('prompt') or ''
            if not isinstance(prompt, str) or not prompt.strip():
                self._send(400, {'error': 'prompt is required'})
                return
            ok, text = ask_claude(prompt, command, timeout)
            self._send(200 if ok else 502,
                       {'text': text} if ok else {'error': text})

        def log_message(self, fmt, *args):
            # One line per call, without the prompt: it is someone's page text.
            sys.stderr.write(f'  {self.command} {self.path} -> {args[1] if len(args) > 1 else ""}\n')

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--command', default='claude',
                        help='the CLI to run (default: claude)')
    parser.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT)
    args = parser.parse_args()

    resolved = shutil.which(args.command)
    if not resolved:
        print(f'{args.command} is not on PATH. Install Claude Code first.',
              file=sys.stderr)
        return 1

    token = load_or_create_token()
    server = ThreadingHTTPServer(('127.0.0.1', args.port),
                                 make_handler(token, args.command, args.timeout))
    print(f'Claude bridge on 127.0.0.1:{args.port} -> {resolved}')
    print(f'Token file: {TOKEN_PATH}')
    print('Give the container:')
    print(f'  CLAUDE_BRIDGE_URL=http://host.docker.internal:{args.port}/ask')
    print(f'  CLAUDE_BRIDGE_TOKEN={token}')
    print('Ctrl-C to stop.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nstopped')
    return 0


if __name__ == '__main__':
    sys.exit(main())
