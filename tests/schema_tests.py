"""Structured-data checks over already-crawled pages.

Exercises src/core/schema_checks.py on its own: every page is a dict built in
memory and `emit` is a local collector rather than technical_checks' grouping
helper, so nothing here crawls, fetches or touches the database.

    python tests/schema_tests.py

Every check in _SPECS gets a page that must raise it and a page that must not:

  1. jsonld_parse_error          a raw block json.loads rejects, and the parse
                                 failures a crawler recorded for us
  2. faq_answer_not_visible      an FAQ question missing from the copy, plus the
                                 numbering-prefix case ("1. What is X?" in the
                                 markup against "What is X?" on the page), which
                                 Google treats as a mismatch
  3. jsonld_dangling_id          an @id reference no block on the page defines
  4. self_serving_review         Review / AggregateRating about the site itself
  5. breadcrumb_position_invalid positions that are not 1..n in display order
  6. breadcrumb_item_incomplete  a step with no name, or no item URL anywhere
                                 but the last step

Plus the quiet cases: correct markup raises nothing, a page with no JSON-LD
raises nothing, and junk input (None, strings where dicts belong, nesting past
the walker's depth cap) must not raise.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import schema_checks  # noqa: E402
from src.core.schema_checks import schema_issues  # noqa: E402

PASSED, FAILED = [], []

CHECKS = tuple(spec[1] for spec in schema_checks._SPECS)


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


# --------------------------------------------------------------------------
# harness: our own emit, so these tests do not depend on technical_checks
# --------------------------------------------------------------------------

class Report:
    """What schema_issues handed to emit, one entry per check."""

    def __init__(self, calls):
        self.calls = calls
        self._by_check = {call['check']: call for call in calls}

    def items(self, check):
        return self._by_check[check]['items']

    def offending(self, check):
        return [item['offending'] for item in self.items(check)]

    def details(self, check):
        return ' | '.join(item['details'] for item in self.items(check))

    def raised(self):
        return sorted(call['check'] for call in self.calls if call['items'])


def run(*pages, **cfg):
    """Run every check over `pages`, collecting what emit is given."""
    calls = []

    def emit(check, severity, name, items, group_details, how_to_fix):
        calls.append({'check': check, 'severity': severity, 'name': name,
                      'items': list(items), 'group_details': group_details,
                      'how_to_fix': how_to_fix})

    schema_issues(list(pages), cfg, emit)
    return Report(calls)


def well_formed(items):
    """Every reported item must carry the four fields the caller renders."""
    return all(isinstance(item, dict)
               and isinstance(item.get('url'), str) and item.get('url')
               and isinstance(item.get('offending'), str)
               and isinstance(item.get('details'), str) and item.get('details')
               and isinstance(item.get('evidence'), dict)
               for item in items)


def page(url='https://example.com/p', **fields):
    data = {'url': url}
    data.update(fields)
    return data


def headings(*texts):
    return [{'level': 2, 'text': text, 'dom_order': index}
            for index, text in enumerate(texts)]


ANSWER = 'Toadstone walks a whole site and reports what it finds.'


def faq(*questions):
    return [{'@context': 'https://schema.org', '@type': 'FAQPage',
             'mainEntity': [{'@type': 'Question', 'name': question,
                             'acceptedAnswer': {'@type': 'Answer', 'text': ANSWER}}
                            for question in questions]}]


def crumbs(*steps):
    return [{'@context': 'https://schema.org', '@type': 'BreadcrumbList',
             'itemListElement': list(steps)}]


def step(position, name, url=None):
    """A ListItem; `item` only carries a name when the step has one."""
    node = {'@type': 'ListItem'}
    if position is not None:
        node['position'] = position
    if name is not None:
        node['name'] = name
    if url:
        node['item'] = {'@id': url, 'name': name} if name else url
    return node


# --------------------------------------------------------------------------
# the contract with the caller
# --------------------------------------------------------------------------

def test_emit_contract():
    """Every check reports once per crawl, with the copy its spec declares."""
    report = run()
    result('contract: every check is emitted once, in spec order',
           [call['check'] for call in report.calls] == list(CHECKS),
           str([call['check'] for call in report.calls]))

    expected = [(spec[1], spec[2], spec[3], spec[4], spec[5])
                for spec in schema_checks._SPECS]
    got = [(call['check'], call['severity'], call['name'],
            call['group_details'], call['how_to_fix']) for call in report.calls]
    result('contract: severity, name and fix text come from the spec',
           got == expected)
    result('contract: severities are error/warning only',
           {call['severity'] for call in report.calls} <= {'error', 'warning'},
           str(sorted({call['severity'] for call in report.calls})))
    result('contract: an empty crawl raises nothing', not report.raised())
    result('contract: the category is Structured Data',
           schema_checks.CATEGORY == 'Structured Data', schema_checks.CATEGORY)

    blank = run({'url': '   ', 'json_ld_raw': ['{nope'],
                 'json_ld': [{'@type': 'Review', 'name': 'x'}]})
    result('contract: a page with no URL is skipped entirely',
           not blank.raised(), str(blank.raised()))


# --------------------------------------------------------------------------
# 1. jsonld_parse_error
# --------------------------------------------------------------------------

GOOD_BLOCK = '{"@context": "https://schema.org", "@type": "Organization", "name": "Toadstone"}'
BAD_BLOCK = '{"@context": "https://schema.org", "@type": "Organization", "name": "Toadstone",}'


def test_jsonld_parse_error():
    """A block the browser tolerates but a parser rejects."""
    items = run(page(json_ld_raw=[GOOD_BLOCK, BAD_BLOCK])).items('jsonld_parse_error')
    result('parse: only the block that fails is reported', len(items) == 1,
           str(items))
    result('parse: the offending block is named, with a snippet and a selector',
           bool(items) and items[0]['offending'] == 'block 2'
           and 'block 2 does not parse' in items[0]['details']
           and 'Toadstone' in items[0]['evidence'].get('snippet', '')
           and 'nth-of-type(2)' in items[0]['evidence'].get('selector', ''),
           str(items[0]) if items else 'nothing reported')

    result('parse: blocks that parse are never reported',
           not run(page(json_ld_raw=[GOOD_BLOCK, GOOD_BLOCK])).items('jsonld_parse_error'))
    result('parse: blank and non-string blocks are ignored',
           not run(page(json_ld_raw=['', '   ', None, 7])).items('jsonld_parse_error'))

    # A block that does not parse never reaches page['json_ld'], so a crawl
    # that only records the failure must still surface it.
    recorded = run(page(json_ld_errors=[
        {'message': 'Unterminated string starting at line 4', 'line': 4,
         'snippet': '{"name": "Libre'},
        'Expecting property name enclosed in double quotes: line 1 column 2',
    ])).items('jsonld_parse_error')
    result('parse: recorded failures are read as dicts and as strings',
           len(recorded) == 2
           and recorded[0]['evidence'].get('line') == 4
           and recorded[0]['evidence'].get('snippet') == '{"name": "Libre'
           and 'Unterminated string' in recorded[0]['details']
           and 'Expecting property name' in recorded[1]['details'],
           str(recorded))
    result('parse: a recorded failure for a block already reported is not doubled',
           len(run(page(json_ld_raw=[BAD_BLOCK],
                        json_ld_errors=['same block, reported by the crawler'])
                   ).items('jsonld_parse_error')) == 1)
    result('parse: a bad block is reported even when the page has no usable JSON-LD',
           len(run(page(json_ld=[], json_ld_raw=[BAD_BLOCK])).items('jsonld_parse_error')) == 1)
    result('parse: a page with no JSON-LD at all is silent',
           not run(page()).raised())


# --------------------------------------------------------------------------
# 2. faq_answer_not_visible
# --------------------------------------------------------------------------

QUESTION = 'What does Toadstone crawl?'


def test_faq_questions_must_be_visible_verbatim():
    """The marked-up question must be the string the page shows."""
    clean = run(page(json_ld=faq(QUESTION), headings=headings(QUESTION),
                     main_text=ANSWER))
    result('faq: a question shown verbatim as a heading is fine',
           not clean.items('faq_answer_not_visible'),
           clean.details('faq_answer_not_visible'))

    body = run(page(json_ld=faq(QUESTION), headings=headings('Frequently asked'),
                    main_text=f'{QUESTION} {ANSWER}'))
    result('faq: a question shown verbatim in the body copy is fine',
           not body.items('faq_answer_not_visible'),
           body.details('faq_answer_not_visible'))

    absent = run(page(json_ld=faq(QUESTION), headings=headings('Frequently asked'),
                      main_text='Some copy that never asks the question.'))
    items = absent.items('faq_answer_not_visible')
    result('faq: a question the page never shows is reported',
           len(items) == 1 and items[0]['offending'] == QUESTION, str(items))
    result('faq: the evidence says the question is not on the page',
           bool(items) and items[0]['evidence'].get('found') == '(not on the page)'
           and 'does not appear in the visible text' in items[0]['details'],
           str(items[0]['evidence']) if items else '')

    # The numbering-prefix case: same words, different string, so it fails.
    numbered = run(page(json_ld=faq(f'1. {QUESTION}'),
                        headings=headings(QUESTION), main_text=ANSWER))
    items = numbered.items('faq_answer_not_visible')
    result('faq: a numbering prefix in the markup is a mismatch',
           len(items) == 1 and items[0]['offending'] == f'1. {QUESTION}', str(items))
    result('faq: the report quotes what the page shows against what is marked up',
           bool(items) and items[0]['evidence'].get('found') == QUESTION
           and f'shows "{QUESTION}"' in items[0]['details']
           and 'character for character' in items[0]['details'],
           items[0]['details'] if items else '')

    reverse = run(page(json_ld=faq(QUESTION),
                       headings=headings(f'2) {QUESTION}'), main_text=ANSWER))
    result('faq: a numbering prefix on the page is a mismatch too',
           len(reverse.items('faq_answer_not_visible')) == 1,
           reverse.details('faq_answer_not_visible'))

    mixed = run(page(json_ld=faq(QUESTION, 'How fast is it?'),
                     headings=headings(QUESTION), main_text=ANSWER))
    result('faq: only the question that is missing is reported',
           mixed.offending('faq_answer_not_visible') == ['How fast is it?'],
           str(mixed.offending('faq_answer_not_visible')))

    result('faq: an FAQPage with no questions is silent',
           not run(page(json_ld=[{'@type': 'FAQPage'}],
                        main_text=ANSWER)).items('faq_answer_not_visible'))
    result('faq: a page with no visible copy is not judged',
           not run(page(json_ld=faq(QUESTION))).items('faq_answer_not_visible'))
    result('faq: Question nodes outside an FAQPage are ignored',
           not run(page(json_ld=[{'@type': 'Question', 'name': QUESTION}],
                        main_text='unrelated copy')).items('faq_answer_not_visible'))


# --------------------------------------------------------------------------
# 3. jsonld_dangling_id
# --------------------------------------------------------------------------

def test_dangling_id_references():
    """{"@id": "..."} with no node behind it on the same page."""
    org = {'@context': 'https://schema.org', '@type': 'Organization',
           '@id': 'https://example.com/#org', 'name': 'Toadstone'}
    resolved = {'@type': 'WebPage', '@id': 'https://example.com/p#page',
                'publisher': {'@id': 'https://example.com/#org'}}
    result('id: a reference the page defines is not reported',
           not run(page(json_ld=[org, resolved])).items('jsonld_dangling_id'))
    result('id: a node defined inside @graph still counts as defined',
           not run(page(json_ld=[{'@context': 'https://schema.org',
                                  '@graph': [org, resolved]}])
                   ).items('jsonld_dangling_id'))

    dangling = {'@type': 'WebPage', '@id': 'https://example.com/p#page',
                'publisher': {'@id': 'https://example.com/#nobody'},
                'about': {'@id': 'https://example.com/#nobody'}}
    items = run(page(json_ld=[dangling])).items('jsonld_dangling_id')
    result('id: a reference to nothing is reported once per @id',
           len(items) == 1 and items[0]['offending'] == 'https://example.com/#nobody',
           str(items))
    result('id: the evidence names the node that is missing',
           bool(items) and items[0]['evidence'].get('found') == '(no such node)'
           and 'https://example.com/#nobody' in items[0]['evidence'].get('expected', ''),
           str(items[0]['evidence']) if items else '')

    two = run(page(json_ld=[{'@type': 'WebPage', '@id': 'https://example.com/p#page',
                             'publisher': {'@id': '#a'}, 'author': {'@id': '#b'}}])
              ).items('jsonld_dangling_id')
    result('id: every unresolved reference is listed', len(two) == 2,
           str([item['offending'] for item in two]))
    result('id: blank and non-string @id values are ignored',
           not run(page(json_ld=[{'@id': '   '}, {'@id': 7}, {'@id': None}])
                   ).items('jsonld_dangling_id'))


# --------------------------------------------------------------------------
# 4. self_serving_review
# --------------------------------------------------------------------------

def test_self_serving_review_markup():
    """Review / AggregateRating on the site's own pages."""
    review = {'@context': 'https://schema.org', '@type': 'Review',
              'reviewRating': {'@type': 'Rating', 'ratingValue': '5'},
              'itemReviewed': {'@type': 'Organization', 'name': 'Toadstone'}}
    items = run(page(json_ld=[review])).items('self_serving_review')
    result('review: Review markup is reported',
           len(items) == 1 and items[0]['offending'] == 'Review', str(items))
    result('review: the fix explains the policy',
           bool(items) and 'self-serving review markup' in items[0]['details'],
           items[0]['details'] if items else '')

    rated = {'@type': 'Product', 'name': 'Toadstone Pro',
             'aggregateRating': {'@type': 'AggregateRating',
                                 'ratingValue': '4.9', 'reviewCount': 120}}
    items = run(page(json_ld=[rated])).items('self_serving_review')
    result('review: a nested aggregateRating is reported',
           len(items) == 1 and items[0]['offending'] == 'AggregateRating', str(items))

    both = run(page(json_ld=[{'@type': 'Product', 'name': 'Pro',
                              'review': [review],
                              'aggregateRating': {'@type': 'AggregateRating',
                                                  'ratingValue': '4.9'}}])
               ).items('self_serving_review')
    result('review: both kinds are named once, on one row per page',
           len(both) == 1 and both[0]['offending'] == 'AggregateRating, Review',
           str([item['offending'] for item in both]))

    clean = run(page(json_ld=[{'@type': 'Organization', 'name': 'Toadstone'},
                              {'@type': 'WebPage', 'name': 'Home'}]))
    result('review: markup without reviews is not reported',
           not clean.items('self_serving_review'), clean.details('self_serving_review'))


