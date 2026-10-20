"""Text fingerprinting helpers for duplicate detection and content diffing.

The crawler stores a page's main text so later crawls can answer three
questions cheaply:

* "is this byte-for-byte the same content as another page?"  -> ``text_hash``
* "is this *nearly* the same content?"                       -> ``simhash`` +
  ``similarity``
* "which passages do the two pages actually share?"          -> ``shared_passages``

Everything here is a pure function over ``str``: no I/O, no globals, no
dependencies outside the standard library, so the module is safe to import
from anywhere in the crawler.

Hashing uses ``hashlib`` (sha1/blake2b) rather than Python's built-in
``hash()``, which is salted per process and would produce a different
fingerprint on every run.
"""
import hashlib
import re
import unicodedata
from collections import Counter

# Whitespace, punctuation and symbols all collapse to a single space so that
# "Hello,  world!" and "hello world" fingerprint identically.
_NON_WORD_RE = re.compile(r'[^\w\s]+', re.UNICODE)
_WHITESPACE_RE = re.compile(r'\s+', re.UNICODE)

# Upper bound on the words considered by shared_passages(). A 4000 word cap
# keeps the worst case bounded on pages that dump an entire book into <main>.
MAX_PASSAGE_WORDS = 4000

# Number of words per shingle fed to the simhash.
SHINGLE_SIZE = 3


def normalise_text(text):
    """Return the canonical form used for hashing and comparison.

    Unicode is NFKC-normalised (so typographic quotes and their ASCII
    equivalents match), case is folded, punctuation and symbols are dropped
    and every whitespace run collapses to one space. The result is the string
    every other function in this module actually works on.
    """
    if not text:
        return ''

    normalised = unicodedata.normalize('NFKC', text)
    normalised = normalised.lower()
    normalised = _NON_WORD_RE.sub(' ', normalised)
    return _WHITESPACE_RE.sub(' ', normalised).strip()


def text_hash(text):
    """Return the sha1 hex digest of the normalised text.

    Two pages with the same digest carry the same content once wording,
    casing, punctuation and whitespace are normalised away - this is the
    exact-duplicate test.
    """
    return hashlib.sha1(normalise_text(text).encode('utf-8')).hexdigest()


def _stable_hash(value, bits):
    """Hash a shingle to an integer that is stable across processes."""
    digest_size = max(1, bits // 8)
    digest = hashlib.blake2b(value.encode('utf-8'), digest_size=digest_size).digest()
    return int.from_bytes(digest, 'big') & ((1 << bits) - 1)


def _shingles(words, size=SHINGLE_SIZE):
    """Yield overlapping word shingles; short texts yield their words."""
    if len(words) < size:
        for word in words:
            yield word
        return

    for i in range(len(words) - size + 1):
        yield ' '.join(words[i:i + size])


def simhash(text, bits=64):
    """Return a locality-sensitive fingerprint of the text as an int.

    Classic Charikar simhash over 3-word shingles: each shingle votes on every
    bit position, weighted by how often the shingle occurs, and the sign of
    each column becomes a bit. Near-identical documents produce fingerprints
    that differ in only a few bits, so ``similarity()`` on two simhashes is a
    cheap stand-in for comparing the documents themselves.

    Returns 0 for empty text.
    """
    words = normalise_text(text).split()
    if not words:
        return 0

    counts = Counter(_shingles(words))
    columns = [0] * bits

    for shingle, weight in counts.items():
        value = _stable_hash(shingle, bits)
        for position in range(bits):
            if (value >> position) & 1:
                columns[position] += weight
            else:
                columns[position] -= weight

    fingerprint = 0
    for position in range(bits):
        if columns[position] > 0:
            fingerprint |= 1 << position
    return fingerprint


def hamming_distance(a, b):
    """Return the number of differing bits between two integer fingerprints."""
    return bin(int(a) ^ int(b)).count('1')


def similarity(a, b, bits=64):
    """Return fingerprint similarity in 0..1 (1.0 means identical).

    ``a`` and ``b`` are simhash integers, not text. The score is
    ``1 - hamming_distance / bits``; in practice anything above ~0.9 is a
    near-duplicate and below ~0.7 is unrelated content.
    """
    if bits <= 0:
        return 0.0
    return 1.0 - (hamming_distance(a, b) / float(bits))


def shared_passages(text_a, text_b, min_words=15, limit=5):
    """Return the longest word sequences the two texts have in common.

    Used to show *what* two near-duplicate pages actually share - a repeated
    boilerplate paragraph reads very differently from a wholesale copy of the
    body. Only maximal runs of at least ``min_words`` words are reported,
    longest first, at most ``limit`` of them, and the passages are returned in
    normalised form.

    Both inputs are truncated to the first MAX_PASSAGE_WORDS words so a huge
    page cannot blow up the comparison.
    """
    if min_words < 1 or limit < 1:
        return []

    words_a = normalise_text(text_a).split()[:MAX_PASSAGE_WORDS]
    words_b = normalise_text(text_b).split()[:MAX_PASSAGE_WORDS]
    if len(words_a) < min_words or len(words_b) < min_words:
        return []

    # Index every min_words-long window of A by its hash, then walk B looking
    # for a window that matches and extend the match as far as it goes. This
    # is O(n*m) in the pathological case of a text made of one repeated word,
    # but linear on real pages.
    index = {}
    for i in range(len(words_a) - min_words + 1):
        key = ' '.join(words_a[i:i + min_words])
        index.setdefault(key, []).append(i)

    matches = []
    j = 0
    while j <= len(words_b) - min_words:
        key = ' '.join(words_b[j:j + min_words])
        starts = index.get(key)
        if not starts:
            j += 1
            continue

        best_length = 0
        for i in starts:
            length = min_words
            while (i + length < len(words_a) and j + length < len(words_b)
                   and words_a[i + length] == words_b[j + length]):
                length += 1
            if length > best_length:
                best_length = length

        matches.append(' '.join(words_b[j:j + best_length]))
        # Skip past the run we just consumed so overlapping windows of the
        # same passage are not reported several times.
        j += best_length - min_words + 1

    if not matches:
        return []

    # Longest first, de-duplicated, without letting a passage that is merely a
    # substring of a longer one take a slot.
    matches.sort(key=len, reverse=True)
    kept = []
    for passage in matches:
        if any(passage in longer for longer in kept):
            continue
        kept.append(passage)
        if len(kept) >= limit:
            break
    return kept
