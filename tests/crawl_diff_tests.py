"""Deterministic tests for crawl-to-crawl diffing (src/core/crawl_diff.py).

Builds crawls in a throwaway sqlite database - no network, no fixture server,
no test framework:

    python tests/crawl_diff_tests.py

Each test pins down a promise the diff makes to the site owner:

  1. a sentence deleted between two crawls is reported, with the text that
     went and an id that stays the same while the deletion stays unfixed
  2. a page that vanished and a page that appeared are both listed, and
     neither is mistaken for edited content
  3. an internal link that disappeared is reported once per source page,
     while a vanished external link is ignored
  4. a page whose copy only changed case or whitespace produces nothing, and
     neither does a page with no stored body text
  5. the regression report sorts issues into new / fixed / still open by their
     stable ids, so a fixed issue never comes back as new
  6. previous_crawl() skips unfinished crawls and other domains
  7. first_seen_map() reports the earliest crawl that saw each issue
"""
import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import crawl_db  # noqa: E402
from src.core import crawl_diff  # noqa: E402
from src.utils.issue_factory import issue_id, make_issue  # noqa: E402
from src.utils.text_fingerprint import text_hash  # noqa: E402

PASSED, FAILED = [], []

DOMAIN = 'diff.test'
SITE = 'http://diff.test'

# The home page copy is rewritten between crawls in ways that are not edits:
# different casing, a doubled space, an extra blank line.
HOME_BEFORE = ('Welcome to the clinic software company.\n'
               'We work with hospitals across Europe.')
HOME_AFTER = ('Welcome to the CLINIC software company.\n\n'
              'We  work with hospitals across Europe.')

# The services page loses one sentence and keeps the rest.
SERVICES_BEFORE = ('We build software for hospitals and clinics.\n'
                   'Our team has shipped forty EHR integrations since 2019.\n'
                   'Book a discovery call with an engineer today.')
SERVICES_AFTER = ('We build software for hospitals and clinics.\n'
                  'Book a discovery call with an engineer today.')
REMOVED_SENTENCE = 'Our team has shipped forty EHR integrations since 2019.'


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


@contextlib.contextmanager
def temp_database():
    """Point crawl_db at a throwaway database for the duration of a test."""
    original_db = crawl_db.DB_FILE
    handle, path = tempfile.mkstemp(suffix='.db')
    os.close(handle)
    try:
        crawl_db.DB_FILE = path
        crawl_db.init_crawl_tables()
        yield
    finally:
        crawl_db.DB_FILE = original_db
        os.unlink(path)


def page(url, main_text=None):
    """One crawled page row, fingerprinted the way the crawler stores it."""
    return {
        'url': url,
        'status_code': 200,
        'is_internal': True,
        'main_text': main_text,
        'main_text_hash': text_hash(main_text) if main_text else None,
        'headings': [],
    }


def link(source, target, anchor='link', internal=True):
    return {'source_url': source, 'target_url': target, 'anchor_text': anchor,
            'is_internal': internal}


def save_crawl(pages=(), links=(), issues=(), domain=DOMAIN, status='completed'):
    """Write one finished crawl into the temp database, return its id."""
    crawl_id = crawl_db.create_crawl(user_id=1, session_id='s', base_url=f'http://{domain}/',
                                     base_domain=domain, config_snapshot={})
    crawl_db.save_url_batch(crawl_id, list(pages))
    crawl_db.save_links_batch(crawl_id, list(links))
    crawl_db.save_issues_batch(crawl_id, list(issues))
    crawl_db.set_crawl_status(crawl_id, status)
    return crawl_id


def two_crawls():
    """The before/after pair every content test in this file works on."""
    previous_id = save_crawl(
        pages=[page(f'{SITE}/', HOME_BEFORE),
               page(f'{SITE}/services', SERVICES_BEFORE),
               page(f'{SITE}/old', 'This page explains the legacy onboarding process in full.'),
               page(f'{SITE}/no-text', None)],
        links=[link(f'{SITE}/', f'{SITE}/services', 'Services'),
               link(f'{SITE}/', f'{SITE}/pricing', 'Pricing'),
               link(f'{SITE}/', 'https://partner.example/', 'Partner', internal=False),
               link(f'{SITE}/services', f'{SITE}/', 'Home')])
    current_id = save_crawl(
        pages=[page(f'{SITE}/', HOME_AFTER),
               page(f'{SITE}/services', SERVICES_AFTER),
               page(f'{SITE}/new', 'A brand new case study about a radiology client.'),
               page(f'{SITE}/no-text', None)],
        links=[link(f'{SITE}/', f'{SITE}/services', 'Services'),
               link(f'{SITE}/', f'{SITE}/new', 'Case study'),
               link(f'{SITE}/services', f'{SITE}/', 'Home')])
    return previous_id, current_id