# --------------------------------------------------------------------------
# 5 and 6. breadcrumbs
# --------------------------------------------------------------------------

def test_breadcrumb_positions():
    """Positions must be 1..n, once each, in display order."""
    good = crumbs(step(1, 'Home', 'https://example.com/'),
                  step(2, 'Docs', 'https://example.com/docs/'),
                  step(3, 'Schema'))
    clean = run(page(json_ld=good))
    result('breadcrumb: 1..n in order raises nothing at all',
           not clean.raised(), str(clean.raised()))

    items = run(page(json_ld=crumbs(step(1, 'Home', 'https://example.com/'),
                                    step(3, 'Schema')))
                ).items('breadcrumb_position_invalid')
    result('breadcrumb: a gap in the numbering is reported',
           len(items) == 1 and items[0]['offending'] == '1, 3'
           and items[0]['evidence'].get('expected') == '1, 2', str(items))

    items = run(page(json_ld=crumbs(step(2, 'Docs', 'https://example.com/docs/'),
                                    step(1, 'Home')))
                ).items('breadcrumb_position_invalid')
    result('breadcrumb: steps numbered out of display order are reported',
           len(items) == 1 and items[0]['offending'] == '2, 1', str(items))

    items = run(page(json_ld=crumbs(step(None, 'Home', 'https://example.com/'),
                                    step(2, 'Docs')))
                ).items('breadcrumb_position_invalid')
    result('breadcrumb: a missing position shows as ?',
           len(items) == 1 and items[0]['offending'] == '?, 2', str(items))

    result('breadcrumb: positions given as strings still count',
           not run(page(json_ld=crumbs(step('1', 'Home', 'https://example.com/'),
                                       step('2', 'Docs')))
                   ).items('breadcrumb_position_invalid'))
    result('breadcrumb: a list with no steps is not judged',
           not run(page(json_ld=[{'@type': 'BreadcrumbList',
                                  'itemListElement': []}])).raised())


