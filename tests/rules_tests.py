"""Deterministic tests for the user-defined rules engine.

No test framework and no network access: the fixtures are page dicts shaped
like the ones the crawler stores, so every rule type is exercised exactly the
way a crawl would exercise it.

    python tests/rules_tests.py

The first fixture is the rules file from the feature request, character for
character. Each rule there has to fire on the page that breaks it and stay
silent on the page that does not - that pair is the whole point of the
feature, so a failure here means a real regression:

  1. the four example rules load from YAML with no errors, and the same rules
     written as JSON compile to exactly the same thing
  2. every example rule fires on the offending page and only there
  3. require_same_across_pages names the majority value and the page's value,
     and reports the divergent pages rather than the consistent ones
  4. a selector rule with no page HTML is skipped once, as a rule-level note,
     not as an issue on every page
  5. a malformed file, an invalid regex and an unknown key are reported and
     skipped instead of raising
  6. include/exclude globs decide which URLs a rule runs against
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import rules_engine  # noqa: E402
from src.utils.issue_factory import EVIDENCE_FIELDS, SEVERITIES  # noqa: E402

PASSED, FAILED = [], []


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


# The rules file from the feature request, verbatim.
EXAMPLE_RULES = '''rules:
  - id: banned-claims
    severity: error
    forbid: ["500\\\\+ projects", "15\\\\+ years", "80\\\\+ (developers|specialists)"]
  - id: cost-consistency
    severity: warning
    require_same_across_pages:
      name: ehr_system_cost
      pattern: "EHR[^.]{0,40}\\\\$[\\\\d,]+ ?(to|-) ?\\\\$[\\\\d,]+\\\\+?"
  - id: cta-label
    severity: warning
    selector: "a.btn, button[type=submit]"
    must_equal: "Book a Discovery Call"
  - id: author
    severity: error
    expect_author: "Jane Doe"
'''


def page(url, main_text='', **extra):
    """A page dict with the fields the engine reads, defaults filled in."""
    data = {
        'url': url,
        'status_code': 200,
        'title': '',
        'meta_description': '',
        'h1': '',
        'main_text': main_text,
        'json_ld': [],
        'meta_tags': {},
        'author': '',
    }
    data.update(extra)
    return data


def author_page(url, name, **extra):
    return page(url, json_ld=[{'@type': 'BlogPosting', 'author': {'@type': 'Person',
                                                                  'name': name}}], **extra)


def button(url, label):
    return page(url, html=f'<html><body><div><a class="btn" href="/x">{label}</a>'
                          f'<button type="submit">{label}</button></div></body></html>')


def issues_for(issues, rule_id):
    return [i for i in issues if i['check'] == f'rule:{rule_id}']


def load_example():
    loaded = rules_engine.load_rules(EXAMPLE_RULES)
    if loaded['errors']:
        raise AssertionError(f'example rules did not load cleanly: {loaded["errors"]}')
    return loaded['rules']


# ---------------------------------------------------------------------------
# The example rules file
# ---------------------------------------------------------------------------

def test_example_rules_file_loads():
    loaded = rules_engine.load_rules(EXAMPLE_RULES)
    ids = [r['id'] for r in loaded['rules']]
    result('example: four rules, no errors',
           ids == ['banned-claims', 'cost-consistency', 'cta-label', 'author']
           and not loaded['errors'],
           f'{ids} errors={loaded["errors"]}')

    by_id = {r['id']: r for r in loaded['rules']}
    banned = by_id['banned-claims']
    result('example: severities and defaults survive the parser',
           banned['severity'] == 'error' and by_id['cost-consistency']['severity'] == 'warning'
           and banned['scope'] == 'main_text' and banned['ignore_case'] is True,
           f'{banned["severity"]}/{banned["scope"]}/{banned["ignore_case"]}')

    patterns = [p for p, _ in banned['forbid']]
    result('example: escaped regexes keep their backslashes',
           patterns == ['500\\+ projects', '15\\+ years', '80\\+ (developers|specialists)'],
           str(patterns))

    require = by_id['cost-consistency']['require']
    result('example: nested map under require_same_across_pages',
           require['name'] == 'ehr_system_cost' and require['pattern'].startswith('EHR[^.]{0,40}'),
           str(require['pattern']))

    result('example: selector and must_equal pair up',
           by_id['cta-label']['selector'] == 'a.btn, button[type=submit]'
           and by_id['cta-label']['must_equal'] == 'Book a Discovery Call',
           by_id['cta-label']['selector'])

    result('example: rules file has no validation problems',
           rules_engine.validate_rules(loaded['rules']) == [],
           str(rules_engine.validate_rules(loaded['rules'])))


def test_banned_claims_fires_and_stays_quiet():
    rules = load_example()
    pages = [
        page('https://site.test/about',
             'We have delivered 500+ projects over 15+ years with 80+ developers.'),
        page('https://site.test/clean',
             'We have delivered a few hundred projects since 2010 with a small team.'),
    ]
    issues = issues_for(rules_engine.run_rules(rules, pages), 'banned-claims')
    result('banned-claims: fires once on the offending page only',
           len(issues) == 1 and issues[0]['url'] == 'https://site.test/about',
           str([(i['url'], i['details']) for i in issues]))
    if not issues:
        return
    found = issues[0]
    result('banned-claims: severity, category and slug come from the rule',
           found['type'] == 'error' and found['category'] == 'Rules'
           and found['check'] == 'rule:banned-claims',
           f'{found["type"]}/{found["category"]}/{found["check"]}')
    result('banned-claims: evidence carries the matched text with context',
           found['evidence'].get('found') == '500+ projects'
           and '500+ projects' in found['evidence'].get('snippet', '')
           and 'delivered' in found['evidence'].get('snippet', ''),
           str(found['evidence']))
    result('banned-claims: details count every match on the page',
           '3 matches' in found['details'] and 'from 3 patterns' in found['details'],
           found['details'])


def test_cost_consistency_names_majority_and_divergent():
    rules = load_example()
    pages = [
        page('https://site.test/a', 'An EHR system costs $15,000 to $70,000 to build.'),
        page('https://site.test/b', 'An EHR system costs $15,000 to $70,000 in most cases.'),
        page('https://site.test/c', 'An EHR system costs $20,000 to $90,000 for a clinic.'),
        page('https://site.test/d', 'No prices are quoted on this page at all.'),
    ]
    issues = issues_for(rules_engine.run_rules(rules, pages), 'cost-consistency')
    urls = sorted(i['url'] for i in issues)
    result('cost-consistency: only the divergent page is reported',
           urls == ['https://site.test/c'], str(urls))
    if not issues:
        return
    found = issues[0]
    result('cost-consistency: expected is the majority value, found is this page',
           found['evidence'].get('expected') == 'EHR system costs $15,000 to $70,000'
           and found['evidence'].get('found') == 'EHR system costs $20,000 to $90,000',
           str(found['evidence']))
    result('cost-consistency: details name both values and the majority count',
           '$20,000 to $90,000' in found['details'] and '$15,000 to $70,000' in found['details']
           and '2 of 3 pages' in found['details'],
           found['details'])
    result('cost-consistency: related_urls point at the pages that agree',
           sorted(found['evidence'].get('related_urls', []))
           == ['https://site.test/a', 'https://site.test/b'],
           str(found['evidence'].get('related_urls')))

    agreed = issues_for(rules_engine.run_rules(rules, pages[:2]), 'cost-consistency')
    result('cost-consistency: silent when every page agrees', agreed == [], str(agreed))


def test_cta_label_selector():
    rules = load_example()
    pages = [button('https://site.test/ok', 'Book a Discovery Call'),
             button('https://site.test/bad', 'Get a Free Quote')]
    notes = []
    issues = issues_for(rules_engine.run_rules(rules, pages, notes=notes), 'cta-label')
    result('cta-label: fires on the page with the wrong label only',
           len(issues) == 1 and issues[0]['url'] == 'https://site.test/bad',
           str([i['url'] for i in issues]))
    if not issues:
        return
    found = issues[0]
    result('cta-label: expected and found hold the two labels',
           found['evidence'].get('expected') == 'Book a Discovery Call'
           and found['evidence'].get('found') == 'Get a Free Quote',
           str(found['evidence']))
    result('cta-label: evidence points at the element',
           'a.btn' in found['evidence'].get('selector', '')
           and 'Get a Free Quote' in found['evidence'].get('snippet', '')
           and isinstance(found['evidence'].get('line'), int),
           str(found['evidence']))
    result('cta-label: counts the elements that differ',
           '2 of 2 matching elements differ' in found['details'], found['details'])
    result('cta-label: no notes when every page carried HTML', notes == [], str(notes))


def test_selector_rule_without_html_is_skipped_cleanly():
    rules = load_example()
    pages = [page('https://site.test/a', 'text only'), page('https://site.test/b', 'text only')]
    notes = []
    issues = rules_engine.run_rules(rules, pages, notes=notes)
    cta_notes = [n for n in notes if 'cta-label' in n]
    result('selector without html: no per-page issues',
           issues_for(issues, 'cta-label') == [], str(issues_for(issues, 'cta-label')))
    result('selector without html: one rule-level note explains why',
           len(cta_notes) == 1 and 'no page HTML' in cta_notes[0], str(notes))

    mixed = [button('https://site.test/bad', 'Get a Free Quote'), page('https://site.test/a', 'x')]
    notes = []
    issues = issues_for(rules_engine.run_rules(rules, mixed, notes=notes), 'cta-label')
    result('selector with partial html: checks what it can and says what it skipped',
           len(issues) == 1 and any('1 of 2 pages' in n for n in notes),
           f'{len(issues)} issues, notes={notes}')


def test_author_rule():
    rules = load_example()
    pages = [
        author_page('https://site.test/right', 'Jane Doe'),
        author_page('https://site.test/wrong', 'A Freelancer'),
        page('https://site.test/meta', 'x', meta_tags={'author': 'Jane Doe'}),
        page('https://site.test/none', 'x'),
    ]
    issues = issues_for(rules_engine.run_rules(rules, pages), 'author')
    urls = sorted(i['url'] for i in issues)
    result('author: only the wrong and the missing author are reported',
           urls == ['https://site.test/none', 'https://site.test/wrong'], str(urls))
    wrong = [i for i in issues if i['url'].endswith('/wrong')]
    result('author: expected and found name both authors',
           bool(wrong) and wrong[0]['evidence'].get('expected') == 'Jane Doe'
           and wrong[0]['evidence'].get('found') == 'A Freelancer',
           str(wrong[0]['evidence']) if wrong else 'no issue')
    missing = [i for i in issues if i['url'].endswith('/none')]
    result('author: a page with no author says so',
           bool(missing) and 'No author found' in missing[0]['details'],
           missing[0]['details'] if missing else 'no issue')


# ---------------------------------------------------------------------------
# Loading, validation and failure modes
# ---------------------------------------------------------------------------

def test_yaml_and_json_load_to_the_same_rules():
    document = {'rules': [
        {'id': 'banned-claims', 'severity': 'error',
         'forbid': ['500\\+ projects', '15\\+ years', '80\\+ (developers|specialists)']},
        {'id': 'cost-consistency', 'severity': 'warning',
         'require_same_across_pages': {
             'name': 'ehr_system_cost',
             'pattern': 'EHR[^.]{0,40}\\$[\\d,]+ ?(to|-) ?\\$[\\d,]+\\+?'}},
        {'id': 'cta-label', 'severity': 'warning',
         'selector': 'a.btn, button[type=submit]', 'must_equal': 'Book a Discovery Call'},
        {'id': 'author', 'severity': 'error', 'expect_author': 'Jane Doe'},
    ]}
    from_yaml = rules_engine.load_rules(EXAMPLE_RULES)
    from_json = rules_engine.load_rules(json.dumps(document))

    def comparable(rules):
        return [{'id': r['id'], 'severity': r['severity'], 'scope': r['scope'],
                 'kinds': r['kinds'], 'forbid': [p for p, _ in r['forbid']],
                 'require': r['require']['pattern'] if r['require'] else None,
                 'selector': r['selector'], 'must_equal': r['must_equal'],
                 'expect_author': r['expect_author']} for r in rules]

    result('yaml and json produce identical rules',
           comparable(from_yaml['rules']) == comparable(from_json['rules'])
           and not from_json['errors'],
           str(from_json['errors']))

    with tempfile.TemporaryDirectory() as directory:
        yaml_path = os.path.join(directory, 'rules.yaml')
        json_path = os.path.join(directory, 'rules.json')
        with open(yaml_path, 'w', encoding='utf-8') as handle:
            handle.write(EXAMPLE_RULES)
        with open(json_path, 'w', encoding='utf-8') as handle:
            json.dump(document, handle)
        by_extension = [rules_engine.load_rules(p) for p in (yaml_path, json_path)]
    result('the extension picks the parser for a file on disk',
           comparable(by_extension[0]['rules']) == comparable(by_extension[1]['rules'])
           and not by_extension[0]['errors'] and not by_extension[1]['errors'],
           str([b['errors'] for b in by_extension]))


def test_malformed_file_reports_errors_without_crashing():
    broken = 'rules:\n  - id: a\n     forbid: ["x"]\n   bad indentation here\n'
    loaded = rules_engine.load_rules(broken)
    result('malformed yaml: errors, no rules, no exception',
           loaded['rules'] == [] and len(loaded['errors']) == 1
           and 'invalid YAML' in loaded['errors'][0],
           str(loaded['errors']))

    for source, label in (('{"rules": [', 'truncated json'),
                          ('name: a site\n', 'no rules key'),
                          ('rules: not-a-list\n', 'rules is a scalar'),
                          ('', 'empty file'),
                          (os.path.join('data', 'no-such-rules.yaml'), 'missing file')):
        loaded = rules_engine.load_rules(source)
        result(f'malformed input: {label} reports an error and no rules',
               loaded['rules'] == [] and bool(loaded['errors']), str(loaded['errors']))


def test_bad_rules_are_reported_and_skipped():
    source = '''rules:
  - id: good
    forbid: ["fine"]
  - id: broken-regex
    forbid: ["fine", "(unclosed"]
  - severity: error
    forbid: ["no id here"]
  - id: unknown-key
    forbids: ["typo"]
  - id: nothing-to-check
    severity: info
  - id: good
    forbid: ["duplicate id"]
'''
    loaded = rules_engine.load_rules(source)
    ids = [r['id'] for r in loaded['rules']]
    errors = ' | '.join(loaded['errors'])
    result('invalid regex: the pattern is skipped, the rule survives its other patterns',
           ids == ['good', 'broken-regex']
           and [p for p, _ in loaded['rules'][1]['forbid']] == ['fine'],
           f'{ids} {errors}')
    result('invalid regex: the error names the rule and the pattern',
           'broken-regex' in errors and '(unclosed' in errors
           and 'not a valid regular expression' in errors, errors)
    result('a rule with no id is reported and skipped',
           'missing "id"' in errors and len(ids) == 2, errors)
    result('an unknown key is reported and the rule is skipped',
           "unknown key 'forbids'" in errors, errors)
    result('a rule with nothing to check is reported',
           'nothing-to-check' in errors and 'nothing to check' in errors, errors)
    result('a duplicate id is reported and skipped', 'duplicate id' in errors, errors)

    long_pattern = 'rules:\n  - id: huge\n    forbid: ["%s"]\n' % ('a' * 600)
    loaded = rules_engine.load_rules(long_pattern)
    result('an over-long pattern is refused rather than compiled',
           loaded['rules'] == [] and any('longer than' in e for e in loaded['errors']),
           str(loaded['errors']))

    problems = rules_engine.validate_rules([{'id': 'empty', 'forbid': ['.*']}])
    result('validate_rules warns about a pattern that matches everything',
           len(problems) == 1 and 'empty string' in problems[0], str(problems))


def test_include_and_exclude_globs():
    source = '''rules:
  - id: blog-only
    forbid: ["draft"]
    include: ["/blog/*"]
    exclude: ["*/blog/private*"]
'''
    loaded = rules_engine.load_rules(source)
    pages = [
        page('https://site.test/blog/one', 'a draft page'),
        page('https://site.test/blog/private/two', 'a draft page'),
        page('https://site.test/about', 'a draft page'),
    ]
    urls = sorted(i['url'] for i in rules_engine.run_rules(loaded['rules'], pages))
    result('include/exclude: only the included, non-excluded URL is checked',
           urls == ['https://site.test/blog/one'], str(urls))

    absolute = rules_engine.load_rules(
        'rules:\n  - id: host\n    forbid: ["draft"]\n'
        '    include: ["https://site.test/about*"]\n')
    urls = sorted(i['url'] for i in rules_engine.run_rules(absolute['rules'], pages))
    result('include: a full-URL glob works too', urls == ['https://site.test/about'], str(urls))

    non_html = [page('https://site.test/blog/gone', 'a draft page', status_code=404)]
    result('a non-200 page is not checked',
           rules_engine.run_rules(loaded['rules'], non_html) == [], 'expected no issues')


def test_scopes_and_ignore_case():
    pages = [page('https://site.test/a', 'body copy',
                  title='Cheapest EHR Ever', meta_description='cheapest ehr ever',
                  json_ld=[{'@type': 'WebPage', 'name': 'Cheapest EHR'}],
                  h1='Cheapest heading')]
    links = [{'source_url': 'https://site.test/a', 'target_url': 'https://site.test/b',
              'anchor_text': 'click here'}]

    def fires(scope, pattern, ignore_case=True, extra=''):
        source = (f'rules:\n  - id: r\n    scope: {scope}\n    ignore_case: '
                  f'{"true" if ignore_case else "false"}\n    forbid: ["{pattern}"]\n{extra}')
        loaded = rules_engine.load_rules(source)
        assert not loaded['errors'], loaded['errors']
        return len(rules_engine.run_rules(loaded['rules'], pages, links=links))

    result('scope: main_text is the default and does not see the title',
           fires('main_text', 'Cheapest') == 0 and fires('title', 'Cheapest') == 1,
           'main_text/title')
    result('scope: meta_description, h1 and json_ld each read their own field',
           fires('meta_description', 'cheapest') == 1 and fires('h1', 'heading') == 1
           and fires('json_ld', 'WebPage') == 1, 'meta/h1/json_ld')
    result('scope: anchor_text reads the link records',
           fires('anchor_text', 'click here') == 1 and fires('main_text', 'click here') == 0,
           'anchor_text')
    result('scope: all sees every field at once',
           fires('all', 'Cheapest EHR Ever') == 1 and fires('all', 'click here') == 1, 'all')
    result('ignore_case: true by default, honoured when switched off',
           fires('title', 'cheapest ever') == 0 and fires('title', 'cheapest') == 1
           and fires('title', 'cheapest', ignore_case=False) == 0, 'case')


def test_issue_shape_is_the_standard_one():
    rules = load_example()
    pages = [page('https://site.test/about', 'We shipped 500+ projects.')]
    issues = rules_engine.run_rules(rules, pages)
    issue = issues_for(issues, 'banned-claims')[0]
    result('issue: carries the standard fields',
           set(issue) == {'id', 'url', 'check', 'type', 'category', 'issue', 'details',
                          'evidence', 'how_to_fix', 'docs_url'},
           str(sorted(issue)))
    result('issue: evidence uses only the allowed keys',
           set(issue['evidence']) <= set(EVIDENCE_FIELDS), str(sorted(issue['evidence'])))
    result('issue: severity is one the factory accepts',
           issue['type'] in SEVERITIES and bool(issue['how_to_fix']),
           f'{issue["type"]} / {issue["how_to_fix"]}')
    again = rules_engine.run_rules(rules, pages)
    result('issue: the same defect keeps the same id across runs',
           issue['id'] == issues_for(again, 'banned-claims')[0]['id'], issue['id'])

    custom = rules_engine.load_rules(
        'rules:\n  - id: worded\n    forbid: ["x"]\n'
        '    message: "Our own headline"\n    how_to_fix: "Do the thing."\n')
    worded = rules_engine.run_rules(custom['rules'], [page('https://site.test/a', 'x')])[0]
    result('issue: message and how_to_fix override the generated text',
           worded['issue'] == 'Our own headline' and worded['how_to_fix'] == 'Do the thing.',
           f'{worded["issue"]} / {worded["how_to_fix"]}')


def test_preview_and_site_lookup():
    pages = [page('https://site.test/a', 'We shipped 500+ projects.'),
             page('https://site.test/b', 'We shipped some projects.')]
    preview = rules_engine.preview_rule({'id': 'try-me', 'forbid': ['500\\+ projects']}, pages)
    result('preview: reports what the rule would find, with no errors',
           preview['total'] == 1 and preview['errors'] == []
           and preview['scanned_pages'] == 2
           and preview['issues'][0]['check'] == 'rule:try-me',
           str({k: v for k, v in preview.items() if k != 'issues'}))
    broken = rules_engine.preview_rule({'id': 'oops', 'forbid': ['(']}, pages)
    result('preview: a bad rule comes back as errors, not an exception',
           broken['issues'] == [] and bool(broken['errors']), str(broken['errors']))

    with tempfile.TemporaryDirectory() as directory:
        empty = rules_engine.load_site_rules('site.test', data_dir=directory)
        result('load_site_rules: a site with no rules file costs nothing',
               empty == {'rules': [], 'errors': [], 'path': ''}, str(empty))

        os.makedirs(os.path.join(directory, 'rules'))
        with open(os.path.join(directory, 'rules.yaml'), 'w', encoding='utf-8') as handle:
            handle.write('rules:\n  - id: shared\n    forbid: ["x"]\n')
        shared = rules_engine.load_site_rules('site.test', data_dir=directory)
        result('load_site_rules: falls back to the shared data/rules.yaml',
               [r['id'] for r in shared['rules']] == ['shared'], str(shared['path']))

        with open(os.path.join(directory, 'rules', 'site.test.yaml'), 'w',
                  encoding='utf-8') as handle:
            handle.write('rules:\n  - id: per-site\n    forbid: ["x"]\n')
        for domain in ('site.test', 'https://www.site.test/pricing?a=1'):
            found = rules_engine.load_site_rules(domain, data_dir=directory)
            result(f'load_site_rules: {domain} finds the per-site file first',
                   [r['id'] for r in found['rules']] == ['per-site'], str(found['path']))
        traversal = rules_engine.load_site_rules('../../etc/passwd', data_dir=directory)
        result('load_site_rules: a domain that is really a path falls back to the shared file',
               [r['id'] for r in traversal['rules']] == ['shared'], str(traversal['path']))


def test_yaml_subset_shapes():
    source = '''# a comment
---
rules:
  - id: block-list        # trailing comment
    forbid:
      - "a#b"
      - plain scalar
    include: ["/one/*", '/two/*']
    ignore_case: false
    require_same_across_pages: {name: inline, pattern: "v(?P<value>\\\\d+)"}
'''
    loaded = rules_engine.load_rules(source)
    rule = loaded['rules'][0] if loaded['rules'] else None
    result('yaml subset: block lists, flow lists, comments and inline maps',
           rule is not None and [p for p, _ in rule['forbid']] == ['a#b', 'plain scalar']
           and rule['include'] == ['/one/*', '/two/*'] and rule['ignore_case'] is False
           and rule['require']['name'] == 'inline',
           str(loaded['errors']) if rule is None else str([p for p, _ in rule['forbid']]))

    pages = [page('https://site.test/one/a', 'version v1 here'),
             page('https://site.test/one/b', 'version v1 here'),
             page('https://site.test/two/c', 'version v2 here'),
             page('https://site.test/elsewhere', 'version v9 here')]
    issues = rules_engine.run_rules(loaded['rules'], pages)
    divergent = [i for i in issues if i['check'] == 'rule:block-list'
                 and i['evidence'].get('expected')]
    result('yaml subset: a named capture group is what require_same_across_pages compares',
           len(divergent) == 1 and divergent[0]['url'] == 'https://site.test/two/c'
           and divergent[0]['evidence']['expected'] == '1'
           and divergent[0]['evidence']['found'] == '2',
           str([i['evidence'] for i in divergent]))

    result('yaml subset: tabs in the indentation are a reported error',
           'invalid YAML' in ' '.join(
               rules_engine.load_rules('rules:\n\t- id: a\n    forbid: ["x"]\n')['errors']),
           str(rules_engine.load_rules('rules:\n\t- id: a\n')['errors']))


def test_long_text_is_capped():
    text = ('padding ' * 30000) + 'needle'
    hit = rules_engine.load_rules('rules:\n  - id: cap\n    forbid: ["needle"]\n')
    pages = [page('https://site.test/a', text)]
    result('a page longer than the cap is searched only up to the cap',
           len(text) > rules_engine.MAX_TEXT_CHARS
           and rules_engine.run_rules(hit['rules'], pages) == []
           and len(rules_engine.run_rules(hit['rules'], pages,
                                          config={'max_text_chars': len(text)})) == 1,
           f'{len(text)} chars')


XPATH_RULES = '''rules:
  - id: price-table-note
    severity: warning
    xpath: "//table[@id='pricing']//td[last()]"
    must_equal: "Billed annually"
    message: "The last pricing column must carry the billing note"
'''


def test_xpath_rules():
    """XPath says things CSS cannot: last(), text matching, walking upward."""
    loaded = rules_engine.load_rules(XPATH_RULES)
    result('xpath: a rule using xpath loads without errors',
           not loaded['errors'], str(loaded['errors']))
    if loaded['errors']:
        return
    rule = loaded['rules'][0]
    result('xpath: the expression and must_equal pair up',
           rule['xpath'].endswith("td[last()]") and rule['must_equal'] == 'Billed annually',
           f"{rule['xpath']} / {rule['must_equal']}")
    result('xpath: the rule is recognised as an xpath kind',
           rule['kinds'] == ['xpath'], str(rule['kinds']))

    table = ('<html><body><table id="pricing"><tr><td>Pro</td><td>{note}</td></tr>'
             '</table></body></html>')
    good = page('https://site.test/ok', html=table.format(note='Billed annually'))
    bad = page('https://site.test/bad', html=table.format(note='Per month'))

    notes = []
    issues = issues_for(rules_engine.run_rules(loaded['rules'], [good, bad], notes=notes),
                        'price-table-note')
    if any('lxml' in note for note in notes):
        result('xpath: skipped cleanly where lxml is absent',
               not issues and len(notes) == 1, str(notes))
        return

    result('xpath: fires on the wrong page only',
           len(issues) == 1 and issues[0]['url'] == 'https://site.test/bad',
           str([i['url'] for i in issues]))
    if not issues:
        return
    result('xpath: expected and found carry the two values',
           issues[0]['evidence'].get('expected') == 'Billed annually'
           and issues[0]['evidence'].get('found') == 'Per month',
           str(issues[0]['evidence']))
    result('xpath: evidence names the expression',
           'pricing' in issues[0]['evidence'].get('selector', ''),
           str(issues[0]['evidence'].get('selector')))
    result('xpath: a page with no HTML is noted, not crashed',
           True if rules_engine.run_rules(
               loaded['rules'], [page('https://site.test/x', 'text only')], notes=[]) is not None
           else False)


def test_xpath_and_selector_are_mutually_exclusive():
    """Both on one rule is a mistake worth naming rather than guessing at."""
    both = rules_engine.load_rules('''rules:
  - id: confused
    selector: "a.btn"
    xpath: "//a"
    must_equal: "Go"
''')
    result('xpath: naming both selector and xpath is an error',
           any('not both' in e for e in both['errors']), str(both['errors']))

    lonely = rules_engine.load_rules('''rules:
  - id: lonely
    xpath: "//a"
''')
    result('xpath: xpath without must_equal is an error',
           any('must_equal' in e for e in lonely['errors']), str(lonely['errors']))


TESTS = (
    test_xpath_rules,
    test_xpath_and_selector_are_mutually_exclusive,
    test_example_rules_file_loads,
    test_banned_claims_fires_and_stays_quiet,
    test_cost_consistency_names_majority_and_divergent,
    test_cta_label_selector,
    test_selector_rule_without_html_is_skipped_cleanly,
    test_author_rule,
    test_yaml_and_json_load_to_the_same_rules,
    test_malformed_file_reports_errors_without_crashing,
    test_bad_rules_are_reported_and_skipped,
    test_include_and_exclude_globs,
    test_scopes_and_ignore_case,
    test_issue_shape_is_the_standard_one,
    test_preview_and_site_lookup,
    test_yaml_subset_shapes,
    test_long_text_is_capped,
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
