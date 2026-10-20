"""Structured-data checks for already-crawled pages.

Split out of src/core/technical_checks.py so that module stays under the
1000-line project limit; everything here is reached through
`technical_issues()` and shares its config dict and its grouping rule.

Nothing here fetches or re-parses anything: the checks read the JSON-LD the
extractor already stored on the page dict.

    json_ld           blocks that parsed (src/core/seo_extractor.py drops the
                      ones that did not)
    json_ld_raw       optional: the raw <script type="application/ld+json">
                      bodies. When a crawl carries them, this module parses
                      them itself so a block that fails to parse - which is
                      invisible in `json_ld` - is reported.
    json_ld_errors    optional: parse failures already recorded by the
                      crawler, as strings or {message, line, snippet} dicts.

The checks:

    jsonld_parse_error          a JSON-LD block the browser can read but a
                                parser cannot
    faq_answer_not_visible      an FAQPage question that is not in the visible
                                copy verbatim. A numbering prefix on either
                                side ("1. What is X?" vs "What is X?") is a
                                mismatch, which is the point: Google requires
                                the marked-up question to be the visible one.
    jsonld_dangling_id          a {"@id": "..."} reference pointing at a node
                                no block on this page defines
    self_serving_review         Review / AggregateRating markup on the site's
                                own page (a Google policy risk since 2019)
    breadcrumb_position_invalid BreadcrumbList positions that are not 1..n
    breadcrumb_item_incomplete  a breadcrumb step with no name, or no item URL
                                anywhere but the last step
"""

import json
import re

CATEGORY = 'Structured Data'

# "1." / "1)" / "-" / bullet at the start of a question
_NUMBERING_RE = re.compile(r'^\s*(?:\d+\s*[.)\]]|[-–—•*])\s*')

_SNIPPET = 200

_SPECS = (
    ('parse_error', 'jsonld_parse_error', 'error', 'JSON-LD Fails to Parse',
     '{count} pages carry a JSON-LD block that does not parse.',
     'Fix the JSON syntax; a block that does not parse is ignored entirely by search engines.'),
    ('faq_mismatch', 'faq_answer_not_visible', 'error', 'FAQ Markup Not in the Visible Text',
     '{count} pages mark up FAQ questions that are not in the visible copy verbatim.',
     'Make the marked-up question the exact string the page shows, numbering prefixes included.'),
    ('dangling_id', 'jsonld_dangling_id', 'warning', 'JSON-LD @id Resolves to Nothing',
     '{count} pages reference a JSON-LD @id that no block on the page defines.',
     'Define the referenced node on the page, or inline the object instead of referencing it.'),
    ('self_review', 'self_serving_review', 'warning', 'Self-Serving Review Markup',
     '{count} pages carry Review or AggregateRating markup about the site itself.',
     'Remove review markup about your own business; Google ignores self-serving reviews '
     'and may issue a structured-data manual action.'),
    ('breadcrumb_position', 'breadcrumb_position_invalid', 'warning',
     'BreadcrumbList Positions Are Wrong',
     '{count} pages carry a BreadcrumbList whose positions are not 1..n.',
     'Number every itemListElement from 1 upwards, once each, in display order.'),
    ('breadcrumb_item', 'breadcrumb_item_incomplete', 'warning', 'Incomplete Breadcrumb Step',
     '{count} pages carry a breadcrumb step with no name or no item URL.',
     'Give every step a name, and every step but the last an item URL.'),
)


def schema_issues(pages, cfg, emit):
    """Run every structured-data check.

    `emit(check, severity, name, items, group_details, how_to_fix)` is
    technical_checks' grouping helper, already bound to the issue category.
    """
    buckets = {spec[0]: [] for spec in _SPECS}

    for page in pages:
        url = (page.get('url') or '').strip()
        if not url:
            continue
        _parse_errors(url, page, buckets)

        nodes = list(_walk(page.get('json_ld') or []))
        if not nodes:
            continue
        _faq(url, page, nodes, buckets)
        _dangling_ids(url, nodes, buckets)
        _reviews(url, nodes, buckets)
        _breadcrumbs(url, nodes, buckets)

    for bucket, check, severity, name, group_details, how_to_fix in _SPECS:
        emit(check, severity, name, buckets[bucket], group_details, how_to_fix)


# --------------------------------------------------------------------------
# reading the blocks
# --------------------------------------------------------------------------

