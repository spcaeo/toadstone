#!/usr/bin/env python3
"""The agent layer: the provider, the analysts, the fixer and the monitor.

No model is ever called. Every test injects a runner, so these run with no
key, no network and no cost.

The behaviours under test are mostly refusals, because that is where the risk
is. An agent that silently spends money, silently sends a client's page text
to a third party, silently rewrites a repository, or silently stops alerting
would each be worse than not having it.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.agents import analysts, fixer, monitor, provider  # noqa: E402
from src.utils.issue_factory import EVIDENCE_FIELDS, SEVERITIES  # noqa: E402

PASSED, FAILED = [], []
SITE = 'https://site.test'


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def answering(text):
    def runner(prompt, cfg):
        return text
    return runner


def refusing(message='the model is down'):
    def runner(prompt, cfg):
        raise provider.ProviderError(message)
    return runner


def page(path='/a', words=800, text=None):
    body = text or ('A sentence about the subject. ' * words)
    return {'url': f'{SITE}{path}', 'title': 'A page', 'main_text': body,
            'word_count': len(body.split()), 'status_code': 200,
            'content_type': 'text/html'}


def issue(check, severity='warning', url=None, details='something'):
    return {'check': check, 'type': severity, 'url': url or f'{SITE}/a',
            'issue': check.replace('_', ' ').title(), 'details': details,
            'evidence': {}, 'how_to_fix': 'do the thing', 'id': check + (url or '')}


# ---------------------------------------------------------------------------
# provider
# ---------------------------------------------------------------------------

def test_nothing_runs_unless_asked_for():
    """Spending money and sending page text are both opt-in."""
    result('provider: the default mode is none',
           provider.DEFAULTS['mode'] == 'none', provider.DEFAULTS['mode'])
    result('provider: nothing is configured by default',
           not provider.configured(), 'ok')
    try:
        provider.ask('hello')
        result('provider: asking with no mode refuses', False, 'it answered')
    except provider.ProviderError:
        result('provider: asking with no mode refuses', True)

    saved = os.environ.pop('ANTHROPIC_API_KEY', None)
    try:
        result('provider: api mode without a key is not configured',
               not provider.configured({'mode': 'api'}), 'ok')
        result('provider: and says so rather than pretending',
               'not set' in provider.describe({'mode': 'api'}),
               provider.describe({'mode': 'api'}))
        os.environ['ANTHROPIC_API_KEY'] = 'test-key'
        result('provider: api mode with a key is configured',
               provider.configured({'mode': 'api'}), 'ok')
    finally:
        os.environ.pop('ANTHROPIC_API_KEY', None)
        if saved is not None:
            os.environ['ANTHROPIC_API_KEY'] = saved


def test_the_person_is_told_where_their_text_goes():
    """For anyone crawling a client's site this is a decision, not a detail."""
    result('provider: with nothing configured, it says findings are skipped',
           'skipped' in provider.describe(), provider.describe())
    result('provider: cli mode says the text goes to a local process',
           'local' in provider.describe({'mode': 'cli'}), provider.describe({'mode': 'cli'}))
    os.environ['ANTHROPIC_API_KEY'] = 'test-key'
    try:
        result('provider: api mode names the destination and the model',
               'Anthropic' in provider.describe({'mode': 'api'}),
               provider.describe({'mode': 'api'}))
    finally:
        os.environ.pop('ANTHROPIC_API_KEY', None)


def test_json_is_extracted_from_whatever_the_model_wraps_it_in():
    cases = [
        ('bare', '{"score": 7}'),
        ('fenced', 'Here you go:\n```json\n{"score": 7}\n```\nHope that helps.'),
        ('chatty', 'Sure! {"score": 7} - let me know if you need more.'),
        ('nested braces', '{"score": 7, "detail": {"a": 1}}'),
        ('a brace inside a string', '{"score": 7, "verdict": "uses { and } oddly"}'),
    ]
    for name, text in cases:
        try:
            got = provider.ask_json('x', runner=answering(text))
            ok = got.get('score') == 7
        except provider.ProviderError:
            ok = False
        result(f'provider: JSON is read when the model returns it {name}', ok, text[:50])

    try:
        provider.ask_json('x', runner=answering('I would rather not.'))
        result('provider: a reply with no JSON is an error, not an empty result',
               False, 'it returned something')
    except provider.ProviderError:
        result('provider: a reply with no JSON is an error, not an empty result', True)


