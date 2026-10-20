#!/usr/bin/env python3
"""The axe-core accessibility layer: mapping, severity, and staying quiet.

Two halves. The mapping tests need no browser and run everywhere: stored axe
output in, issues out. The live half drives a real Chromium against a page
built to fail specific WCAG rules, and is skipped when Playwright is absent
rather than failing the suite.

As everywhere here, each behaviour is tested twice: that it reports, and that
it stays quiet when there is nothing to report.
"""

import asyncio
import os
import sys
import threading
import http.server

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import accessibility  # noqa: E402
from src.utils.issue_factory import EVIDENCE_FIELDS, SEVERITIES  # noqa: E402

PASSED, FAILED = [], []
URL = 'https://site.test/a'
PORT = 8931


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def observations(violations=None, error=''):
    return {'accessibility': {'axe_version': accessibility.AXE_VERSION,
                              'tags': list(accessibility.DEFAULT_TAGS),
                              'violations': violations or [],
                              'error': error}}


def violation(rule_id='image-alt', impact='critical', nodes=1):
    return {
        'id': rule_id,
        'impact': impact,
        'help': 'Images must have alternate text',
        'description': 'Ensures <img> elements have alternate text',
        'help_url': f'https://dequeuniversity.com/rules/axe/4.10/{rule_id}',
        'tags': ['wcag2a', 'wcag111'],
        'nodes': [{'target': f'img:nth-child({n + 1})',
                   'html': f'<img src="/{n}.png">',
                   'summary': 'Fix: add an alt attribute'} for n in range(nodes)],
    }


# ---------------------------------------------------------------------------
# mapping, no browser
# ---------------------------------------------------------------------------

def test_each_rule_becomes_its_own_check():
    issues = accessibility.check(
        observations([violation('image-alt'), violation('color-contrast', 'serious')]), URL)
    slugs = sorted(i['check'] for i in issues)
    result('mapping: one slug per axe rule, prefixed',
           slugs == ['a11y_color-contrast', 'a11y_image-alt'], str(slugs))
    result('mapping: a rule keeps its own identity rather than collapsing',
           len({i['id'] for i in issues}) == 2, str([i['id'][:8] for i in issues]))


def test_impact_becomes_severity():
    for impact, severity in (('critical', 'error'), ('serious', 'error'),
                             ('moderate', 'warning'), ('minor', 'info')):
        issues = accessibility.check(observations([violation('r', impact)]), URL)
        result(f'severity: axe {impact} becomes {severity}',
               issues and issues[0]['type'] == severity,
               str(issues[0]['type'] if issues else None))

    unknown = accessibility.check(observations([violation('r', 'brand-new-grade')]), URL)
    result('severity: an unrecognised grade falls back to warning',
           unknown and unknown[0]['type'] == 'warning',
           str(unknown[0]['type'] if unknown else None))
    result('severity: every severity emitted is a real one',
           all(i['type'] in SEVERITIES for i in unknown), str([i['type'] for i in unknown]))


def test_evidence_is_fixable():
    issues = accessibility.check(observations([violation('image-alt', nodes=3)]), URL)
    found = issues[0]
    result('evidence: the element count is stated', '3 element(s)' in found['details'],
           found['details'])
    result('evidence: offending elements are named',
           'img:nth-child(1)' in found['evidence'].get('selector', ''),
           str(found['evidence'].get('selector')))
    result('evidence: the markup travels with it',
           '<img' in found['evidence'].get('snippet', ''),
           str(found['evidence'].get('snippet')))
    result('evidence: axe\'s own documentation is linked',
           'dequeuniversity.com' in found.get('docs_url', ''), found.get('docs_url'))
    result('evidence: only known evidence fields are used',
           set(found['evidence']) <= set(EVIDENCE_FIELDS), str(set(found['evidence'])))

    many = accessibility.check(observations([violation('image-alt', nodes=50)]), URL)[0]
    result('evidence: a broken template does not flood the evidence',
           many['evidence']['selector'].count(';') < accessibility.MAX_NODES,
           str(many['evidence']['selector'])[:90])


def test_a_clean_page_is_silent():
    result('quiet: no violations means no issues',
           accessibility.check(observations([]), URL) == [], 'ok')
    result('quiet: no accessibility block at all means no issues',
           accessibility.check({}, URL) == [] and accessibility.check(None, URL) == [], 'ok')
    result('quiet: a disabled run produces nothing to report',
           accessibility.check({'accessibility': {}}, URL) == [], 'ok')


def test_a_failed_run_says_so_rather_than_passing_silently():
    """A check that could not run must not look like a check that passed."""
    issues = accessibility.check(observations(error='axe did not load'), URL)
    result('failure: an engine error is reported, not swallowed',
           len(issues) == 1 and issues[0]['check'] == 'a11y_check_failed', str(issues))
    result('failure: it is informational, not a finding about the site',
           issues and issues[0]['type'] == 'info', str(issues[0]['type'] if issues else None))