def test_breadcrumb_steps():
    """Every step needs a name, and every step but the last an item URL."""
    items = run(page(json_ld=crumbs(step(1, None, 'https://example.com/'),
                                    step(2, 'Docs')))
                ).items('breadcrumb_item_incomplete')
    result('breadcrumb: a step with no name is reported',
           len(items) == 1 and items[0]['offending'] == 'step 1'
           and items[0]['evidence'].get('found') == 'no name', str(items))

    items = run(page(json_ld=crumbs(step(1, 'Home'), step(2, 'Docs')))
                ).items('breadcrumb_item_incomplete')
    result('breadcrumb: a non-final step with no item URL is reported',
           len(items) == 1 and items[0]['offending'] == 'step 1'
           and items[0]['evidence'].get('found') == 'no item URL', str(items))

    items = run(page(json_ld=crumbs(step(1, None), step(2, 'Docs')))
                ).items('breadcrumb_item_incomplete')
    result('breadcrumb: both problems are named on one row',
           len(items) == 1 and items[0]['evidence'].get('found') == 'no name, no item URL'
           and 'no name and no item URL' in items[0]['details'], str(items))

    result('breadcrumb: the last step may omit its item URL',
           not run(page(json_ld=crumbs(step(1, 'Home', 'https://example.com/'),
                                       step(2, 'Docs')))
                   ).items('breadcrumb_item_incomplete'))
    result('breadcrumb: a name carried on item instead of the step is accepted',
           not run(page(json_ld=crumbs(
               {'@type': 'ListItem', 'position': 1,
                'item': {'@id': 'https://example.com/', 'name': 'Home'}},
               {'@type': 'ListItem', 'position': 2, 'name': 'Docs'}))
           ).items('breadcrumb_item_incomplete'))
    result('breadcrumb: item given as a bare URL string is accepted',
           not run(page(json_ld=crumbs(
               {'@type': 'ListItem', 'position': 1, 'name': 'Home',
                'item': 'https://example.com/'},
               {'@type': 'ListItem', 'position': 2, 'name': 'Docs'}))
           ).items('breadcrumb_item_incomplete'))


