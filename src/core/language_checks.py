"""Spelling and grammar, via LanguageTool.

The commercial crawlers both ship a spell check and this shipped none. As
with accessibility, the engine already exists and is better than anything
worth hand-rolling here, so this is a client for it rather than a dictionary.

The hard part of a spell check on a website is not finding mistakes. It is
not reporting things that are not mistakes. A product name, a code
identifier, a URL fragment and a technical term all look like misspellings to
a general dictionary, and a check that cries wolf on every brand name gets
switched off within a day - at which point it finds nothing at all. So:

  * only the page's main text is checked, with template chrome already
    stripped by the extractor;
  * anything that looks like code, a URL, or an identifier is dropped before
    the text is sent;
  * an ignore list is a first-class setting, because every site has one;
  * matches are grouped per page per category, so a page with forty typos is
    one issue with examples rather than forty issues;
  * style suggestions are informational. They are opinions, and LanguageTool
    has many of them.

The server is configurable. The default is LanguageTool's public API, which
is rate-limited and sends text to a third party; a self-hosted container is
one setting away and is what anyone crawling a client's site should use.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from src.utils.issue_factory import make_issue

CATEGORY = 'Content'

PUBLIC_ENDPOINT = 'https://api.languagetool.org/v2/check'

# LanguageTool's category ids, mapped to what we call them and how loudly.
CATEGORY_MAP = {
    'TYPOS': ('spelling_mistake', 'warning', 'Spelling Mistake'),
    'GRAMMAR': ('grammar_mistake', 'warning', 'Grammar Mistake'),
    'CONFUSED_WORDS': ('grammar_mistake', 'warning', 'Grammar Mistake'),
    'PUNCTUATION': ('punctuation_mistake', 'info', 'Punctuation Mistake'),
    'CASING': ('casing_mistake', 'info', 'Casing Mistake'),
    'TYPOGRAPHY': ('typography_suggestion', 'info', 'Typography Suggestion'),
}
DEFAULT_MAPPING = ('style_suggestion', 'info', 'Style Suggestion')

DEFAULTS = {
    'enabled': False,
    'endpoint': PUBLIC_ENDPOINT,
    'language': 'auto',
    'ignore': [],              # words this site uses on purpose
    'max_chars': 40000,        # LanguageTool's public limit is lower than most pages
    'max_examples': 5,
    'min_matches': 1,
    'timeout': 30,
    'skip_categories': [],     # e.g. ['TYPOGRAPHY'] to silence smart quotes
}

# Things a general dictionary always gets wrong on a technical site.
_URL = re.compile(r'https?://\S+|www\.\S+|\S+@\S+\.\S+')
_CODE = re.compile(r'`[^`]*`|<[^>]{1,80}>|\{\{[^}]*\}\}|\$\{[^}]*\}')
_IDENTIFIER = re.compile(r'\b(?:[a-z]+[A-Z]\w*|\w+_\w+|\w+\.(?:js|py|css|html|json|xml|md))\b')
_LONG_TOKEN = re.compile(r'\S{40,}')


def _config(config=None):
    cfg = dict(DEFAULTS)
    cfg.update(config or {})
    return cfg


def clean_for_checking(text, cfg=None):
    """Strip what a general dictionary cannot judge, keeping the prose.

    Replaced rather than deleted so offsets stay roughly meaningful and two
    sentences are never run together into a false grammar error.
    """
    cfg = _config(cfg)
    if not text:
        return ''
    for pattern in (_URL, _CODE, _IDENTIFIER, _LONG_TOKEN):
        text = pattern.sub(' ', text)
    return re.sub(r'\s+', ' ', text).strip()[:cfg['max_chars']]


def _ignored(cfg):
    return {str(word).strip().lower() for word in (cfg.get('ignore') or []) if str(word).strip()}


def fetch_matches(text, cfg=None, opener=None):
    """Ask LanguageTool about one page's text. Returns {'matches': [...]} or {'error': ...}."""
    cfg = _config(cfg)
    if not text:
        return {'matches': []}
    payload = {'text': text, 'language': cfg['language']}
    if cfg['language'] == 'auto':
        payload['language'] = 'auto'
    body = urllib.parse.urlencode(payload).encode('utf-8')
    request = urllib.request.Request(
        cfg['endpoint'], data=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded',
                 'Accept': 'application/json'})
    try:
        send = opener or urllib.request.urlopen
        with send(request, timeout=cfg['timeout']) as response:
            data = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            return {'error': 'rate limited by the language server'}
        return {'error': f'language server returned HTTP {exc.code}'}
    except Exception as exc:
        return {'error': f'language server unreachable: {type(exc).__name__}'}
    return {'matches': (data or {}).get('matches') or []}


def _match_word(match):
    """The text the match is about, from the context LanguageTool returns."""
    context = (match.get('context') or {})
    text = context.get('text') or ''
    offset = context.get('offset')
    length = context.get('length')
    if isinstance(offset, int) and isinstance(length, int) and length > 0:
        return text[offset:offset + length]
    return ''


def issues_for_page(url, matches, config=None):
    """Group one page's matches into one issue per category."""
    cfg = _config(config)
    ignore = _ignored(cfg)
    skip = {str(c).upper() for c in (cfg.get('skip_categories') or [])}

    grouped = {}
    for match in matches or []:
        if not isinstance(match, dict):
            continue
        rule = match.get('rule') or {}
        category_id = ((rule.get('category') or {}).get('id') or '').upper()
        if category_id in skip:
            continue
        word = _match_word(match)
        if word and word.strip().lower() in ignore:
            continue
        slug, severity, name = CATEGORY_MAP.get(category_id, DEFAULT_MAPPING)
        entry = grouped.setdefault(slug, {'severity': severity, 'name': name,
                                          'examples': [], 'count': 0})
        entry['count'] += 1
        if len(entry['examples']) < cfg['max_examples']:
            suggestion = ', '.join(
                r.get('value', '') for r in (match.get('replacements') or [])[:3] if r.get('value'))
            entry['examples'].append(
                f'"{word}" -> {suggestion}' if word and suggestion
                else (f'"{word}"' if word else (match.get('message') or '')[:80]))

    issues = []
    for slug, entry in sorted(grouped.items()):
        if entry['count'] < cfg['min_matches']:
            continue
        examples = '; '.join(entry['examples'])
        issues.append(make_issue(
            url=url, check=slug, severity=entry['severity'], category=CATEGORY,
            issue=entry['name'],
            details=(f'{entry["count"]} {entry["name"].lower()}(s) in the page text'
                     + (f': {examples}' if examples else '') + '.'),
            offending_value=f'{slug}:{entry["count"]}',
            evidence={'found': f'{entry["count"]} match(es)', 'snippet': examples[:300]},
            how_to_fix=('Correct the text, or add the word to the ignore list if the '
                        'site uses it deliberately.'),
        ))
    return issues


def check_page(page, config=None, opener=None):
    """Fetch and map in one step, for a caller that has a page and a server."""
    cfg = _config(config)
    if not cfg['enabled']:
        return []
    text = clean_for_checking(page.get('main_text') or '', cfg)
    if not text:
        return []
    language = cfg['language']
    if language == 'auto' and page.get('lang'):
        cfg = dict(cfg, language=str(page['lang']).split('-')[0])
    found = fetch_matches(text, cfg, opener=opener)
    if found.get('error'):
        return []  # a server we could not reach says nothing about the page
    return issues_for_page(page.get('url') or '', found['matches'], cfg)
