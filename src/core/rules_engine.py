"""User-defined consistency rules: plain regex and CSS selectors, no AI.

A site states the same facts on many pages - a price range, a CTA label, an
author, a claim about company size - and nothing in a generic SEO check knows
which of those facts is right. This module lets the site owner say so in a
small rules file, then reports every page that disagrees:

    rules:
      - id: banned-claims
        severity: error
        forbid: ["500\\+ projects", "15\\+ years"]
      - id: cta-label
        selector: "a.btn, button[type=submit]"
        must_equal: "Book a Discovery Call"

Nothing here fetches anything: `run_rules` reads already-crawled page dicts and
link records and returns issues built by `src.utils.issue_factory.make_issue`,
so a rule finding looks like a built-in check to the UI, the API and the
exports. Every issue's `check` slug is `rule:<rule id>` and its category is
'Rules', so one user rule is one filterable check.

Rule schema
-----------
Every rule is a map. Common keys:

    id            required, unique; becomes the check slug `rule:<id>`
    severity      error | warning | info          (default: warning)
    scope         main_text | title | meta_description | h1 | json_ld |
                  anchor_text | all               (default: main_text)
    ignore_case   true | false                    (default: true)
    message       headline shown in the UI        (default: generated)
    how_to_fix    one sentence                    (default: generated)
    docs_url      link shown next to the issue    (default: none)
    include       list of URL globs; when present, only matching URLs run
    exclude       list of URL globs; matching URLs never run

and one or more check keys:

    forbid: ["<regex>", ...]
        Any match in the scope is a finding.
    require_same_across_pages: {name: <label>, pattern: "<regex>"}
        One value is extracted per page and pages whose value differs from
        the most common one are reported. The value is the `(?P<value>...)`
        group when the pattern names one (the first named group otherwise)
        and the whole match when it names none, because a plain `(...)` is
        usually an alternation like `(to|-)`, not the value being compared.
    selector: "<css>" + must_equal: "<text>"
        Every matching element must carry exactly that text. Needs the raw
        HTML, which pages may carry in an optional 'html' key; with no HTML
        the rule is skipped once as a rule-level note, not a page issue.
    expect_author: "<name>"
        The page's JSON-LD author (or its author meta tag) must be that name.

`ignore_case` applies to the regex checks only. `must_equal` and
`expect_author` always compare exactly, after collapsing runs of whitespace.

File formats
------------
PyYAML is not a dependency and this module does not add one, so the format is
chosen by extension: `.json` is parsed by the standard library, everything else
by the tiny YAML subset parser below; both produce identical rules. The subset
supports what the rule format needs: nested maps, block sequences
(`- item`), inline flow sequences and maps (`[a, b]`, `{k: v}`), single- and
double-quoted scalars, plain scalars, booleans, null and numbers, `#` comments
and a leading `---`. It does NOT support anchors and aliases, merge keys,
multiple documents, block scalars (`|`, `>`), multi-line plain scalars,
explicit tags, complex keys, flow collections split over several lines, or tab
indentation. One deliberate deviation helps regex authors: an unknown escape in
a double-quoted scalar (`"\d+"` rather than `"\\d+"`) keeps both characters
instead of being an error, so a pattern survives either spelling.

Safety
------
Every pattern is compiled once, at load time, and a pattern that fails to
compile is reported and skipped rather than raised. Nothing here sandboxes
regex backtracking - Python's `re` has no timeout - so two blunt guards keep a
pathological rule from stalling a crawl: a pattern may not exceed
MAX_PATTERN_CHARS and the text handed to a pattern is truncated to
MAX_TEXT_CHARS. A rule that is slow anyway is the author's problem.
"""

import fnmatch
import json
import os
import re
from itertools import islice
from urllib.parse import urlsplit

from src.utils.issue_factory import make_issue

CATEGORY = 'Rules'

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
DEFAULT_RULES_PATH = os.path.join(DATA_DIR, 'rules.yaml')
SITE_RULES_DIR = os.path.join(DATA_DIR, 'rules')

