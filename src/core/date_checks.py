"""Dates and freshness checks for one crawled page.

A page states when it was written and when it last changed in up to five
places, and a CMS keeps at most two of them honest:

    JSON-LD datePublished / dateModified
    <meta property="article:published_time" / "article:modified_time">
    the HTTP Last-Modified header
    the sitemap's <lastmod>
    the visible "Updated on ..." line in the copy

This module reads all five off an already-crawled page dict, parses whatever
format each one happens to use, and reports the ways they go wrong: missing,
unparseable, timezone-less, contradicting each other, in the future, modified
before published, or simply old.

Nothing here fetches anything. Sitemap <lastmod> values are passed in as a
plain ``{url: lastmod_string}`` dict so sitemap auditing stays in its own
module; an empty dict (the default) just means "no sitemap in this crawl".

Two deliberate boundaries with that sitemap module:

  * ``invalid_date_format`` and ``future_date`` only look at dates the page
    itself serves. A malformed or future <lastmod> belongs to the sitemap
    audit, so reporting it here too would double-report one defect.
  * ``sitemap_lastmod`` still takes part in the mismatch comparison (that
    comparison is the whole point of the check) and still gates stale_content.

``now`` is injectable on every entry point so tests do not drift.
"""

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from src.utils.issue_factory import make_issue

CATEGORY = 'Freshness'

# A JSON-LD node counts as "article-ish" - i.e. a node that is expected to
# carry dates - when its @type is one of these, or ends in "Article"/"Page"
# (TechArticle, MedicalWebPage, ... all inherit the same date properties).
ARTICLE_TYPES = frozenset({
    'article', 'blogposting', 'newsarticle', 'report', 'liveblogposting',
    'socialmediaposting', 'webpage', 'blog',
})

# Sources compared against each other by modified_date_mismatch, in the order
# they are listed in the issue details.
MODIFIED_SOURCES = (
    'jsonld_modified',
    'meta_modified',
    'http_last_modified',
    'sitemap_lastmod',
    'visible_updated',
)

# Sources the page itself serves: only these are format-checked and
# future-checked (see the module docstring for why the sitemap is excluded).
PAGE_DATE_SOURCES = (
    'jsonld_published',
    'jsonld_modified',
    'meta_published',
    'meta_modified',
    'http_last_modified',
)

# ISO-8601 and friends want a timezone; a visible date in the copy never has
# one and an HTTP date always does, so only these four are held to it.
TIMEZONE_REQUIRED = (
    'jsonld_published',
    'jsonld_modified',
    'meta_published',
    'meta_modified',
)

SOURCE_LABELS = {
    'jsonld_published': 'JSON-LD datePublished',
    'jsonld_modified': 'JSON-LD dateModified',
    'meta_published': 'meta article:published_time',
    'meta_modified': 'meta article:modified_time',
    'http_last_modified': 'HTTP Last-Modified',
    'sitemap_lastmod': 'sitemap lastmod',
    'visible_updated': 'visible "updated" text',
}

# A clock a few minutes fast is not a content defect.
FUTURE_SKEW = timedelta(minutes=5)

# Visible-date formats, tried in order. Every one of them is timezone-less.
TEXT_DATE_FORMATS = (
    '%Y-%m-%d',
    '%Y/%m/%d',
    '%B %d %Y',
    '%b %d %Y',
    '%d %B %Y',
    '%d %b %Y',
    '%m/%d/%Y',
)

# The date-shaped part of a visible "Updated on ..." line.
_TEXT_DATE = (
    r'(?:'
    r'[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}'      # January 5, 2026
    r'|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3,9}\.?,?\s+\d{4}'     # 5 Jan 2026
    r'|\d{4}[-/]\d{1,2}[-/]\d{1,2}'                              # 2026-01-05
    r'|\d{1,2}/\d{1,2}/\d{4}'                                    # 01/05/2026
    r')'
)

# "Updated" wins over "Published" when a page carries both (see
# _visible_date): a published line is only a fallback signal of freshness.
_VISIBLE_UPDATED = re.compile(
    r'(?:last\s+updated|updated\s+on|last\s+modified|updated)\s*'
    r'(?::|-|–|—)?\s*(' + _TEXT_DATE + r')',
    re.IGNORECASE,
)
_VISIBLE_PUBLISHED = re.compile(
    r'(?:published\s+on|first\s+published|published)\s*'
    r'(?::|-|–|—)?\s*(' + _TEXT_DATE + r')',
    re.IGNORECASE,
)

