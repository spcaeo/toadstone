"""Structured data that is not JSON-LD: microdata and RDFa.

Everything else in this project reads `<script type="application/ld+json">`,
because that is what Google recommends and what most new markup uses. It is
not what most *existing* markup uses. WooCommerce, older Shopify themes,
Drupal and a decade of hand-written templates put their structured data in
`itemscope`/`itemprop` attributes, and plenty of publishing platforms emit
RDFa's `typeof`/`property`. A crawler that reads only JSON-LD tells those
sites they have no structured data at all, which is both wrong and the kind
of wrong that sends someone off to add markup they already have.

So this reads both and returns nodes in the **same shape** the JSON-LD path
produces — `@type` and flat properties — so every check downstream works on
them without knowing which syntax they came from.

Two deliberate limits. Microdata's `itemref` is not followed: it is rare,
and resolving it properly means a second pass over the document for a feature
almost nothing uses. RDFa is read in its common shallow form — `vocab`,
`typeof`, `property` — not the full specification with prefixed CURIEs and
datatype coercion, which is a parser in its own right. Both limits are
reported honestly rather than papered over: what is found is found, and what
is not found is not claimed.
"""

import html
import re

# Attribute-level scanning rather than a DOM parse, to stay dependency-free
# and to tolerate the malformed markup these syntaxes tend to live in.
_TAG = re.compile(r'<(\w+)([^>]*)>', re.I)
# The value is optional: `itemscope` is a bare attribute, and requiring
# name=value made every microdata item invisible.
_ATTR = re.compile(
    r'''(\w[\w:-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?''')

# Attributes an item's value comes from, by tag, in HTML's own order.
_VALUE_ATTRS = {
    'meta': 'content',
    'audio': 'src', 'embed': 'src', 'iframe': 'src', 'img': 'src',
    'source': 'src', 'track': 'src', 'video': 'src',
    'a': 'href', 'area': 'href', 'link': 'href',
    'object': 'data',
    'data': 'value', 'meter': 'value',
    'time': 'datetime',
}


def _attrs(blob):
    found = {}
    for match in _ATTR.finditer(blob or ''):
        name = match.group(1).lower()
        value = match.group(2) or match.group(3) or match.group(4) or ''
        found[name] = html.unescape(value)
    return found


def _short_type(value):
    """`http://schema.org/Product` and `schema:Product` both become `Product`."""
    text = (value or '').strip()
    if not text:
        return ''
    text = text.rstrip('/')
    for separator in ('/', '#', ':'):
        if separator in text:
            text = text.rsplit(separator, 1)[-1]
    return text


def _strip_tags(markup):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', markup or '')).strip()


def _slice_element(markup, start_index, tag):
    """The markup of one element, from its opening tag to its match.

    Counts nested tags of the same name rather than finding the first close,
    which is what makes nested items - a Product holding an Offer - come out
    as separate nodes instead of one node swallowing the other.
    """
    opener = re.compile(rf'<{tag}\b', re.I)
    closer = re.compile(rf'</{tag}\s*>', re.I)
    depth, position = 0, start_index
    while position < len(markup):
        next_open = opener.search(markup, position)
        next_close = closer.search(markup, position)
        if not next_close:
            return markup[start_index:]
        if next_open and next_open.start() < next_close.start():
            depth += 1
            position = next_open.end()
            continue
        depth -= 1
        if depth <= 0:
            return markup[start_index:next_close.end()]
        position = next_close.end()
    return markup[start_index:]


def _property_value(tag, attributes, inner_markup):
    """What one annotated element contributes as a value."""
    attribute = _VALUE_ATTRS.get(tag)
    if attribute and attributes.get(attribute) is not None:
        return attributes.get(attribute, '').strip()
    if attributes.get('content') is not None:
        return attributes['content'].strip()
    return _strip_tags(inner_markup)


def _collect(markup, scope_attr, type_attr, prop_attr):
    """One pass for either syntax; they differ only in attribute names."""
    nodes = []
    for match in _TAG.finditer(markup or ''):
        tag = match.group(1).lower()
        attributes = _attrs(match.group(2))
        if scope_attr is not None and scope_attr not in attributes:
            continue
        if scope_attr is None and type_attr not in attributes:
            continue

        type_value = attributes.get(type_attr, '')
        element = _slice_element(markup, match.start(), tag)
        # Drop this element's own opening tag so an item carrying both a type
        # and a property does not read itself as one of its own properties.
        inner = element[len(match.group(0)):]

        node = {'@type': _short_type(type_value) or 'Thing'}
        if attributes.get('itemid') or attributes.get('resource'):
            node['@id'] = attributes.get('itemid') or attributes.get('resource')

        # Properties inside a nested item belong to that item, not to this
        # one. Skipping only the nested item's own tag is not enough - its
        # children are still in `inner` - so the scan jumps past the whole
        # element, or a Product silently acquires its Offer's price.
        skip_until = 0
        for inner_match in _TAG.finditer(inner):
            if inner_match.start() < skip_until:
                continue
            inner_attrs = _attrs(inner_match.group(2))
            inner_tag = inner_match.group(1).lower()
            if (scope_attr and scope_attr in inner_attrs) or \
               (not scope_attr and type_attr in inner_attrs):
                skip_until = inner_match.start() + len(
                    _slice_element(inner, inner_match.start(), inner_tag))
                continue
            name = inner_attrs.get(prop_attr)
            if not name:
                continue
            nested = _slice_element(inner, inner_match.start(), inner_tag)
            value = _property_value(
                inner_tag, inner_attrs, nested[len(inner_match.group(0)):])
            key = name.split()[0]
            key = key.split(':')[-1]
            if not value:
                continue
            if key in node:
                existing = node[key]
                node[key] = existing + [value] if isinstance(existing, list) \
                    else [existing, value]
            else:
                node[key] = value
        nodes.append(node)
    return nodes


def microdata_nodes(markup):
    """Schema.org microdata as JSON-LD-shaped dicts."""
    return _collect(markup, 'itemscope', 'itemtype', 'itemprop')


def rdfa_nodes(markup):
    """RDFa Lite as JSON-LD-shaped dicts."""
    return _collect(markup, None, 'typeof', 'property')


def extract(markup):
    """Every non-JSON-LD structured-data node on a page, with its syntax.

    Returned alongside `json_ld` rather than merged into it, so a check that
    cares which syntax was used can tell, and the common case - a check that
    does not care - can simply concatenate them.
    """
    return {
        'microdata': microdata_nodes(markup),
        'rdfa': rdfa_nodes(markup),
    }


def all_nodes(page):
    """JSON-LD, microdata and RDFa together, for checks that treat them alike."""
    nodes = list(page.get('json_ld') or [])
    nodes.extend(page.get('microdata') or [])
    nodes.extend(page.get('rdfa') or [])
    return nodes