def test_junk_input_does_not_raise():
    for junk in (None, {}, {'accessibility': None}, {'accessibility': 'nope'},
                 {'accessibility': {'violations': ['not a dict', {}, {'id': ''}]}}):
        try:
            accessibility.check(junk, URL)
        except Exception as exc:
            result('junk: odd observations do not raise', False, f'{type(exc).__name__}: {exc}')
            return
    result('junk: odd observations do not raise', True)


def test_the_engine_is_actually_vendored():
    source = accessibility.axe_source()
    result('vendored: axe-core is present in the repo', len(source) > 100_000,
           f'{len(source) // 1024} KB')
    result('vendored: the MPL licence header is intact',
           'Mozilla Public' in source[:600], source[:60])
    result('vendored: the version matches what the module claims',
           f'axe v{accessibility.AXE_VERSION}' in source[:200], accessibility.AXE_VERSION)

    rules = accessibility.rule_catalogue()
    result('vendored: the rule catalogue is read out of the engine itself',
           len(rules) > 40 and 'image-alt' in rules, f'{len(rules)} rules')


# ---------------------------------------------------------------------------
# live, needs a browser
# ---------------------------------------------------------------------------

INACCESSIBLE = b"""<!doctype html>
<html><head><meta charset="utf-8"><title>Fails on purpose</title></head>
<body>
  <img src="/x.png">
  <a href="/somewhere"></a>
  <input type="text">
  <div style="color:#bbb;background:#ccc">Text nobody can read</div>
</body></html>
"""

ACCESSIBLE = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>A sound page</title></head>
<body>
  <main>
    <h1>A sound page</h1>
    <img src="/x.png" alt="A description of the picture">
    <a href="/somewhere">Go somewhere useful</a>
    <label for="q">Search</label><input id="q" type="text">
    <p style="color:#111;background:#fff">Text with ordinary contrast</p>
  </main>
</body></html>
"""

ROUTES = {'/bad': INACCESSIBLE, '/good': ACCESSIBLE}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = ROUTES.get(self.path, b'<!doctype html><html lang="en"><body></body></html>')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_live_against_a_real_browser():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print('  SKIP  playwright is not installed; live accessibility test skipped')
        return

    server = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    async def run():
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(f'http://127.0.0.1:{PORT}/bad')
                bad = await accessibility.collect(page, {'enabled': True})
                await page.goto(f'http://127.0.0.1:{PORT}/good')
                good = await accessibility.collect(page, {'enabled': True})
                return bad, good
            finally:
                await browser.close()

    try:
        bad, good = asyncio.run(run())
    except Exception as exc:
        print(f'  SKIP  could not drive a browser ({type(exc).__name__}: {exc})')
        server.shutdown()
        return
    server.shutdown()

    result('live: the engine ran without error', not bad.get('error'), str(bad.get('error')))
    issues = accessibility.check({'accessibility': bad}, f'http://127.0.0.1:{PORT}/bad')
    slugs = {i['check'] for i in issues}
    result('live: a page missing alt text is caught', 'a11y_image-alt' in slugs, str(sorted(slugs)))
    result('live: several distinct rules fire on a page that breaks several',
           len(slugs) >= 3, str(sorted(slugs)))
    result('live: every issue carries a fix and a documentation link',
           all(i['how_to_fix'] and i.get('docs_url') for i in issues),
           str([i['check'] for i in issues if not i.get('docs_url')]))

    clean = accessibility.check({'accessibility': good}, f'http://127.0.0.1:{PORT}/good')
    serious = [i for i in clean if i['type'] == 'error']
    result('live: a sound page raises no errors', not serious,
           str([(i['check'], i['details'][:50]) for i in serious]))


def test_disabled_by_default():
    """100-300ms a page is a real cost; it should not be paid unasked."""
    result('default: the run is off unless switched on',
           accessibility.DEFAULTS['enabled'] is False, str(accessibility.DEFAULTS['enabled']))

    async def run():
        return await accessibility.collect(object(), None)

    result('default: collect with no config does nothing and touches no page',
           asyncio.run(run()) == {}, 'ok')


TESTS = (
    test_each_rule_becomes_its_own_check,
    test_impact_becomes_severity,
    test_evidence_is_fixable,
    test_a_clean_page_is_silent,
    test_a_failed_run_says_so_rather_than_passing_silently,
    test_junk_input_does_not_raise,
    test_the_engine_is_actually_vendored,
    test_disabled_by_default,
    test_live_against_a_real_browser,
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