# --------------------------------------------------------------------------
# the quiet cases
# --------------------------------------------------------------------------

def test_correct_structured_data_is_silent():
    """A page whose markup is right must produce nothing at all."""
    org = {'@type': 'Organization', '@id': 'https://example.com/#org',
           'name': 'Toadstone', 'url': 'https://example.com/'}
    webpage = {'@type': 'WebPage', '@id': 'https://example.com/faq#page',
               'name': 'FAQ', 'publisher': {'@id': 'https://example.com/#org'}}
    graph = {'@context': 'https://schema.org',
             '@graph': [org, webpage, faq(QUESTION)[0],
                        crumbs(step(1, 'Home', 'https://example.com/'),
                               step(2, 'FAQ'))[0]]}
    report = run(page('https://example.com/faq', json_ld=[graph],
                      json_ld_raw=[GOOD_BLOCK],
                      headings=headings('FAQ', QUESTION),
                      main_text=f'{QUESTION} {ANSWER}'))
    result('clean: correct structured data raises nothing',
           not report.raised(), str(report.raised()) + ' ' +
           ' / '.join(report.details(check) for check in report.raised()))
    result('clean: every check still reported back to the caller',
           len(report.calls) == len(CHECKS))


def test_pages_without_structured_data():
    """No JSON-LD is not a defect."""
    report = run(page('https://example.com/a'),
                 page('https://example.com/b', json_ld=[]),
                 page('https://example.com/c', json_ld=None, json_ld_raw=[]),
                 page('https://example.com/d', json_ld=[], json_ld_errors=[],
                      main_text='Plain copy with no markup at all.',
                      headings=headings('A heading')))
    result('empty: pages with no JSON-LD raise nothing',
           not report.raised(), str(report.raised()))


