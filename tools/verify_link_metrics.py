"""Check the link-metrics integrations against the real vendors.

Moz, Majestic and Ahrefs each need a paid subscription, so the success
path cannot be exercised without one. That is a purchasing decision, not
an engineering one, and this tool is written so that it stops being a
blocker the moment somebody makes it:

    python tools/verify_link_metrics.py

With no keys it still does real work. It calls all three live endpoints
and confirms they refuse in the shape this code expects - which is the
part most likely to have been guessed wrong, and the part that silently
rots when a vendor changes its API. Majestic in particular answers errors
with **HTTP 200 and the reason in the body**, so code that trusts a status
code reads its refusal as data.

With keys set it makes a real call and checks the response carries the
fields the parser reads:

    MOZ_ACCESS_ID=... MOZ_SECRET_KEY=... python tools/verify_link_metrics.py
    MAJESTIC_API_KEY=... python tools/verify_link_metrics.py
    AHREFS_API_TOKEN=... python tools/verify_link_metrics.py

Nothing here is billed unless a key is supplied: without one, every call
is refused before any quota is touched.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import link_metrics  # noqa: E402

PASSED, FAILED, SKIPPED = [], [], []

# One URL, so a keyed run costs a single lookup rather than a batch.
SAMPLE = 'https://example.com/'

# What each parser reads back out. A vendor that stops sending one of these
# is the failure this tool exists to catch early.
READS = ('provider', 'page_authority', 'domain_authority',
         'external_links', 'linking_domains', 'spam_score')


def check(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def skip(name, why):
    SKIPPED.append(name)
    print(f'  SKIP  {name} - {why}')


def refusal(name, call):
    """A call with no credentials must raise MetricsError, not return junk.

    An integration that turns a refusal into an empty result is worse than
    one that fails: empty reads as "this page has no backlinks", which is a
    finding, and a wrong one.
    """
    try:
        out = call()
    except link_metrics.MetricsError as exc:
        message = str(exc)
        check(f'{name}: refuses without a key', True, message[:72])
        check(f'{name}: the refusal says what to do',
              len(message) > 20 and 'None' not in message, message[:72])
        return
    except Exception as exc:
        check(f'{name}: refuses without a key', False,
              f'raised {type(exc).__name__}: {str(exc)[:60]}')
        return
    check(f'{name}: refuses without a key', False,
          f'returned {type(out).__name__} instead of raising: {str(out)[:60]}')


def success(name, call):
    try:
        rows = call()
    except link_metrics.MetricsError as exc:
        check(f'{name}: answers with a key', False, str(exc)[:90])
        return
    check(f'{name}: answers with a key', bool(rows), f'{len(rows or {})} row(s)')
    if not rows:
        return
    row = next(iter(rows.values()))
    missing = [field for field in READS if field not in row]
    check(f'{name}: the response carries the fields the parser reads',
          not missing, f'missing: {missing}')
    numeric = [f for f in ('external_links', 'linking_domains')
               if row.get(f) is not None and not isinstance(row[f], (int, float))]
    check(f'{name}: counts come back as numbers', not numeric,
          f'not numeric: {numeric}')


def main(live):
    print(__doc__.strip().split('\n')[0])

    moz_id = os.environ.get('MOZ_ACCESS_ID', '')
    moz_secret = os.environ.get('MOZ_SECRET_KEY', '')
    majestic = os.environ.get('MAJESTIC_API_KEY', '')
    ahrefs = os.environ.get('AHREFS_API_TOKEN', '')

    print('\nMoz:')
    if moz_id and moz_secret:
        success('Moz', lambda: link_metrics.moz_metrics([SAMPLE], moz_id, moz_secret))
    elif live:
        refusal('Moz', lambda: link_metrics.moz_metrics([SAMPLE], 'none', 'none'))
    else:
        skip('Moz', 'no key, and --offline was given')

    print('\nMajestic:')
    if majestic:
        success('Majestic', lambda: link_metrics.majestic_metrics([SAMPLE], majestic))
    elif live:
        # The interesting one: HTTP 200 with the error in the body.
        refusal('Majestic', lambda: link_metrics.majestic_metrics([SAMPLE], 'none'))
    else:
        skip('Majestic', 'no key, and --offline was given')

    print('\nAhrefs:')
    if ahrefs:
        success('Ahrefs', lambda: link_metrics.ahrefs_metrics(SAMPLE, ahrefs))
    elif live:
        refusal('Ahrefs', lambda: link_metrics.ahrefs_metrics(SAMPLE, 'none'))
    else:
        skip('Ahrefs', 'no key, and --offline was given')

    print('\n' + '=' * 64)
    print(f'  {len(PASSED)} passed, {len(FAILED)} failed, {len(SKIPPED)} skipped')
    if FAILED:
        print('  failed: ' + ', '.join(FAILED))
    if not (moz_id or majestic or ahrefs):
        print('  NOTE: no vendor key was set, so only the refusal paths were\n'
              '        checked. Those are real calls to the real services. The\n'
              '        success path needs a paid subscription; set a key above\n'
              '        and run this again to prove it in one command.')
    return 1 if FAILED else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--offline', action='store_true',
                        help='skip the live refusal checks (no network)')
    sys.exit(main(live=not parser.parse_args().offline))
