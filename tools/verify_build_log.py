"""Check that no entry has ever fallen out of the build log.

`BUILD-LOG.md` is this programme's single source of truth, and on
2026-10-19 it was found to have lost 511 of its 515 lines eight commits
earlier: a rewrite kept only the newest few entries, every commit after
it appended to the stump, and nothing noticed. The file was still
committed, still valid Markdown, still growing. Nothing a test suite
watched had changed.

    python tools/verify_build_log.py

This walks every committed version of the log, collects every dated
entry any of them ever carried, and confirms each one is still in the
working copy. An entry may be reworded at its tail - corrections and
withdrawals are part of the record - but an entry that disappears
entirely is reported, with the commit that last had it.

To prove the check rather than trust it, point it at an older version:

    git show 21b18ee:BUILD-LOG.md > /tmp/clobbered.md
    python tools/verify_build_log.py --file /tmp/clobbered.md

That should fail and name what is missing.
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys

LOG = 'BUILD-LOG.md'
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# An entry is identified by its date and the opening of its text, so that
# fixing a typo or appending a correction does not read as a deletion.
KEY_CHARS = 60

ROW = re.compile(r'^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*(.*)$')

# Entries whose opening was deliberately changed, or that were deliberately
# removed, each with the reason. An entry is keyed by its date and its first
# characters, so rewording the start of one reads here exactly like deleting
# it - which is the right default. The cost of a deliberate change is one line
# in this dict, and the benefit is that an accidental deletion cannot hide.
ACCEPTED_REMOVALS = {
    # Keyed by digest rather than by the old text, because the old text is what
    # was scrubbed - reproducing it here to record the scrub would undo it.
    'd5d51d7e9d64': 'reworded 2026-10-20: a client domain scrubbed before publication',
    '9770d2d1e850': 'reworded 2026-10-20: a client domain scrubbed before publication',
    '72d273d4b861': 'reworded 2026-10-20: an owner domain scrubbed before publication',
}


def git(*args):
    return subprocess.run(('git',) + args, cwd=REPO, check=True,
                          capture_output=True, text=True).stdout


def rows(text):
    """Every dated entry in one version of the log, keyed for comparison.

    Rewrites have glued entries onto the end of other lines - '... |`|`
    2026-10-18 | UI redesign parked ...' - so a line can hold more than
    one entry and splitting on the date is what finds them.
    """
    found = {}
    for line in text.split('\n'):
        for piece in re.split(r'\|\|(?=\s*\d{4}-\d{2}-\d{2}\s*\|)', line):
            m = ROW.match(piece.strip())
            if not m:
                continue
            date, body = m.group(1), ' '.join(m.group(2).split())
            if len(body) < 10:
                continue
            found[(date, body[:KEY_CHARS])] = body
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', help='check this file instead of the working copy')
    args = ap.parse_args()

    path = args.file or os.path.join(REPO, LOG)
    with open(path) as fh:
        current = rows(fh.read())

    shas = git('log', '--format=%H', '--', LOG).split()
    history = {}
    for sha in shas:
        for key in rows(git('show', '%s:%s' % (sha, LOG))):
            history.setdefault(key, sha)

    def accepted(key):
        return hashlib.sha1(key[1].encode()).hexdigest()[:12] in ACCEPTED_REMOVALS

    missing = [(k, s) for k, s in history.items()
               if k not in current and not accepted(k)]

    print('%s: %d entries' % (path, len(current)))
    print('%d committed versions, %d distinct entries in all of them'
          % (len(shas), len(history)))
    if ACCEPTED_REMOVALS:
        print('%d accepted removals' % len(ACCEPTED_REMOVALS))

    if missing:
        print('\nFAIL: %d entries are in the history and not in this file'
              % len(missing))
        for (date, body), sha in sorted(missing):
            print('  %s %s  (last seen in %s)' % (date, body, sha[:7]))
        return 1

    print('\nOK: every entry the log has ever carried is still in it')
    return 0


if __name__ == '__main__':
    sys.exit(main())