# A trailing timezone, for telling "2026-01-05T10:00:00" (naive, a defect on a
# JSON-LD date) from "2026-01-05T10:00:00Z".
_HAS_TZ = re.compile(r'(?:Z|GMT|UTC|UT|[A-Z]{3}|[+-]\d{2}:?\d{2})\s*$')

_ORDINAL = re.compile(r'(\d{1,2})(?:st|nd|rd|th)\b', re.IGNORECASE)


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------

def parse_date(value):
    """Parse any date Toadstone meets into an aware datetime, or None.

    Accepts ISO-8601 (with or without a timezone), RFC 2822/1123 HTTP dates
    and the common visible formats ("January 5, 2026", "5 Jan 2026",
    "2026-01-05"). A value with no timezone is read as UTC - use
    ``parse_date_detailed`` when you need to know that it had none. Never
    raises: anything unrecognisable comes back as None.
    """
    return parse_date_detailed(value)[0]


def parse_date_detailed(value):
    """``(datetime|None, had_timezone)`` - parse_date plus the naive flag."""
    if isinstance(value, datetime):
        return (_as_aware(value), value.tzinfo is not None)
    if not isinstance(value, str):
        return (None, False)

    raw = value.strip()
    if not raw:
        return (None, False)

    had_tz = bool(_HAS_TZ.search(raw))
    parsed = _parse_iso(raw) or _parse_rfc2822(raw) or _parse_text(raw)
    if parsed is None:
        return (None, False)
    return (_as_aware(parsed), had_tz or parsed.tzinfo is not None)


