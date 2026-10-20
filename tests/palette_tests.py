"""Browser tests for the command palette (web/static/js/palette.js).

Drives the real page with Playwright against a running Toadstone, because
the matcher only exists in the browser and testing a reimplementation of it
in Python would test the reimplementation.

    docker run --rm --network container:toadstone \\
        -v "$PWD/tests:/app/tests:ro" toadstone:latest python tests/palette_tests.py

Set TOADSTONE_URL to test another server (default http://127.0.0.1:5000).
Nothing here starts a crawl or writes anything.
"""

import os
import sys

from playwright.sync_api import sync_playwright

APP = os.environ.get('TOADSTONE_URL', 'http://127.0.0.1:5000').rstrip('/')

PASSED, FAILED = [], []


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def titles(page, query):
    """What the palette would show for a query, in order."""
    return page.evaluate(
        'q => ToadstonePalette.search(q).map(c => c.title)', query)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(f'{APP}/', wait_until='networkidle')
        page.wait_for_function('typeof window.ToadstonePalette !== "undefined"',
                               timeout=15000)

        print('\nthe registry:')
        total = page.evaluate('ToadstonePalette.commands.length')
        result('every destination is registered', total >= 28, f'{total} commands')
        groups = page.evaluate(
            '[...new Set(ToadstonePalette.commands.map(c => c.group))].sort()')
        result('commands are grouped', 'Views' in groups and 'Settings' in groups,
               str(groups))

        # Each settings panel needs its own entry. They are otherwise reachable
        # only by opening Settings and reading nine unlabelled tabs, which is
        # the complaint this whole feature answers.
        panels = ('crawler', 'google', 'requests', 'filters', 'export',
                  'javascript', 'issues', 'customcss', 'advanced')
        ids = page.evaluate('ToadstonePalette.commands.map(c => c.id)')
        missing = [p for p in panels if f'settings:{p}' not in ids]
        result('every settings panel is its own command', not missing,
               f'missing: {missing}')

        print('\nmatching:')
        result('a prefix finds its command',
               titles(page, 'exp')[:1] == ['Export settings'],
               str(titles(page, 'exp')[:3]))

        # The bug this test exists for. Scores carry bonuses as negative
        # numbers (a title prefix is worth -60) and the no-match sentinel was
        # also negative, so `score < 0` discarded every strongest match:
        # "exp" returned nothing at all while Export sat in the registry, and
        # "goog" returned every settings panel except Google.
        goog = titles(page, 'goog')
        result('a strong match is not mistaken for no match at all',
               len(goog) > 0 and goog[0].startswith('Google'),
               f'{len(goog)} results, top = {goog[0] if goog else None!r}')

        result('an initialism matches across words',
               titles(page, 'stcd')[:1] == ['Status Codes'],
               str(titles(page, 'stcd')[:3]))
        result('letters from two words match',
               titles(page, 'jsrend')[:1] == ['JavaScript rendering'],
               str(titles(page, 'jsrend')[:3]))

        # A subsequence must be found within one field, not assembled from
        # several. Joined fields matched "gugl" to "Log out", by taking
        # lo(g) o(u)t ... si(g)n ... (l)eave.
        result('a match may not borrow letters from different fields',
               titles(page, 'gugl') == [], str(titles(page, 'gugl')))

        result('nonsense matches nothing', titles(page, 'qqzzxx') == [],
               str(titles(page, 'qqzzxx')))
        result('an empty query shows everything',
               len(titles(page, '')) == total)

        print('\nopening and closing:')
        page.keyboard.press('Meta+k')
        page.wait_for_timeout(300)
        result('the shortcut opens it',
               page.evaluate('!document.getElementById("commandPalette").hidden'))
        result('focus lands in the field',
               page.evaluate('document.activeElement.id') == 'cpInput')

        page.keyboard.press('Escape')
        page.wait_for_timeout(250)
        result('escape closes it',
               page.evaluate('document.getElementById("commandPalette").hidden'))

        print('\nkeyboard:')
        page.keyboard.press('Meta+k')
        page.wait_for_timeout(250)
        page.fill('#cpInput', 'settings')
        page.wait_for_timeout(250)
        first = page.evaluate(
            '(document.querySelector(".cp-row.is-on .cp-row-title")||{}).textContent')
        page.keyboard.press('ArrowDown')
        page.wait_for_timeout(150)
        second = page.evaluate(
            '(document.querySelector(".cp-row.is-on .cp-row-title")||{}).textContent')
        result('arrow keys move the cursor', first and second and first != second,
               f'{first!r} -> {second!r}')

        rows = page.evaluate('document.querySelectorAll(".cp-row").length')
        for _ in range(rows + 1):
            page.keyboard.press('ArrowDown')
        page.wait_for_timeout(150)
        result('the cursor wraps rather than running off the end',
               page.evaluate('document.querySelectorAll(".cp-row.is-on").length') == 1)

        print('\nrunning a command:')
        page.fill('#cpInput', 'google')
        page.wait_for_timeout(250)
        page.keyboard.press('Enter')
        page.wait_for_timeout(700)
        result('it closes on enter',
               page.evaluate('document.getElementById("commandPalette").hidden'))
        result('and lands on the panel it named',
               'settings=google' in page.evaluate('location.search'),
               page.evaluate('location.search'))

        print('\nevery settings panel is actually reachable:')
        # applyTierRestrictions() hides any tab missing from a hard-coded
        # allow-list. A tab left out of it is not restricted, it is invisible,
        # with nothing on screen saying why. That has happened twice: the
        # Google tab, and Integrations within an hour of being added. This
        # asserts the markup and the allow-list agree.
        page.evaluate('closePalette()')
        page.evaluate('openSettings()')
        page.wait_for_timeout(900)
        declared = page.evaluate("""
            [...document.querySelectorAll('.settings-tab-btn')].map(b =>
                (b.getAttribute('onclick') || '').match(/switchSettingsTab\\('(.+?)'\\)/)[1])
        """)
        hidden = page.evaluate("""
            [...document.querySelectorAll('.settings-tab-btn')]
                .filter(b => getComputedStyle(b).display === 'none')
                .map(b => (b.getAttribute('onclick') || '').match(/switchSettingsTab\\('(.+?)'\\)/)[1])
        """)
        result('every settings tab in the markup is visible to an admin',
               not hidden, f'declared {declared}, hidden {hidden}')

        for panel in declared:
            page.evaluate('n => switchSettingsTab(n)', panel)
            page.wait_for_timeout(120)
            shown = page.evaluate(
                'n => { const el = document.getElementById(n + "-settings");'
                ' return !!el && el.classList.contains("active"); }', panel)
            if not shown:
                result(f'panel "{panel}" opens', False)
                break
        else:
            result(f'all {len(declared)} settings panels open', True)

        # And the tab strip must show them all rather than scrolling the last
        # ones off an edge with a hidden scrollbar.
        overflow = page.evaluate("""
            (() => { const s = document.querySelector('.settings-tabs');
                     return s.scrollWidth - s.clientWidth; })()
        """)
        result('no settings tab is off the edge of the strip', overflow <= 1,
               f'{overflow}px hidden')
        page.evaluate('closeSettings()')
        page.wait_for_timeout(250)

        print('\nunavailable commands:')
        page.keyboard.press('Meta+k')
        page.wait_for_timeout(250)
        page.fill('#cpInput', 'internal')
        page.wait_for_timeout(250)
        # With no crawl open, a view that needs one is shown with its reason
        # rather than hidden: "why can't I" is a question the menu should
        # answer rather than dodge.
        result('a command that cannot run says why, and is not hidden',
               page.evaluate('document.querySelectorAll(".cp-row.is-off").length') >= 1,
               page.evaluate(
                   '(document.querySelector(".cp-row-why")||{}).textContent'))
        page.keyboard.press('Escape')

        print('\nno command may lead nowhere:')
        # 'Why "Toadstone"?' opened /docs/about-the-name for weeks. There has
        # never been such a route: the command was in the palette, looked
        # right, and opened a 404 in a new tab where nobody would connect it
        # back. Any command whose action is a URL on this app gets fetched.
        page.keyboard.press('Escape')
        page.wait_for_timeout(200)
        sources = page.evaluate("""
            (() => {
                const out = [];
                for (const c of ((window.ToadstonePalette || {}).commands || [])) {
                    const body = (c.run || '').toString();
                    const m = body.match(/open\\(\\s*['\"](\\/[^'\"]*)['\"]/);
                    if (m) out.push([c.id, m[1]]);
                }
                return out;
            })()
        """)
        broken = []
        for command_id, path in sources:
            status = page.evaluate('p => fetch(p, {method: "GET"}).then(r => r.status)', path)
            if status >= 400:
                broken.append(f'{command_id} -> {path} ({status})')
        result('every command that opens a local URL opens one that exists',
               not broken, '; '.join(broken) or f'{len(sources)} checked')

        print('\nthe story is reachable:')
        page.keyboard.press('Meta+k')
        page.wait_for_timeout(250)
        page.fill('#cpInput', 'why toad')
        page.wait_for_timeout(300)
        page.keyboard.press('Enter')
        page.wait_for_timeout(1200)
        active = page.evaluate(
            '(document.querySelector(".tab-btn.active")||{}).textContent') or ''
        result('"Why Toadstone?" lands on the About tab', active.strip() == 'About',
               repr(active.strip()))
        result('and the About tab actually rendered',
               page.evaluate('!!document.querySelector(".ab-hero h2")'))

        print('\nthe plugin tabs are reachable by clicking them:')
        # Both panels render perfectly when you call their function. The
        # question a test has to answer is whether a person can get to them,
        # which is the one the Google settings tab failed twice.
        for label, marker in (('About', '.ab-hero h2'), ('Licence', '.lc-head h2')):
            button = page.locator('.tab-btn', has_text=label).first
            box = button.bounding_box()
            visible = bool(box) and box['width'] > 0 and box['height'] > 0
            result(f'the {label} tab has a clickable box', visible, str(box))
            if visible:
                button.click()
                page.wait_for_timeout(1200)
                result(f'clicking {label} renders its panel',
                       page.evaluate(f'!!document.querySelector("{marker}")'))

        result('no page errors', not errors, str(errors[:3]))
        browser.close()

    print('\n' + '=' * 64)
    print(f'  {len(PASSED)} passed, {len(FAILED)} failed')
    if FAILED:
        print('  failed: ' + ', '.join(FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
