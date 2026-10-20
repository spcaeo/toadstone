"""Crawl-to-crawl diffing: what changed on the site since the last crawl.

A single crawl can only report what a page looks like today. Most of the
defects a site owner actually cares about are *changes*: a paragraph deleted by
a botched edit, an internal link that quietly disappeared from a template, an
issue that was fixed last month and has come back.

This module answers those questions by comparing two crawls of the same site:

    previous_crawl()            which crawl to compare against
    diff_pages()                per-URL text changes, plus pages gone / new
    diff_links()                internal links added and removed
    content_integrity_issues()  the two issues the diff produces
    regression_report()         new / fixed / still open, by stable issue id
    first_seen_map()            when each issue id was first seen

``diff_pages`` and ``diff_links`` are pure functions over the row dicts
``src/crawl_db.py`` hands back, so they can be tested without a database. The
three functions that take crawl ids do their I/O exclusively through
``src/crawl_db.py``.

Identity is the whole point of the regression report: every issue here is built
by ``make_issue``, whose id is ``sha1(url + check + offending_value)``. The
offending value for a removed-content issue is a hash of the removed text, so
the same deletion keeps the same id for as long as it is unfixed, and a
different deletion on the same page is a different issue rather than a mutation
of the first one.
"""
import re

from src import crawl_db
from src.utils.issue_factory import make_issue
from src.utils.text_fingerprint import normalise_text, similarity, simhash, text_hash

# Sentences shorter than this are nav crumbs, button labels and table cells
# rather than copy; reporting them as "content removed" on every menu tweak
# would bury the paragraph that actually went missing.
MIN_SENTENCE_WORDS = 3

# How much removed text travels in the issue evidence snippet.
SNIPPET_LIMIT = 300

# How many removed link targets travel in the issue evidence.
MAX_RELATED_URLS = 20

# How many crawls first_seen_map() looks back over by default.
DEFAULT_HISTORY = 10

