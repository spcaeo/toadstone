#!/usr/bin/env python3
"""Spelling and grammar: the mapping, the grouping, and what must be ignored.

The hard part of a spell check on a website is not finding mistakes, it is
not reporting things that are not mistakes. Most of these tests are about the
second half, because a check that flags every product name gets switched off,
and a check that is switched off finds nothing.

No network: the fetch tests inject a fake opener.
"""

import io
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import language_checks as lc  # noqa: E402
from src.utils.issue_factory import EVIDENCE_FIELDS, SEVERITIES  # noqa: E402

PASSED, FAILED = [], []
URL = 'https://site.test/a'


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def match(word='recieve', category='TYPOS', replacements=('receive',), message='Possible typo'):
    return {
        'message': message,
        'replacements': [{'value': r} for r in replacements],
        'context': {'text': f'We will {word} it soon', 'offset': 8, 'length': len(word)},
        'rule': {'id': 'MORFOLOGIK_RULE', 'category': {'id': category}},
    }


def slugs(issues):
    return sorted(i['check'] for i in issues)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def opener_for(payload):
    def send(request, timeout=None):
        return FakeResponse(json.dumps(payload).encode('utf-8'))
    return send


def failing_opener(code):
    def send(request, timeout=None):
        raise urllib.error.HTTPError(lc.PUBLIC_ENDPOINT, code, 'no', {}, None)
    return send


def test_text_cleaning_drops_what_a_dictionary_cannot_judge():
    """Every one of these would be a false positive on a technical site."""
    cases = [
        ('a URL', 'Visit https://site.test/some-slug for more', 'site.test'),
        ('an email', 'Write to hello@site.test today', 'hello@site.test'),
        ('inline code', 'Call `getUserById` to fetch it', 'getUserById'),
        ('a camelCase identifier', 'The parseConfig helper does it', 'parseConfig'),
        ('a snake_case identifier', 'Set the max_retries value', 'max_retries'),
        ('a filename', 'Edit the settings.json file', 'settings.json'),
        ('an HTML tag', 'Use the <amp-img> component', '<amp-img>'),
        ('a template placeholder', 'Shows {{user.name}} here', '{{user.name}}'),
        ('a very long token', 'Key ' + 'x' * 50 + ' ends', 'x' * 50),
    ]
    for name, text, removed in cases:
        cleaned = lc.clean_for_checking(text)
        result(f'cleaning: {name} is removed before checking',
               removed not in cleaned, f'{cleaned!r}')

    keeps = lc.clean_for_checking('This is ordinary prose that should survive intact.')
    result('cleaning: ordinary prose survives',
           'ordinary prose that should survive' in keeps, keeps)
    result('cleaning: empty input is handled',
           lc.clean_for_checking('') == '' and lc.clean_for_checking(None) == '', 'ok')


def test_categories_map_to_slugs_and_severities():
    for category, slug, severity in (
        ('TYPOS', 'spelling_mistake', 'warning'),
        ('GRAMMAR', 'grammar_mistake', 'warning'),
        ('CONFUSED_WORDS', 'grammar_mistake', 'warning'),
        ('PUNCTUATION', 'punctuation_mistake', 'info'),
        ('CASING', 'casing_mistake', 'info'),
        ('COLLOQUIALISMS', 'style_suggestion', 'info'),
    ):
        issues = lc.issues_for_page(URL, [match(category=category)])
        result(f'mapping: {category} becomes {slug} at {severity}',
               issues and issues[0]['check'] == slug and issues[0]['type'] == severity,
               str([(i['check'], i['type']) for i in issues]))

    result('mapping: style suggestions are never errors',
           all(i['type'] != 'error'
               for i in lc.issues_for_page(URL, [match(category='STYLE')])), 'ok')


def test_matches_group_per_page():
    """Forty typos on one page is one issue with examples, not forty issues."""
    many = [match(word=f'teh{n}') for n in range(40)]
    issues = lc.issues_for_page(URL, many)
    result('grouping: forty typos become one issue', len(issues) == 1, str(len(issues)))
    result('grouping: the count is stated', '40 spelling' in issues[0]['details'],
           issues[0]['details'][:80])
    result('grouping: a handful of examples travel with it',
           issues[0]['details'].count('->') <= lc.DEFAULTS['max_examples'],
           issues[0]['details'][:160])

    mixed = lc.issues_for_page(URL, [match(category='TYPOS'), match(category='GRAMMAR')])
    result('grouping: different categories stay separate issues',
           slugs(mixed) == ['grammar_mistake', 'spelling_mistake'], str(slugs(mixed)))


