#!/usr/bin/env python3
"""The false positives found by crawling ten real SEO sites, each pinned.

Issue #1. Every case here was produced by a real crawl of a real site on
2026-10-23 and reported a defect that did not exist. They are gathered in one
file because they share a cause: the crawler treating "we could not see it" as
"it is broken", which is the single fastest way to lose the trust of somebody
who knows their own site is fine.

Each test states the site behaviour that triggered it.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import http_status                                    # noqa: E402
from src.core.header_checks import _srcset_urls                     # noqa: E402
from src.core.issue_detector import is_tracking_pixel               # noqa: E402
from src.core.technical_checks import technical_issues              # noqa: E402

PASSED, FAILED = [], []


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def page(url, **extra):
    row = {'url': url, 'status_code': 200, 'content_type': 'text/html',
           'title': 'A page', 'meta_description': 'x' * 80, 'h1': 'A page',
           'word_count': 400, 'images': [], 'links': []}
    row.update(extra)
    return row


def link(source, target, status):
    return {'source_url': source, 'target_url': target, 'target_status': status,
            'is_internal': False, 'placement': 'body', 'anchor_text': 'a link'}


def checks_for(pages, links):
    """Every check slug the technical pass emits for this crawl."""
    issues = technical_issues(pages, links, 'https://site.test') or []
    return [i.get('check') for i in issues]


# --------------------------------------------------------------- status codes

def test_a_bot_wall_is_not_a_broken_link():
    """Moz answers an automated client with 403. The URLs are fine.

    This produced "301 pages link to broken URLs" on a site whose links all
    work in a browser.
    """
    pages = [page('https://site.test/a')]
    links = [link('https://site.test/a', 'https://moz.test/blocked', 403)]
    slugs = checks_for(pages, links)
    result('403: not reported as a broken link',
           'links_to_broken_urls' not in slugs, str(slugs))
    result('403: reported as blocked instead, so the fact is not lost',
           'links_to_blocked_urls' in slugs, str(slugs))

    for status in (401, 407, 429):
        slugs = checks_for(pages, [link('https://site.test/a', 'https://x.test/y', status)])
        result(f'{status}: blocked, not broken',
               'links_to_broken_urls' not in slugs and 'links_to_blocked_urls' in slugs,
               str(slugs))


def test_a_404_is_still_a_broken_link():
    """The fix must not hide real breakage, which is the obvious way to fail."""
    pages = [page('https://site.test/a')]
    for status in (404, 410, 500, 503):
        slugs = checks_for(pages, [link('https://site.test/a', 'https://x.test/gone', status)])
        result(f'{status}: still reported as broken',
               'links_to_broken_urls' in slugs, str(slugs))


def test_the_status_vocabulary():
    result('200 and 301 are neither broken nor blocked',
           http_status.classify(200) == 'ok' and http_status.classify(301) == 'ok')
    result('0 is its own thing, not broken',
           http_status.classify(0) == 'no_response')
    result('a blocked status describes itself for a report',
           '403' in http_status.describe_blocked(403)
           and 'refused' in http_status.describe_blocked(403),
           http_status.describe_blocked(403))


# ------------------------------------------------------------------- srcset

def test_a_url_with_commas_is_one_url():
    """Cloudflare's resizer writes commas into the path. Seen on Yoast."""
    srcset = ('https://x.test/cdn-cgi/image/width=800,quality=85,format=auto/a.png 800w, '
              'https://x.test/cdn-cgi/image/width=1600,quality=85,format=auto/a.png 1600w')
    got = _srcset_urls(srcset)
    result('srcset: two candidates, not six fragments', len(got) == 2, str(got))
    result('srcset: no fragment masquerades as a URL',
           not any(u.startswith(('quality=', 'format=')) for u in got), str(got))
    result('srcset: ordinary srcsets still split',
           _srcset_urls('a.png 1x, b.png 2x') == ['a.png', 'b.png'])


# ------------------------------------------------------------- tracking pixels

def test_a_tracking_pixel_is_not_a_content_image():
    """Every one of the ten sites tripped this."""
    result('1x1 beacon is recognised', is_tracking_pixel({'width': '1', 'height': '1'}))
    result('2x2 spacer is recognised', is_tracking_pixel({'width': 2, 'height': 2}))
    result('a real image is not', not is_tracking_pixel({'width': '800', 'height': '600'}))
    result('an image with no declared size is not guessed at',
           not is_tracking_pixel({'width': '', 'height': ''}))
    result('a 1px-wide rule is not a beacon',
           not is_tracking_pixel({'width': '1', 'height': '600'}))


# ------------------------------------------------------------------- robots.txt

class _Response:
    def __init__(self, status, text='', content_type='text/plain'):
        self.status_code = status
        self.text = text
        self.headers = {'Content-Type': content_type}


def test_an_unreadable_robots_txt_does_not_disallow_the_site():
    """RFC 9309 2.3.1: 4xx means unavailable, and unavailable means allowed.

    The old code set disallow_all on 401/403, so one Cloudflare challenge on
    /robots.txt stopped the entire crawl and then produced canonical findings
    about a block nobody had configured.
    """
    from src.crawler import WebCrawler

    challenge = _Response(200, '<!DOCTYPE html><html><title>Just a moment...</title>',
                          'text/html; charset=UTF-8')
    result('a Cloudflare interstitial is not read as a robots file',
           WebCrawler._looks_like_a_challenge(challenge))
    result('a real robots.txt is not mistaken for one',
           not WebCrawler._looks_like_a_challenge(
               _Response(200, 'User-agent: *\nDisallow: /private/')))

    crawler = WebCrawler()
    for status, expect_allowed, label in ((403, True, '403'), (401, True, '401'),
                                          (404, True, '404'), (503, False, '503')):
        parser_state = {}

        class _Session:
            @staticmethod
            def get(url, timeout=None):
                return _Response(status, 'blocked' if status >= 400 else '')

        crawler.session = _Session()
        crawler._robots_cache = {}
        crawler._robots_notice_given = False
        allowed = crawler._check_robots_txt('https://site.test/page')
        result(f'robots.txt {label}: crawling is {"allowed" if expect_allowed else "held off"}',
               allowed is expect_allowed, f'allowed={allowed}')
        del parser_state


TESTS = (
    test_a_bot_wall_is_not_a_broken_link,
    test_a_404_is_still_a_broken_link,
    test_the_status_vocabulary,
    test_a_url_with_commas_is_one_url,
    test_a_tracking_pixel_is_not_a_content_image,
    test_an_unreadable_robots_txt_does_not_disallow_the_site,
)


def main():
    for test in TESTS:
        print(f'\n{test.__name__}:')
        try:
            test()
        except Exception as exc:
            result(test.__name__, False, f'raised {type(exc).__name__}: {exc}')
    print('\n' + '=' * 64)
    print(f'  {len(PASSED)} passed, {len(FAILED)} failed')
    if FAILED:
        print('  failed: ' + ', '.join(FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