# ---------------------------------------------------------------------------
# analysts
# ---------------------------------------------------------------------------

def test_analyst_findings_are_marked_as_opinions():
    """A crawler whose errors are generated text is a crawler nobody trusts."""
    runner = answering('{"score": 3, "verdict": "Generic advice throughout.", '
                       '"missing": ["your own numbers"]}')
    found = analysts.issues([page()], ('experience',), {'model': 'test-model'}, runner)
    result('analysts: a low score becomes a finding', len(found) == 1, str(len(found)))
    if not found:
        return
    one = found[0]
    result('analysts: every agent finding is info severity, never error',
           one['type'] == 'info', one['type'])
    result('analysts: the slug marks it as an agent finding',
           one['check'].startswith('agent_'), one['check'])
    result('analysts: the model that judged it is named in the evidence',
           'test-model' in str(one['evidence']), str(one['evidence']))
    result('analysts: the model\'s own words are quoted rather than paraphrased',
           'Generic advice' in one['details'], one['details'][:90])


def test_a_good_page_produces_nothing():
    runner = answering('{"score": 9, "verdict": "Specific and first-hand.", "missing": []}')
    result('analysts: a high score is not a finding',
           analysts.issues([page()], ('experience',), None, runner) == [], 'ok')


def test_an_unreachable_model_is_visible_not_silent():
    """The failure that matters: never asked must not look like nothing found."""
    found = analysts.issues([page()], ('experience',), None, refusing())
    result('analysts: a provider failure produces a visible note',
           len(found) == 1 and found[0]['check'] == 'agent_unavailable', str(found))
    result('analysts: the note says no judgement was made either way',
           'either way' in found[0]['details'], found[0]['details'])
    result('analysts: it does not keep hammering a model that is down',
           len(analysts.issues([page('/a'), page('/b'), page('/c')],
                               ('experience', 'citability'), None, refusing())) == 1, 'ok')


def test_analysts_are_skipped_when_nothing_is_configured():
    result('analysts: no provider means no findings and no notes',
           analysts.issues([page()], ('experience',)) == [], 'ok')
    result('analysts: the report says why rather than returning an empty list',
           analysts.report([page()])['available'] is False, str(analysts.report([page()])))


def test_only_pages_worth_judging_are_sent():
    runner = answering('{"score": 2, "verdict": "x", "missing": []}')
    tiny = {'url': f'{SITE}/t', 'title': 't', 'main_text': 'Three words only',
            'word_count': 3, 'status_code': 200}
    found = analysts.issues([tiny], ('experience',), None, runner)
    result('analysts: a page too short to judge is not sent', found == [], str(found))

    many = [page(f'/p{n}') for n in range(40)]
    sent = analysts.issues(many, ('experience',), {'max_pages': 5}, runner)
    result('analysts: the page budget is respected', len(sent) == 5, str(len(sent)))


# ---------------------------------------------------------------------------
# fixer
# ---------------------------------------------------------------------------

def test_the_fixer_only_offers_what_code_can_fix():
    issues = [issue('missing_canonical'), issue('a11y_image-alt'),
              issue('slow_response_time'), issue('gsc_low_ctr'),
              issue('crawl_budget_wasted'), issue('agent_weak_experience')]
    workable = [i['check'] for i in fixer.fixable(issues)]
    result('fixer: markup defects are offered',
           'missing_canonical' in workable and 'a11y_image-alt' in workable, str(workable))
    result('fixer: a slow server is not a coding task',
           'slow_response_time' not in workable, str(workable))
    result('fixer: Search Console and log findings are not coding tasks',
           'gsc_low_ctr' not in workable and 'crawl_budget_wasted' not in workable,
           str(workable))
    result('fixer: a model\'s own opinion is not handed back to a model',
           'agent_weak_experience' not in workable, str(workable))