# A sentence ends at .!?… (with any closing quote or bracket), or at a line
# break: extracted main text keeps one block per line, and plenty of blocks
# (headings, list items) carry no terminator at all.
_SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?…])["\'”’)\]]*\s+|[\r\n]+')


# ---------------------------------------------------------------------------
# choosing the crawl to compare against
# ---------------------------------------------------------------------------

def previous_crawl(base_domain, before_crawl_id, user_id=None):
    """Return the newest completed crawl of ``base_domain`` before a crawl id.

    ``before_crawl_id`` may be None to mean "the latest completed crawl".
    ``user_id`` scopes the search to one account; None searches every account's
    crawls of that domain.

    Returns the crawl dict ``crawl_db.get_crawl_by_id`` produces, or None when
    the site has never been crawled to completion before.
    """
    if not base_domain:
        return None

    crawl_ids = _completed_crawl_ids(base_domain, before_crawl_id, limit=1, user_id=user_id)
    if not crawl_ids:
        return None
    return crawl_db.get_crawl_by_id(crawl_ids[0])


def _completed_crawl_ids(base_domain, before_crawl_id, limit, user_id=None):
    """Ids of the most recent completed crawls of a domain, newest first."""
    query = "SELECT id FROM crawls WHERE base_domain = ? AND status = 'completed'"
    params = [base_domain]

    if before_crawl_id:
        query += ' AND id < ?'
        params.append(before_crawl_id)
    if user_id is not None:
        query += ' AND user_id = ?'
        params.append(user_id)

    query += ' ORDER BY id DESC LIMIT ?'
    params.append(limit)

    try:
        with crawl_db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [row['id'] for row in cursor.fetchall()]
    except Exception as e:
        print(f'Error finding previous crawls for {base_domain}: {e}')
        return []


# ---------------------------------------------------------------------------
# the diffs themselves (pure)
# ---------------------------------------------------------------------------

def diff_pages(previous_pages, current_pages, min_words=MIN_SENTENCE_WORDS):
    """Compare two crawls' page rows and report what changed per URL.

    Returns::

        {
          'text_changed': [{'url', 'previous_hash', 'current_hash',
                            'similarity', 'removed_sentences',
                            'added_sentences'}, ...],
          'text_removed': [{'url', 'removed_sentences', 'removed_text',
                            'removed_count'}, ...],
          'pages_gone':   ['https://site/gone', ...],
          'pages_new':    ['https://site/new', ...],
        }

    ``text_removed`` is the subset of ``text_changed`` that lost at least one
    sentence: a sentence that was in the previous crawl's ``main_text`` and is
    not in this one's, compared after ``normalise_text`` so casing, punctuation
    and whitespace churn do not count as a deletion.

    Pages with no stored ``main_text`` on either side are skipped entirely -
    a crawl that predates body storage must not read as "all content removed".
    """
    previous_by_url = _by_url(previous_pages)
    current_by_url = _by_url(current_pages)

    text_changed = []
    text_removed = []

    for url, current in current_by_url.items():
        previous = previous_by_url.get(url)
        if previous is None:
            continue

        before = (previous.get('main_text') or '').strip()
        after = (current.get('main_text') or '').strip()
        if not before or not after:
            continue
        if normalise_text(before) == normalise_text(after):
            continue

        before_sentences = _sentences(before, min_words)
        after_sentences = _sentences(after, min_words)
        removed = [text for key, text in before_sentences.items() if key not in after_sentences]
        added = [text for key, text in after_sentences.items() if key not in before_sentences]

        text_changed.append({
            'url': url,
            'previous_hash': _page_hash(previous, before),
            'current_hash': _page_hash(current, after),
            'similarity': round(similarity(_page_simhash(previous, before),
                                           _page_simhash(current, after)), 4),
            'removed_sentences': removed,
            'added_sentences': added,
        })

        if removed:
            text_removed.append({
                'url': url,
                'removed_sentences': removed,
                'removed_text': ' '.join(removed),
                'removed_count': len(removed),
            })

    return {
        'text_changed': text_changed,
        'text_removed': text_removed,
        'pages_gone': [url for url in previous_by_url if url not in current_by_url],
        'pages_new': [url for url in current_by_url if url not in previous_by_url],
    }


def diff_links(previous_links, current_links):
    """Compare two crawls' internal links, keyed by (source_url, target_url).

    Returns ``{'links_removed': [...], 'links_added': [...]}`` where each entry
    is ``{'source_url', 'target_url', 'anchor_text'}``. External links are
    ignored: a link to another site disappearing is the other site's business,
    and third-party links change constantly.
    """
    previous_edges = _internal_edges(previous_links)
    current_edges = _internal_edges(current_links)

    removed = [_edge(key, anchor) for key, anchor in previous_edges.items()
               if key not in current_edges]
    added = [_edge(key, anchor) for key, anchor in current_edges.items()
             if key not in previous_edges]

    return {'links_removed': removed, 'links_added': added}


# ---------------------------------------------------------------------------
# issues
# ---------------------------------------------------------------------------

def content_integrity_issues(previous_crawl_id, current_crawl_id):
    """Issues for content and internal links lost since the previous crawl.

    Emits two checks:

      ``content_removed``       one per page that lost sentences
      ``internal_link_removed`` one per page that lost internal links

    Returns [] when there is no previous crawl to compare against: a first
    crawl has removed nothing.
    """
    if not previous_crawl_id or not current_crawl_id:
        return []

    page_diff = diff_pages(crawl_db.load_crawled_urls(previous_crawl_id),
                           crawl_db.load_crawled_urls(current_crawl_id))
    link_diff = diff_links(crawl_db.load_crawl_links(previous_crawl_id),
                           crawl_db.load_crawl_links(current_crawl_id))

    issues = []
    issues.extend(_content_removed_issues(page_diff['text_removed'], previous_crawl_id))
    issues.extend(_link_removed_issues(link_diff['links_removed'], previous_crawl_id))
    return issues


def _content_removed_issues(text_removed, previous_crawl_id):
    """One 'Content Removed' issue per page that lost sentences."""
    issues = []
    for entry in text_removed:
        removed_text = entry['removed_text']
        count = entry['removed_count']
        first = entry['removed_sentences'][0]

        issues.append(make_issue(
            url=entry['url'],
            check='content_removed',
            severity='warning',
            category='Content',
            issue='Content Removed',
            details=(f'{count} {_plural(count, "sentence")} present in crawl '
                     f'{previous_crawl_id} {_plural(count, "is", "are")} gone from this page. '
                     f'First: "{_clip(first, 160)}"'),
            # The id must stay the same while this deletion stays unfixed, and
            # must differ from an unrelated deletion on the same page, so the
            # offending value is a fingerprint of the text that went.
            offending_value=text_hash(removed_text)[:16],
            evidence={'snippet': _clip(removed_text, SNIPPET_LIMIT)},
            how_to_fix=('Restore the missing copy, or confirm the deletion was intended - '
                        'compare this page against the previous crawl before publishing.'),
        ))
    return issues


def _link_removed_issues(links_removed, previous_crawl_id):
    """One 'Internal Link Removed' issue per page that lost internal links."""
    by_source = {}
    for link in links_removed:
        by_source.setdefault(link['source_url'], set()).add(link['target_url'])

    issues = []
    for source_url in sorted(by_source):
        targets = sorted(by_source[source_url])
        count = len(targets)
        shown = ', '.join(targets[:3])
        if count > 3:
            shown += f' (+{count - 3} more)'

        issues.append(make_issue(
            url=source_url,
            check='internal_link_removed',
            severity='warning',
            category='Links',
            issue='Internal Link Removed',
            details=(f'{count} internal {_plural(count, "link")} that existed in crawl '
                     f'{previous_crawl_id} {_plural(count, "is", "are")} gone from this page: {shown}'),
            # The sorted target list is the identity of this defect: lose one
            # more link from the same page and it is a different issue.
            offending_value=' '.join(targets),
            evidence={'related_urls': targets[:MAX_RELATED_URLS]},
            how_to_fix=('Restore the links, or confirm they were removed on purpose - '
                        'check whether a template or menu change dropped them site-wide.'),
        ))
    return issues


# ---------------------------------------------------------------------------
# regression tracking
# ---------------------------------------------------------------------------

def regression_report(previous_crawl_id, current_crawl_id):
    """Compare two crawls' saved issues by their stable ids.

    Returns::

        {'new': [...], 'fixed': [...], 'still_open': [...],
         'counts': {'new': n, 'fixed': n, 'still_open': n,
                    'by_severity': {'error': n, 'warning': n, 'info': n}}}

    ``fixed`` holds the *previous* crawl's issue dicts (the current crawl has
    nothing to show for them); ``new`` and ``still_open`` hold the current
    crawl's. With no previous crawl every current issue is new.
    """
    current = _issues_by_id(crawl_db.load_crawl_issues(current_crawl_id) if current_crawl_id else [])
    previous = _issues_by_id(crawl_db.load_crawl_issues(previous_crawl_id) if previous_crawl_id else [])

    new = [issue for issue_id, issue in current.items() if issue_id not in previous]
    still_open = [issue for issue_id, issue in current.items() if issue_id in previous]
    fixed = [issue for issue_id, issue in previous.items() if issue_id not in current]

    by_severity = {'error': 0, 'warning': 0, 'info': 0}
    for issue in new:
        severity = issue.get('type') or 'info'
        by_severity[severity] = by_severity.get(severity, 0) + 1

    return {
        'new': new,
        'fixed': fixed,
        'still_open': still_open,
        'counts': {
            'new': len(new),
            'fixed': len(fixed),
            'still_open': len(still_open),
            'by_severity': by_severity,
        },
    }


def first_seen_map(base_domain, current_crawl_id, history=DEFAULT_HISTORY):
    """Map each issue id to the earliest crawl that reported it.

    Looks back over the last ``history`` completed crawls of the domain that
    precede ``current_crawl_id``, oldest first, so the caller can stamp
    ``first_seen_crawl`` on this crawl's issues: an id missing from the map is
    being seen for the first time, and its first_seen_crawl is this crawl.

    One pass over each crawl's issues, and no crawl is loaded twice.
    """
    if not base_domain or not history or history < 1:
        return {}

    # Newest-first from the database, walked oldest-first so the first crawl to
    # mention an id wins and no comparison is needed per row.
    crawl_ids = _completed_crawl_ids(base_domain, current_crawl_id, limit=history)

    first_seen = {}
    for crawl_id in sorted(crawl_ids):
        for issue in crawl_db.load_crawl_issues(crawl_id):
            issue_id = issue.get('id')
            if issue_id and issue_id not in first_seen:
                first_seen[issue_id] = crawl_id
    return first_seen


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _by_url(pages):
    """Index page rows by URL, keeping the first row for a repeated URL."""
    indexed = {}
    for page in pages or []:
        url = page.get('url')
        if url:
            indexed.setdefault(url, page)
    return indexed


def _sentences(text, min_words=MIN_SENTENCE_WORDS):
    """Map each sentence's normalised form to the text as it was written.

    Normalised keys are what the diff compares; the original text is what the
    issue shows a human. Repeated sentences collapse to one entry - a sentence
    that appears twice and is deleted once has not been removed from the page.
    """
    if not text:
        return {}

    sentences = {}
    for fragment in _SENTENCE_BOUNDARY.split(text):
        original = fragment.strip()
        if not original:
            continue
        key = normalise_text(original)
        if len(key.split()) < min_words:
            continue
        sentences.setdefault(key, original)
    return sentences


def _page_hash(page, text):
    """The page's stored text hash, or one computed from the text."""
    return page.get('main_text_hash') or text_hash(text)


def _page_simhash(page, text):
    """The page's stored simhash as an int, or one computed from the text.

    Stored simhashes are decimal strings (sqlite's INTEGER is signed and would
    wrap the top bit), and rows written before the column existed have none.
    """
    stored = page.get('simhash')
    if stored in (None, ''):
        return simhash(text)
    try:
        return int(stored)
    except (TypeError, ValueError):
        return simhash(text)


def _internal_edges(links):
    """Map (source_url, target_url) -> anchor text for internal links only."""
    edges = {}
    for link in links or []:
        if not link.get('is_internal'):
            continue
        source = link.get('source_url')
        target = link.get('target_url')
        if not source or not target:
            continue
        edges.setdefault((source, target), link.get('anchor_text') or '')
    return edges


def _edge(key, anchor_text):
    source, target = key
    return {'source_url': source, 'target_url': target, 'anchor_text': anchor_text}


def _issues_by_id(issues):
    """Index issue rows by their stable id, keeping the first of a repeat."""
    indexed = {}
    for issue in issues or []:
        issue_id = issue.get('id')
        if issue_id and issue_id not in indexed:
            indexed[issue_id] = issue
    return indexed


def _clip(text, limit):
    """Shorten text for a details line or an evidence snippet."""
    text = ' '.join((text or '').split())
    if len(text) <= limit:
        return text
    return text[:limit - 3].rstrip() + '...'


def _plural(count, singular, plural=None):
    """'1 sentence' / '2 sentences', and 'is' / 'are'."""
    if count == 1:
        return singular
    return plural if plural is not None else singular + 's'
