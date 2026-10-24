"""What a response status means for a link or an image.

This exists because two checks disagreed with reality in the same way. Crawling
ten well-known SEO sites produced "301 pages link to broken URLs" on Moz, and
the URLs were not broken: they were behind a bot wall returning 403. An image
CDN doing the same produced broken-image findings for images that load
perfectly in a browser.

The distinction the code was missing:

    broken      the resource is not there, for anybody.      404 410 5xx
    blocked     it is there; *we* were refused.              401 403 407 429
    unknown     we never got an answer at all.               0

A crawler is an unusual client - no cookies, no session, a declared bot user
agent, often from a datacentre IP. Being refused is a fact about the crawler,
not about the site, and reporting it as a defect is the fastest way to lose the
trust of somebody who knows their own site is fine.

So blocked targets are reported separately, at info severity, saying what they
are: unverified. Nobody has to guess which of their "broken links" are real.
"""

# Not there for anyone. 404/410 are definitive; 5xx is the server failing.
BROKEN_CODES = frozenset({404, 410})

# There, but refused to us. Each is about the requester, not the resource.
#   401 needs credentials        403 forbidden / bot wall
#   407 proxy auth               429 rate limited (often a bot wall in disguise)
BLOCKED_CODES = frozenset({401, 403, 407, 429})

BLOCKED_REASON = {
    401: 'needs credentials',
    403: 'refused the crawler',
    407: 'needs proxy authentication',
    429: 'rate-limited the crawler',
}


def is_broken(status):
    """True when the resource is absent for everyone, not just for us."""
    if not isinstance(status, int):
        return False
    return status in BROKEN_CODES or 500 <= status <= 599


def is_blocked(status):
    """True when the resource exists and the crawler specifically was refused."""
    return isinstance(status, int) and status in BLOCKED_CODES


def is_no_response(status):
    """True when nothing answered at all - DNS, TLS, refused connection."""
    return status == 0


def describe_blocked(status):
    """A phrase for a report, e.g. 'refused the crawler (403)'."""
    return f'{BLOCKED_REASON.get(status, "refused the crawler")} ({status})'


def classify(status):
    """'broken' | 'blocked' | 'no_response' | 'ok' - one word for one status."""
    if is_no_response(status):
        return 'no_response'
    if is_blocked(status):
        return 'blocked'
    if is_broken(status):
        return 'broken'
    return 'ok'