def test_malformed_input_never_raises():
    """Junk on a page dict must be ignored, not crash the crawl."""
    deep = {'@type': 'Thing', 'name': 'bottom'}
    for _ in range(40):
        deep = {'@type': 'Thing', 'child': deep, 'list': [deep]}

    junk = [
        page('https://example.com/none', json_ld=None, json_ld_raw=None,
             json_ld_errors=None, main_text=None, headings=None),
        page('https://example.com/strings', json_ld='not a list at all',
             json_ld_raw='not a list', json_ld_errors='oops',
             headings='not a list', main_text=''),
        page('https://example.com/mixed',
             json_ld=[None, 'str', 7, [], {}, {'@type': None},
                      {'@type': {'a': 1}}, {'@type': 7}, {'@type': ['Thing', None]}],
             headings=[None, 'str', 7, {'text': None}, {'nope': 1}],
             main_text='copy'),
        page('https://example.com/faq-junk',
             json_ld=[{'@type': 'FAQPage', 'mainEntity': 'nope'},
                      {'@type': 'FAQPage', 'mainEntity': {'@type': 'Question'}},
                      {'@type': 'FAQPage',
                       'mainEntity': [{'@type': 'Question', 'name': {'@value': 'Q?'}},
                                      {'@type': 'Question', 'name': None},
                                      {'@type': 'Question', 'name': ['A?', 'B?']},
                                      'str', None, 7]}],
             main_text='nothing matching here', headings=headings('x')),
        page('https://example.com/faq-no-headings',
             json_ld=faq(QUESTION), main_text='copy without the question',
             headings='a string where a list belongs'),
        page('https://example.com/faq-no-copy',
             json_ld=faq(QUESTION), main_text=None,
             headings=[{'text': None}, {'text': ''}]),
        page('https://example.com/crumbs-junk',
             json_ld=[{'@type': 'BreadcrumbList', 'itemListElement': 'nope'},
                      {'@type': 'BreadcrumbList', 'itemListElement': None},
                      {'@type': 'BreadcrumbList',
                       'itemListElement': [{'position': None, 'item': 7, 'name': []},
                                           {'position': 'x',
                                            'item': {'name': {'@value': 'N'}}},
                                           {'position': [1], 'item': None,
                                            'name': {'text': 'T'}},
                                           'str', None]}]),
        page('https://example.com/deep', json_ld=[deep]),
        page('https://example.com/errors',
             json_ld_errors=[{'message': None}, {}, None, 7, []],
             json_ld_raw=[{'not': 'a string'}, b'bytes', 7]),
    ]

    problems = []
    for bad in junk:
        try:
            report = run(bad)
        except Exception as exc:  # a raise here is the failure
            problems.append((bad['url'], f'raised {type(exc).__name__}: {exc}'))
            continue
        for call in report.calls:
            if not well_formed(call['items']):
                problems.append((bad['url'], f"malformed item in {call['check']}"))
    result('junk: no malformed page makes the module raise',
           not problems, str(problems[:3]))

    try:
        report = run(*junk)
        ok, detail = True, f'{len(report.raised())} checks raised over {len(junk)} pages'
    except Exception as exc:
        ok, detail = False, f'raised {type(exc).__name__}: {exc}'
    result('junk: a whole crawl of malformed pages still completes', ok, detail)


