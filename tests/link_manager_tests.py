"""Tests for link records (src/core/link_manager.py).

    python tests/link_manager_tests.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bs4 import BeautifulSoup  # noqa: E402

from src.core.link_manager import LinkManager  # noqa: E402

BASE = 'https://site.test'
PASSED, FAILED = [], []


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def collect(html):
    manager = LinkManager('site.test')
    soup = BeautifulSoup(html, 'html.parser')
    return manager.collect_all_links(soup, BASE + '/', [])


def test_body_copy_wins_over_the_header_link():
    links = collect('<header><nav><a href="/signup/">Sign up</a></nav></header>'
                    '<main><p>Ready? <a href="/signup/">Start your trial</a></p></main>'
                    '<footer><a href="/signup/">Sign up</a></footer>')
    signup = [l for l in links if l['target_url'].endswith('/signup/')]
    ok = (len(signup) == 1 and signup[0]['placement'] == 'body'
          and signup[0]['anchor_text'] == 'Start your trial')
    result('test_body_copy_wins_over_the_header_link', ok,
           str([(l['placement'], l['anchor_text']) for l in signup]))


def test_a_header_only_link_is_still_recorded():
    links = collect('<header><nav><a href="/pricing/">Pricing</a></nav></header>'
                    '<main><p>No link here.</p></main>')
    pricing = [l for l in links if l['target_url'].endswith('/pricing/')]
    ok = len(pricing) == 1 and pricing[0]['placement'] == 'navigation'
    result('test_a_header_only_link_is_still_recorded', ok, str(pricing))


TESTS = (
    test_body_copy_wins_over_the_header_link,
    test_a_header_only_link_is_still_recorded,
)

if __name__ == '__main__':
    for test in TESTS:
        print(f'\n{test.__name__}:')
        try:
            test()
        except Exception as exc:  # a crash is a failure, not an abort
            result(test.__name__, False, f'raised {type(exc).__name__}: {exc}')
    print('\n' + '=' * 64)
    print(f'  {len(PASSED)} passed, {len(FAILED)} failed')
    sys.exit(1 if FAILED else 0)