SEVERITIES = ('error', 'warning', 'info')
DEFAULT_SEVERITY = 'warning'
SCOPES = ('main_text', 'title', 'meta_description', 'h1', 'json_ld', 'anchor_text', 'all')
DEFAULT_SCOPE = 'main_text'

# See "Safety" in the module docstring.
MAX_PATTERN_CHARS = 500
MAX_TEXT_CHARS = 200000

SNIPPET_CONTEXT = 40    # characters kept either side of a match in evidence.snippet
MAX_MATCHES = 20        # matches examined per pattern per page
MAX_RELATED_URLS = 5    # peer URLs listed in evidence.related_urls

CHECK_KEYS = ('forbid', 'require_same_across_pages', 'selector', 'xpath',
              'expect_author')

RULE_KEYS = frozenset({
    'id', 'severity', 'scope', 'ignore_case', 'message', 'how_to_fix', 'docs_url',
    'include', 'exclude', 'must_equal', 'enabled',
} | set(CHECK_KEYS))


# The YAML subset these files are written in lives in its own module;
# it is a parser, not a rules concern.
from src.core.yaml_subset import (_ParseError, _parse_node, _scan,  # noqa: F401
                                  _split_flow, _split_key, _strip_comment,
                                  _unquote, _yaml_lines, parse_yaml_subset)


# ---------------------------------------------------------------------------
# Loading and compiling rules
# ---------------------------------------------------------------------------

def _read_source(path_or_text):
    """Return (document, errors). Accepts a path, raw text, or parsed data."""
    if isinstance(path_or_text, (dict, list)):
        return path_or_text, []
    if not isinstance(path_or_text, str) or not path_or_text.strip():
        return None, ['rules source is empty']

    text = path_or_text
    as_json = None
    is_path = '\n' not in path_or_text and len(path_or_text) < 4096
    extension = os.path.splitext(path_or_text)[1].lower() if is_path else ''
    if is_path and os.path.isfile(path_or_text):
        try:
            with open(path_or_text, 'r', encoding='utf-8') as handle:
                text = handle.read()
        except OSError as exc:
            return None, [f'could not read {path_or_text}: {exc}']
        as_json = extension == '.json'
    elif extension in ('.yaml', '.yml', '.json'):
        return None, [f'rules file not found: {path_or_text}']
    if as_json is None:
        as_json = text.lstrip()[:1] in ('{', '[')
    if as_json:
        try:
            return json.loads(text), []
        except ValueError as exc:
            return None, [f'invalid JSON: {exc}']
    try:
        return parse_yaml_subset(text), []
    except _ParseError as exc:
        return None, [f'invalid YAML: {exc}']
    except Exception as exc:  # a rules file must never take the crawl down
        return None, [f'could not parse rules: {type(exc).__name__}: {exc}']


def _as_list(value):
    if value in (None, ''):
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v not in (None, '')]
    return [value]


def _compile_pattern(pattern, ignore_case, label, rule_name, errors):
    """Compile one pattern, or record why it was skipped and return None."""
    if not isinstance(pattern, str) or not pattern:
        errors.append(f'{rule_name}: {label} must be a non-empty string')
        return None
    if len(pattern) > MAX_PATTERN_CHARS:
        errors.append(f'{rule_name}: {label} is longer than {MAX_PATTERN_CHARS} characters '
                      f'({len(pattern)}); shorten it or split the rule')
        return None
    try:
        return re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        errors.append(f'{rule_name}: {label} is not a valid regular expression ({exc}): {pattern}')
        return None


def _choice(raw, key, allowed, default, rule_name, errors):
    """One of `allowed`, or the default with a note about the bad value."""
    value = str(raw.get(key) or default).strip().lower()
    if value in allowed:
        return value
    errors.append(f'{rule_name}: {key} {raw.get(key)!r} is not one of '
                  f'{", ".join(allowed)}; using "{default}"')
    return default


