"""How the MCP server is configured, and what it refuses by default.

Everything here has a safe default, because the server is started by an agent
runtime (Claude Code, Cursor) from a config file that nobody re-reads once it
works. The defaults are the ones you would want if you forgot the file existed:

    read-only                 writes need an explicit opt-in
    hosts already crawled     an empty allow-list does not mean "any host"
    2,000 URLs per crawl      a cap the agent cannot raise from the tool call

Every setting can come from the environment (so a stdio config block can set
it without a wrapper script) or from the command line (so a human can override
it for one run). The command line wins.
"""

import os
from dataclasses import dataclass, field

DEFAULT_BASE_URL = 'http://localhost:5000'
DEFAULT_MAX_URLS = 2000
DEFAULT_TIMEOUT = 120
# A tool result is read by a model, so a 40MB CSV helps nobody: the export tool
# truncates at this many characters and says that it did.
DEFAULT_MAX_BYTES = 200_000

ENV_PREFIX = 'TOADSTONE_MCP_'
# The project was called LibreCrawl until 2026-10-19. An agent runtime's config
# file is written once and never looked at again, so the old prefix keeps
# working rather than silently resolving to a default.
LEGACY_ENV_PREFIX = 'LIBRECRAWL_MCP_'


def _flag(value, default=False):
    if value is None or value == '':
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _hosts(value):
    """Parse a comma- or space-separated host list. '*' means no restriction."""
    if not value:
        return []
    parts = [p.strip().lower().rstrip('.') for p in value.replace(',', ' ').split()]
    return [p for p in parts if p]


@dataclass
class Settings:
    """One resolved configuration. Built by `from_env`, never mutated after."""

    base_url: str = DEFAULT_BASE_URL
    cookie: str = ''
    username: str = ''
    password: str = ''
    allow_hosts: list = field(default_factory=list)
    max_urls: int = DEFAULT_MAX_URLS
    allow_writes: bool = False
    timeout: int = DEFAULT_TIMEOUT
    max_bytes: int = DEFAULT_MAX_BYTES

    @property
    def read_only(self):
        return not self.allow_writes

    @property
    def any_host(self):
        """True when the allow-list is the explicit wildcard."""
        return '*' in self.allow_hosts

    def describe(self):
        """The guardrails in force, as a tool result can report them."""
        if self.any_host:
            hosts = 'any host (allow-list is *)'
        elif self.allow_hosts:
            hosts = ', '.join(self.allow_hosts)
        else:
            hosts = 'only hosts already crawled in this install'
        return {
            'mode': 'read-only' if self.read_only else 'read-write',
            'allowed_hosts': hosts,
            'max_urls_per_crawl': self.max_urls,
            'toadstone_url': self.base_url,
        }


def from_env(environ=None, overrides=None):
    """Resolve settings from the environment, then apply CLI overrides.

    `overrides` is a plain dict of the same field names; a None value means
    "not given on the command line", so a flag that was not passed never
    overwrites an environment variable that was.
    """
    env = os.environ if environ is None else environ

    def get(name, default=''):
        value = env.get(ENV_PREFIX + name)
        if value is None:
            value = env.get(LEGACY_ENV_PREFIX + name)
        return default if value is None else value

    settings = Settings(
        base_url=(get('URL') or DEFAULT_BASE_URL).rstrip('/'),
        cookie=get('COOKIE'),
        username=get('USERNAME'),
        password=get('PASSWORD'),
        allow_hosts=_hosts(get('ALLOW_HOSTS')),
        max_urls=_int(get('MAX_URLS'), DEFAULT_MAX_URLS),
        allow_writes=_flag(get('ALLOW_WRITES')),
        timeout=_int(get('TIMEOUT'), DEFAULT_TIMEOUT),
        max_bytes=_int(get('MAX_BYTES'), DEFAULT_MAX_BYTES),
    )

    for key, value in (overrides or {}).items():
        if value is None:
            continue
        if key == 'base_url':
            value = str(value).rstrip('/')
        if key == 'allow_hosts' and isinstance(value, str):
            value = _hosts(value)
        setattr(settings, key, value)

    settings.max_urls = max(1, int(settings.max_urls))
    return settings


def _int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default