def issues_by_check(issues):
    grouped = {}
    for issue in issues:
        grouped.setdefault(issue['check'], []).append(issue)
    return grouped


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_removed_sentence_is_reported():
    """A sentence deleted between crawls becomes one Content Removed issue."""
    with temp_database():
        previous_id, current_id = two_crawls()
        issues = issues_by_check(crawl_diff.content_integrity_issues(previous_id, current_id))
        removed = issues.get('content_removed', [])

        result('content: one issue, on the page that lost copy',
               len(removed) == 1 and removed[0]['url'] == f'{SITE}/services',
               str([(i['url'], i['details']) for i in removed]))
        if not removed:
            return

        issue = removed[0]
        result('content: the removed sentence travels as evidence',
               REMOVED_SENTENCE in issue['evidence'].get('snippet', ''),
               str(issue['evidence']))
        result('content: shaped like every other issue',
               (issue['type'] == 'warning' and issue['category'] == 'Content'
                and issue['issue'] == 'Content Removed' and bool(issue['how_to_fix'])),
               str({k: issue.get(k) for k in ('type', 'category', 'issue')}))
        result('content: the count is in the details',
               '1 sentence' in issue['details'] and str(previous_id) in issue['details'],
               issue['details'])
        result('content: id is sha1(url + check + hash of the removed text)',
               issue['id'] == issue_id(f'{SITE}/services', 'content_removed',
                                       text_hash(REMOVED_SENTENCE)[:16]),
               issue['id'])

        again = crawl_diff.content_integrity_issues(previous_id, current_id)
        result('content: the same deletion keeps the same id across runs',
               [i['id'] for i in again if i['check'] == 'content_removed'] == [issue['id']])


def test_page_gone_and_page_new_are_listed():
    """A page that disappeared is reported as gone, not as edited content."""
    with temp_database():
        previous_id, current_id = two_crawls()
        diff = crawl_diff.diff_pages(crawl_db.load_crawled_urls(previous_id),
                                     crawl_db.load_crawled_urls(current_id))

        result('pages: the missing page is listed as gone',
               diff['pages_gone'] == [f'{SITE}/old'], str(diff['pages_gone']))
        result('pages: the added page is listed as new',
               diff['pages_new'] == [f'{SITE}/new'], str(diff['pages_new']))
        result('pages: a gone page is not also reported as removed content',
               all(entry['url'] != f'{SITE}/old' for entry in diff['text_removed']),
               str([e['url'] for e in diff['text_removed']]))
        result('pages: only the edited page counts as changed',
               [e['url'] for e in diff['text_changed']] == [f'{SITE}/services'],
               str([e['url'] for e in diff['text_changed']]))

        changed = diff['text_changed'][0] if diff['text_changed'] else {}
        result('pages: the change carries both hashes and a similarity score',
               (changed.get('previous_hash') != changed.get('current_hash')
                and 0.0 <= changed.get('similarity', -1) <= 1.0),
               str({k: changed.get(k) for k in ('previous_hash', 'current_hash', 'similarity')}))


