"""What a multilingual site's hreflang says about its own structure.

A site that serves one page in several locales declares the versions with
<link rel="alternate" hreflang>. Read across the whole crawl, those
declarations answer two questions the other checks need:

ALTERNATE GROUPS
    Which URLs are one logical page. Two crawled pages are in the same group
    when each lists the other as an alternate (any code, x-default included);
    the relation is transitive, so en-us <-> en-ca <-> fr-ca is one group even
    if en-us never names fr-ca. A one-way declaration groups nothing: it is a
    defect hreflang_no_return_link reports, and trusting it here would let a
    stray tag hide a real duplicate.

    Members of one group are translations or regional copies of each other.
    They are expected to share a template, often a title and H1, sometimes
    the whole body, so duplicate and anchor checks must not compare them.

LOCALE PREFIXES
    Which leading path segments are a locale rather than a section. For each
    hreflang code (x-default aside) take the paths of every URL declared
    under it; their common leading segments are the code's prefix, provided
    the code covers at least two URLs and the prefix itself - the locale
    home - is one of them. So '/ca/' (en-ca) and '/ca/fr/' (fr-ca) are
    prefixes, and a code served from the site root ('/', '/pricing/') has
    none. A site without hreflang has no prefixes and every check behaves as
    it always did.

    build(pages) -> Locales
    Locales.same_page(url_a, url_b)   both URLs in one alternate group
    Locales.logical(url)              the group id, or the URL's own key
    Locales.split(path)               (locale prefix, rest of the path)
    Locales.is_locale_home(url)       the path is exactly a locale prefix

Everything is one pass over the pages and their declarations: O(P + H) for
P pages and H hreflang entries.
"""

from collections import defaultdict
from urllib.parse import urlsplit

from src.utils.url_params import DEFAULT_TRACKING_PARAMS, strip_tracking_params

X_DEFAULT = 'x-default'


def url_key(url):
    """Scheme, fragment, 'www.', one trailing slash and tracking parameters
    ignored; host lowercased; meaningful query kept. O(len(url))."""
    if not url:
        return ''
    parts = urlsplit(strip_tracking_params(str(url).strip(), DEFAULT_TRACKING_PARAMS))
    host = (parts.hostname or '').lower()
    if host.startswith('www.'):
        host = host[4:]
    path = parts.path or '/'
    if len(path) > 1:
        path = path.rstrip('/') or '/'
    return host + path + ('?' + parts.query if parts.query else '')


def _segments(path):
    return tuple(part for part in (path or '').split('/') if part)


def hreflang_entries(page):
    """A page's hreflang declarations as [(code, url), ...].

    Accepts the crawler's list of {lang, url} dicts (or hreflang/href and
    code keys) and a plain {code: url} mapping.
    """
    raw = page.get('hreflang')
    entries = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                code = (item.get('hreflang') or item.get('lang') or item.get('code') or '').strip()
                target = (item.get('href') or item.get('url') or '').strip()
                if code and target:
                    entries.append((code, target))
    elif isinstance(raw, dict):
        for code, target in raw.items():
            if isinstance(code, str) and isinstance(target, str):
                entries.append((code.strip(), target.strip()))
    return entries


class Locales:
    """Alternate groups and locale prefixes of one crawl. See the module
    docstring; build one with `build(pages)`."""

    def __init__(self, groups=None, prefixes=()):
        self._groups = groups or {}                # url key -> group id
        # Longest first, so '/ca/fr/' is tried before '/ca/'.
        self.prefixes = tuple(sorted(set(prefixes), key=len, reverse=True))

    def group_of(self, url):
        return self._groups.get(url_key(url))

    def logical(self, url):
        """One id per logical page: the group, or the URL itself."""
        key = url_key(url)
        return self._groups.get(key, key)

    def same_page(self, url_a, url_b):
        group = self.group_of(url_a)
        return group is not None and group == self.group_of(url_b)

    def split(self, path):
        """(prefix, rest) where prefix is the locale prefix the path starts
        with ('' for none) and rest its remaining segments."""
        segments = _segments(path)
        for prefix in self.prefixes:
            if segments[:len(prefix)] == prefix:
                return '/' + '/'.join(prefix) + '/', segments[len(prefix):]
        return '', segments

    def is_locale_home(self, url):
        segments = _segments(urlsplit(str(url or '')).path)
        return bool(segments) and segments in self.prefixes


def build(pages):
    """Read every page's hreflang into a Locales. O(P + H)."""
    declared = {}                                  # url key -> {target key}
    code_paths = defaultdict(set)                  # code -> {path segments}
    for page in pages or ():
        if not isinstance(page, dict) or not page.get('url'):
            continue
        entries = hreflang_entries(page)
        if not entries:
            continue
        source = url_key(page['url'])
        targets = declared.setdefault(source, set())
        for code, target in entries:
            targets.add(url_key(target))
            if code.lower() != X_DEFAULT:
                code_paths[code.lower()].add(_segments(urlsplit(target).path))

    return Locales(_groups(declared), _prefixes(code_paths))


def _groups(declared):
    """Union of every mutual declaration between crawled pages."""
    parent = {}

    def find(key):
        root = key
        while parent.get(root, root) != root:
            root = parent[root]
        while key != root:
            parent[key], key = root, parent.get(key, key)
        return root

    for source, targets in declared.items():
        for target in targets:
            if target != source and source in declared.get(target, ()):
                parent.setdefault(source, source)
                parent.setdefault(target, target)
                a, b = find(source), find(target)
                if a != b:
                    # The smaller key is the root, so a group's id does not
                    # depend on the order pages were crawled in.
                    parent[max(a, b)] = min(a, b)

    return {key: find(key) for key in parent}


def _prefixes(code_paths):
    """The locale prefix of every code that has one."""
    found = []
    for paths in code_paths.values():
        if len(paths) < 2:
            continue
        ordered = list(paths)
        common = ordered[0]
        for path in ordered[1:]:
            size = 0
            while size < min(len(common), len(path)) and common[size] == path[size]:
                size += 1
            common = common[:size]
            if not common:
                break
        if common and common in paths:
            found.append(common)
    return found