def _as_aware(value):
    """A naive datetime means UTC; an aware one is left alone."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _parse_iso(raw):
    candidate = raw
    if candidate.endswith(('Z', 'z')):
        candidate = candidate[:-1] + '+00:00'
    # "2026-01-05T10:00:00+0000" - fromisoformat wants the colon before 3.11.
    candidate = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', candidate)
    try:
        return datetime.fromisoformat(candidate)
    except (ValueError, TypeError):
        return None


def _parse_rfc2822(raw):
    if ',' not in raw and not re.match(r'^[A-Za-z]{3}\s', raw):
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _parse_text(raw):
    candidate = _ORDINAL.sub(r'\1', raw).replace(',', ' ').replace('.', ' ')
    candidate = ' '.join(candidate.split())
    for fmt in TEXT_DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# reading the dates off a page
# --------------------------------------------------------------------------

def extract_page_dates(page, sitemap_lastmod=None):
    """Every date this page states, keyed by source.

    Returns a dict with a key per source (see SOURCE_LABELS); each value is
    ``{'raw': str, 'parsed': datetime|None, 'source': str}`` or None when the
    page does not carry that source at all.
    """
    page = page or {}
    meta = _lower_keys(page.get('meta_tags'))
    og = _lower_keys(page.get('og_tags'))
    node = article_node(page)

    dates = {
        'jsonld_published': _entry(_jsonld_value(node, 'datePublished'), 'jsonld_published'),
        'jsonld_modified': _entry(_jsonld_value(node, 'dateModified'), 'jsonld_modified'),
        'meta_published': _entry(
            _first(meta.get('article:published_time'), og.get('article:published_time'),
                   og.get('published_time')),
            'meta_published'),
        'meta_modified': _entry(
            _first(meta.get('article:modified_time'), og.get('article:modified_time'),
                   og.get('modified_time'), og.get('updated_time')),
            'meta_modified'),
        'http_last_modified': _entry(page.get('last_modified'), 'http_last_modified'),
        'sitemap_lastmod': _entry(_sitemap_value(page.get('url'), sitemap_lastmod),
                                  'sitemap_lastmod'),
        'visible_updated': _entry(_visible_date(page.get('main_text')), 'visible_updated'),
    }
    return dates


def article_node(page):
    """The JSON-LD node on the page that is expected to carry dates.

    "Expected to carry dates" means an @type of Article/BlogPosting/WebPage,
    or anything ending in "Article" (TechArticle, NewsArticle), found anywhere
    in the block - including inside a list or an @graph. Returns None when the
    page has no such node, which keeps missing_dates off pages that never
    claimed to be articles.

    Types that merely end in "Page" (FAQPage, ContactPage, CollectionPage) do
    NOT qualify: they describe what a page contains, not when it was written,
    and a site that marks its FAQ block is not claiming an article. A page
    whose JSON-LD holds several qualifying nodes reports the most specific
    one, so the fix hint names the Article rather than the generic WebPage.
    """
    fallback = None
    for node in _walk_jsonld(page.get('json_ld') or []):
        if not _is_article_type(node.get('@type')):
            continue
        if _is_generic_page(node.get('@type')):
            fallback = fallback or node
        else:
            return node
    return fallback


def _is_generic_page(value):
    """True when every @type is the bare WebPage (no more specific type)."""
    values = value if isinstance(value, list) else [value]
    names = [_type_slug(item) for item in values if isinstance(item, str)]
    return bool(names) and all(name == 'webpage' for name in names)


def _walk_jsonld(blocks, depth=0):
    """Yield every dict in the JSON-LD, flattening lists and @graph."""
    if depth > 6:
        return
    if isinstance(blocks, dict):
        yield blocks
        for key in ('@graph', 'mainEntity', 'mainEntityOfPage', 'hasPart'):
            nested = blocks.get(key)
            if isinstance(nested, (dict, list)):
                for node in _walk_jsonld(nested, depth + 1):
                    yield node
        return
    if isinstance(blocks, list):
        for item in blocks:
            for node in _walk_jsonld(item, depth + 1):
                yield node


def _is_article_type(value):
    values = value if isinstance(value, list) else [value]
    for item in values:
        if not isinstance(item, str):
            continue
        name = _type_slug(item)
        if name in ARTICLE_TYPES or name.endswith('article'):
            return True
    return False


def _type_slug(value):
    """'https://schema.org/BlogPosting' -> 'blogposting'"""
    return value.rsplit('/', 1)[-1].rsplit(':', 1)[-1].strip().lower()


def _jsonld_value(node, key):
    if not node:
        return None
    value = node.get(key)
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):  # {"@value": "2026-01-05"}
        value = value.get('@value')
    return value if isinstance(value, str) else None


def _sitemap_value(url, sitemap_lastmod):
    if not url or not sitemap_lastmod:
        return None
    for candidate in _url_variants(url):
        if candidate in sitemap_lastmod:
            return sitemap_lastmod[candidate]
    return None


def _url_variants(url):
    """The page URL as the sitemap might have spelled it."""
    yield url
    if url.endswith('/'):
        yield url.rstrip('/')
    else:
        yield url + '/'


def _visible_date(main_text):
    """The date from an "Updated on ..." line, else from a "Published ..." one.

    Updated wins because that is what the mismatch check compares against;
    a published line is only consulted when the page shows no updated line.
    """
    if not isinstance(main_text, str) or not main_text:
        return None
    for pattern in (_VISIBLE_UPDATED, _VISIBLE_PUBLISHED):
        for match in pattern.finditer(main_text):
            candidate = match.group(1).strip()
            if parse_date(candidate) is not None:
                return candidate
    return None


def _entry(raw, source):
    if raw is None:
        return None
    raw = raw.strip() if isinstance(raw, str) else raw
    if raw == '' or raw is None:
        return None
    return {'raw': raw, 'parsed': parse_date(raw), 'source': source}


def _first(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return None


def _lower_keys(mapping):
    if not isinstance(mapping, dict):
        return {}
    return {str(k).lower(): v for k, v in mapping.items()}


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def date_issues(page, sitemap_lastmod=None, now=None,
                stale_after_months=12, tolerance_hours=24):
    """Every dates-and-freshness issue on one crawled page.

    ``sitemap_lastmod`` is a ``{url: lastmod_string}`` dict supplied by the
    caller (may be empty). ``now`` is injectable so tests are deterministic.
    """
    page = page or {}
    url = page.get('url', '')
    now = _as_aware(now) if now else datetime.now(timezone.utc)
    dates = extract_page_dates(page, sitemap_lastmod)

    issues = []
    issues.extend(_missing_dates(url, page, dates))
    issues.extend(_invalid_formats(url, dates))
    issues.extend(_mismatch(url, dates, tolerance_hours))
    issues.extend(_stale(url, page, dates, now, stale_after_months, sitemap_lastmod))
    issues.extend(_future(url, dates, now))
    issues.extend(_modified_before_published(url, dates))
    issues.extend(_clock_read_dates(url, dates))
    return issues


def date_issues_for_crawl(pages, sitemap_lastmod=None, base_url=None, **options):
    """date_issues over many pages, plus the checks that need the whole crawl."""
    pages = pages or []
    issues = []
    for page in pages:
        issues.extend(date_issues(page, sitemap_lastmod, **options))
    issues.extend(_dates_from_one_machine_run(pages, base_url))
    return issues


# ---------------------------------------------------------------------------
# plausibility
#
# Everything above asks whether a date is present and well-formed. These two
# ask whether its value could be true. A field can be populated, correctly
# formatted, timezone-qualified and in the right order while carrying no
# information at all - and a crawler that only tests presence passes it.
#
# Both come from a real site whose 43 guide pages each claimed to have been
# published at a distinct millisecond inside one 7.7-second window, which was
# the moment a seeder ran.
# ---------------------------------------------------------------------------

# Two timestamps closer together than this, but not identical, were produced
# by two separate reads of the same clock rather than by two events.
CLOCK_READ_SECONDS = 5

# How close together published dates must fall, and how many pages must share
# the window, before the crawl looks like one machine run.
RUN_WINDOW_SECONDS = 60
RUN_MIN_PAGES = 5


def _clock_read_dates(url, dates):
    """Published and modified a few milliseconds apart: one render, two clocks.

    A page published and never edited since legitimately carries the same
    value in both fields, and that value is byte-identical because it was
    stored once and rendered twice. Two timestamps a few milliseconds apart
    cannot be that: they are two ``new Date()`` calls in a single render, and
    the page is reporting the moment it was built as the moment it was
    written.

    So near-equal is the signal and exactly-equal is not. Getting that the
    wrong way round would fail every honest unedited page.
    """
    published = _effective(dates, 'jsonld_published', 'meta_published')
    modified = _effective(dates, 'jsonld_modified', 'meta_modified')
    if not published or not modified:
        return []
    if published['parsed'] is None or modified['parsed'] is None:
        return []
    if str(published['raw']) == str(modified['raw']):
        return []
    gap = abs((modified['parsed'] - published['parsed']).total_seconds())
    if gap >= CLOCK_READ_SECONDS:
        return []

    return [make_issue(
        url=url,
        check='dates_read_at_render',
        severity='warning',
        category=CATEGORY,
        issue='Dates Taken From the Clock, Not the Content',
        details=(f'datePublished ({published["raw"]}) and dateModified '
                 f'({modified["raw"]}) are {gap:.3f}s apart, which is two reads '
                 f'of the same clock rather than two events.'),
        offending_value=f'{published["raw"]}~{modified["raw"]}',
        evidence={'found': f'{gap:.3f}s apart', 'expected': 'dates describing real events'},
        how_to_fix=('Take the published date from the content\'s own metadata and '
                    'the modified date from its last real edit, rather than '
                    'calling the clock while the page renders.'),
    )]


def _dates_from_one_machine_run(pages, base_url=None):
    """Many pages claiming to be published inside the same few seconds.

    A publication is something a person does; a build or a seeder is a loop.
    Dozens of distinct timestamps a few seconds apart are the loop, frozen
    into the content.

    A genuine simultaneous launch is a real thing and must not be reported,
    so the discriminator is not clustering alone: a batch published together
    shares one timestamp, because one event happened. A loop produces values
    that are close but all different, one per iteration. Distinct-but-adjacent
    is the signal.
    """
    stamps = []
    for page in pages:
        entry = _effective(extract_page_dates(page), 'jsonld_published', 'meta_published')
        if entry and entry['parsed'] is not None:
            stamps.append((entry['parsed'], entry['raw'], page.get('url', '')))
    if len(stamps) < RUN_MIN_PAGES:
        return []
    stamps.sort(key=lambda item: item[0])

    # Widest group of pages whose dates all fall inside one window.
    best, start = [], 0
    for end in range(len(stamps)):
        while (stamps[end][0] - stamps[start][0]).total_seconds() > RUN_WINDOW_SECONDS:
            start += 1
        if end - start + 1 > len(best):
            best = stamps[start:end + 1]

    if len(best) < RUN_MIN_PAGES:
        return []
    distinct = {raw for _, raw, _ in best}
    if len(distinct) < 3:
        return []  # one shared timestamp is a batch publish, which is honest

    span = (best[-1][0] - best[0][0]).total_seconds()
    urls = [url for _, _, url in best if url]
    return [make_issue(
        url=base_url or urls[0] if urls else '',
        check='dates_from_one_machine_run',
        severity='warning',
        category=CATEGORY,
        issue='Published Dates Come From One Machine Run',
        details=(f'{len(best)} pages claim publication dates inside {span:.1f}s of '
                 f'each other, with {len(distinct)} different values. That is a '
                 f'build or seeder loop, not {len(best)} publication events.'),
        offending_value=f'{len(best)}@{best[0][0].isoformat()}',
        evidence={'found': f'{len(best)} pages within {span:.1f}s',
                  'expected': 'dates describing when each page was published',
                  'related_urls': urls[:50]},
        how_to_fix=('Store a publication date per page when it first goes live and '
                    'never rewrite it. If these pages really were published in one '
                    'batch, give them all the same timestamp and accept this issue.'),
    )]


def _missing_dates(url, page, dates):
    """An article-ish JSON-LD block with neither date property."""
    node = article_node(page)
    if node is None:
        return []

    missing = [name for name, key in (('datePublished', 'jsonld_published'),
                                      ('dateModified', 'jsonld_modified'))
               if dates.get(key) is None]
    if not missing:
        return []

    type_name = _type_name(node.get('@type'))
    return [make_issue(
        url=url,
        check='missing_dates',
        severity='warning',
        category=CATEGORY,
        issue='Missing Article Dates',
        details=(f'The {type_name} JSON-LD block on this page has no '
                 f'{" or ".join(missing)}, so search engines cannot tell when '
                 'the content was written or last changed.'),
        offending_value=','.join(missing),
        evidence={
            'snippet': f'"@type": "{type_name}"',
            'expected': ' and '.join(('datePublished', 'dateModified')),
            'found': ', '.join(f'{name} missing' for name in missing),
        },
        how_to_fix=(f'Add {" and ".join(missing)} to the {type_name} JSON-LD '
                    'block as ISO-8601 values with a timezone offset.'),
    )]


def _invalid_formats(url, dates):
    """A page-served date that will not parse, or parses without a timezone."""
    issues = []
    for key in PAGE_DATE_SOURCES:
        entry = dates.get(key)
        if entry is None:
            continue
        label = SOURCE_LABELS[key]
        if entry['parsed'] is None:
            problem = 'is not a date Toadstone can parse'
            fix = 'Replace it with an ISO-8601 value such as 2026-01-05T09:30:00+00:00.'
        elif key in TIMEZONE_REQUIRED and not parse_date_detailed(entry['raw'])[1]:
            problem = 'has no timezone, so it means a different instant in every region'
            fix = ('Append the timezone offset (for example +00:00 or Z) to the '
                   f'{label} value.')
        else:
            continue
        issues.append(make_issue(
            url=url,
            check='invalid_date_format',
            severity='warning',
            category=CATEGORY,
            issue='Invalid Date Format',
            details=f'The {label} value "{entry["raw"]}" {problem}.',
            offending_value=f'{key}={entry["raw"]}',
            evidence={
                'expected': 'ISO-8601 with a timezone, e.g. 2026-01-05T09:30:00+00:00',
                'found': entry['raw'],
            },
            how_to_fix=fix,
        ))
    return issues


def _mismatch(url, dates, tolerance_hours):
    """Two "last modified" sources that disagree by more than the tolerance."""
    present = [(key, dates[key]) for key in MODIFIED_SOURCES
               if dates.get(key) and dates[key]['parsed'] is not None]
    if len(present) < 2:
        return []

    moments = [entry['parsed'] for _, entry in present]
    spread = max(moments) - min(moments)
    tolerance = timedelta(hours=tolerance_hours)
    if spread <= tolerance:
        return []

    listed = '; '.join(f'{SOURCE_LABELS[key]}={entry["raw"]}' for key, entry in present)
    compact = '; '.join(f'{key}={entry["raw"]}' for key, entry in present)
    return [make_issue(
        url=url,
        check='modified_date_mismatch',
        severity='warning',
        category=CATEGORY,
        issue='Modified Date Mismatch',
        details=(f'This page reports {len(present)} different last-modified '
                 f'dates, {_describe_spread(spread)} apart (tolerance '
                 f'{tolerance_hours}h): {listed}.'),
        offending_value=compact,
        evidence={
            'expected': f'all last-modified sources within {tolerance_hours}h of each other',
            'found': compact,
        },
        how_to_fix=('Make one source authoritative - usually the CMS modified '
                    'timestamp - and derive the JSON-LD, meta tag, sitemap '
                    'lastmod and visible date from it.'),
    )]


def _stale(url, page, dates, now, stale_after_months, sitemap_lastmod):
    """A sitemap page whose dateModified is older than the stale window."""
    if not _in_sitemap(page.get('url'), sitemap_lastmod):
        return []
    entry = _effective(dates, 'jsonld_modified', 'meta_modified')
    if entry is None or entry['parsed'] is None:
        return []

    cutoff = _months_before(now, stale_after_months)
    if entry['parsed'] >= cutoff:
        return []

    age_months = _months_between(entry['parsed'], now)
    return [make_issue(
        url=url,
        check='stale_content',
        severity='info',
        category=CATEGORY,
        issue='Stale Content',
        details=(f'This page is in the sitemap but has not been modified for '
                 f'about {age_months} months (last modified '
                 f'{_format(entry["parsed"])}, threshold {stale_after_months} months).'),
        offending_value=entry['raw'],
        evidence={
            'expected': f'modified within the last {stale_after_months} months',
            'found': f'{SOURCE_LABELS[entry["source"]]}={entry["raw"]}',
        },
        how_to_fix=('Review and refresh the content, then bump dateModified, '
                    'article:modified_time and the sitemap lastmod together.'),
    )]


def _future(url, dates, now):
    """A page-served date later than now (beyond a few minutes of clock skew)."""
    limit = now + FUTURE_SKEW
    issues = []
    for key in PAGE_DATE_SOURCES + ('visible_updated',):
        entry = dates.get(key)
        if entry is None or entry['parsed'] is None or entry['parsed'] <= limit:
            continue
        label = SOURCE_LABELS[key]
        issues.append(make_issue(
            url=url,
            check='future_date',
            severity='error',
            category=CATEGORY,
            issue='Future Date',
            details=(f'The {label} value "{entry["raw"]}" is in the future '
                     f'({_format(entry["parsed"])} vs now {_format(now)}).'),
            offending_value=f'{key}={entry["raw"]}',
            evidence={
                'expected': f'a date at or before {_format(now)}',
                'found': entry['raw'],
            },
            how_to_fix=(f'Correct the {label} value to the real publication or '
                        'modification time; a future date suppresses indexing '
                        'and misreports freshness.'),
        ))
    return issues


def _modified_before_published(url, dates):
    """A page that says it changed before it existed."""
    modified = _effective(dates, 'jsonld_modified', 'meta_modified')
    published = _effective(dates, 'jsonld_published', 'meta_published')
    if not modified or not published:
        return []
    if modified['parsed'] is None or published['parsed'] is None:
        return []
    if modified['parsed'] >= published['parsed']:
        return []

    return [make_issue(
        url=url,
        check='modified_before_published',
        severity='error',
        category=CATEGORY,
        issue='Modified Before Published',
        details=(f'{SOURCE_LABELS[modified["source"]]} ({modified["raw"]}) is '
                 f'earlier than {SOURCE_LABELS[published["source"]]} '
                 f'({published["raw"]}), which cannot be true.'),
        offending_value=f'{modified["raw"]}<{published["raw"]}',
        evidence={
            'expected': f'a modified date at or after {published["raw"]}',
            'found': modified['raw'],
        },
        how_to_fix=('Set the modified date to the real last-edit time, or to '
                    'the published date when the page has never been edited.'),
    )]


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def _effective(dates, *keys):
    """The first present source among ``keys`` - JSON-LD before meta."""
    for key in keys:
        entry = dates.get(key)
        if entry is not None:
            return entry
    return None


def _in_sitemap(url, sitemap_lastmod):
    if not url or not sitemap_lastmod:
        return False
    return any(candidate in sitemap_lastmod for candidate in _url_variants(url))


def _type_name(value):
    values = value if isinstance(value, list) else [value]
    for item in values:
        if isinstance(item, str) and item.strip():
            return item.rsplit('/', 1)[-1].rsplit(':', 1)[-1].strip()
    return 'Article'


def _months_before(moment, months):
    """``moment`` shifted back N months, clamping the day (31 Mar -> 28 Feb)."""
    months = max(int(months), 0)
    year = moment.year - (months // 12)
    month = moment.month - (months % 12)
    if month < 1:
        month += 12
        year -= 1
    day = min(moment.day, _days_in_month(year, month))
    return moment.replace(year=year, month=month, day=day)


def _days_in_month(year, month):
    if month == 12:
        following = datetime(year + 1, 1, 1)
    else:
        following = datetime(year, month + 1, 1)
    return (following - timedelta(days=1)).day


def _months_between(earlier, later):
    months = (later.year - earlier.year) * 12 + (later.month - earlier.month)
    if later.day < earlier.day:
        months -= 1
    return max(months, 0)


def _describe_spread(delta):
    hours = delta.total_seconds() / 3600
    if hours >= 48:
        return f'{delta.days} days'
    return f'{hours:.1f}h'


def _format(moment):
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