def test_the_brief_groups_by_cause():
    """Forty pages missing a canonical is one template, not forty edits."""
    issues = [issue('missing_canonical', url=f'{SITE}/p{n}') for n in range(40)]
    issues += [issue('missing_h1', 'error', f'{SITE}/x')]
    text = fixer.brief(issues, '/repo', 7, SITE)
    result('fixer: the brief groups issues by check', 'missing_canonical' in text
           and text.count('## missing_canonical') == 1, 'ok')
    result('fixer: it says how many pages share the cause', 'x40' in text, 'ok')
    result('fixer: errors come before warnings',
           text.index('missing_h1') < text.index('missing_canonical'), 'ok')
    result('fixer: it names the repository to work in', '/repo' in text, 'ok')
    result('fixer: it tells the agent to fix the template, not the output',
           'template' in text.lower() or 'generates the markup' in text, 'ok')
    result('fixer: it invites the agent to push back on a wrong finding',
           'looks wrong' in text, 'ok')
    result('fixer: it does not list all forty pages',
           text.count(f'{SITE}/p') <= fixer.DEFAULTS['max_per_check'] + 1, 'ok')


def test_the_fixer_never_touches_anything():
    """It proposes. Applying a change is not a crawler's decision."""
    names = [n for n in dir(fixer) if not n.startswith('_')]
    forbidden = [n for n in names if any(word in n.lower()
                                         for word in ('commit', 'push', 'write', 'apply'))]
    result('fixer: it exposes nothing that commits, pushes or writes',
           not forbidden, str(forbidden))

    record = fixer.run([issue('missing_canonical')], '/repo', 1, SITE,
                       runner=answering('I changed the layout template.'))
    result('fixer: a run returns a record of what the agent said',
           record['ran'] and 'layout template' in record['report'], str(record)[:90])
    result('fixer: and keeps the brief it was given, so it can be audited',
           record['brief'].startswith('You are fixing'), record['brief'][:40])

    nothing = fixer.run([issue('slow_response_time')], '/repo', 1, SITE,
                        runner=answering('x'))
    result('fixer: nothing fixable means it does not run at all',
           not nothing['ran'] and 'fixable' in nothing['reason'], str(nothing['reason']))

    down = fixer.run([issue('missing_canonical')], '/repo', 1, SITE, runner=refusing())
    result('fixer: a failure is reported with the brief intact',
           not down['ran'] and down['brief'] and down['reason'], str(down['reason']))


# ---------------------------------------------------------------------------
# monitor
# ---------------------------------------------------------------------------

def report(new=(), fixed=(), still_open=()):
    return {'new': list(new), 'fixed': list(fixed), 'still_open': list(still_open)}


def test_what_is_worth_waking_someone_for():
    quiet = monitor.assess(report(new=[issue('thin_content', 'info')]))
    result('monitor: a new info finding is not an alert', not quiet['alert'],
           quiet['headline'])

    loud = monitor.assess(report(new=[issue('missing_h1', 'error')]))
    result('monitor: a new error is an alert', loud['alert'], loud['headline'])
    result('monitor: the reason names the page',
           any(f'{SITE}/a' in (r.get('urls') or []) for r in loud['reasons']),
           str(loud['reasons']))

    nothing = monitor.assess(report())
    result('monitor: no change is not an alert and says so',
           not nothing['alert'] and 'nothing changed' in nothing['headline'],
           nothing['headline'])


def test_a_template_breaking_reads_differently_from_scattered_findings():
    spike = monitor.assess(report(
        new=[issue('missing_canonical', 'warning', f'{SITE}/p{n}') for n in range(30)],
        still_open=[issue('missing_canonical', 'warning', f'{SITE}/old')]))
    result('monitor: a check firing everywhere at once is an alert',
           spike['alert'], spike['headline'])
    result('monitor: and is described as a template change',
           any('template' in r['text'] for r in spike['reasons']), str(spike['reasons']))

    scattered = monitor.assess(report(
        new=[issue(f'check_{n}', 'warning', f'{SITE}/p{n}') for n in range(6)]))
    result('monitor: six unrelated warnings are not a spike',
           not scattered['alert'], scattered['headline'])


def test_losing_pages_is_an_alert():
    lost = monitor.assess(report(), previous_pages=100, current_pages=40)
    result('monitor: a crawl that lost most of the site is an alert',
           lost['alert'] and any(r['kind'] == 'pages_lost' for r in lost['reasons']),
           lost['headline'])
    steady = monitor.assess(report(), previous_pages=100, current_pages=99)
    result('monitor: one page fewer is not', not steady['alert'], steady['headline'])


