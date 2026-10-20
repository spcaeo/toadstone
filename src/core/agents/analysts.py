"""Agents that judge a page, where a rule cannot.

A rule can count words. It cannot tell whether 2,000 of them say anything.
A rule can find an author byline. It cannot tell whether the page shows any
sign the author has done the thing they are describing. These are the
questions that decide whether content earns a citation, and they are the ones
this crawler had no way to ask.

Three analysts, each answering one question about one page:

``experience``  Does the page show first-hand experience - specific numbers,
                named tools, things that went wrong - or is it a summary of
                what is already on the internet? This is the E in E-E-A-T,
                and it is the one most sites fail.
``citability``  Could a passage be lifted and quoted by an AI answer as it
                stands, or does every sentence depend on the one before it?
``substance``   Is this thin in substance rather than in length? A page can be
                two thousand words of nothing.

**Everything here is `info` severity and marked as a model's judgement.** The
model that said it is named in the evidence. A crawler whose errors are
generated text is a crawler nobody can trust, and the moment a reader cannot
tell which findings were measured and which were opinions, the measured ones
stop being believed too.
"""

from src.core.agents.provider import ProviderError, ask_json, configured
from src.utils.issue_factory import make_issue

CATEGORY = 'Content'

# Enough of a page to judge it. Beyond this the answer does not improve and
# the bill does.
MAX_CHARS = 6000

DEFAULTS = {
    'max_pages': 25,
    'min_words': 150,     # below this there is nothing to judge
    'score_threshold': 5,  # out of 10; at or below this, report it
}

PROMPTS = {
    'experience': (
        'You are auditing one web page for first-hand experience, the "E" in '
        'E-E-A-T.\n\n'
        'Look for evidence the writer has actually done this: specific numbers '
        'from their own work, named tools and versions, things that went wrong, '
        'decisions with trade-offs, original screenshots or data. Generic advice '
        'that could have been assembled from other articles is the absence of it.\n\n'
        'Reply with JSON only:\n'
        '{"score": 0-10, "verdict": "one sentence", '
        '"missing": ["what would most improve it", "..."]}\n\n'
        'URL: {url}\nTITLE: {title}\n\nCONTENT:\n{content}'),
    'citability': (
        'You are auditing one web page for whether an AI answer engine could '
        'quote it.\n\n'
        'A citable passage answers a question completely on its own: it does not '
        'depend on the sentence before it, it names its subject rather than '
        'saying "it" or "this", and it is two or three sentences long. Judge '
        'whether this page contains such passages.\n\n'
        'Reply with JSON only:\n'
        '{"score": 0-10, "verdict": "one sentence", '
        '"missing": ["what would most improve it", "..."]}\n\n'
        'URL: {url}\nTITLE: {title}\n\nCONTENT:\n{content}'),
    'substance': (
        'You are auditing one web page for substance, not length.\n\n'
        'A page is thin in substance when it restates its own title at length, '
        'pads with definitions the reader already has, or promises information '
        'it never gives. Length is not the test: 400 specific words beat 2,000 '
        'vague ones.\n\n'
        'Reply with JSON only:\n'
        '{"score": 0-10, "verdict": "one sentence", '
        '"missing": ["what would most improve it", "..."]}\n\n'
        'URL: {url}\nTITLE: {title}\n\nCONTENT:\n{content}'),
}

NAMES = {
    'experience': ('agent_weak_experience', 'Little Sign of First-Hand Experience'),
    'citability': ('agent_not_citable', 'Hard for an AI Answer to Quote'),
    'substance': ('agent_thin_substance', 'Thin in Substance, Not Only Length'),
}

FIXES = {
    'experience': ('Add what only you could write: your own numbers, the tools you '
                   'actually used, and what went wrong.'),
    'citability': ('Rewrite key passages so each answers its question alone, naming '
                   'its subject instead of referring back.'),
    'substance': ('Cut what restates the title and add what the reader came for.'),
}


def _config(config=None):
    cfg = dict(DEFAULTS)
    cfg.update(config or {})
    return cfg


