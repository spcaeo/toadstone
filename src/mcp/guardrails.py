"""What the MCP server refuses, and why.

An MCP server is a crawler with its trigger wired to a language model. The
failure that matters is not a wrong answer, it is a thousand requests to
somebody else's site because a model read a URL in a page and decided to look
into it. Three rules stop that, and they are enforced here rather than in each
tool so that no tool can be added without them:

    host allow-list   empty does not mean "anything". It means "hosts this
                      install has already crawled", which is the set a human
                      has already pointed Toadstone at. '*' opts out.
    URL cap           a crawl may not be asked for more URLs than the cap, and
                      a crawl that does not say gets the cap.
    read-only         every write tool refuses unless writes were opted into
                      at startup. Not a tool argument: a model cannot grant
                      itself the permission.

Refusals are returned to the caller as plain sentences that say what to change,
because the caller is a model that will otherwise retry the same call.
"""

from urllib.parse import urlsplit

MAX_DEPTH_CEILING = 100


class GuardrailError(Exception):
    """A refusal. The message is addressed to whoever made the tool call."""

    def __init__(self, message, rule=''):
        super().__init__(message)
        self.message = message
        self.rule = rule

    def as_dict(self):
        return {'refused': True, 'rule': self.rule, 'error': self.message}


class Guardrails:
    """The rules in force for one server, given its settings and its client."""

    def __init__(self, settings, client):
        self.settings = settings
        self.client = client
        self._known_hosts = None

    # -- writes -------------------------------------------------------------

    def check_write(self, tool):
        """Refuse a write tool when the server was started read-only."""
        if self.settings.read_only:
            raise GuardrailError(
                f'{tool} writes, and this MCP server is running read-only. '
                'Restart it with --allow-writes (or TOADSTONE_MCP_ALLOW_WRITES=true) '
                'if that is what you want; there is no tool argument that turns '
                'this off.', 'read_only')

    # -- hosts --------------------------------------------------------------

    def known_hosts(self):
        """Hosts this install has already crawled, read over the API.

        Looked up once per process. A host that becomes known only because of
        a crawl started in this session is therefore still refused until the
        server restarts, which is the conservative direction.
        """
        if self._known_hosts is None:
            hosts = set()
            try:
                body = self.client.get_json('/api/crawls/list', {'limit': 500})
            except Exception:
                body = {}
            for crawl in body.get('crawls') or []:
                for value in (crawl.get('base_domain'), crawl.get('base_url')):
                    host = _host_of(value)
                    if host:
                        hosts.add(host)
            self._known_hosts = hosts
        return self._known_hosts

    def check_host(self, url):
        """Validate a crawl target and return its normalised URL.

        Raises `GuardrailError` for anything that is not an ordinary http(s)
        URL on a permitted host, including the ones that look harmless:
        credentials in the URL (they would be logged), and a scheme such as
        file: or ftp: that the crawler would not handle anyway.
        """
        url = (url or '').strip()
        if not url:
            raise GuardrailError('A url is required.', 'url')

        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https'):
            raise GuardrailError(
                f'Only http and https URLs can be crawled; got {parts.scheme or "no"} '
                f'scheme in {url!r}.', 'url')
        if parts.username or parts.password:
            raise GuardrailError(
                'Credentials in the URL are not accepted. Configure crawl '
                'authentication in Toadstone instead.', 'url')
        host = (parts.hostname or '').lower().rstrip('.')
        if not host:
            raise GuardrailError(f'{url!r} has no host.', 'url')

        if self.settings.any_host:
            return url

        allow = self.settings.allow_hosts
        if allow:
            if not any(_matches(host, entry) for entry in allow):
                raise GuardrailError(
                    f'Host {host!r} is not on this server\'s allow-list '
                    f'({", ".join(allow)}). Add it to TOADSTONE_MCP_ALLOW_HOSTS '
                    'to crawl it.', 'host_allow_list')
            return url

        known = self.known_hosts()
        if not any(_matches(host, entry) for entry in known):
            listed = ', '.join(sorted(known)[:10]) or 'none yet'
            raise GuardrailError(
                f'Host {host!r} has never been crawled in this Toadstone install, '
                'and this MCP server has no host allow-list, so it only permits '
                f'hosts that have (currently: {listed}). Crawl it once from the '
                'Toadstone UI, or set TOADSTONE_MCP_ALLOW_HOSTS.',
                'host_allow_list')
        return url

    # -- size ---------------------------------------------------------------

    def check_max_urls(self, requested):
        """The URL cap to apply. A request above the cap is refused, not clamped.

        Silently clamping would let an agent believe it crawled 50,000 pages
        and report on a 2,000-page sample as if it were the site.
        """
        cap = self.settings.max_urls
        if requested in (None, ''):
            return cap
        try:
            wanted = int(requested)
        except (TypeError, ValueError):
            raise GuardrailError(f'max_urls must be a whole number, got {requested!r}.',
                                 'max_urls')
        if wanted < 1:
            raise GuardrailError('max_urls must be at least 1.', 'max_urls')
        if wanted > cap:
            raise GuardrailError(
                f'max_urls {wanted} exceeds this server\'s cap of {cap}. Ask for '
                f'{cap} or fewer, or raise TOADSTONE_MCP_MAX_URLS.', 'max_urls')
        return wanted

    def check_max_depth(self, requested):
        """A depth, or None. Guards against a nonsense value, not against size."""
        if requested in (None, ''):
            return None
        try:
            depth = int(requested)
        except (TypeError, ValueError):
            raise GuardrailError(f'max_depth must be a whole number, got {requested!r}.',
                                 'max_depth')
        if depth < 0 or depth > MAX_DEPTH_CEILING:
            raise GuardrailError(
                f'max_depth must be between 0 and {MAX_DEPTH_CEILING}, got {depth}.',
                'max_depth')
        return depth


def _matches(host, entry):
    """A host matches an allow-list entry exactly, or as a subdomain of it."""
    entry = (entry or '').lower().lstrip('*.').rstrip('.')
    if not entry:
        return False
    return host == entry or host.endswith('.' + entry)


def _host_of(value):
    """The hostname in a URL, or a bare domain, lowercased."""
    value = (value or '').strip().lower()
    if not value:
        return ''
    if '://' in value:
        return (urlsplit(value).hostname or '').rstrip('.')
    return value.split('/')[0].split(':')[0].rstrip('.')