def test_the_decision_is_never_delegated_to_a_model():
    """A model that quietly decided not to mention an outage would be worse
    than no monitoring at all."""
    assessment = monitor.assess(report(new=[issue('missing_h1', 'error')]))
    deterministic = assessment['alert']
    summary = monitor.summarise(assessment, SITE, runner=answering('All is well, relax.'))
    result('monitor: the model writes the sentence but not the verdict',
           deterministic is True and assessment['alert'] is True, str(assessment['alert']))
    result('monitor: the summary it wrote is used', 'All is well' in summary, summary)

    fallback = monitor.summarise(assessment, SITE, runner=refusing())
    result('monitor: a model that is down leaves the deterministic headline standing',
           fallback == assessment['headline'] and fallback, fallback)

    payload = monitor.webhook_payload(assessment, 9, SITE, summary)
    result('monitor: the webhook carries the alert flag and the reasons',
           payload['alert'] is True and payload['reasons'], str(payload)[:110])
    result('monitor: a quiet crawl sends a different event',
           monitor.webhook_payload(monitor.assess(report()), 9, SITE)['event']
           == 'crawl_complete', 'ok')


def test_shape_and_resilience():
    runner = answering('{"score": 1, "verdict": "x", "missing": []}')
    issues = analysts.issues([page()], ('experience',), None, runner)
    result('shape: severities are real', all(i['type'] in SEVERITIES for i in issues), 'ok')
    result('shape: only known evidence fields are used',
           all(set(i['evidence']) <= set(EVIDENCE_FIELDS) for i in issues),
           str([set(i['evidence']) for i in issues]))

    for junk in (None, [], [{}], [{'url': None}]):
        try:
            analysts.issues(junk, ('experience',), None, runner)
            fixer.brief(junk)
            monitor.assess({'new': junk, 'fixed': None, 'still_open': junk})
        except Exception as exc:
            result('junk: odd input does not raise', False, f'{type(exc).__name__}: {exc}')
            return
    result('junk: odd input does not raise', True)


def test_live_against_a_real_model():
    """End to end with an actual model, when one is reachable.

    Everything above injects a runner, which proves the mapping and the
    refusals but never the prompts. This proves the prompts: that a real
    model answers them with JSON this code can read, and that the verdicts
    land the right way round.

    Skipped rather than failed when no provider is reachable, because the
    suite has to run on a machine with no model and no key.
    """
    import shutil
    if not shutil.which('claude'):
        print('  SKIP  no local claude CLI; live agent test skipped')
        return
    cfg = {'mode': 'cli', 'cli_command': 'claude', 'timeout': 180, 'max_pages': 1,
           'model': 'claude (local CLI)'}

    try:
        reply = provider.ask('Reply with exactly the word: ready', cfg)
    except provider.ProviderError as exc:
        print(f'  SKIP  the local CLI did not answer ({exc})')
        return
    result('live: the provider reaches a real model', 'ready' in reply.lower(), repr(reply[:60]))

    generic = {'url': f'{SITE}/tips', 'title': 'Top SEO Tips', 'word_count': 200,
               'main_text': ('Search engine optimisation is important for every '
                             'business. Make your website fast and write good content. '
                             'Research keywords and use them in titles. Get backlinks '
                             'from other sites. Optimise for mobile. Use HTTPS. Update '
                             'content regularly. Track progress with analytics. These '
                             'best practices will help you rank better over time. '
                             'SEO is a long-term strategy, so be patient and consistent.')}
    found = analysts.issues([generic], ('experience',), cfg)
    result('live: a page of generic advice is reported',
           len(found) == 1 and found[0]['check'] == 'agent_weak_experience',
           str([i['check'] for i in found]))
    if found:
        result('live: and it is info severity, carrying the model that judged it',
               found[0]['type'] == 'info' and 'claude' in str(found[0]['evidence']).lower(),
               str(found[0]['evidence'])[:120])

    specific = {'url': f'{SITE}/bugs', 'title': 'Four checks that were lying', 'word_count': 300,
                'main_text': ('We ran the crawler against one site on 17 October and it '
                              'reported a redirect loop on three healthy pages. curl showed '
                              'a straight 200 with no Location header under every user agent '
                              'we tried. The cause was Chromium: a Critical-CH header makes '
                              'it reissue the request and report the restart as a 307 to the '
                              'same address. Separately, a check called an image "at the top '
                              'of the page" when getBoundingClientRect put it 2,403px down at '
                              '1920x1080. It was reading DOM order, index < 2, while the '
                              'renderer already measured real geometry and discarded it. '
                              'Of 90 findings, 45 were image files audited as web pages.')}
    result('live: a page with first-hand detail is left alone',
           analysts.issues([specific], ('experience',), cfg) == [], 'quiet')