def test_internal_link_removed_is_reported():
    """A vanished internal link is an issue; a vanished external link is not."""
    with temp_database():
        previous_id, current_id = two_crawls()
        diff = crawl_diff.diff_links(crawl_db.load_crawl_links(previous_id),
                                     crawl_db.load_crawl_links(current_id))

        result('links: the dropped internal link is reported',
               [(l['source_url'], l['target_url']) for l in diff['links_removed']]
               == [(f'{SITE}/', f'{SITE}/pricing')], str(diff['links_removed']))
        result('links: the dropped external link is ignored',
               all('partner.example' not in l['target_url'] for l in diff['links_removed']),
               str(diff['links_removed']))
        result('links: the new link is reported as added',
               [(l['source_url'], l['target_url']) for l in diff['links_added']]
               == [(f'{SITE}/', f'{SITE}/new')], str(diff['links_added']))

        issues = issues_by_check(crawl_diff.content_integrity_issues(previous_id, current_id))
        link_issues = issues.get('internal_link_removed', [])
        result('links: one issue per source page',
               len(link_issues) == 1 and link_issues[0]['url'] == f'{SITE}/',
               str([(i['url'], i['details']) for i in link_issues]))
        if not link_issues:
            return

        issue = link_issues[0]
        result('links: the lost targets travel as evidence',
               issue['evidence'].get('related_urls') == [f'{SITE}/pricing'],
               str(issue['evidence']))
        result('links: shaped like every other issue',
               (issue['type'] == 'warning' and issue['category'] == 'Links'
                and issue['issue'] == 'Internal Link Removed' and bool(issue['how_to_fix'])),
               str({k: issue.get(k) for k in ('type', 'category', 'issue')}))
        result('links: id is sha1(url + check + sorted targets)',
               issue['id'] == issue_id(f'{SITE}/', 'internal_link_removed', f'{SITE}/pricing'),
               issue['id'])


def test_unchanged_pages_produce_nothing():
    """Case, whitespace and missing body text are not content changes."""
    with temp_database():
        previous_id, current_id = two_crawls()
        diff = crawl_diff.diff_pages(crawl_db.load_crawled_urls(previous_id),
                                     crawl_db.load_crawled_urls(current_id))
        touched = {entry['url'] for entry in diff['text_changed']}

        result('unchanged: re-cased, re-spaced copy is not a change',
               f'{SITE}/' not in touched, str(sorted(touched)))
        result('unchanged: a page with no stored body text is skipped',
               f'{SITE}/no-text' not in touched, str(sorted(touched)))

        issues = crawl_diff.content_integrity_issues(previous_id, current_id)
        result('unchanged: no issue for either page',
               not [i for i in issues if i['url'] in (f'{SITE}/', f'{SITE}/no-text')
                    and i['check'] == 'content_removed'],
               str([(i['url'], i['check']) for i in issues]))

        # A crawl compared against itself is the strongest form of "nothing changed".
        result('unchanged: a crawl compared with itself reports nothing',
               crawl_diff.content_integrity_issues(current_id, current_id) == [])


def test_no_previous_crawl_means_no_issues():
    """A first crawl has removed nothing, so it reports nothing removed."""
    with temp_database():
        current_id = save_crawl(pages=[page(f'{SITE}/', SERVICES_BEFORE)])
        result('first crawl: no content issues without a previous crawl',
               crawl_diff.content_integrity_issues(None, current_id) == [])
        result('first crawl: nothing to compare against',
               crawl_diff.previous_crawl(DOMAIN, current_id) is None)


def test_regression_report_sorts_issues_by_stable_id():
    """One issue fixed, one new, one still open - keyed by stable id."""
    with temp_database():
        persists = make_issue(f'{SITE}/a', 'missing_title', 'warning', 'Meta',
                              'Missing Title', 'This page has no title tag.')
        fixed = make_issue(f'{SITE}/b', 'links_to_broken_urls', 'error', 'Links',
                           'Broken Link', 'This page links to a 404.',
                           offending_value=f'{SITE}/gone')
        appeared = make_issue(f'{SITE}/c', 'missing_h1', 'error', 'Headings',
                              'Missing H1', 'This page has no H1.')

        previous_id = save_crawl(pages=[page(f'{SITE}/a', SERVICES_BEFORE)],
                                 issues=[persists, fixed])
        # The persisting issue is rebuilt from scratch, exactly as a second
        # crawl would build it: same inputs must produce the same id.
        rebuilt = make_issue(f'{SITE}/a', 'missing_title', 'warning', 'Meta',
                             'Missing Title', 'This page has no title tag.')
        current_id = save_crawl(pages=[page(f'{SITE}/a', SERVICES_AFTER)],
                                issues=[rebuilt, appeared])

        report = crawl_diff.regression_report(previous_id, current_id)
        result('regression: the new issue is new',
               [i['id'] for i in report['new']] == [appeared['id']],
               str([(i['url'], i['issue']) for i in report['new']]))
        result('regression: the resolved issue is fixed',
               [i['id'] for i in report['fixed']] == [fixed['id']],
               str([(i['url'], i['issue']) for i in report['fixed']]))
        result('regression: the unchanged issue is still open, not new',
               [i['id'] for i in report['still_open']] == [persists['id']],
               str([(i['url'], i['issue']) for i in report['still_open']]))
        result('regression: fixed entries come from the previous crawl',
               all(i['crawl_id'] == previous_id for i in report['fixed'])
               and all(i['crawl_id'] == current_id for i in report['new'] + report['still_open']),
               str([(i['issue'], i['crawl_id']) for i in
                    report['fixed'] + report['new'] + report['still_open']]))
        result('regression: counts match the lists',
               report['counts']['new'] == 1 and report['counts']['fixed'] == 1
               and report['counts']['still_open'] == 1, str(report['counts']))
        result('regression: new issues are broken down by severity',
               report['counts']['by_severity'] == {'error': 1, 'warning': 0, 'info': 0},
               str(report['counts']['by_severity']))

        without_previous = crawl_diff.regression_report(None, current_id)
        result('regression: with no previous crawl everything is new',
               without_previous['counts']['new'] == 2
               and without_previous['counts']['fixed'] == 0
               and without_previous['counts']['still_open'] == 0,
               str(without_previous['counts']))


