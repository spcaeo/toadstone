"""Duplicate-content detection over a page's main text.

Template chrome used to dominate the score: the crawl compared title, meta
description, H1 and word count, so two hub pages that share a layout scored
89% similar while the copy a reader actually sees was completely different.
The crawler now stores each page's `main_text` (header/nav/footer/sidebar
stripped by the extractor) together with `main_text_hash` and a 64-bit
`simhash`, so this pass can compare what the pages say rather than how they
are framed:

* identical `main_text_hash`            -> the same content, reported as such
* simhash similarity >= min_similarity  -> a near duplicate
* neither page stored any main text     -> fall back to the old
  title/description/H1/length score, so crawls saved before the body was kept
  (and non-HTML-ish pages that yield no body text) still report duplicates

An issue carries the passages the two pages actually share, not just a
percentage, so the reader can see whether it is one repeated paragraph or a
wholesale copy.

Everything the previous implementation did to stay cheap is kept: one issue
per page (never one per pair), identical pages grouped before scoring, a
blocking step so most pairs are never scored at all, a wall-clock budget, and
alias suppression (two addresses of one page are not duplicates of each
other).

Locale versions of one page are not duplicates of each other either: pages
that declare each other as hreflang alternates (see src/core/locales.py) are
one page in several languages or regions, and only a duplicate outside that
group is reported.
"""
import math
import re
import time
from collections import defaultdict

from src.core import locales
from src.utils.issue_factory import make_issue
from src.utils.text_fingerprint import (
    shared_passages,
    similarity,
    simhash,
    text_hash,
)

# Near-duplicate detection is bounded so a large site can never hold a crawl in
# "finishing up" indefinitely: when the budget is exhausted the pass stops with
# a warning and partial results instead of running to completion.
DUPLICATION_TIME_BUDGET_SECONDS = 120
DUPLICATION_EXAMPLES_PER_PAGE = 5

# How often the budget is consulted while scoring pairs. One comparison is a
# handful of integer operations, so checking the clock every time would cost
# more than the comparisons.
BUDGET_CHECK_EVERY = 2000

# Width of the fingerprints produced by text_fingerprint.simhash().
SIMHASH_BITS = 64

# A page needs this many words of main text before its simhash is trusted:
# below it the fingerprint is built from a handful of shingles and two
# unrelated stubs can land close together. Shorter pages use the fallback.
MIN_MAIN_TEXT_WORDS = 20

# Never split the fingerprint into bands narrower than this many bits: with a
# very low threshold the band count would otherwise explode into buckets that
# match everything.
MAX_SIMHASH_BANDS = 16

# Shared-passage evidence: runs of at least PASSAGE_MIN_WORDS words, the
# longest PASSAGE_LIMIT of them, each trimmed to PASSAGE_MAX_CHARS.
PASSAGE_MIN_WORDS = 15
PASSAGE_LIMIT = 2
PASSAGE_MAX_CHARS = 300

DUPLICATE_CHECK = 'duplicate_content'
DUPLICATE_ISSUE = 'Duplicate Content Detected'
DUPLICATE_CATEGORY = 'Duplication'
DUPLICATE_FIX = ('Keep one page as the canonical version of this content and either rewrite '
                 'the others or point their rel=canonical at it.')

_TOKEN_SPLIT = re.compile(r'[^\w]+')


def _same_page_different_query(page_a, page_b):
    """Two URLs that differ only in their query string and serve the same body.

    A parameter the crawler does not know to strip - a session id it has not
    seen, a sort order that changes nothing - still addresses one page. When
    the bodies hash identically, treating the pair as a duplicate defect would
    blame the site for the crawler's own blind spot.
    """
    hash_a, hash_b = page_a.get('text_hash'), page_b.get('text_hash')
    if not hash_a or hash_a != hash_b:
        return False
    url_a, url_b = page_a.get('url') or '', page_b.get('url') or ''
    if url_a == url_b:
        return False
    return url_a.split('?', 1)[0] == url_b.split('?', 1)[0]