def test_reported_items_are_well_formed():
    """Whatever is reported, the caller gets url/offending/details/evidence."""
    report = run(page('https://example.com/bad', json_ld=[
        {'@type': 'FAQPage', 'mainEntity': [{'@type': 'Question', 'name': QUESTION}]},
        {'@type': 'WebPage', '@id': 'https://example.com/bad#page',
         'publisher': {'@id': '#nobody'}},
        {'@type': 'Review', 'name': 'Great'},
        crumbs(step(1, None), step(3, 'Docs'))[0],
    ], json_ld_raw=[BAD_BLOCK], main_text='copy that matches nothing',
        headings=headings('Heading')))
    result('shape: every check fires on the page that breaks them all',
           report.raised() == sorted(CHECKS), str(report.raised()))
    result('shape: every reported item carries the four rendered fields',
           all(well_formed(call['items']) for call in report.calls))
    result('shape: every item is attributed to its page',
           all(item['url'] == 'https://example.com/bad'
               for call in report.calls for item in call['items']))


def test_breadcrumb_item_id_is_not_a_dangling_reference():
    """{"item": {"@id": "https://site/page"}} points at a page, not a node.

    That is how real BreadcrumbList markup names its steps; reading it as an
    unresolved reference reported a defect on every breadcrumbed page.
    """
    crumbs = {
        'url': 'https://site/deep/page',
        'json_ld': [{'@type': 'BreadcrumbList', 'itemListElement': [
            {'@type': 'ListItem', 'position': 1, 'name': 'Home',
             'item': {'@id': 'https://site/'}},
            {'@type': 'ListItem', 'position': 2, 'name': 'Deep',
             'item': {'@id': 'https://site/deep/'}},
        ]}],
    }
    report = run(crumbs)
    result('breadcrumb @id items are not dangling references',
           not report.items('jsonld_dangling_id'), str(report.offending('jsonld_dangling_id')))

    # A fragment id still has to resolve on the page that claims it
    unresolved = {'url': 'https://site/p',
                  'json_ld': [{'@type': 'WebPage', 'publisher': {'@id': '#nowhere'}}]}
    result('a fragment id with nothing behind it is still reported',
           report_has(run(unresolved), 'jsonld_dangling_id', '#nowhere'),
           str(run(unresolved).offending('jsonld_dangling_id')))


