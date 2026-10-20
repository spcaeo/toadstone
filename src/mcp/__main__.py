"""Run the Toadstone MCP server from the command line.

    python -m src.mcp                        stdio, read-only, default guardrails
    python -m src.mcp --allow-writes         let an agent start crawls
    python -m src.mcp --allow-host site.com  and crawl that host
    python -m src.mcp --print-config         show what the guardrails would be

The flags mirror the TOADSTONE_MCP_* environment variables and win over them,
so an agent runtime can configure the server through its `env` block and a
human can still override one setting for a single run.
"""

import argparse
import json
import os
import sys

# Started by an agent runtime, the working directory is whatever that runtime
# felt like; the repository root has to be importable either way.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog='python -m src.mcp',
        description='MCP server for Toadstone (stdio transport).')
    parser.add_argument('--url', dest='base_url',
                        help='Toadstone base URL (default http://localhost:5000).')
    parser.add_argument('--allow-host', dest='allow_hosts', action='append',
                        metavar='HOST',
                        help='Permit crawls of this host and its subdomains. '
                             'Repeatable. Use * to permit any host. Without any, '
                             'only hosts already crawled in this install are '
                             'allowed.')
    parser.add_argument('--max-urls', type=int,
                        help='Cap on URLs per crawl (default 2000).')
    parser.add_argument('--allow-writes', action='store_true', default=None,
                        help='Permit start_crawl and accept_issue. Off by default.')
    parser.add_argument('--read-only', action='store_true',
                        help='Force read-only, overriding the environment.')
    parser.add_argument('--username', help='Toadstone account to log in as.')
    parser.add_argument('--password', help='Its password.')
    parser.add_argument('--timeout', type=int, help='HTTP timeout in seconds.')
    parser.add_argument('--print-config', action='store_true',
                        help='Print the resolved guardrails and exit.')
    return parser.parse_args(argv)


def settings_from(args):
    from src.mcp import config

    overrides = {'base_url': args.base_url, 'allow_hosts': args.allow_hosts,
                 'max_urls': args.max_urls, 'allow_writes': args.allow_writes,
                 'username': args.username, 'password': args.password,
                 'timeout': args.timeout}
    if args.read_only:
        overrides['allow_writes'] = False
    return config.from_env(overrides=overrides)


def main(argv=None):
    args = parse_args(argv)
    settings = settings_from(args)

    if args.print_config:
        print(json.dumps(settings.describe(), indent=2))
        return 0

    from src.mcp.client import HttpClient
    from src.mcp.stdio import serve

    serve(settings, HttpClient(settings))
    return 0


if __name__ == '__main__':
    sys.exit(main())