class DuplicationDetector:
    """The duplicate-content pass, run once over a finished crawl.

    `should_exclude` and `alias_key` come from the owning IssueDetector so the
    pass honours the same exclusion patterns and the same notion of "these two
    URLs address one page" as every other check.
    """

    # Weights of the signals in the fallback score, used only when at least
    # one side of a pair stored no main text.
    _WEIGHTS = {'title': 0.35, 'desc': 0.35, 'h1': 0.20, 'word_count': 0.10}

    def __init__(self, min_similarity=0.85, should_exclude=None, alias_key=None):
        self.min_similarity = min_similarity
        self._should_exclude = should_exclude or (lambda url: False)
        self._alias_key = alias_key or (lambda url: url or '')
        self.truncated = False

    # ------------------------------------------------------------------ pass

    def detect(self, all_results, similarity_threshold=None):
        """Return the duplicate-content issues for one crawl.

        Args:
            all_results: the crawled result dictionaries.
            similarity_threshold: minimum similarity to flag (0.0-1.0);
                defaults to the configured `min_similarity`.

        Returns:
            A list of issues built by `make_issue`, at most one per page.
        """
        threshold = self.min_similarity if similarity_threshold is None else similarity_threshold
        started = time.monotonic()
        deadline = started + DUPLICATION_TIME_BUDGET_SECONDS
        self.truncated = False

        pages = self._candidates(all_results)
        if len(pages) < 2:
            return []
        alternates = locales.build(all_results)
        for page in pages:
            page['alternate_group'] = alternates.group_of(page['url'])

        groups, labels = self._group_identical(pages)
        representatives = [group[0] for group in groups]
        similar = self._score_groups(representatives, threshold, started)
        return self._issues(groups, labels, similar, deadline)

    def _score_groups(self, representatives, threshold, started):
        """Score the candidate pairs of groups, each pair once."""
        similar = defaultdict(list)  # group idx -> [(similarity, other group idx)]
        compared = 0
        for i, j in self._candidate_pairs(representatives, threshold):
            compared += 1
            if (compared % BUDGET_CHECK_EVERY == 0
                    and time.monotonic() - started > DUPLICATION_TIME_BUDGET_SECONDS):
                self.truncated = True
                print(f"Duplication detection stopped after {DUPLICATION_TIME_BUDGET_SECONDS}s "
                      f"and {compared} comparisons; duplicate results are partial")
                break
            score = self._similarity(representatives[i], representatives[j], threshold)
            if score is not None:
                similar[i].append((score, j))
                similar[j].append((score, i))
        return similar

    # ------------------------------------------------------------ candidates

    def _candidates(self, all_results):
        """Pages eligible for duplicate detection, with their fingerprints."""
        pages = []
        for result in all_results or ():
            if not isinstance(result, dict):
                continue
            url = result.get('url', '')
            if not url or self._should_exclude(url):
                continue
            status = result.get('status_code')
            if status is not None and status != 200:
                continue
            content_type = (result.get('content_type') or '').lower()
            if content_type and 'html' not in content_type:
                continue
            page = self.page(result)
            if not page['has_text'] and not page['title'] and not page['desc']:
                continue
            pages.append(page)
        return pages

    def page(self, result):
        """One crawl result reduced to what duplicate detection compares."""
        text = result.get('main_text')
        if not isinstance(text, str):
            text = ''
        word_count = result.get('main_word_count') or len(text.split())
        has_text = bool(text) and word_count >= MIN_MAIN_TEXT_WORDS

        title = self._normalise(result.get('title'))
        desc = self._normalise(result.get('meta_description'))
        h1 = self._normalise(result.get('h1'))
        url = result.get('url', '')
        return {
            'url': url,
            'key': self._alias_key(url),
            'canonical': self._alias_key(result.get('canonical_url', '')),
            'text': text,
            'has_text': has_text,
            'text_hash': (result.get('main_text_hash') or text_hash(text)) if has_text else '',
            'simhash': self._fingerprint(result, text) if has_text else 0,
            'title': title,
            'desc': desc,
            'h1': h1,
            'title_tokens': self._tokens(title),
            'desc_tokens': self._tokens(desc),
            'h1_tokens': self._tokens(h1),
            'word_count': result.get('word_count') or 0,
        }

    @staticmethod
    def _fingerprint(result, text):
        """The stored simhash as an int, recomputed if it is missing or junk.

        The crawler stores it as a decimal string so SQLite and JSON keep all
        64 bits; older rows and hand-built dicts may carry nothing at all.
        """
        try:
            stored = int(result.get('simhash') or 0)
        except (TypeError, ValueError):
            stored = 0
        return stored if stored else simhash(text, SIMHASH_BITS)

    @staticmethod
    def _normalise(value):
        """Lower-cased, whitespace-collapsed text; H1 may arrive as a list."""
        if isinstance(value, list):
            value = next((v for v in value if isinstance(v, str) and v.strip()), '')
        if not isinstance(value, str):
            return ''
        return ' '.join(value.lower().split())

    @staticmethod
    def _tokens(text):
        return frozenset(t for t in _TOKEN_SPLIT.split(text) if t)

    def _group_identical(self, pages):
        """Group pages that are identical by content (or, without main text,
        by title/description/H1) so only one of each group is ever scored.

        Returns (groups, labels): `labels[i]` is how members of group `i`
        describe each other in the issue text.
        """
        index = {}
        groups = []
        labels = []
        for page in pages:
            if page['has_text']:
                signature = ('text', page['text_hash'])
                label = 'identical page content'
            else:
                signature = ('meta', page['title'], page['desc'], page['h1'])
                label = 'identical title, description and H1'
            idx = index.get(signature)
            if idx is None:
                idx = index[signature] = len(groups)
                groups.append([])
                labels.append(label)
            groups[idx].append(page)
        return groups, labels

    # ----------------------------------------------------------- pair filter

    def _candidate_pairs(self, representatives, threshold):
        """Yield (i, j), i < j, for the pairs of groups worth scoring.

        Two blocking strategies, because the two scores prune differently:

        * pairs where both sides have main text are blocked on the simhash
          (see `_simhash_pairs`);
        * every other pair - at least one side has no main text and is scored
          on its metadata - is blocked on the title prefix filter that this
          check has always used (see `_metadata_pairs`).

        The two sets are disjoint by construction, so no pair is scored twice.
        """
        for pair in self._metadata_pairs(representatives, threshold):
            yield pair
        for pair in self._simhash_pairs(representatives, threshold):
            yield pair

    def _simhash_pairs(self, representatives, threshold):
        """Yield text-vs-text pairs whose fingerprints share a band.

        A pair can only be flagged when its fingerprints differ in at most
        `d = floor((1 - threshold) * 64)` bits. Splitting the 64 bits into
        `d + 1` disjoint bands and bucketing on each one loses nothing: `d`
        differing bits cannot touch all `d + 1` bands, so a qualifying pair is
        always identical on at least one band and always turns up as a
        candidate. At the default 0.85 that is 10 bands of 6-7 bits, and a
        site whose pages differ lands a handful of pages per bucket.

        Below a threshold of 0.75 the band count is capped at
        MAX_SIMHASH_BANDS, which gives up the guarantee - but 16 bands of 4
        bits already put nearly every page of a templated site in a shared
        bucket, so little is lost in practice.
        """
        indexed = [i for i, rep in enumerate(representatives) if rep['has_text']]
        if len(indexed) < 2:
            return

        distance = int(math.floor((1.0 - threshold) * SIMHASH_BITS))
        bands = min(max(distance + 1, 1), MAX_SIMHASH_BANDS)
        bounds = [(SIMHASH_BITS * b) // bands for b in range(bands + 1)]

        index = defaultdict(list)
        keyed = []
        for i in indexed:
            fingerprint = representatives[i]['simhash']
            keys = [(b, (fingerprint >> bounds[b]) & ((1 << (bounds[b + 1] - bounds[b])) - 1))
                    for b in range(bands)]
            keyed.append((i, keys))
            for key in keys:
                index[key].append(i)

        for i, keys in keyed:
            seen = set()
            for key in keys:
                for j in index[key]:
                    if j < i and j not in seen:
                        seen.add(j)
                        yield j, i

    def _metadata_pairs(self, representatives, threshold):
        """Yield the pairs scored on metadata, i.e. those where at least one
        side stored no main text.

        Uses a prefix filter on title tokens. The title carries 35% of the
        fallback score, so a pair can only reach `threshold` if the titles'
        Dice similarity is at least (threshold - 0.65) / 0.35. Dice(A, B) >= t
        implies |A n B| >= t*|A| / (2 - t); ordering tokens by global rarity,
        two such sets must share a token within the first |A| - need + 1
        tokens of each, so indexing those prefixes finds every qualifying pair
        while skipping most pairs of distinct titles.
        """
        weights = self._WEIGHTS
        min_title = (threshold - (1.0 - weights['title'])) / weights['title']
        count = len(representatives)
        has_text = [rep['has_text'] for rep in representatives]

        if min_title <= 0:
            # Threshold is too low for the title to prune anything.
            for i in range(count):
                for j in range(i + 1, count):
                    if not (has_text[i] and has_text[j]):
                        yield i, j
            return

        frequency = defaultdict(int)
        for rep in representatives:
            for token in rep['title_tokens']:
                frequency[token] += 1

        index = defaultdict(list)
        prefixes = []
        for i, rep in enumerate(representatives):
            ordered = sorted(rep['title_tokens'], key=lambda t: (frequency[t], t))
            need = math.ceil(min_title * len(ordered) / (2 - min_title))
            prefix = ordered[:max(1, len(ordered) - need + 1)] if ordered else []
            prefixes.append(prefix)
            for token in prefix:
                index[token].append(i)

        for i, prefix in enumerate(prefixes):
            seen = set()
            for token in prefix:
                for j in index[token]:
                    if j < i and j not in seen:
                        seen.add(j)
                        if not (has_text[i] and has_text[j]):
                            yield j, i

    # ---------------------------------------------------------------- scores

    def _similarity(self, a, b, threshold):
        """Similarity of two candidate pages, or None when they are not
        duplicates. Main text decides whenever both pages carry it."""
        if a['has_text'] and b['has_text']:
            if a['text_hash'] and a['text_hash'] == b['text_hash']:
                return 1.0
            score = similarity(a['simhash'], b['simhash'], SIMHASH_BITS)
            return score if score >= threshold else None
        return self.metadata_similarity(a, b, threshold)

    def metadata_similarity(self, a, b, threshold):
        """The pre-main-text score: word-level Dice similarity of title (35%),
        meta description (35%) and H1 (20%), plus the ratio of the word counts
        (10%). Returns None once it is clear the score cannot reach
        `threshold`, so the cheap signals short-circuit."""
        weights = self._WEIGHTS
        score = self._dice(a['title_tokens'], b['title_tokens']) * weights['title']
        if score + weights['desc'] + weights['h1'] + weights['word_count'] < threshold:
            return None
        score += self._dice(a['desc_tokens'], b['desc_tokens']) * weights['desc']
        if score + weights['h1'] + weights['word_count'] < threshold:
            return None
        score += self._dice(a['h1_tokens'], b['h1_tokens']) * weights['h1']
        if score + weights['word_count'] < threshold:
            return None
        score += self._length_similarity(a['word_count'], b['word_count']) * weights['word_count']
        return score if score >= threshold else None

    @staticmethod
    def _dice(tokens_a, tokens_b):
        """Dice coefficient of two token sets (0.0 when either is empty)."""
        if not tokens_a or not tokens_b:
            return 0.0
        return 2.0 * len(tokens_a & tokens_b) / (len(tokens_a) + len(tokens_b))

    @staticmethod
    def _length_similarity(count_a, count_b):
        if not count_a or not count_b:
            return 0.0
        return min(count_a, count_b) / max(count_a, count_b)

    @staticmethod
    def _are_aliases(page_a, page_b):
        """True when two candidate pages are two addresses of one page.

        Either both declare the same canonical URL, or one declares the other's
        URL as its canonical, or they are hreflang alternates of each other.
        Such a pair is identical by design, not a duplicate-content defect.
        """
        group = page_a.get('alternate_group')
        if group is not None and group == page_b.get('alternate_group'):
            return True
        canonical_a, canonical_b = page_a['canonical'], page_b['canonical']
        if canonical_a and canonical_a == canonical_b:
            return True
        if canonical_a and canonical_a == page_b['key']:
            return True
        if canonical_b and canonical_b == page_a['key']:
            return True
        return _same_page_different_query(page_a, page_b)

    # ---------------------------------------------------------------- issues

    def _issues(self, groups, labels, similar, deadline):
        """One issue per page: how many duplicates it has, the closest few,
        and the passages it shares with the closest one."""
        issues = []
        for idx, group in enumerate(groups):
            partners = [(1.0, member['url'], labels[idx], member) for member in group]
            for score, j in similar.get(idx, ()):
                partners.extend((score, other['url'], '', other) for other in groups[j])
            if len(partners) < 2:
                continue
            partners.sort(key=lambda p: (-p[0], p[1]))

            for page in group:
                others = [p for p in partners
                          if p[1] != page['url'] and not self._are_aliases(page, p[3])]
                if not others:
                    continue
                issues.append(self._issue(page, others, deadline))
        return issues

    def _issue(self, page, others, deadline):
        """Build the issue for one page from its duplicate partners."""
        total = len(others)
        examples = others[:DUPLICATION_EXAMPLES_PER_PAGE]
        described = '; '.join(
            f"{url} ({label})" if label else f"{url} ({score * 100:.1f}% similar)"
            for score, url, label, _ in examples
        )
        more = total - len(examples)
        details = (f"{total} similar page{'s' if total != 1 else ''}: {described}"
                   + (f" (+{more} more)" if more > 0 else ''))

        partner_urls = [url for _, url, _, _ in examples]
        return make_issue(
            url=page['url'],
            check=DUPLICATE_CHECK,
            severity='warning',
            category=DUPLICATE_CATEGORY,
            issue=DUPLICATE_ISSUE,
            details=details,
            # Sorted, so the issue keeps its id when the examples come back in
            # a different order on the next crawl.
            offending_value=','.join(sorted(partner_urls)),
            evidence={
                'snippet': self._shared_snippet(page, examples, deadline),
                'related_urls': partner_urls,
            },
            how_to_fix=DUPLICATE_FIX,
        )

    @staticmethod
    def _shared_snippet(page, examples, deadline):
        """The longest passages this page shares with one of the examples.

        Only the examples the issue actually names are considered, and only
        until one of them yields a passage, so a page costs at most a handful
        of comparisons rather than one per duplicate. The passages come back
        normalised (lower case, punctuation dropped) because that is the form
        the two pages are compared in.
        """
        if not page['has_text']:
            return ''
        for _, _, _, other in examples:
            if not other['has_text'] or time.monotonic() > deadline:
                continue
            passages = shared_passages(page['text'], other['text'],
                                       min_words=PASSAGE_MIN_WORDS, limit=PASSAGE_LIMIT)
            if passages:
                return ' … '.join(
                    passage[:PASSAGE_MAX_CHARS].rstrip() + '…'
                    if len(passage) > PASSAGE_MAX_CHARS else passage
                    for passage in passages
                )
        return ''