def test_the_ignore_list_is_honoured():
    """Every site has words a general dictionary will never know."""
    brand = [match(word='Quillmark')]
    result('ignore: an unknown brand name is reported by default',
           lc.issues_for_page(URL, brand), str(slugs(lc.issues_for_page(URL, brand))))
    result('ignore: the same word on the ignore list is silent',
           lc.issues_for_page(URL, brand, {'ignore': ['Quillmark']}) == [], 'ok')
    result('ignore: matching is case-insensitive',
           lc.issues_for_page(URL, brand, {'ignore': ['quillmark']}) == [], 'ok')
    result('ignore: an unrelated ignore entry changes nothing',
           lc.issues_for_page(URL, brand, {'ignore': ['something-else']}), 'ok')

    result('ignore: a whole category can be silenced',
           lc.issues_for_page(URL, [match(category='TYPOGRAPHY')],
                              {'skip_categories': ['TYPOGRAPHY']}) == [], 'ok')


def test_a_clean_page_is_silent():
    for empty in ([], None):
        result(f'quiet: {empty} matches produce no issues',
               lc.issues_for_page(URL, empty) == [], 'ok')
    result('quiet: junk matches do not raise',
           lc.issues_for_page(URL, ['not a dict', {}, {'rule': None}]) is not None, 'ok')


def test_fetching():
    payload = {'matches': [match()]}
    got = lc.fetch_matches('We will recieve it', opener=opener_for(payload))
    result('fetch: matches come back', len(got.get('matches', [])) == 1, str(got)[:80])

    result('fetch: empty text is not sent anywhere',
           lc.fetch_matches('', opener=opener_for(payload)) == {'matches': []}, 'ok')

    limited = lc.fetch_matches('x', opener=failing_opener(429))
    result('fetch: rate limiting is named as such',
           'rate limited' in limited.get('error', ''), str(limited))

    down = lc.fetch_matches('x', opener=failing_opener(503))
    result('fetch: a server error is reported', 'HTTP 503' in down.get('error', ''), str(down))


def test_an_unreachable_server_says_nothing_about_the_page():
    """A check that could not run must not look like a page with no mistakes."""
    page = {'url': URL, 'main_text': 'We will recieve it soon.', 'lang': 'en'}
    got = lc.check_page(page, {'enabled': True}, opener=failing_opener(503))
    result('failure: an unreachable server produces no findings about the page',
           got == [], str(got))


def test_end_to_end_and_defaults():
    page = {'url': URL, 'main_text': 'We will recieve it soon.', 'lang': 'en-GB'}
    issues = lc.check_page(page, {'enabled': True}, opener=opener_for({'matches': [match()]}))
    result('end to end: a page with a typo reports one',
           slugs(issues) == ['spelling_mistake'], str(slugs(issues)))
    result('end to end: evidence and fix are present',
           issues and issues[0]['how_to_fix'] and issues[0]['evidence'].get('found'),
           str(issues[0]['evidence']) if issues else None)
    result('end to end: only known evidence fields are used',
           all(set(i['evidence']) <= set(EVIDENCE_FIELDS) for i in issues), 'ok')
    result('end to end: severities are real',
           all(i['type'] in SEVERITIES for i in issues), 'ok')

    result('default: the check is off unless switched on',
           lc.DEFAULTS['enabled'] is False
           and lc.check_page(page, None, opener=opener_for({'matches': [match()]})) == [], 'ok')
    result('default: a page with no text is skipped',
           lc.check_page({'url': URL, 'main_text': ''}, {'enabled': True},
                         opener=opener_for({'matches': [match()]})) == [], 'ok')


TESTS = (
    test_text_cleaning_drops_what_a_dictionary_cannot_judge,
    test_categories_map_to_slugs_and_severities,
    test_matches_group_per_page,
    test_the_ignore_list_is_honoured,
    test_a_clean_page_is_silent,
    test_fetching,
    test_an_unreachable_server_says_nothing_about_the_page,
    test_end_to_end_and_defaults,
)


def main():
    for test in TESTS:
        print(f'\n{test.__name__}:')
        try:
            test()
        except Exception as exc:  # a raised test is a failed test
            result(test.__name__, False, f'raised {type(exc).__name__}: {exc}')

    print('\n' + '=' * 64)
    print(f'  {len(PASSED)} passed, {len(FAILED)} failed')
    if FAILED:
        print('  failed: ' + ', '.join(FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
