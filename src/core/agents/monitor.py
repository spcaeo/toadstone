"""Deciding which changes between two crawls are worth waking someone for.

A scheduled crawl that emails every diff teaches people to ignore the email.
The regression report already says what is new, fixed and still open by
stable issue id; this decides which of that matters.

Most of the work is deterministic and stays that way, because the rules are
clear and a rule is cheaper and more predictable than a model:

  * a new error on a page that had none is worth knowing about
  * a check that goes from firing once to firing on fifty pages is a template
    that broke, which matters more than fifty separate findings suggest
  * pages disappearing from a crawl is worth knowing about immediately
  * a long tail of new `info` findings is not

A model is used for one thing only, and only when configured: writing the
sentence a person reads first. The decision about whether to alert is made
by the rules above and is never delegated - a model that quietly decided not
to mention an outage would be worse than no monitoring at all.
"""

from collections import Counter

from src.core.agents.provider import ProviderError, ask, configured

DEFAULTS = {
    'spike_factor': 5,        # a check firing this many times more than before
    'spike_minimum': 5,       # and at least this many times, to ignore noise
    'pages_lost_ratio': 0.1,  # losing this share of the crawl is an alert
    'max_examples': 10,
}

# How loud each kind of change is. The order here is the order a person
# should read them in.
SEVERITY_RANK = {'error': 0, 'warning': 1, 'info': 2}


def _config(config=None):
    cfg = dict(DEFAULTS)
    cfg.update(config or {})
    return cfg


def assess(report, previous_pages=0, current_pages=0, config=None):
    """Turn a regression report into a decision and a reason.

    `report` is what `crawl_diff.regression_report` returns. Returns
    {'alert': bool, 'reasons': [...], 'headline': str, 'detail': {...}}.
    """
    cfg = _config(config)
    new = [i for i in (report or {}).get('new') or [] if isinstance(i, dict)]
    fixed = [i for i in (report or {}).get('fixed') or [] if isinstance(i, dict)]

    reasons = []
    new_errors = [i for i in new if i.get('type') == 'error']
    if new_errors:
        pages = sorted({i.get('url', '') for i in new_errors})
        reasons.append({
            'kind': 'new_errors',
            'text': (f'{len(new_errors)} new error(s) across {len(pages)} page(s)'),
            'urls': pages[:cfg['max_examples']],
        })

    # A check that suddenly fires everywhere is one broken template, and it
    # reads very differently from fifty unrelated findings.
    before = Counter(i.get('check') for i in (report or {}).get('still_open') or []
                     if isinstance(i, dict))
    after = Counter(i.get('check') for i in new)
    for check, count in after.items():
        baseline = before.get(check, 0)
        if count < cfg['spike_minimum']:
            continue
        if baseline and count < baseline * cfg['spike_factor']:
            continue
        reasons.append({
            'kind': 'spike',
            'text': (f'{check} went from {baseline} to {baseline + count} pages, '
                     f'which usually means one template changed'),
            'urls': [i.get('url', '') for i in new
                     if i.get('check') == check][:cfg['max_examples']],
        })

    if previous_pages and current_pages < previous_pages * (1 - cfg['pages_lost_ratio']):
        lost = previous_pages - current_pages
        reasons.append({
            'kind': 'pages_lost',
            'text': (f'the crawl found {lost} fewer page(s) than last time '
                     f'({current_pages} against {previous_pages})'),
            'urls': [],
        })

    alert = bool(reasons)
    if alert:
        headline = '; '.join(reason['text'] for reason in reasons[:2])
    elif new or fixed:
        headline = (f'{len(new)} new and {len(fixed)} fixed, none of it urgent')
    else:
        headline = 'nothing changed'

    return {
        'alert': alert,
        'reasons': reasons,
        'headline': headline,
        'detail': {'new': len(new), 'fixed': len(fixed),
                   'still_open': len((report or {}).get('still_open') or []),
                   'pages_before': previous_pages, 'pages_now': current_pages},
    }


def summarise(assessment, base_url='', config=None, runner=None):
    """A sentence a person reads first, written by a model when one is configured.

    The decision to alert is already made by `assess` and is never revisited
    here. If the model cannot be reached, the deterministic headline stands -
    a monitor that goes quiet because a third party is down is not a monitor.
    """
    cfg = _config(config)
    assessment = assessment or {}
    if runner is None and not configured(cfg):
        return assessment.get('headline', '')

    reasons = '\n'.join(f'- {r["text"]}' for r in assessment.get('reasons') or [])
    if not reasons:
        return assessment.get('headline', '')
    prompt = (
        'Write one sentence for someone who has just been alerted that their '
        'website changed. Say what happened and what it probably means. No '
        'preamble, no greeting, no advice to "monitor the situation".\n\n'
        f'Site: {base_url}\n'
        f'Pages crawled: {assessment.get("detail", {}).get("pages_now")}\n'
        f'What changed:\n{reasons}')
    try:
        return ask(prompt, cfg, runner).strip().split('\n')[0][:400]
    except ProviderError:
        return assessment.get('headline', '')


def webhook_payload(assessment, crawl_id=None, base_url='', summary=''):
    """What to send when something is worth sending, in a stable shape."""
    return {
        'event': 'crawl_regression' if assessment.get('alert') else 'crawl_complete',
        'crawl_id': crawl_id,
        'url': base_url,
        'alert': bool(assessment.get('alert')),
        'summary': summary or assessment.get('headline', ''),
        'reasons': [r['text'] for r in assessment.get('reasons') or []],
        'counts': assessment.get('detail', {}),
    }