def _pages_worth_judging(pages, cfg):
    """Longest first: a model's time is better spent on the real pages."""
    candidates = []
    for page in pages or []:
        if not isinstance(page, dict) or not page.get('url'):
            continue
        text = (page.get('main_text') or '').strip()
        words = page.get('word_count') or len(text.split())
        if not text or words < cfg['min_words']:
            continue
        candidates.append(page)
    candidates.sort(key=lambda p: -(p.get('word_count') or 0))
    return candidates[:cfg['max_pages']]


def judge_page(page, which, config=None, runner=None):
    """One analyst's verdict on one page, or a ProviderError."""
    cfg = _config(config)
    # Deliberately not str.format: these prompts contain literal JSON, and
    # format reads {"score": ...} as a replacement field and raises.
    prompt = (PROMPTS[which]
              .replace('{url}', page.get('url', ''))
              .replace('{title}', (page.get('title') or '')[:200])
              .replace('{content}', (page.get('main_text') or '')[:MAX_CHARS]))
    answer = ask_json(prompt, cfg, runner)
    try:
        score = float(answer.get('score'))
    except (TypeError, ValueError):
        raise ProviderError(f'The {which} analyst returned no usable score')
    return {
        'score': max(0.0, min(10.0, score)),
        'verdict': str(answer.get('verdict') or '')[:300],
        'missing': [str(m)[:160] for m in (answer.get('missing') or [])][:5],
    }


def issues(pages, which=('experience', 'citability', 'substance'),
           config=None, runner=None):
    """Run the analysts and turn low scores into findings.

    A provider that cannot run produces one visible note, not silence: an
    agent that was never asked must not look like an agent that found nothing.
    """
    cfg = _config(config)
    if runner is None and not configured(cfg):
        return []  # nothing configured is a choice, not a failure

    found, chosen = [], _pages_worth_judging(pages, cfg)
    model = cfg.get('model', '')
    for analyst in which:
        if analyst not in PROMPTS:
            continue
        for page in chosen:
            try:
                verdict = judge_page(page, analyst, cfg, runner)
            except ProviderError as exc:
                found.append(make_issue(
                    url=page.get('url', ''), check='agent_unavailable', severity='info',
                    category=CATEGORY, issue='An Agent Could Not Run',
                    details=(f'The {analyst} analyst did not run on this page: {exc}. '
                             f'No judgement was made either way.'),
                    offending_value=f'{analyst}:{exc}'[:80],
                    evidence={'found': str(exc)[:200]},
                    how_to_fix='Check the model configuration, or switch agents off.'))
                return found  # one failure means the rest will fail too
            if verdict['score'] > cfg['score_threshold']:
                continue
            slug, name = NAMES[analyst]
            missing = '; '.join(verdict['missing'])
            found.append(make_issue(
                url=page.get('url', ''), check=slug, severity='info', category=CATEGORY,
                issue=name,
                details=(f'A model scored this {verdict["score"]:.0f}/10 for '
                         f'{analyst}: {verdict["verdict"]}'
                         + (f' Most missing: {missing}' if missing else '')),
                offending_value=f'{analyst}:{verdict["score"]:.0f}',
                evidence={'found': f'{verdict["score"]:.0f}/10 from {model or "a model"}',
                          'expected': f'above {cfg["score_threshold"]}/10',
                          'snippet': verdict['verdict'][:300]},
                how_to_fix=FIXES[analyst]))
    return found


def report(pages, config=None, runner=None):
    """Scores per page, for a screen rather than an issue list."""
    cfg = _config(config)
    if runner is None and not configured(cfg):
        return {'available': False, 'reason': 'no model configured', 'pages': []}
    rows = []
    for page in _pages_worth_judging(pages, cfg):
        row = {'url': page.get('url', '')}
        for analyst in PROMPTS:
            try:
                row[analyst] = judge_page(page, analyst, cfg, runner)['score']
            except ProviderError as exc:
                return {'available': False, 'reason': str(exc), 'pages': rows}
        rows.append(row)
    return {'available': True, 'reason': '', 'pages': rows}
