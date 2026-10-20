#!/usr/bin/env python3
"""Check a connected Google account against Google's own published schema.

Everything about this integration can be verified without credentials except
one thing: whether a *successful* response carries the fields the code reads.
A Search Console property cannot be read without authorisation on it, so that
check waits for someone with an account — and waiting checks are checks that
never get run.

So this is the check, as one command. Point it at a connected account and it
makes the real calls, fetches Google's Discovery documents, and compares what
came back against what Google says should come back — every field the code
reads, every enum value it branches on.

    python tools/verify_google.py                    # the account in the store
    python tools/verify_google.py --adc              # a gcloud login instead
    python tools/verify_google.py --property sc-domain/example.com

It prints a line per check and exits non-zero if any fail. Nothing is written
anywhere; no credential is printed.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import google_oauth  # noqa: E402

DISCOVERY = {
    'searchconsole': 'https://searchconsole.googleapis.com/$discovery/rest?version=v1',
    'analyticsadmin': 'https://analyticsadmin.googleapis.com/$discovery/rest?version=v1beta',
}

PASSED, FAILED, SKIPPED = [], [], []


def check(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def skip(name, why):
    SKIPPED.append(name)
    print(f"  SKIP  {name} - {why}")


def mask(value):
    """Enough of an address to recognise, not enough to leak."""
    value = str(value or '')
    if '@' not in value:
        return value[:3] + '...' if len(value) > 3 else value
    user, host = value.split('@', 1)
    return f'{user[:2]}{"*" * max(len(user) - 2, 0)}@{host}'


def fetch_discovery(which):
    with urllib.request.urlopen(DISCOVERY[which], timeout=30) as response:
        return json.loads(response.read().decode('utf-8'))


def schema_properties(document, name):
    return set(((document.get('schemas') or {}).get(name) or {}).get('properties') or {})


def schema_enum(document, name, field):
    prop = (((document.get('schemas') or {}).get(name) or {})
            .get('properties') or {}).get(field) or {}
    return set(prop.get('enum') or [])


# ---------------------------------------------------------------------------
# getting a token
# ---------------------------------------------------------------------------

def token_from_adc():
    path = os.path.join(os.path.expanduser('~'), '.config', 'gcloud',
                        'application_default_credentials.json')
    try:
        with open(path, encoding='utf-8') as handle:
            adc = json.load(handle)
    except (OSError, ValueError):
        return None, 'no gcloud application-default credentials on this machine'
    if adc.get('type') != 'authorized_user':
        return None, 'those gcloud credentials are a service account, which cannot read Search Console'
    try:
        got = google_oauth.refresh_access_token(
            adc['refresh_token'], adc['client_id'], adc['client_secret'])
    except google_oauth.OAuthError as exc:
        return None, str(exc)
    return got['access_token'], ''


def token_from_store(user_id, secret_key):
    from src.core import token_store
    stored = token_store.load(user_id, secret_key or token_store.app_secret())
    if not stored or not stored.get('refresh_token'):
        return None, 'no Google account is connected (Settings > Google)'
    client_id = os.environ.get('GOOGLE_CLIENT_ID', '')
    client_secret = os.environ.get('GOOGLE_CLIENT_SECRET', '')
    if not google_oauth.configured(client_id, client_secret):
        return None, 'GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set here'
    try:
        got = google_oauth.refresh_access_token(
            stored['refresh_token'], client_id, client_secret)
    except google_oauth.OAuthError as exc:
        return None, str(exc)
    return got['access_token'], ''


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------

def verify_identity(token):
    print('\nthe account:')
    email = google_oauth.account_email(token)
    check('userinfo returns an address', bool(email) and '@' in email, mask(email))


def verify_search_console(token, console_doc):
    print('\nSearch Console:')
    raw = None
    try:
        raw = google_oauth._get(google_oauth.SITES_ENDPOINT, token)
    except google_oauth.OAuthError as exc:
        skip('Search Console responds', str(exc))
        print('       (this is the check that needs an account authorised on a '
              'property; everything above it passed)')
        return

    check('Search Console responds', True)
    entries = raw.get('siteEntry') or []
    check('the response carries siteEntry, as SitesListResponse documents',
          'siteEntry' in raw, str(sorted(raw)))
    if not entries:
        skip('a property can be parsed', 'this account has no Search Console properties')
        return

    documented = schema_properties(console_doc, 'WmxSite')
    seen = set()
    for entry in entries:
        seen |= set(entry)
    check('every field Google sent is one WmxSite documents',
          seen <= documented, f'unexpected: {sorted(seen - documented)}')
    check('the fields this code reads were present',
          {'siteUrl', 'permissionLevel'} <= seen,
          f'missing: {sorted({"siteUrl", "permissionLevel"} - seen)}')

    documented_levels = schema_enum(console_doc, 'WmxSite', 'permissionLevel')
    sent_levels = {e.get('permissionLevel') for e in entries if e.get('permissionLevel')}
    # Google spells this enum two ways across its own documents, so compare
    # normalised rather than literally.
    def norm(value):
        return ''.join(c for c in str(value).upper() if c.isalnum())
    unknown = {lvl for lvl in sent_levels
               if norm(lvl) not in {norm(d) for d in documented_levels}}
    check('every permissionLevel sent is one the schema defines',
          not unknown, f'unknown: {sorted(unknown)}')

    rows = google_oauth.search_console_properties(token)
    check('the parser returned one row per property',
          len(rows) == len([e for e in entries if e.get('siteUrl')]),
          f'{len(rows)} rows from {len(entries)} entries')
    check('every row has an id, a label and a kind',
          all(r.get('id') and r.get('label') and r.get('kind') for r in rows))
    check('domain properties are labelled without the sc-domain: prefix',
          all(not r['label'].startswith('sc-domain:') for r in rows),
          str([r['label'] for r in rows if r['label'].startswith('sc-domain:')]))

    unreadable = [r for r in rows if not r['can_read']]
    print(f'       {len(rows)} propert{"y" if len(rows) == 1 else "ies"}, '
          f'{len(unreadable)} not readable by this account')


def verify_analytics(token, admin_doc):
    print('\nAnalytics:')
    raw = None
    try:
        raw = google_oauth._get(google_oauth.GA_ADMIN_ENDPOINT, token)
    except google_oauth.OAuthError as exc:
        skip('Analytics responds', str(exc))
        return

    check('Analytics responds', True)
    summaries = raw.get('accountSummaries') or []
    if not summaries:
        skip('a GA4 property can be parsed', 'this account has no GA4 properties')
        return

    account_fields = schema_properties(
        admin_doc, 'GoogleAnalyticsAdminV1betaAccountSummary')
    property_fields = schema_properties(
        admin_doc, 'GoogleAnalyticsAdminV1betaPropertySummary')

    seen_account, seen_property = set(), set()
    for summary in summaries:
        seen_account |= set(summary)
        for prop in summary.get('propertySummaries') or []:
            seen_property |= set(prop)

    check('every account field Google sent is documented',
          seen_account <= account_fields,
          f'unexpected: {sorted(seen_account - account_fields)}')
    check('every property field Google sent is documented',
          seen_property <= property_fields,
          f'unexpected: {sorted(seen_property - property_fields)}')
    check('the fields this code reads were present',
          {'displayName', 'propertySummaries'} <= seen_account
          and {'property', 'displayName'} <= seen_property)

    rows = google_oauth.analytics_properties(token)
    check('every row has a numeric id and a label',
          all(r['id'].isdigit() and r['label'] for r in rows),
          str([r for r in rows if not r['id'].isdigit()][:2]))
    if 'nextPageToken' in raw:
        check('more than one page was followed', len(rows) > len(
            [p for s in summaries for p in (s.get('propertySummaries') or [])]),
            'the first page was not the whole answer')
    print(f'       {len(rows)} GA4 propert{"y" if len(rows) == 1 else "ies"}')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--adc', action='store_true',
                        help='use a gcloud application-default login')
    parser.add_argument('--user-id', type=int, default=1,
                        help='whose connected account to check (default 1)')
    parser.add_argument('--secret-key', default='',
                        help='the app SECRET_KEY, if not in the environment')
    args = parser.parse_args()

    if args.adc:
        token, why = token_from_adc()
    else:
        token, why = token_from_store(args.user_id, args.secret_key)
        if not token:
            print(f'No connected account ({why}); trying a gcloud login instead.')
            token, why = token_from_adc()

    if not token:
        print(f'Could not get a token: {why}')
        print('\nConnect an account in Settings > Google, or run:')
        print('  gcloud auth application-default login --scopes='
              '"https://www.googleapis.com/auth/webmasters.readonly,'
              'https://www.googleapis.com/auth/analytics.readonly,openid,'
              'https://www.googleapis.com/auth/userinfo.email"')
        return 2

    print('Fetching Google\'s Discovery documents...')
    try:
        console_doc = fetch_discovery('searchconsole')
        admin_doc = fetch_discovery('analyticsadmin')
    except Exception as exc:
        print(f'Could not fetch them ({type(exc).__name__}); is this machine online?')
        return 2

    verify_identity(token)
    verify_search_console(token, console_doc)
    verify_analytics(token, admin_doc)

    print('\n' + '=' * 64)
    print(f'  {len(PASSED)} passed, {len(FAILED)} failed, {len(SKIPPED)} skipped')
    if FAILED:
        print('  failed: ' + ', '.join(FAILED))
    if SKIPPED:
        print('  skipped: ' + ', '.join(SKIPPED))
        print('  A skip means the account could not reach that API, not that the '
              'code is wrong.')
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