def _compile_rule(raw, index):
    """Return (compiled_rule_or_None, errors) for one raw rule map."""
    errors = []
    position = f'rule #{index + 1}'
    if not isinstance(raw, dict):
        return None, [f'{position}: expected a map of rule keys, found {type(raw).__name__}']

    rule_id = raw.get('id')
    if not isinstance(rule_id, str) or not rule_id.strip():
        return None, [f'{position}: missing "id" (every rule needs a unique id)']
    rule_id = rule_id.strip()
    name = f'rule "{rule_id}"'

    unknown = sorted(set(raw) - RULE_KEYS)
    if unknown:
        return None, [f'{name}: unknown key{"s" if len(unknown) > 1 else ""} '
                      f'{", ".join(repr(k) for k in unknown)}; known keys are '
                      f'{", ".join(sorted(RULE_KEYS))}']

    severity = _choice(raw, 'severity', SEVERITIES, DEFAULT_SEVERITY, name, errors)
    scope = _choice(raw, 'scope', SCOPES, DEFAULT_SCOPE, name, errors)
    ignore_case = raw.get('ignore_case', True)
    ignore_case = True if ignore_case in (None, '') else bool(ignore_case)

    rule = {
        '_compiled': True,
        'id': rule_id,
        'check': f'rule:{rule_id}',
        'severity': severity,
        'scope': scope,
        'ignore_case': ignore_case,
        'message': str(raw.get('message') or ''),
        'how_to_fix': str(raw.get('how_to_fix') or ''),
        'docs_url': str(raw.get('docs_url') or ''),
        'include': [str(p) for p in _as_list(raw.get('include'))],
        'exclude': [str(p) for p in _as_list(raw.get('exclude'))],
        'enabled': raw.get('enabled', True) not in (False, 'false', 'False', 0),
        'forbid': [],
        'require': None,
        'selector': '',
        'xpath': '',
        'must_equal': '',
        'expect_author': '',
        'kinds': [],
        'raw': raw,
    }

    for pattern in _as_list(raw.get('forbid')):
        compiled = _compile_pattern(pattern, ignore_case, 'forbid pattern', name, errors)
        if compiled is not None:
            rule['forbid'].append((pattern, compiled))
    if rule['forbid']:
        rule['kinds'].append('forbid')
    elif 'forbid' in raw and not _as_list(raw.get('forbid')):
        errors.append(f'{name}: "forbid" is empty')

    require = raw.get('require_same_across_pages')
    if require not in (None, ''):
        if not isinstance(require, dict):
            errors.append(f'{name}: require_same_across_pages must be a map with '
                          f'"name" and "pattern"')
        else:
            pattern = require.get('pattern')
            compiled = _compile_pattern(pattern, ignore_case,
                                        'require_same_across_pages pattern', name, errors)
            if compiled is not None:
                rule['require'] = {
                    'name': str(require.get('name') or rule_id),
                    'pattern': pattern,
                    'regex': compiled,
                }
                rule['kinds'].append('require_same_across_pages')

    selector, xpath = raw.get('selector'), raw.get('xpath')
    must_equal = raw.get('must_equal')
    if selector and xpath:
        errors.append(f'{name}: use "selector" or "xpath", not both')
    elif selector and must_equal:
        rule['selector'], rule['must_equal'] = str(selector), str(must_equal)
        rule['kinds'].append('selector')
    elif xpath and must_equal:
        rule['xpath'], rule['must_equal'] = str(xpath), str(must_equal)
        rule['kinds'].append('xpath')
    elif selector or xpath or must_equal:
        errors.append(f'{name}: "selector" or "xpath" only works paired with "must_equal"')

    author = raw.get('expect_author')
    if author not in (None, ''):
        rule['expect_author'] = str(author)
        rule['kinds'].append('expect_author')

    if not rule['kinds']:
        detail = '; '.join(errors) if errors else 'none of ' + ', '.join(CHECK_KEYS) + ' is set'
        return None, [f'{name}: nothing to check ({detail})']
    return rule, errors


