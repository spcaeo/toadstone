#!/usr/bin/env python3
"""Stands in for the `claude` CLI inside the container.

Toadstone's provider runs `<cli_command> -p <prompt>` and reads stdout. This
accepts exactly that, forwards the prompt to the bridge on the host, and
prints the answer - so nothing in the agent layer has to know that the real
CLI is on the other side of a container boundary.

Failures go to stderr with a non-zero exit, which is what the provider already
expects, and the message says which half is wrong: the bridge not running is a
different problem from the CLI failing.

Installed in the container as /usr/local/bin/claude-bridge. Configured by:

    CLAUDE_BRIDGE_URL    default http://host.docker.internal:14898/ask
    CLAUDE_BRIDGE_TOKEN  the shared secret the bridge printed
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_URL = 'http://host.docker.internal:14898/ask'


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('-p', '--print', dest='prompt', required=True)
    args, _ = parser.parse_known_args()

    url = os.environ.get('CLAUDE_BRIDGE_URL', DEFAULT_URL)
    token = os.environ.get('CLAUDE_BRIDGE_TOKEN', '')
    if not token:
        print('CLAUDE_BRIDGE_TOKEN is not set in this container', file=sys.stderr)
        return 2

    request = urllib.request.Request(
        url, data=json.dumps({'prompt': args.prompt}).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            detail = (json.loads(exc.read().decode('utf-8')) or {}).get('error', '')
        except Exception:
            pass
        if exc.code == 401:
            detail = detail or 'the bridge rejected this container\'s token'
        print(f'bridge returned HTTP {exc.code}: {detail}', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'could not reach the Claude bridge at {url} ({type(exc).__name__}). '
              f'Is tools/claude_bridge.py running on the host?', file=sys.stderr)
        return 1

    text = (payload or {}).get('text') or ''
    if not text:
        print((payload or {}).get('error') or 'the bridge returned nothing',
              file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