def test_the_two_agent_kinds_nothing_could_reach():
    """The package documents four agent kinds. `__init__` imported one.

    analysts ran during a crawl and provider was imported by everything.
    fixer and monitor were imported by nothing at all — no route, no crawl
    hook, no CLI. Found with an AST import graph after `grep` gave two
    different wrong answers, because `\b` behaves differently in macOS grep
    and words like "monitor" and "report" match half the codebase.

    So the project claimed four agent kinds and shipped two.
    """
    from flask import Flask
    from src.api.fixer_routes import create_blueprint

    issues = [
        {'check': 'missing_canonical', 'type': 'error', 'url': 'https://site.test/a',
         'issue': 'Missing Canonical URL', 'details': 'No canonical.'},
        {'check': 'gsc_coverage', 'type': 'warning', 'url': 'https://site.test/b',
         'issue': 'Coverage', 'details': 'Not indexed.'},
    ]
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(
        lambda fn: fn, lambda crawl_id: issues, lambda: {'agent_mode': 'none'}))
    client = app.test_client()

    brief = client.get('/api/fixer/brief?crawl_id=1').get_json()
    result('the fixer brief is reachable over HTTP', brief.get('success'))
    result('and only issues fixable in a repository reach it',
           brief['issues_fixable'] == 1 and brief['issues_total'] == 2,
           str((brief['issues_fixable'], brief['issues_total'])))
    result('the brief says plainly that nothing was sent anywhere',
           'Nothing has been sent' in brief.get('note', ''))

    ran = client.post('/api/fixer/run', json={'crawl_id': 1}).get_json()
    result('running with no model configured is an answer, not an error',
           ran.get('success') and ran.get('ran') is False
           and 'no model' in ran.get('reason', ''), str(ran.get('reason')))
    result('and the brief comes back even then, so it can be read',
           bool(ran.get('brief')))

    missing = client.get('/api/fixer/brief').get_json()
    result('a request without a crawl id is refused', not missing.get('success'))

    # the monitor now decides whether a change is worth an alert
    from src.core.agents import monitor
    quiet = monitor.assess({'new': [], 'fixed': [], 'counts': {}}, 10, 10,
                           {'mode': 'none'})
    result('a crawl that changed nothing raises no alert',
           quiet.get('alert') is False, str(quiet.get('headline')))

    loud = monitor.assess(
        {'new': [{'type': 'error', 'url': 'https://site.test/a', 'check': 'x'}],
         'fixed': [], 'counts': {}}, 10, 10, {'mode': 'none'})
    result('a crawl that introduced an error does',
           loud.get('alert') is True and bool(loud.get('headline')),
           str(loud.get('headline')))
    result('and it works without any model, so a webhook is never silent '
           'just because agents are off',
           bool(loud.get('reasons')))


TESTS = (
    test_the_two_agent_kinds_nothing_could_reach,
    test_nothing_runs_unless_asked_for,
    test_the_person_is_told_where_their_text_goes,
    test_json_is_extracted_from_whatever_the_model_wraps_it_in,
    test_analyst_findings_are_marked_as_opinions,
    test_a_good_page_produces_nothing,
    test_an_unreachable_model_is_visible_not_silent,
    test_analysts_are_skipped_when_nothing_is_configured,
    test_only_pages_worth_judging_are_sent,
    test_the_fixer_only_offers_what_code_can_fix,
    test_the_brief_groups_by_cause,
    test_the_fixer_never_touches_anything,
    test_what_is_worth_waking_someone_for,
    test_a_template_breaking_reads_differently_from_scattered_findings,
    test_losing_pages_is_an_alert,
    test_the_decision_is_never_delegated_to_a_model,
    test_shape_and_resilience,
    test_live_against_a_real_model,
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
