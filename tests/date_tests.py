"""Deterministic tests for the dates-and-freshness checks.

No network, no crawler, no test framework - every page is an in-memory dict
shaped like a crawl result, and "now" is injected so a passing run today still
passes next year:

    python tests/date_tests.py

Each test pins down one way a CMS lies about dates:

  1. every date format Toadstone meets parses, and nothing else raises
  2. a page whose five sources agree reports nothing at all
  3. only a page with an Article-ish JSON-LD block is asked for dates
  4. a date that will not parse, and a naive ISO date, are both format issues
  5. an HTTP Last-Modified two hours off JSON-LD is not a mismatch; a visible
     "Updated on ..." three months off is
  6. stale content is reported only for pages the sitemap lists
  7. future dates and modified-before-published are errors
  8. issue ids are stable across runs and distinct per offending value
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.date_checks import (  # noqa: E402
    article_node,
    date_issues,
    date_issues_for_crawl,
    extract_page_dates,
    parse_date,
    parse_date_detailed,
)
from src.utils.issue_factory import issue_id  # noqa: E402

NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)
URL = 'https://example.com/blog/post'

PASSED, FAILED = [], []


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def page(json_ld=None, meta=None, last_modified=None, main_text='', url=URL):
    """A crawl result carrying only the fields these checks read."""
    return {
        'url': url,
        'status_code': 200,
        'content_type': 'text/html',
        'json_ld': json_ld if json_ld is not None else [],
        'meta_tags': meta or {},
        'last_modified': last_modified,
        'main_text': main_text,
        'headings': [],
    }


def article(published='2026-01-05T09:00:00+00:00',
            modified='2026-06-01T12:00:00+00:00', type_name='BlogPosting'):
    block = {'@context': 'https://schema.org', '@type': type_name}
    if published is not None:
        block['datePublished'] = published
    if modified is not None:
        block['dateModified'] = modified
    return [block]


def checks(issues):
    return sorted(i['check'] for i in issues)


def of(issues, check):
    return [i for i in issues if i['check'] == check]


def test_parse_date_accepts_every_format_we_meet():
    cases = (
        ('2026-01-05T09:30:00+00:00', datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc), True),
        ('2026-01-05T09:30:00Z', datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc), True),
        ('2026-01-05T09:30:00+0000', datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc), True),
        ('2026-01-05T09:30:00', datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc), False),
        ('2026-01-05', datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc), False),
        ('Mon, 05 Jan 2026 09:30:00 GMT', datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc), True),
        ('January 5, 2026', datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc), False),
        ('5 Jan 2026', datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc), False),
        ('5th January 2026', datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc), False),
    )
    for raw, expected, aware in cases:
        parsed, had_tz = parse_date_detailed(raw)
        result(f'parse: {raw!r}', parsed == expected and had_tz == aware,
               f'{parsed} (had_tz={had_tz})')

    junk = ('', '   ', 'yesterday', 'not a date', '0000-99-99', None, 42, [], {'a': 1})
    survived = True
    for value in junk:
        try:
            if parse_date(value) is not None:
                survived = False
                result(f'parse: junk {value!r} returns None', False, str(parse_date(value)))
        except Exception as exc:
            survived = False
            result(f'parse: junk {value!r} never raises', False,
                   f'{type(exc).__name__}: {exc}')
    result('parse: junk values return None and never raise', survived)

    # Timezone offsets are honoured, not dropped.
    result('parse: a non-UTC offset keeps its instant',
           parse_date('2026-01-05T09:30:00+05:30')
           == datetime(2026, 1, 5, 4, 0, tzinfo=timezone.utc),
           str(parse_date('2026-01-05T09:30:00+05:30')))


def test_extract_reads_every_source():
    p = page(
        json_ld=article(),
        meta={'article:published_time': '2026-01-05T09:00:00+00:00',
              'article:modified_time': '2026-06-01T12:00:00+00:00'},
        last_modified='Mon, 01 Jun 2026 12:00:00 GMT',
        main_text='Intro copy. Last updated: June 1, 2026. Body copy.',
    )
    dates = extract_page_dates(p, {URL: '2026-06-01T12:00:00+00:00'})

    expected_keys = {'jsonld_published', 'jsonld_modified', 'meta_modified',
                     'meta_published', 'http_last_modified', 'sitemap_lastmod',
                     'visible_updated'}
    result('extract: every source key is present',
           set(dates) == expected_keys, str(set(dates) ^ expected_keys))
    result('extract: every source was found',
           all(dates[k] is not None for k in expected_keys),
           str([k for k in expected_keys if dates[k] is None]))
    result('extract: entries carry raw, parsed and source',
           all(set(dates[k]) == {'raw', 'parsed', 'source'} and dates[k]['source'] == k
               for k in expected_keys))
    result('extract: the visible line yields its date',
           dates['visible_updated']['raw'] == 'June 1, 2026'
           and dates['visible_updated']['parsed']
           == datetime(2026, 6, 1, tzinfo=timezone.utc),
           str(dates['visible_updated']))
    result('extract: a page with no sitemap entry has no sitemap_lastmod',
           extract_page_dates(p, {})['sitemap_lastmod'] is None)
    result('extract: an empty page yields no sources at all',
           all(v is None for v in extract_page_dates({}).values()))


def test_a_consistent_page_reports_nothing():
    p = page(
        json_ld=article(),
        meta={'article:published_time': '2026-01-05T09:00:00+00:00',
              'article:modified_time': '2026-06-01T12:00:00+00:00'},
        last_modified='Mon, 01 Jun 2026 12:00:00 GMT',
        main_text='Last updated: June 1, 2026. The rest of the article.',
    )
    issues = date_issues(p, {URL: '2026-06-01T12:00:00+00:00'}, now=NOW)
    result('clean: a page whose sources agree reports no issues',
           issues == [], str(checks(issues)))


def test_missing_dates_only_for_article_like_json_ld():
    bare = page(json_ld=[{'@context': 'https://schema.org', '@type': 'BlogPosting',
                          'headline': 'A post'}])
    issues = of(date_issues(bare, now=NOW), 'missing_dates')
    result('missing: a BlogPosting with neither date is flagged once',
           len(issues) == 1, str(checks(date_issues(bare, now=NOW))))
    if issues:
        issue = issues[0]
        result('missing: severity, category and name match the contract',
               issue['type'] == 'warning' and issue['category'] == 'Freshness'
               and issue['issue'] == 'Missing Article Dates',
               f"{issue['type']}/{issue['category']}/{issue['issue']}")
        result('missing: both absent properties are named',
               'datePublished' in issue['details'] and 'dateModified' in issue['details'],
               issue['details'])

    half = page(json_ld=article(published=None))
    issues = of(date_issues(half, now=NOW), 'missing_dates')
    result('missing: only the absent property is reported',
           len(issues) == 1 and 'datePublished' in issues[0]['details']
           and 'dateModified' not in issues[0]['details'],
           str([i['details'] for i in issues]))

    org = page(json_ld=[{'@type': 'Organization', 'name': 'Example'}])
    result('missing: a non-article block is never asked for dates',
           of(date_issues(org, now=NOW), 'missing_dates') == [])
    result('missing: a page with no JSON-LD at all is never asked for dates',
           of(date_issues(page(), now=NOW), 'missing_dates') == [])

    graph = page(json_ld=[{'@context': 'https://schema.org', '@graph': [
        {'@type': 'Organization', 'name': 'Example'},
        {'@type': ['WebPage', 'AboutPage'], 'name': 'About'},
    ]}])
    result('missing: an article-ish node inside @graph still counts',
           len(of(date_issues(graph, now=NOW), 'missing_dates')) == 1,
           str(checks(date_issues(graph, now=NOW))))


def test_invalid_formats():
    unparseable = page(json_ld=article(modified='last Tuesday'))
    issues = of(date_issues(unparseable, now=NOW), 'invalid_date_format')
    result('format: an unparseable date is a warning quoting the raw value',
           len(issues) == 1 and issues[0]['type'] == 'warning'
           and issues[0]['evidence']['found'] == 'last Tuesday',
           str([i['evidence'] for i in issues]))

    naive = page(json_ld=article(modified='2026-06-01T12:00:00'))
    issues = of(date_issues(naive, now=NOW), 'invalid_date_format')
    dates = extract_page_dates(naive)
    result('format: a naive ISO date is flagged for its missing timezone',
           len(issues) == 1 and 'timezone' in issues[0]['details'],
           str([i['details'] for i in issues]))
    result('format: the naive date is still parsed, as UTC',
           dates['jsonld_modified']['parsed']
           == datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
           str(dates['jsonld_modified']))
    result('format: parsing it as UTC does not also trip a mismatch',
           of(date_issues(naive, now=NOW), 'modified_date_mismatch') == [])

    visible_only = page(json_ld=article(),
                        main_text='Updated on June 1, 2026 by the team.')
    result('format: a visible date is never held to a timezone',
           of(date_issues(visible_only, now=NOW), 'invalid_date_format') == [],
           str(of(date_issues(visible_only, now=NOW), 'invalid_date_format')))

    result('format: a malformed sitemap lastmod is left to the sitemap audit',
           of(date_issues(page(json_ld=article()), {URL: 'whenever'}, now=NOW),
              'invalid_date_format') == [])


def test_mismatch_tolerance():
    close = page(
        json_ld=article(modified='2026-06-01T12:00:00+00:00'),
        last_modified='Mon, 01 Jun 2026 14:00:00 GMT',
    )
    result('mismatch: an HTTP header two hours off JSON-LD is within tolerance',
           of(date_issues(close, now=NOW), 'modified_date_mismatch') == [],
           str(checks(date_issues(close, now=NOW))))

    far = page(
        json_ld=article(modified='2026-06-01T12:00:00+00:00'),
        last_modified='Mon, 01 Jun 2026 14:00:00 GMT',
        main_text='Updated on 3 March 2026 by the editorial team.',
    )
    issues = of(date_issues(far, now=NOW), 'modified_date_mismatch')
    result('mismatch: a visible date three months off does trip it',
           len(issues) == 1, str(checks(date_issues(far, now=NOW))))
    if issues:
        issue = issues[0]
        result('mismatch: severity, category and name match the contract',
               issue['type'] == 'warning' and issue['category'] == 'Freshness'
               and issue['issue'] == 'Modified Date Mismatch',
               f"{issue['type']}/{issue['category']}/{issue['issue']}")
        result('mismatch: the details list every source and its value',
               all(part in issue['details'] for part in
                   ('JSON-LD dateModified=2026-06-01T12:00:00+00:00',
                    'HTTP Last-Modified=Mon, 01 Jun 2026 14:00:00 GMT',
                    'visible "updated" text=3 March 2026')),
               issue['details'])
        result('mismatch: the evidence is a compact source=value list',
               issue['evidence']['found'] ==
               'jsonld_modified=2026-06-01T12:00:00+00:00; '
               'http_last_modified=Mon, 01 Jun 2026 14:00:00 GMT; '
               'visible_updated=3 March 2026',
               issue['evidence']['found'])

    four_sources = page(
        json_ld=article(modified='2026-06-01T12:00:00+00:00'),
        meta={'article:modified_time': '2026-05-01T12:00:00+00:00'},
        last_modified='Mon, 01 Jun 2026 12:00:00 GMT',
    )
    issues = of(date_issues(four_sources, {URL: '2026-02-01'}, now=NOW),
                'modified_date_mismatch')
    result('mismatch: four disagreeing sources are still one issue per page',
           len(issues) == 1, str(len(issues)))
    if issues:
        result('mismatch: the sitemap lastmod takes part in the comparison',
               'sitemap_lastmod=2026-02-01' in issues[0]['evidence']['found'],
               issues[0]['evidence']['found'])

    result('mismatch: one source alone can never disagree',
           of(date_issues(page(json_ld=article()), now=NOW),
              'modified_date_mismatch') == [])

    loose = date_issues(far, now=NOW, tolerance_hours=24 * 365)
    result('mismatch: the tolerance is configurable',
           of(loose, 'modified_date_mismatch') == [], str(checks(loose)))


def test_stale_content():
    old = page(json_ld=article(published='2019-01-01T00:00:00+00:00',
                               modified='2024-01-01T00:00:00+00:00'))
    issues = of(date_issues(old, {URL: '2024-01-01'}, now=NOW), 'stale_content')
    result('stale: an old page listed in the sitemap is info-severity',
           len(issues) == 1 and issues[0]['type'] == 'info'
           and issues[0]['issue'] == 'Stale Content',
           str([(i['type'], i['issue']) for i in issues]))
    if issues:
        result('stale: the id is keyed on the modified date itself',
               issues[0]['id'] == issue_id(URL, 'stale_content',
                                           '2024-01-01T00:00:00+00:00'),
               issues[0]['id'][:8])

    result('stale: the same page outside the sitemap is not reported',
           of(date_issues(old, {}, now=NOW), 'stale_content') == [])

    recent = page(json_ld=article(modified='2026-06-01T12:00:00+00:00'))
    result('stale: a recently modified page is not reported',
           of(date_issues(recent, {URL: '2026-06-01'}, now=NOW), 'stale_content') == [])

    result('stale: the window is configurable',
           len(of(date_issues(recent, {URL: '2026-06-01'}, now=NOW,
                              stale_after_months=1), 'stale_content')) == 1)


def test_future_dates_and_reversed_pairs():
    future = page(json_ld=article(modified='2027-01-01T00:00:00+00:00'))
    issues = of(date_issues(future, now=NOW), 'future_date')
    result('future: a date next year is an error',
           len(issues) == 1 and issues[0]['type'] == 'error'
           and issues[0]['issue'] == 'Future Date',
           str([(i['type'], i['issue']) for i in issues]))

    skew = page(json_ld=article(modified='2026-09-22T12:02:00+00:00'))
    result('future: two minutes of clock skew is not an error',
           of(date_issues(skew, now=NOW), 'future_date') == [],
           str(checks(date_issues(skew, now=NOW))))

    both = page(json_ld=article(published='2027-01-01T00:00:00+00:00',
                                modified='2027-02-01T00:00:00+00:00'))
    result('future: each future source is reported once',
           len(of(date_issues(both, now=NOW), 'future_date')) == 2,
           str(checks(date_issues(both, now=NOW))))

    reversed_pair = page(json_ld=article(published='2026-06-01T12:00:00+00:00',
                                         modified='2026-01-05T09:00:00+00:00'))
    issues = of(date_issues(reversed_pair, now=NOW), 'modified_before_published')
    result('order: modified before published is an error naming both values',
           len(issues) == 1 and issues[0]['type'] == 'error'
           and '2026-01-05T09:00:00+00:00' in issues[0]['details']
           and '2026-06-01T12:00:00+00:00' in issues[0]['details'],
           str([i['details'] for i in issues]))

    equal = page(json_ld=article(published='2026-06-01T12:00:00+00:00',
                                 modified='2026-06-01T12:00:00+00:00'))
    result('order: equal dates are fine',
           of(date_issues(equal, now=NOW), 'modified_before_published') == [])


def test_ids_are_stable_and_distinct():
    p = page(json_ld=article(modified='last Tuesday'))
    first = date_issues(p, {URL: '2026-06-01'}, now=NOW)
    second = date_issues(page(json_ld=article(modified='last Tuesday')),
                         {URL: '2026-06-01'}, now=NOW)
    result('ids: two runs over identical input give identical ids',
           [i['id'] for i in first] == [i['id'] for i in second] and first,
           f"{[i['id'][:8] for i in first]} vs {[i['id'][:8] for i in second]}")

    other = date_issues(page(json_ld=article(modified='some time in March')),
                        {URL: '2026-06-01'}, now=NOW)
    first_invalid = of(first, 'invalid_date_format')
    other_invalid = of(other, 'invalid_date_format')
    result('ids: a different offending value gives a different id',
           first_invalid and other_invalid
           and first_invalid[0]['id'] != other_invalid[0]['id'],
           f"{first_invalid[0]['id'][:8]} vs {other_invalid[0]['id'][:8]}"
           if first_invalid and other_invalid else 'missing issues')

    result('ids: every issue on one page has its own id',
           len({i['id'] for i in first}) == len(first), str(len(first)))


def test_crawl_level_helper():
    pages = [
        page(json_ld=article(), url='https://example.com/a'),
        page(json_ld=article(modified='2027-01-01T00:00:00+00:00'),
             url='https://example.com/b'),
        page(json_ld=[{'@type': 'BlogPosting'}], url='https://example.com/c'),
    ]
    issues = date_issues_for_crawl(pages, {}, now=NOW)
    result('crawl: issues from every page come back in one flat list',
           checks(issues) == ['future_date', 'missing_dates'],
           str(checks(issues)))
    result('crawl: each issue carries its own page URL',
           {i['url'] for i in issues} == {'https://example.com/b',
                                          'https://example.com/c'},
           str({i['url'] for i in issues}))
    result('crawl: no pages means no issues',
           date_issues_for_crawl([], now=NOW) == []
           and date_issues_for_crawl(None, now=NOW) == [])


def test_faqpage_is_not_an_article():
    """A block describing what a page contains is not a claim about dates.

    Real case: 47 pages on one site were told to add datePublished to their
    FAQPage block, because any @type ending in "Page" counted as article-like.
    """
    faq_only = {'url': 'https://site/faq', 'json_ld': [{'@type': 'FAQPage', 'mainEntity': []}]}
    result('faqpage: a FAQ block alone asks for no dates',
           article_node(faq_only) is None and date_issues(faq_only) == [],
           str([i['check'] for i in date_issues(faq_only)]))

    with_webpage = {'url': 'https://site/faq',
                    'json_ld': [{'@type': 'FAQPage'}, {'@type': 'WebPage'}]}
    node = article_node(with_webpage)
    checks = [i['check'] for i in date_issues(with_webpage)]
    result('faqpage: a WebPage alongside it still needs dates',
           node is not None and node.get('@type') == 'WebPage' and checks == ['missing_dates'],
           f'node={node} checks={checks}')

    specific = {'url': 'https://site/post',
                'json_ld': [{'@type': 'WebPage'},
                            {'@type': 'TechArticle',
                             'datePublished': '2026-01-01T00:00:00+00:00',
                             'dateModified': '2026-02-01T00:00:00+00:00'}]}
    result('faqpage: the most specific node is the one reported',
           article_node(specific).get('@type') == 'TechArticle'
           and date_issues(specific, now=NOW) == [],
           str(article_node(specific)))


def test_dates_that_are_populated_but_meaningless():
    """A field can be present, well-formed, ordered - and still say nothing.

    From a real site: 43 guide pages each claimed a distinct publication
    millisecond inside one 7.7-second window, which was when a seeder ran.
    Every presence-and-format check passed it.
    """
    # Two clock reads in one render: milliseconds apart, not identical.
    render = page(json_ld=article(published='2026-10-17T12:22:40.538000+00:00',
                                  modified='2026-10-17T12:22:40.541000+00:00'))
    found = of(date_issues(render, now=NOW), 'dates_read_at_render')
    result('plausibility: dates milliseconds apart are two clock reads',
           len(found) == 1, str(checks(date_issues(render, now=NOW))))
    result('plausibility: the finding says how far apart they are',
           found and '0.003s' in found[0]['details'], str(found[0]['details'] if found else None))

    # The honest case this must never fail: published, never edited since, so
    # one stored value rendered into both fields - byte-identical.
    same = page(json_ld=article(published='2026-01-05T09:00:00+00:00',
                                modified='2026-01-05T09:00:00+00:00'))
    result('plausibility: identical dates are an unedited page, not a defect',
           not of(date_issues(same, now=NOW), 'dates_read_at_render'),
           str(checks(date_issues(same, now=NOW))))

    # Far enough apart to be two real events.
    real = page(json_ld=article(published='2026-01-05T09:00:00+00:00',
                                modified='2026-06-01T12:00:00+00:00'))
    result('plausibility: dates months apart are quiet',
           not of(date_issues(real, now=NOW), 'dates_read_at_render'),
           str(checks(date_issues(real, now=NOW))))


def test_a_whole_library_published_in_one_run():
    """Distinct-but-adjacent dates are a loop; one shared date is a launch."""
    loop = [page(json_ld=article(published=f'2026-10-17T12:22:4{n}.100000+00:00',
                                 modified='2026-11-01T00:00:00+00:00'),
                 url=f'https://example.com/g{n}') for n in range(6)]
    found = of(date_issues_for_crawl(loop, {}, now=NOW), 'dates_from_one_machine_run')
    result('plausibility: six pages seconds apart are one machine run',
           len(found) == 1, str(checks(date_issues_for_crawl(loop, {}, now=NOW))))
    result('plausibility: the finding counts the pages and the distinct values',
           found and '6 pages' in found[0]['details'] and '6 different' in found[0]['details'],
           str(found[0]['details'] if found else None))
    result('plausibility: the pages are named so they can be found',
           found and len(found[0]['evidence'].get('related_urls', [])) == 6,
           str(found[0]['evidence'] if found else None))

    # A real simultaneous launch shares one timestamp, because one event
    # happened. Reporting that would be telling the truth to fix itself.
    launch = [page(json_ld=article(published='2026-10-17T12:22:38+00:00',
                                   modified='2026-11-01T00:00:00+00:00'),
                   url=f'https://example.com/g{n}') for n in range(8)]
    result('plausibility: one shared timestamp is a batch publish, not a run',
           not of(date_issues_for_crawl(launch, {}, now=NOW), 'dates_from_one_machine_run'),
           str(checks(date_issues_for_crawl(launch, {}, now=NOW))))

    # Pages published over real time are quiet however many there are.
    spread = [page(json_ld=article(published=f'2026-0{n}-05T09:00:00+00:00',
                                   modified='2026-11-01T00:00:00+00:00'),
                   url=f'https://example.com/p{n}') for n in range(1, 9)]
    result('plausibility: pages published over months are quiet',
           not of(date_issues_for_crawl(spread, {}, now=NOW), 'dates_from_one_machine_run'),
           str(checks(date_issues_for_crawl(spread, {}, now=NOW))))

    # Too few pages to tell a loop from a coincidence.
    few = [page(json_ld=article(published=f'2026-10-17T12:22:4{n}.100000+00:00'),
                url=f'https://example.com/s{n}') for n in range(3)]
    result('plausibility: three pages are not enough to call it a run',
           not of(date_issues_for_crawl(few, {}, now=NOW), 'dates_from_one_machine_run'),
           str(checks(date_issues_for_crawl(few, {}, now=NOW))))


TESTS = (
    test_dates_that_are_populated_but_meaningless,
    test_a_whole_library_published_in_one_run,
    test_parse_date_accepts_every_format_we_meet,
    test_extract_reads_every_source,
    test_a_consistent_page_reports_nothing,
    test_missing_dates_only_for_article_like_json_ld,
    test_faqpage_is_not_an_article,
    test_invalid_formats,
    test_mismatch_tolerance,
    test_stale_content,
    test_future_dates_and_reversed_pairs,
    test_ids_are_stable_and_distinct,
    test_crawl_level_helper,
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