def load_rules(path_or_text):
    """Load and compile a rules file, a rules string, or parsed rules data.

    Returns {'rules': [compiled rules], 'errors': [human-readable strings]} and
    never raises: a rule that cannot be compiled is reported in 'errors' and
    left out of 'rules', so one broken rule never costs the others.
    """
    try:
        document, errors = _read_source(path_or_text)
    except Exception as exc:  # defensive: the loader is on the crawl path
        return {'rules': [], 'errors': [f'could not read rules: {type(exc).__name__}: {exc}']}
    if document is None:
        return {'rules': [], 'errors': errors}

    if isinstance(document, dict):
        raw_rules = document.get('rules')
        if raw_rules in (None, ''):
            return {'rules': [], 'errors': errors + ['rules file has no "rules:" list']}
    else:
        raw_rules = document
    if not isinstance(raw_rules, list):
        return {'rules': [], 'errors': errors + ['"rules" must be a list of rules']}

    rules = []
    seen = {}
    for index, raw in enumerate(raw_rules):
        try:
            rule, rule_errors = _compile_rule(raw, index)
        except Exception as exc:  # defensive: never raise out of load_rules
            errors.append(f'rule #{index + 1}: could not read ({type(exc).__name__}: {exc})')
            continue
        errors.extend(rule_errors)
        if rule is None:
            continue
        if rule['id'] in seen:
            errors.append(f'rule "{rule["id"]}": duplicate id (also rule #{seen[rule["id"]]}); '
                          f'this one is skipped')
            continue
        seen[rule['id']] = index + 1
        if rule['enabled']:
            rules.append(rule)
    return {'rules': rules, 'errors': errors}


def _safe_domain(base_domain):
    """A domain reduced to a bare hostname, or '' when it cannot be a filename."""
    if not base_domain or not isinstance(base_domain, str):
        return ''
    domain = base_domain.strip().lower()
    if '://' in domain:
        domain = urlsplit(domain).netloc or domain.split('://', 1)[1]
    domain = domain.split('/')[0].split('@')[-1].split(':')[0]
    if not domain or '..' in domain or not re.match(r'^[a-z0-9._-]+$', domain):
        return ''
    return domain


def load_site_rules(base_domain, data_dir=None):
    """Find this site's rules file, falling back to the shared one.

    Looks for data/rules/<domain>.yaml (then .yml/.json, then the www / non-www
    spelling of the domain), then data/rules.yaml (then .yml/.json). A site
    with no rules file costs nothing: the result is empty and no file is read.

    Returns load_rules' dict plus 'path' - '' when no rules file exists.
    """
    root = data_dir or DATA_DIR
    domain = _safe_domain(base_domain)
    candidates = []
    if domain:
        alternates = [domain, domain[4:] if domain.startswith('www.') else 'www.' + domain]
        for name in dict.fromkeys(alternates):
            for extension in ('.yaml', '.yml', '.json'):
                candidates.append(os.path.join(root, 'rules', name + extension))
    for extension in ('.yaml', '.yml', '.json'):
        candidates.append(os.path.join(root, 'rules' + extension))

    for path in candidates:
        if os.path.isfile(path):
            result = load_rules(path)
            result['path'] = path
            return result
    return {'rules': [], 'errors': [], 'path': ''}


def validate_rules(rules):
    """Human-readable problems with a rule list, for "test before saving".

    Accepts raw rules (straight from a form or a parsed file) or rules already
    compiled by load_rules. Returns [] when everything is usable.
    """
    problems = []
    if isinstance(rules, dict):
        rules = rules.get('rules') or []
    seen = {}
    for index, rule in enumerate(rules or []):
        raw = rule.get('raw', rule) if isinstance(rule, dict) else rule
        compiled, errors = _compile_rule(raw, index)
        problems.extend(errors)
        if compiled is None:
            continue
        if compiled['id'] in seen:
            problems.append(f'rule "{compiled["id"]}": duplicate id '
                            f'(also rule #{seen[compiled["id"]]})')
        seen[compiled['id']] = index + 1
        patterns = list(compiled['forbid'])
        if compiled['require']:
            patterns.append((compiled['require']['pattern'], compiled['require']['regex']))
        for pattern, regex in patterns:
            if regex.search(''):
                problems.append(f'rule "{compiled["id"]}": this pattern matches the empty string, '
                                f'so it would fire on every page: {pattern}')
        if ({'selector', 'xpath'} & set(compiled['kinds'])
                and compiled['scope'] != DEFAULT_SCOPE):
            problems.append(f'rule "{compiled["id"]}": "scope" does not apply to a selector rule '
                            f'(selectors always run against the page HTML)')
    return problems


