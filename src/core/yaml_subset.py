"""A deliberately small YAML parser, for rule files and nothing else.

Rule files are written by people, so they are YAML. Pulling in PyYAML to
read a few mappings and lists would add a dependency to a project that
keeps very few, and would accept far more syntax than a rule file should
contain - anchors, tags, multi-document streams, and the handful of
constructs that have caused real security problems elsewhere.

So this reads the subset the documentation describes and refuses the
rest. Refusing is the point: a rule file that uses something unsupported
gets an error naming the line, rather than being silently misread.

One deliberate deviation from YAML, for the people writing these files:
an unknown escape inside a double-quoted string keeps its backslash, so
a regex can be written the way a regex is written.

Split out of rules_engine.py to bring that file under the 1,000-line
limit. Nothing changed but the location.
"""

import re


class _ParseError(Exception):
    """A rules file this parser cannot read. Never escapes load_rules."""


_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f',
            '0': '\0', '"': '"', "'": "'", '\\': '\\', '/': '/'}


def _scan(text):
    """Yield (index, char, quote, depth) left to right.

    `quote` is the quote character when the position sits inside a quoted
    scalar (None outside one) and `depth` is the `[]`/`{}` nesting, so the
    three callers below can find a structural `#`, `:` or `,` without each
    repeating the quote-and-escape bookkeeping.
    """
    quote = None
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            yield i, ch, quote, depth
            if ch == '\\' and quote == '"' and i + 1 < len(text):
                yield i + 1, text[i + 1], quote, depth
                i += 2
                continue
            if ch == quote:
                quote = None
        else:
            if ch in '"\'':
                quote = ch
            elif ch in '[{':
                depth += 1
            elif ch in ']}':
                depth -= 1
            yield i, ch, None, depth
        i += 1


def _strip_comment(line):
    """Drop a trailing `# comment`, leaving a quoted `#` alone."""
    for i, ch, quote, _ in _scan(line):
        if ch == '#' and not quote and (i == 0 or line[i - 1] in ' \t'):
            return line[:i]
    return line


def _yaml_lines(text):
    """Significant lines as (indent, content, line number).

    `  - id: x` becomes (2, '-') then (4, 'id: x'), so an item's own keys and
    the keys on the lines below it share one indent and one code path.
    """
    lines = []
    for number, raw in enumerate(text.replace('\r\n', '\n').replace('\r', '\n').split('\n'), 1):
        leading = raw[:len(raw) - len(raw.lstrip(' \t'))]
        if '\t' in leading:
            raise _ParseError(f'line {number}: tabs cannot be used for indentation')
        content = _strip_comment(raw).rstrip()
        if not content.strip() or content.strip() in ('---', '...'):
            continue
        indent = len(content) - len(content.lstrip(' '))
        content = content.strip()
        while content == '-' or content.startswith('- '):
            lines.append((indent, '-', number))
            if content == '-':
                content = ''
                break
            rest = content[2:].lstrip()
            indent += len(content) - len(rest)
            content = rest
        if content:
            lines.append((indent, content, number))
    return lines


def _split_key(content):
    """Split `key: value` at the first structural colon, or (None, None)."""
    for i, ch, quote, depth in _scan(content):
        if (ch == ':' and not quote and depth == 0
                and (i + 1 == len(content) or content[i + 1] == ' ')):
            return content[:i].strip(), content[i + 1:].strip()
    return None, None


def _unquote(text):
    body = text[1:-1]
    if text[0] == "'":
        return body.replace("''", "'")
    out = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == '\\' and i + 1 < len(body):
            nxt = body[i + 1]
            # An unknown escape keeps both characters: see the module docstring.
            out.append(_ESCAPES.get(nxt, '\\' + nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _split_flow(body):
    """Split a flow collection's body on its top-level commas."""
    parts = []
    start = 0
    for i, ch, quote, depth in _scan(body):
        if ch == ',' and not quote and depth == 0:
            parts.append(body[start:i].strip())
            start = i + 1
    parts.append(body[start:].strip())
    return [p for p in parts if p != '']


def _parse_scalar(text):
    text = text.strip()
    if not text:
        return ''
    if len(text) >= 2 and text[0] == text[-1] and text[0] in '"\'':
        return _unquote(text)
    if text[0] == '[':
        if text[-1] != ']':
            raise _ParseError(f'unterminated flow sequence: {text}')
        return [_parse_scalar(part) for part in _split_flow(text[1:-1])]
    if text[0] == '{':
        if text[-1] != '}':
            raise _ParseError(f'unterminated flow mapping: {text}')
        out = {}
        for part in _split_flow(text[1:-1]):
            key, value = _split_key(part)
            if key is None:
                raise _ParseError(f'expected "key: value" inside {{...}}, found "{part}"')
            out[_parse_scalar(key)] = _parse_scalar(value)
        return out
    lowered = text.lower()
    if lowered in ('true', 'yes', 'on'):
        return True
    if lowered in ('false', 'no', 'off'):
        return False
    if lowered in ('null', '~'):
        return None
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text


def _parse_node(lines, i, indent):
    if lines[i][1] == '-':
        return _parse_sequence(lines, i, indent)
    key, _ = _split_key(lines[i][1])
    if key is None:
        return _parse_scalar(lines[i][1]), i + 1
    return _parse_mapping(lines, i, indent)


def _parse_mapping(lines, i, indent):
    out = {}
    total = len(lines)
    while i < total:
        line_indent, content, number = lines[i]
        if line_indent < indent or (line_indent == indent and content == '-'):
            break
        if line_indent > indent:
            raise _ParseError(f'line {number}: unexpected indentation at "{content}"')
        key, rest = _split_key(content)
        if key is None:
            raise _ParseError(f'line {number}: expected "key: value", found "{content}"')
        key = _parse_scalar(key)
        i += 1
        if rest:
            out[key] = _parse_scalar(rest)
            continue
        if i < total and (lines[i][0] > indent or (lines[i][0] == indent and lines[i][1] == '-')):
            out[key], i = _parse_node(lines, i, lines[i][0])
        else:
            out[key] = ''
    return out, i


def _parse_sequence(lines, i, indent):
    out = []
    total = len(lines)
    while i < total and lines[i][0] == indent and lines[i][1] == '-':
        i += 1
        if i < total and lines[i][0] > indent:
            value, i = _parse_node(lines, i, lines[i][0])
        else:
            value = ''
        out.append(value)
    return out, i


def parse_yaml_subset(text):
    """Parse the documented YAML subset. Raises _ParseError on anything else."""
    lines = _yaml_lines(text)
    if not lines:
        return {}
    value, index = _parse_node(lines, 0, lines[0][0])
    if index < len(lines):
        raise _ParseError(f'line {lines[index][2]}: unexpected content "{lines[index][1]}"')
    return value

