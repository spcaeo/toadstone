#!/usr/bin/env python3
"""AMP validation: the mandatory markup, the forbidden constructs, the pairing.

The first rule is the one that matters most: a page that does not claim to be
AMP is never judged against AMP rules. Get that wrong and every site on the
internet reports as broken AMP.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import amp_checks  # noqa: E402
from src.utils.issue_factory import EVIDENCE_FIELDS, SEVERITIES  # noqa: E402

PASSED, FAILED = [], []
URL = 'https://site.test/amp'

BOILERPLATE = ('<style amp-boilerplate>body{-webkit-animation:none}</style>'
               '<noscript><style amp-boilerplate>body{-webkit-animation:none}</style></noscript>')


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def valid_amp(body='<h1>Hello</h1>', head='', canonical='https://site.test/page'):
    return f"""<!doctype html><html amp lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,minimum-scale=1">
<link rel="canonical" href="{canonical}">
<script async src="https://cdn.ampproject.org/v0.js"></script>
{BOILERPLATE}
<style amp-custom>h1{{color:#000}}</style>
{head}
</head><body>{body}</body></html>"""


def page(url=URL, html=''):
    return {'url': url, 'status_code': 200, 'content_type': 'text/html', 'html': html}


def slugs(issues):
    return sorted(i['check'] for i in issues)


def test_a_page_that_is_not_amp_is_never_judged():
    ordinary = page('https://site.test/a', '<!doctype html><html lang="en"><head>'
                                           '<title>Ordinary</title></head><body>'
                                           '<img src="/x.png"><style>a{color:red}</style>'
                                           '<script>var x = 1</script></body></html>')
    result('scope: an ordinary page raises no AMP issues',
           amp_checks.page_issues(ordinary) == [], str(slugs(amp_checks.page_issues(ordinary))))
    result('scope: an empty page raises nothing',
           amp_checks.page_issues(page(html='')) == [], 'ok')
    result('scope: is_amp recognises both spellings',
           amp_checks.is_amp('<html amp lang="en">')
           and amp_checks.is_amp('<html ⚡ lang="en">')
           and not amp_checks.is_amp('<html lang="en">'), 'ok')
    result('scope: a word merely containing "amp" is not the AMP attribute',
           not amp_checks.is_amp('<html class="campaign" lang="en">'), 'ok')


def test_a_valid_amp_page_is_quiet():
    found = amp_checks.page_issues(page(html=valid_amp()))
    result('valid: a correct AMP page reports nothing at all', found == [], str(slugs(found)))


def test_mandatory_markup():
    cases = [
        ('amp_runtime_missing', valid_amp().replace(
            '<script async src="https://cdn.ampproject.org/v0.js"></script>', '')),
        ('amp_boilerplate_missing', valid_amp().replace(BOILERPLATE, '')),
        ('amp_charset_missing', valid_amp().replace('<meta charset="utf-8">', '')),
        ('amp_viewport_missing', valid_amp().replace(
            '<meta name="viewport" content="width=device-width,minimum-scale=1">', '')),
        ('amp_canonical_missing', valid_amp().replace(
            '<link rel="canonical" href="https://site.test/page">', '')),
    ]
    for slug, html in cases:
        found = slugs(amp_checks.page_issues(page(html=html)))
        result(f'required: {slug} fires when that markup is absent',
               slug in found, str(found))


def test_forbidden_constructs():
    img = amp_checks.page_issues(page(html=valid_amp('<img src="/x.png" alt="x">')))
    result('forbidden: a plain <img> is reported', 'amp_forbidden_tag' in slugs(img), str(slugs(img)))
    result('forbidden: the replacement component is named',
           'amp-img' in str([i['evidence'] for i in img]), str([i['evidence'] for i in img]))

    amp_img = amp_checks.page_issues(page(html=valid_amp(
        '<amp-img src="/x.png" width="4" height="3" alt="x"></amp-img>')))
    result('forbidden: <amp-img> is the right answer and stays quiet',
           'amp_forbidden_tag' not in slugs(amp_img), str(slugs(amp_img)))

    sheet = amp_checks.page_issues(page(html=valid_amp(
        head='<link rel="stylesheet" href="/app.css">')))
    result('forbidden: an external stylesheet is reported',
           'amp_external_stylesheet' in slugs(sheet), str(slugs(sheet)))

    inline = amp_checks.page_issues(page(html=valid_amp('<p style="color:red">x</p>')))
    result('forbidden: an inline style attribute is reported',
           'amp_inline_style' in slugs(inline), str(slugs(inline)))

    two = amp_checks.page_issues(page(html=valid_amp(head='<style amp-custom>p{color:red}</style>')))
    result('forbidden: a second amp-custom block is reported',
           'amp_multiple_custom_styles' in slugs(two), str(slugs(two)))

    script = amp_checks.page_issues(page(html=valid_amp(body='<script>var x = 1</script>')))
    result('forbidden: author JavaScript is reported',
           'amp_custom_javascript' in slugs(script), str(slugs(script)))


def test_scripts_that_are_allowed():
    """The quiet half: AMP pages legitimately carry three kinds of script."""
    allowed = valid_amp(head=(
        '<script async custom-element="amp-carousel" '
        'src="https://cdn.ampproject.org/v0/amp-carousel-0.1.js"></script>'
        '<script type="application/ld+json">{"@type":"Article"}</script>'))
    found = slugs(amp_checks.page_issues(page(html=allowed)))
    result('allowed: an AMP extension script is not author JavaScript',
           'amp_custom_javascript' not in found, str(found))
    result('allowed: a JSON-LD block is not author JavaScript',
           'amp_custom_javascript' not in found, str(found))
    result('allowed: a valid page with extensions is entirely quiet', found == [], str(found))


def test_pairing_between_amp_and_canonical():
    amp = page('https://site.test/amp', valid_amp(canonical='https://site.test/page'))
    bare = page('https://site.test/page',
                '<!doctype html><html lang="en"><head><title>Page</title></head><body>x</body></html>')
    found = slugs(amp_checks.pairing_issues([amp, bare]))
    result('pairing: a canonical with no rel=amphtml back is reported',
           'amphtml_missing_canonical' in found, str(found))

    linked = page('https://site.test/page',
                  '<!doctype html><html lang="en"><head>'
                  '<link rel="amphtml" href="https://site.test/amp"></head><body>x</body></html>')
    result('pairing: a correctly wired pair is quiet',
           amp_checks.pairing_issues([amp, linked]) == [],
           str(slugs(amp_checks.pairing_issues([amp, linked]))))

    wrong = page('https://site.test/page',
                 '<!doctype html><html lang="en"><head>'
                 '<link rel="amphtml" href="https://site.test/not-amp"></head><body>x</body></html>')
    not_amp = page('https://site.test/not-amp',
                   '<!doctype html><html lang="en"><head></head><body>x</body></html>')
    found = slugs(amp_checks.pairing_issues([wrong, not_amp]))
    result('pairing: pointing rel=amphtml at a non-AMP page is an error',
           'amphtml_target_not_amp' in found, str(found))

    uncrawled = slugs(amp_checks.pairing_issues([wrong]))
    result('pairing: an AMP version the crawl never saw is noted, not judged',
           uncrawled == ['amphtml_target_not_crawled'], str(uncrawled))


def test_issue_shape_and_resilience():
    issues = amp_checks.amp_issues([page(html=valid_amp('<img src="/x.png">'))])
    result('shape: every severity is real', all(i['type'] in SEVERITIES for i in issues),
           str({i['type'] for i in issues}))
    result('shape: only known evidence fields are used',
           all(set(i['evidence']) <= set(EVIDENCE_FIELDS) for i in issues),
           str([set(i['evidence']) for i in issues]))
    result('shape: every issue carries a fix',
           all(i['how_to_fix'] for i in issues), 'ok')
    result('shape: ids are stable across runs',
           [i['id'] for i in issues]
           == [i['id'] for i in amp_checks.amp_issues([page(html=valid_amp('<img src="/x.png">'))])],
           'stable')

    for junk in (None, [], [None], [{}], [{'url': URL}], [{'url': URL, 'html': None}]):
        try:
            amp_checks.amp_issues(junk)
        except Exception as exc:
            result('junk: odd input does not raise', False, f'{type(exc).__name__}: {exc}')
            return
    result('junk: odd input does not raise', True)



def test_the_crawler_keeps_the_markup_these_checks_read():
    """Every AMP check was dead on a live crawl, and the tests could not see it.

    They read page['html']. Nothing in the crawler ever set that field, so on
    a real site not one of them could fire - while every test here passed,
    because each one hands the markup in directly. The checks were correct and
    unreachable, which is the failure a unit test is worst at noticing.

    The crawler now keeps the document for pages that claim to be AMP, and
    only those: holding every page's HTML would cost hundreds of megabytes on
    a large crawl, for a feature most sites do not use.
    """
    from src.core.crawl.fetching import _keep_html_if_amp

    amp = ('<!doctype html><html amp lang="en"><head><title>A</title>'
           '</head><body></body></html>')

    kept = {}
    _keep_html_if_amp(kept, amp)
    result('the markup is kept for a page declaring amp',
           kept.get('html') == amp, str(list(kept)))

    for markup in ('<html \u26a1 lang="en"><head></head></html>',
                   '<html><head><style amp-boilerplate></style></head></html>',
                   '<html><head><script src="https://cdn.ampproject.org/v0.js">'
                   '</script></head></html>'):
        probe = {}
        _keep_html_if_amp(probe, markup)
        if not probe.get('html'):
            result('every way of declaring AMP is recognised', False, markup[:60])
            break
    else:
        result('every way of declaring AMP is recognised', True)

    ordinary = {}
    _keep_html_if_amp(ordinary, '<!doctype html><html lang="en"><head><title>B</title>'
                                '</head><body><p>An ordinary page.</p></body></html>')
    result('an ordinary page does not have its markup kept',
           'html' not in ordinary, str(list(ordinary)))

    found = amp_checks.amp_issues([{'url': 'https://site.test/a', 'html': amp,
                                    'status_code': 200, 'content_type': 'text/html'}])
    result('and the checks fire on exactly what the crawler keeps',
           bool(found), str(len(found)))


TESTS = (
    test_the_crawler_keeps_the_markup_these_checks_read,
    test_a_page_that_is_not_amp_is_never_judged,
    test_a_valid_amp_page_is_quiet,
    test_mandatory_markup,
    test_forbidden_constructs,
    test_scripts_that_are_allowed,
    test_pairing_between_amp_and_canonical,
    test_issue_shape_and_resilience,
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