# ---------------------------------------------------------------------------
# Running rules against crawled pages
# ---------------------------------------------------------------------------

def _url_matches(url, pattern):
    if fnmatch.fnmatchcase(url, pattern):
        return True
    if '://' not in pattern:
        parts = urlsplit(url)
        path = parts.path or '/'
        if fnmatch.fnmatchcase(path, pattern):
            return True
        if parts.query and fnmatch.fnmatchcase(f'{path}?{parts.query}', pattern):
            return True
    return False


def _rule_applies(rule, page):
    url = page.get('url') or ''
    if not url:
        return False
    status = page.get('status_code')
    if isinstance(status, int) and status and not 200 <= status < 300:
        return False
    if rule['include'] and not any(_url_matches(url, p) for p in rule['include']):
        return False
    if any(_url_matches(url, p) for p in rule['exclude']):
        return False
    return True


def _collapse(text):
    return ' '.join(str(text).split())


def _anchors_by_page(links):
    """{source_url: 'anchor\\nanchor\\n...'} from link records."""
    anchors = {}
    for link in links or []:
        if not isinstance(link, dict):
            continue
        source = link.get('source_url')
        text = link.get('anchor_text')
        if not source or not text:
            continue
        anchors.setdefault(source, []).append(str(text))
    return {url: '\n'.join(texts) for url, texts in anchors.items()}


def _scope_text(page, scope, anchors):
    """The text one scope hands to a pattern ('all' joins every other scope)."""
    if scope in ('main_text', 'title', 'meta_description'):
        return str(page.get(scope) or '')
    if scope == 'h1':
        h1 = page.get('h1') or ''
        return '\n'.join(str(v) for v in h1) if isinstance(h1, list) else str(h1)
    if scope == 'json_ld':
        nodes = page.get('json_ld') or []
        try:
            return json.dumps(nodes, ensure_ascii=False, default=str) if nodes else ''
        except (TypeError, ValueError):
            return str(nodes)
    if scope == 'anchor_text':
        return anchors.get(page.get('url') or '', '')
    if scope == 'all':
        parts = (_scope_text(page, s, anchors) for s in SCOPES if s != 'all')
        return '\n'.join(part for part in parts if part)
    return ''


def _page_text(page, rule, anchors, max_chars):
    text = _scope_text(page, rule['scope'], anchors)
    return text[:max_chars] if len(text) > max_chars else text


def _snippet(text, start, end):
    low = max(0, start - SNIPPET_CONTEXT)
    high = min(len(text), end + SNIPPET_CONTEXT)
    out = _collapse(text[low:high])
    if low > 0:
        out = '...' + out
    if high < len(text):
        out += '...'
    return out


def _limited_matches(regex, text):
    return list(islice(regex.finditer(text), MAX_MATCHES))


def _issue(rule, url, default_message, details, offending_value, evidence, default_fix):
    return make_issue(
        url=url, check=rule['check'], severity=rule['severity'], category=CATEGORY,
        issue=rule['message'] or default_message, details=details,
        offending_value=offending_value, evidence=evidence,
        how_to_fix=rule['how_to_fix'] or default_fix, docs_url=rule['docs_url'])


def _run_forbid(rule, page, text):
    """One issue per page: the first matching pattern, with the rest counted."""
    hits = []
    for pattern, regex in rule['forbid']:
        matches = _limited_matches(regex, text)
        if matches:
            hits.append((pattern, matches[0], len(matches)))
    if not hits:
        return None

    pattern, match, _ = hits[0]
    matched = _collapse(match.group(0))[:200]
    total = sum(count for _, _, count in hits)
    extra = (f' ({total} matches{f" from {len(hits)} patterns" if len(hits) > 1 else ""} '
             f'on this page)') if total > 1 else ''
    return _issue(
        rule, page.get('url') or '',
        f'Forbidden phrase (rule "{rule["id"]}")',
        f'Forbidden pattern "{pattern}" matches "{matched}" in {rule["scope"]}{extra}.',
        matched,
        {'snippet': _snippet(text, match.start(), match.end()), 'found': matched},
        f'Remove or reword the text matching "{pattern}" on this page.',
    )