def test_previous_crawl_skips_unfinished_crawls_and_other_domains():
    """Only completed crawls of the same domain are a baseline."""
    with temp_database():
        oldest = save_crawl(pages=[page(f'{SITE}/', HOME_BEFORE)])
        save_crawl(pages=[page(f'{SITE}/', HOME_BEFORE)], status='failed')
        save_crawl(pages=[page('http://other.test/', HOME_BEFORE)], domain='other.test')
        newest = save_crawl(pages=[page(f'{SITE}/', HOME_AFTER)])

        found = crawl_diff.previous_crawl(DOMAIN, newest)
        result('previous: the newest completed crawl of this domain wins',
               found is not None and found['id'] == oldest,
               str(found and found['id']))
        result('previous: the first crawl has no baseline',
               crawl_diff.previous_crawl(DOMAIN, oldest) is None)
        result('previous: a user filter that matches nothing finds nothing',
               crawl_diff.previous_crawl(DOMAIN, newest, user_id=99) is None)
        result('previous: an unknown domain finds nothing',
               crawl_diff.previous_crawl('nope.test', newest) is None)


def test_first_seen_map_reports_the_earliest_crawl():
    """An issue that has been around since crawl 1 says so."""
    with temp_database():
        longstanding = make_issue(f'{SITE}/a', 'missing_title', 'warning', 'Meta',
                                  'Missing Title', 'This page has no title tag.')
        newer = make_issue(f'{SITE}/b', 'missing_h1', 'error', 'Headings',
                           'Missing H1', 'This page has no H1.')

        first = save_crawl(pages=[page(f'{SITE}/a', HOME_BEFORE)], issues=[longstanding])
        second = save_crawl(pages=[page(f'{SITE}/a', HOME_BEFORE)], issues=[longstanding, newer])
        third = save_crawl(pages=[page(f'{SITE}/a', HOME_BEFORE)], issues=[longstanding, newer])
        current = save_crawl(pages=[page(f'{SITE}/a', HOME_AFTER)], status='running')

        seen = crawl_diff.first_seen_map(DOMAIN, current)
        result('first seen: the long-standing issue points at the first crawl',
               seen.get(longstanding['id']) == first, str(seen))
        result('first seen: the later issue points at the crawl that found it',
               seen.get(newer['id']) == second, str(seen))
        result('first seen: an issue nobody has reported yet is absent',
               'deadbeef' not in seen)

        recent_only = crawl_diff.first_seen_map(DOMAIN, current, history=1)
        result('first seen: history limits how far back it looks',
               recent_only.get(longstanding['id']) == third, str(recent_only))


TESTS = (
    test_removed_sentence_is_reported,
    test_page_gone_and_page_new_are_listed,
    test_internal_link_removed_is_reported,
    test_unchanged_pages_produce_nothing,
    test_no_previous_crawl_means_no_issues,
    test_regression_report_sorts_issues_by_stable_id,
    test_previous_crawl_skips_unfinished_crawls_and_other_domains,
    test_first_seen_map_reports_the_earliest_crawl,
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
