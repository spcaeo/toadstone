"""Tracking-parameter handling for URLs.

Campaign parameters and click ids never change the response body, but a link
that carries them (/contact-us/?utm_source=SidebarCTA) would otherwise be
crawled as a page of its own and reported as a duplicate of the clean URL.
"""

from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

# Exact names and prefixes (trailing '*') stripped by default
DEFAULT_TRACKING_PARAMS = [
    # campaign parameters and click ids
    'utm_*', 'gclid', 'gbraid', 'wbraid', 'fbclid', 'msclkid', 'mc_cid',
    'mc_eid', '_ga', '_gl', 'ref', 'igshid', 'yclid', 'dclid', 'ttclid',
    'li_fat_id', 's_kwcid', 'vero_id', 'hsa_*',
    # session ids: the same page, a different visitor
    'phpsessid', 'jsessionid', 'aspsessionid*', 'sessionid', 'sid', 'sessid',
    'cfid', 'cftoken', 'zenid', 'osCsid',
]


def is_tracking_param(name, patterns=DEFAULT_TRACKING_PARAMS):
    """True if a query parameter name matches one of `patterns`.

    A pattern ending in '*' matches by prefix ('utm_*' -> utm_source); every
    other pattern matches the whole name. Matching ignores case.
    """
    lowered = name.lower()
    for pattern in patterns:
        pattern = pattern.strip().lower()
        if not pattern:
            continue
        if pattern.endswith('*'):
            if lowered.startswith(pattern[:-1]):
                return True
        elif lowered == pattern:
            return True
    return False


def strip_tracking_params(url, patterns=DEFAULT_TRACKING_PARAMS):
    """Return `url` without its tracking parameters.

    Parameters that carry meaning (?page=2, ?id=7) are kept, in their original
    order. A URL whose only parameters were tracking ones loses the '?' too.
    """
    if not url or '?' not in url:
        return url

    parsed = urlparse(url)
    if not parsed.query:
        return url

    kept = [(name, value) for name, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not is_tracking_param(name, patterns)]
    if len(kept) == len(parse_qsl(parsed.query, keep_blank_values=True)):
        return url

    return urlunparse(parsed._replace(query=urlencode(kept)))


def tracking_params_in(url, patterns=DEFAULT_TRACKING_PARAMS):
    """The tracking parameter names present in `url`, in order."""
    if not url or '?' not in url:
        return []
    query = urlparse(url).query
    return [name for name, _ in parse_qsl(query, keep_blank_values=True)
            if is_tracking_param(name, patterns)]