def _run_expect_author(rule, page):
    """One issue per page when the page's author is not the expected one."""
    expected = rule['expect_author']
    authors = [_collapse(a) for a in _page_authors(page)]
    if _collapse(expected) in authors:
        return None
    found = authors[0] if authors else ''
    details = (f'Author is "{found}" but this site\'s rules expect "{expected}".' if found
               else f'No author found on this page; this site\'s rules expect "{expected}".')
    return _issue(
        rule, page.get('url') or '',
        f'Unexpected author (rule "{rule["id"]}")',
        details,
        found,
        {'expected': expected, 'found': found or '(none)'},
        f'Set the author to "{expected}" in the page\'s JSON-LD and author meta tag.',
    )


def _page_authors(page):
    """Author names from JSON-LD first, then the author meta tag."""
    found = []

    def collect(value):
        if isinstance(value, dict):
            value = value.get('name')
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
        elif isinstance(value, list):
            for item in value:
                collect(item)

    def walk(node):
        if isinstance(node, dict):
            if 'author' in node:
                collect(node['author'])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(page.get('json_ld') or [])
    meta = page.get('author') or (page.get('meta_tags') or {}).get('author') or ''
    if isinstance(meta, str) and meta.strip():
        found.append(meta.strip())
    return list(dict.fromkeys(found))


def _element_selector(element):
    """A short, human-checkable path to one element ('body > a.btn:nth-of-type(2)')."""
    parts = []
    node = element
    while node is not None and getattr(node, 'name', None) and node.name != '[document]':
        step = node.name
        classes = node.get('class') if hasattr(node, 'get') else None
        if classes:
            step += '.' + str(classes[0])
        parent = node.parent
        if parent is not None and getattr(parent, 'name', None):
            siblings = parent.find_all(node.name, recursive=False)
            if len(siblings) > 1 and node in siblings:
                step += f':nth-of-type({siblings.index(node) + 1})'
        parts.append(step)
        if node.name == 'body':
            break
        node = node.parent
    return ' > '.join(reversed(parts))


def _run_selector(rule, page, soup):
    """One issue per page: the first element whose text is not must_equal."""
    try:
        elements = soup.select(rule['selector'])
    except Exception as exc:  # an unusable selector is a rule problem, not a page problem
        raise ValueError(f'selector "{rule["selector"]}" could not be used ({exc})')
    if not elements:
        return None
    expected = _collapse(rule['must_equal'])
    wrong = [el for el in elements if _collapse(el.get_text()) != expected]
    if not wrong:
        return None

    element = wrong[0]
    found = _collapse(element.get_text())
    extra = f' ({len(wrong)} of {len(elements)} matching elements differ)' if len(elements) > 1 else ''
    details = (f'Element matching "{rule["selector"]}" reads "{found or "(empty)"}" '
               f'but must read "{expected}"{extra}.')
    evidence = {
        'snippet': str(element)[:300],
        'selector': _element_selector(element) or rule['selector'],
        'expected': expected,
        'found': found or '(empty)',
    }
    line = getattr(element, 'sourceline', None)
    if isinstance(line, int):
        evidence['line'] = line
    return _issue(
        rule, page.get('url') or '',
        f'Unexpected element text (rule "{rule["id"]}")',
        details,
        found,
        evidence,
        f'Change every element matching "{rule["selector"]}" to read "{expected}".',
    )