def report_has(report, check, needle):
    return any(needle in str(item) for item in report.items(check))


def test_odd_field_types_do_not_raise():
    """A page dict can hold anything; one odd page must not lose the crawl."""
    faq = {'@type': 'FAQPage', 'mainEntity': [
        {'@type': 'Question', 'name': 'What is X?',
         'acceptedAnswer': {'@type': 'Answer', 'text': 'A'}}]}
    for label, page in (
        ('main_text is a number', {'url': 'https://site/a', 'main_text': 123, 'json_ld': [faq]}),
        ('headings is a string', {'url': 'https://site/b', 'main_text': 'copy',
                                  'headings': 'nope', 'json_ld': [faq]}),
        ('heading text is a dict', {'url': 'https://site/c', 'main_text': 'copy',
                                    'headings': [{'text': {'oops': True}}], 'json_ld': [faq]}),
    ):
        try:
            run(page)
            result(f'odd data: {label} does not raise', True)
        except Exception as exc:
            result(f'odd data: {label} does not raise', False, f'{type(exc).__name__}: {exc}')


TESTS = (
    test_breadcrumb_item_id_is_not_a_dangling_reference,
    test_odd_field_types_do_not_raise,
    test_emit_contract,
    test_jsonld_parse_error,
    test_faq_questions_must_be_visible_verbatim,
    test_dangling_id_references,
    test_self_serving_review_markup,
    test_breadcrumb_positions,
    test_breadcrumb_steps,
    test_correct_structured_data_is_silent,
    test_pages_without_structured_data,
    test_malformed_input_never_raises,
    test_reported_items_are_well_formed,
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