def _walk(value, depth=0):
    """Every dict inside the JSON-LD blocks, @graph and nesting included."""
    if depth > 12:
        return
    if isinstance(value, dict):
        yield value
        for item in value.values():
            if isinstance(item, (dict, list)):
                yield from _walk(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item, depth + 1)


def _types(node):
    """The @type values of one node, lowercased."""
    raw = node.get('@type') or node.get('type') or []
    if isinstance(raw, str):
        raw = [raw]
    elif not isinstance(raw, list):
        return set()
    return {str(item).rsplit('/', 1)[-1].strip().lower() for item in raw if item}


def _text(value):
    """A JSON-LD string value, whatever wrapper it arrived in."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ('@value', 'name', 'text'):
            if isinstance(value.get(key), str):
                return value[key]
    if isinstance(value, list) and value:
        return _text(value[0])
    return ''


def _norm(text):
    """Whitespace collapsed, ends trimmed - the form everything compares in."""
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def _unnumbered(text):
    return _NUMBERING_RE.sub('', _norm(text)).casefold()


def _as_list(value):
    if isinstance(value, list):
        return value
    if value in (None, ''):
        return []
    return [value]


# --------------------------------------------------------------------------
# the checks
# --------------------------------------------------------------------------

def _raw_blocks(page):
    for key in ('json_ld_raw', 'jsonld_raw', 'json_ld_blocks'):
        raw = page.get(key)
        if isinstance(raw, list):
            return raw
    return []


def _parse_errors(url, page, buckets):
    """JSON-LD that a parser rejects, from raw bodies or recorded failures."""
    seen = set()

    for index, raw in enumerate(_raw_blocks(page), start=1):
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            json.loads(raw)
            continue
        except (ValueError, TypeError) as exc:
            reason = str(exc)
        offending = f'block {index}'
        seen.add(offending)
        buckets['parse_error'].append({
            'url': url, 'offending': offending,
            'details': f'JSON-LD block {index} does not parse: {reason}.',
            'evidence': {'snippet': _norm(raw)[:_SNIPPET], 'found': reason,
                         'selector': f'script[type="application/ld+json"]:nth-of-type({index})'},
        })

    for index, entry in enumerate(page.get('json_ld_errors') or [], start=1):
        if isinstance(entry, dict):
            reason = str(entry.get('message') or entry.get('error') or 'invalid JSON')
            snippet = _norm(entry.get('snippet') or '')[:_SNIPPET]
            line = entry.get('line')
        else:
            reason, snippet, line = str(entry), '', None
        offending = f'block {index}'
        if offending in seen:
            continue
        evidence = {'found': reason}
        if snippet:
            evidence['snippet'] = snippet
        if line:
            evidence['line'] = line
        buckets['parse_error'].append({
            'url': url, 'offending': offending,
            'details': f'JSON-LD block {index} does not parse: {reason}.',
            'evidence': evidence,
        })


def _visible_text(page):
    """The copy a reader sees: main text plus every heading.

    A crawl result is data, not a contract: a page loaded from an older
    database or a plugin can carry anything in these fields, and a check that
    raises on one odd page loses the whole crawl's findings.
    """
    main_text = page.get('main_text')
    parts = [main_text if isinstance(main_text, str) else '']
    headings = page.get('headings')
    if isinstance(headings, (list, tuple)):
        for heading in headings:
            if isinstance(heading, dict):
                text = heading.get('text')
                parts.append(text if isinstance(text, str) else '')
    return _norm(' • '.join(part for part in parts if part))


def _faq(url, page, nodes, buckets):
    """FAQPage questions that the page does not show verbatim."""
    questions = []
    for node in nodes:
        if 'faqpage' in _types(node):
            for entry in _as_list(node.get('mainEntity') or node.get('mainEntityOfPage')):
                if isinstance(entry, dict) and 'question' in _types(entry):
                    name = _norm(_text(entry.get('name') or entry.get('text')))
                    if name:
                        questions.append(name)
    if not questions:
        return

    visible = _visible_text(page)
    if not visible:
        return
    headings = [_norm(h.get('text')) for h in (page.get('headings') or [])
                if isinstance(h, dict) and _norm(h.get('text'))]

    for question in questions:
        near = [heading for heading in headings
                if _unnumbered(heading) == _unnumbered(question)]
        if near:
            if any(heading == question for heading in near):
                continue
            found = near[0]
            detail = (f'The page shows "{found}" but the FAQ markup says "{question}" - '
                      'they must match character for character.')
        else:
            if question in visible:
                continue
            found = ''
            detail = (f'The FAQ markup asks "{question}", which does not appear in the '
                      'visible text of the page.')
        buckets['faq_mismatch'].append({
            'url': url, 'offending': question, 'details': detail,
            'evidence': {'expected': question, 'found': found or '(not on the page)',
                         'snippet': found[:_SNIPPET] if found else ''},
        })


def _dangling_ids(url, nodes, buckets):
    """{"@id": "..."} references with nothing on the page behind them."""
    defined = set()
    references = []
    for node in nodes:
        identifier = node.get('@id')
        if not isinstance(identifier, str) or not identifier.strip():
            continue
        identifier = identifier.strip()
        if len(node) > 1 or _is_reference_holder(node):
            defined.add(identifier)
        else:
            references.append(identifier)

    for identifier in sorted(set(references)):
        if identifier in defined:
            continue
        buckets['dangling_id'].append({
            'url': url, 'offending': identifier,
            'details': f'JSON-LD references @id "{identifier}", which no block on this page defines.',
            'evidence': {'expected': f'a node with "@id": "{identifier}"',
                         'found': '(no such node)',
                         'snippet': json.dumps({'@id': identifier})},
        })


def _is_reference_holder(node):
    """True for the bare {"@id": "<url>"} form that names a page, not a node.

    A BreadcrumbList writes its steps as {"item": {"@id": "https://site/x"}},
    which points at another page rather than at a node defined here - so it is
    not a dangling reference. Only fragment ids (#organization) and relative
    ids are expected to resolve inside this page.
    """
    identifier = (node.get('@id') or '').strip()
    return identifier.startswith(('http://', 'https://')) and '#' not in identifier


def _reviews(url, nodes, buckets):
    """Review / AggregateRating markup about the site's own offering."""
    found = set()
    for node in nodes:
        types = _types(node)
        if 'aggregaterating' in types:
            found.add('AggregateRating')
        if 'review' in types or 'userreview' in types:
            found.add('Review')
        if isinstance(node.get('aggregateRating'), dict):
            found.add('AggregateRating')
        if node.get('review') and 'review' not in types:
            found.add('Review')
    if not found:
        return

    names = ', '.join(sorted(found))
    buckets['self_review'].append({
        'url': url, 'offending': names,
        'details': (f'This page carries {names} markup about the site itself. Google ignores '
                    'self-serving review markup and treats it as a policy violation.'),
        'evidence': {'found': names, 'expected': 'reviews collected by a third party'},
    })


def _breadcrumbs(url, nodes, buckets):
    """BreadcrumbList position numbering and step completeness."""
    for node in nodes:
        if 'breadcrumblist' not in _types(node):
            continue
        steps = [step for step in _as_list(node.get('itemListElement'))
                 if isinstance(step, dict)]
        if not steps:
            continue

        positions = []
        for index, step in enumerate(steps, start=1):
            raw = step.get('position')
            try:
                positions.append(int(str(raw).strip()))
            except (TypeError, ValueError):
                positions.append(None)

            item = step.get('item')
            if isinstance(item, str):
                item_url, item_name = _norm(item), ''
            elif isinstance(item, dict):
                item_url = _norm(item.get('@id') or item.get('url') or '')
                item_name = _norm(_text(item.get('name')))
            else:
                item_url, item_name = '', ''
            name = _norm(_text(step.get('name'))) or item_name
            problems = []
            if not name:
                problems.append('no name')
            if not item_url and index < len(steps):
                problems.append('no item URL')
            if problems:
                buckets['breadcrumb_item'].append({
                    'url': url, 'offending': f'step {index}',
                    'details': (f'Breadcrumb step {index} of {len(steps)} has '
                                f"{' and '.join(problems)}."),
                    'evidence': {'snippet': json.dumps(step, ensure_ascii=False)[:_SNIPPET],
                                 'expected': 'name and item on every step',
                                 'found': ', '.join(problems)},
                })

        expected = list(range(1, len(steps) + 1))
        if positions != expected:
            shown = ', '.join('?' if value is None else str(value) for value in positions)
            buckets['breadcrumb_position'].append({
                'url': url, 'offending': shown,
                'details': (f'BreadcrumbList positions are {shown}; they must be '
                            f"{', '.join(str(value) for value in expected)}."),
                'evidence': {'found': shown,
                             'expected': ', '.join(str(value) for value in expected),
                             'snippet': json.dumps(node.get('itemListElement'),
                                                   ensure_ascii=False)[:_SNIPPET]},
            })