def _run_xpath(rule, page, tree):
    """One issue per page: the first node whose text is not must_equal.

    XPath earns its place next to CSS because it can say things CSS cannot -
    select on text content, walk back up to a parent, index into a set - and
    those are exactly the assertions a content rule wants to make.
    """
    try:
        nodes = tree.xpath(rule['xpath'])
    except Exception as exc:  # an unusable expression is a rule problem
        raise ValueError(f'xpath "{rule["xpath"]}" could not be used ({exc})')
    if not nodes:
        return None

    def text_of(node):
        if isinstance(node, str):
            return _collapse(node)
        method = getattr(node, 'itertext', None)
        return _collapse(''.join(method())) if method else _collapse(str(node))

    expected = _collapse(rule['must_equal'])
    wrong = [node for node in nodes if text_of(node) != expected]
    if not wrong:
        return None

    node = wrong[0]
    found = text_of(node)
    extra = f' ({len(wrong)} of {len(nodes)} matching nodes differ)' if len(nodes) > 1 else ''
    evidence = {
        'selector': rule['xpath'],
        'expected': expected,
        'found': found or '(empty)',
    }
    line = getattr(node, 'sourceline', None)
    if isinstance(line, int):
        evidence['line'] = line
    return _issue(
        rule, page.get('url') or '',
        f'Unexpected element text (rule "{rule["id"]}")',
        (f'Node matching "{rule["xpath"]}" reads "{found or "(empty)"}" '
         f'but must read "{expected}"{extra}.'),
        found,
        evidence,
        f'Change every node matching "{rule["xpath"]}" to read "{expected}".',
    )


def _run_xpath_rule(rule, pages, notes):
    """Like a selector rule, but needs lxml, which an install may not have."""
    issues = []
    try:
        from lxml import html as lxml_html
    except ImportError:
        notes.append(f'rule "{rule["id"]}": skipped - xpath rules need lxml, which is not '
                     f'installed here (pip install lxml)')
        return issues

    with_html = [p for p in pages if p.get('html')]
    if not with_html:
        notes.append(f'rule "{rule["id"]}": skipped - no page HTML available in this crawl '
                     f'(xpath rules need the raw HTML of a page)')
        return issues
    if len(with_html) < len(pages):
        notes.append(f'rule "{rule["id"]}": checked {len(with_html)} of {len(pages)} pages; '
                     f'the rest carried no HTML')
    for page in with_html:
        try:
            issue = _run_xpath(rule, page, lxml_html.fromstring(page['html']))
        except ValueError as exc:
            notes.append(f'rule "{rule["id"]}": skipped - {exc}')
            return issues
        except Exception as exc:
            notes.append(f'rule "{rule["id"]}": could not read {page.get("url")} '
                         f'({type(exc).__name__}: {exc})')
            continue
        if issue:
            issues.append(issue)
    return issues


def _run_selector_rule(rule, pages, notes):
    """Selector rules need raw HTML, which a stored page dict does not carry."""
    issues = []
    with_html = [p for p in pages if p.get('html')]
    if not with_html:
        notes.append(f'rule "{rule["id"]}": skipped - no page HTML available in this crawl '
                     f'(selector rules need the raw HTML of a page)')
        return issues
    from bs4 import BeautifulSoup  # already a dependency; only selectors need it
    if len(with_html) < len(pages):
        notes.append(f'rule "{rule["id"]}": checked {len(with_html)} of {len(pages)} pages; '
                     f'the rest carried no HTML')
    for page in with_html:
        try:
            soup = BeautifulSoup(page['html'], 'html.parser')
            issue = _run_selector(rule, page, soup)
        except ValueError as exc:
            notes.append(f'rule "{rule["id"]}": skipped - {exc}')
            return issues
        except Exception as exc:
            notes.append(f'rule "{rule["id"]}": could not read {page.get("url")} '
                         f'({type(exc).__name__}: {exc})')
            continue
        if issue:
            issues.append(issue)
    return issues


def _extracted_value(match):
    """The compared part of a match: a named group means "this is the value",
    while an unnamed one is usually just an alternation, so it is ignored."""
    named = match.re.groupindex
    if named:
        first = 'value' if 'value' in named else min(named, key=lambda n: named[n])
        return match.group(first) or ''
    return match.group(0)


def _run_require_same(rule, pages, anchors, max_chars):
    """Report every page whose extracted value differs from the majority."""
    require = rule['require']
    values = []  # (page, display value, comparison key, match, text)
    counts = {}
    for page in pages:
        text = _page_text(page, rule, anchors, max_chars)
        match = require['regex'].search(text)
        if not match:
            continue
        display = _collapse(_extracted_value(match))
        if not display:
            continue
        key = display.lower() if rule['ignore_case'] else display
        values.append((page, display, key, match, text))
        counts.setdefault(key, {'count': 0, 'display': display})['count'] += 1
    if len(values) < 2:
        return []
    majority_key = max(counts, key=lambda k: counts[k]['count'])
    majority = counts[majority_key]['display']
    if counts[majority_key]['count'] == len(values):
        return []

    peers = [p.get('url') for p, _, key, _, _ in values
             if key == majority_key and p.get('url')][:MAX_RELATED_URLS]
    issues = []
    for page, display, key, match, text in values:
        if key == majority_key:
            continue
        details = (f'{require["name"]} reads "{display}" here but "{majority}" on '
                   f'{counts[majority_key]["count"]} of {len(values)} pages that state it.')
        issues.append(_issue(
            rule, page.get('url') or '',
            rule['message'] or f'Inconsistent {require["name"]} (rule "{rule["id"]}")',
            details,
            display,
            {'snippet': _snippet(text, match.start(), match.end()), 'expected': majority,
             'found': display, 'related_urls': peers},
            f'Make this page say "{majority}", or update every page at once if the '
            f'new value is the correct one.',
        ))
    return issues


def run_rules(rules, pages, links=None, config=None, notes=None):
    """Run compiled rules over crawled pages and return issues.

    `rules` is the list from load_rules (its dict is accepted too; raw rules
    are compiled on the way in and skipped if they fail). `pages` are crawled
    page dicts, plus the optional 'html' that only selector rules need.
    `links` (source_url, target_url, anchor_text) are read by the
    'anchor_text' scope only. `config` takes {'max_text_chars': int}. Pass a
    list as `notes` to collect rule-level notes: a selector rule skipped for
    want of HTML, a rule that could not run.

    One issue per rule per page for forbid, selector and expect_author; one
    issue per divergent page for require_same_across_pages.
    """
    if isinstance(rules, dict):
        rules = rules.get('rules') or []
    if notes is None:
        notes = []
    pages = [p for p in (pages or []) if isinstance(p, dict)]
    max_chars = int((config or {}).get('max_text_chars') or MAX_TEXT_CHARS)
    anchors = _anchors_by_page(links)

    issues = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        if not rule.get('_compiled'):
            rule, errors = _compile_rule(rule, 0)
            notes.extend(errors)
            if rule is None:
                continue
        if not rule.get('enabled', True):
            continue
        scoped = [p for p in pages if _rule_applies(rule, p)]
        if not scoped:
            continue
        try:
            for kind in rule['kinds']:
                if kind == 'require_same_across_pages':
                    issues.extend(_run_require_same(rule, scoped, anchors, max_chars))
                elif kind == 'selector':
                    issues.extend(_run_selector_rule(rule, scoped, notes))
                elif kind == 'xpath':
                    issues.extend(_run_xpath_rule(rule, scoped, notes))
                else:  # forbid and expect_author look at one page at a time
                    for page in scoped:
                        issue = (_run_forbid(rule, page, _page_text(page, rule, anchors, max_chars))
                                 if kind == 'forbid' else _run_expect_author(rule, page))
                        if issue:
                            issues.append(issue)
        except Exception as exc:  # one broken rule must not lose the others
            notes.append(f'rule "{rule.get("id")}": stopped after an error '
                         f'({type(exc).__name__}: {exc})')
    return issues


def preview_rule(rule, pages, links=None, limit=20):
    """What one rule would report, for "test a rule before saving it".

    Takes a raw rule map (straight from the editor) or a compiled one; returns
    {'issues': [...to limit...], 'total', 'errors', 'notes', 'scanned_pages'}.
    """
    raw = rule.get('raw', rule) if isinstance(rule, dict) else rule
    compiled, errors = _compile_rule(raw, 0)
    pages = [p for p in (pages or []) if isinstance(p, dict)]
    if compiled is None:
        return {'issues': [], 'total': 0, 'errors': errors, 'notes': [], 'scanned_pages': 0}
    notes = []
    issues = run_rules([compiled], pages, links=links, notes=notes)
    return {
        'issues': issues[:limit] if limit else issues,
        'total': len(issues),
        'errors': errors + validate_rules([raw]),
        'notes': notes,
        'scanned_pages': len([p for p in pages if _rule_applies(compiled, p)]),
    }
