"""Tests for the header, mixed-content and pagination checks
(src/core/header_checks.py).

    python tests/header_tests.py

Every check gets a page that should trip it and a page that should not. The
quiet half is the half that matters: this crawler's recurring defect is a check
that fires on a healthy page, so each test states the correct markup as well as
the broken markup and asserts that the correct markup is left alone.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bs4 import BeautifulSoup  # noqa: E402

from src.core.header_checks import extract_page_resources, header_issues  # noqa: E402

BASE = 'https://site.test'
PASSED, FAILED = [], []

# What a site that has done everything right sends back.
GOOD_HEADERS = {
    'content-type': 'text/html; charset=utf-8',
    'strict-transport-security': 'max-age=31536000; includeSubDomains',
    'content-security-policy': "default-src 'self'; frame-ancestors 'self'",
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'strict-origin-when-cross-origin',
    'permissions-policy': 'camera=(), microphone=(), geolocation=()',
    'server': 'nginx',
}


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def page(url, headers=None, **fields):
    """A page served correctly; keyword arguments spoil it."""
    base = {
        'url': url, 'content_type': 'text/html; charset=utf-8', 'status_code': 200,
        'canonical_url': url, 'is_internal': True,
        'response_headers': dict(GOOD_HEADERS, **(headers or {})),
        'insecure_resources': [], 'insecure_form_actions': [], 'pagination': {},
    }
    base.update(fields)
    return base


def run(pages, links=None, **config):
    return header_issues(pages, links or [], BASE, config=config)


def checks(issues):
    return sorted({issue['check'] for issue in issues})


def find(issues, check):
    return [issue for issue in issues if issue['check'] == check]


def fires(name, pages, check, links=None, **config):
    """One page trips one check, and nothing else trips with it."""
    issues = run(pages, links, **config)
    hits = find(issues, check)
    result(f'{name}: fires', bool(hits), f'got {checks(issues) or "nothing"}')
    return hits


def quiet(name, pages, check, links=None, **config):
    issues = run(pages, links, **config)
    hits = find(issues, check)
    result(f'{name}: quiet', not hits,
           f'{hits[0]["details"]}' if hits else '')


# ---------------------------------------------------------------------------
# the baseline
# ---------------------------------------------------------------------------

def test_a_correctly_served_site_reports_nothing():
    issues = run([page(f'{BASE}/'), page(f'{BASE}/about'), page(f'{BASE}/pricing')], [
        {'source_url': f'{BASE}/', 'target_url': f'{BASE}/about', 'is_internal': True,
         'target_status': 200},
    ])
    result('a correctly served site reports nothing', not issues,
           ', '.join(f"{i['check']} on {i['url']}" for i in issues))


def test_pages_with_no_recorded_headers_are_not_judged():
    # A crawl loaded from the database has no headers; reading that as "sends
    # none" would report every check against every page.
    issues = run([page(f'{BASE}/', response_headers=None),
                  page(f'{BASE}/b', response_headers={})])
    result('no recorded headers: nothing is judged', not issues, str(checks(issues)))


def test_error_pages_and_non_html_are_not_judged():
    bare = {'content-type': 'application/pdf'}
    issues = run([
        page(f'{BASE}/missing', headers=bare, status_code=404, response_headers=bare),
        page(f'{BASE}/doc.pdf', content_type='application/pdf', response_headers=bare),
        page('https://other.test/', is_internal=False, response_headers=bare),
    ])
    result('error pages, PDFs and other sites are not judged', not issues, str(checks(issues)))


# ---------------------------------------------------------------------------
# security response headers
# ---------------------------------------------------------------------------

def test_hsts():
    hits = fires('missing_hsts', [page(f'{BASE}/', {'strict-transport-security': ''})],
                 'missing_hsts')
    if hits:
        result('missing_hsts: names the header', 'Strict-Transport-Security' in hits[0]['details'])
    quiet('missing_hsts', [page(f'{BASE}/')], 'missing_hsts')

    # HSTS is meaningless over http, and reporting it there is noise.
    quiet('missing_hsts on an http page',
          [page('http://site.test/', {'strict-transport-security': ''})], 'missing_hsts')


def test_hsts_max_age():
    fires('hsts_max_age_too_short',
          [page(f'{BASE}/', {'strict-transport-security': 'max-age=86400'})],
          'hsts_max_age_too_short')
    fires('hsts with no max-age',
          [page(f'{BASE}/', {'strict-transport-security': 'includeSubDomains'})],
          'hsts_max_age_too_short')
    quiet('hsts_max_age_too_short', [page(f'{BASE}/')], 'hsts_max_age_too_short')
    quiet('hsts_max_age_too_short at exactly six months',
          [page(f'{BASE}/', {'strict-transport-security': 'max-age=15552000'})],
          'hsts_max_age_too_short')


def test_csp_presence():
    hits = fires('missing_csp',
                 [page(f'{BASE}/', {'content-security-policy': '',
                                    'x-frame-options': 'SAMEORIGIN'})], 'missing_csp')
    if hits:
        result('missing_csp: severity is info', hits[0]['type'] == 'info', hits[0]['type'])
    quiet('missing_csp', [page(f'{BASE}/')], 'missing_csp')

    # A report-only policy enforces nothing, so the page still has no CSP -
    # but the finding should say so rather than pretend none was sent.
    hits = find(run([page(f'{BASE}/', {
        'content-security-policy': '', 'x-frame-options': 'DENY',
        'content-security-policy-report-only': "default-src 'self'"})]), 'missing_csp')
    result('missing_csp: mentions a report-only policy',
           bool(hits) and 'report-only' in hits[0]['details'],
           hits[0]['details'] if hits else 'did not fire')


def test_csp_unsafe_inline():
    fires('csp_unsafe_inline',
          [page(f'{BASE}/', {'content-security-policy':
                             "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                             "frame-ancestors 'self'"})], 'csp_unsafe_inline')
    quiet('csp_unsafe_inline', [page(f'{BASE}/')], 'csp_unsafe_inline')

    # A nonce or a hash alongside 'unsafe-inline' makes the keyword inert in
    # every CSP2 browser, so the policy is not actually permissive.
    quiet('csp_unsafe_inline with a nonce',
          [page(f'{BASE}/', {'content-security-policy':
                             "script-src 'self' 'unsafe-inline' 'nonce-r4nd0m'; "
                             "frame-ancestors 'self'"})], 'csp_unsafe_inline')
    quiet('csp_unsafe_inline with a hash',
          [page(f'{BASE}/', {'content-security-policy':
                             "script-src 'unsafe-inline' 'sha256-abc123'; "
                             "frame-ancestors 'self'"})], 'csp_unsafe_inline')
    quiet('csp_unsafe_inline with strict-dynamic',
          [page(f'{BASE}/', {'content-security-policy':
                             "script-src 'unsafe-inline' 'strict-dynamic'; "
                             "frame-ancestors 'self'"})], 'csp_unsafe_inline')

    # style-src 'unsafe-inline' is near-universal and is not what the check is
    # about; only scripts are judged.
    quiet('csp_unsafe_inline for styles only',
          [page(f'{BASE}/', {'content-security-policy':
                             "default-src 'self'; style-src 'unsafe-inline'; "
                             "frame-ancestors 'self'"})], 'csp_unsafe_inline')


def test_csp_unsafe_eval():
    fires('csp_unsafe_eval',
          [page(f'{BASE}/', {'content-security-policy':
                             "script-src 'self' 'unsafe-eval'; frame-ancestors 'self'"})],
          'csp_unsafe_eval')
    quiet('csp_unsafe_eval', [page(f'{BASE}/')], 'csp_unsafe_eval')


def test_x_content_type_options():
    fires('missing_x_content_type_options',
          [page(f'{BASE}/', {'x-content-type-options': ''})], 'missing_x_content_type_options')
    quiet('missing_x_content_type_options', [page(f'{BASE}/')],
          'missing_x_content_type_options')


def test_x_frame_options():
    fires('missing_x_frame_options',
          [page(f'{BASE}/', {'content-security-policy': "default-src 'self'"})],
          'missing_x_frame_options')

    # The whole point: a page with a correct CSP is not also missing XFO.
    quiet('missing_x_frame_options with CSP frame-ancestors', [page(f'{BASE}/')],
          'missing_x_frame_options')
    quiet('missing_x_frame_options with the header itself',
          [page(f'{BASE}/', {'content-security-policy': "default-src 'self'",
                             'x-frame-options': 'SAMEORIGIN'})], 'missing_x_frame_options')


def test_referrer_policy():
    fires('missing_referrer_policy', [page(f'{BASE}/', {'referrer-policy': ''})],
          'missing_referrer_policy')
    quiet('missing_referrer_policy', [page(f'{BASE}/')], 'missing_referrer_policy')

    # <meta name="referrer"> does the same job as the header.
    quiet('missing_referrer_policy with a meta tag',
          [page(f'{BASE}/', {'referrer-policy': ''},
                meta_tags={'referrer': 'strict-origin'})], 'missing_referrer_policy')


def test_insecure_referrer_policy():
    fires('insecure_referrer_policy', [page(f'{BASE}/', {'referrer-policy': 'unsafe-url'})],
          'insecure_referrer_policy')
    fires('insecure_referrer_policy in a list',
          [page(f'{BASE}/', {'referrer-policy': 'no-referrer, unsafe-url'})],
          'insecure_referrer_policy')
    quiet('insecure_referrer_policy', [page(f'{BASE}/')], 'insecure_referrer_policy')
    quiet('insecure_referrer_policy for no-referrer-when-downgrade',
          [page(f'{BASE}/', {'referrer-policy': 'no-referrer-when-downgrade'})],
          'insecure_referrer_policy')


def test_permissions_policy():
    fires('missing_permissions_policy', [page(f'{BASE}/', {'permissions-policy': ''})],
          'missing_permissions_policy')
    quiet('missing_permissions_policy', [page(f'{BASE}/')], 'missing_permissions_policy')
    quiet('missing_permissions_policy with the legacy header',
          [page(f'{BASE}/', {'permissions-policy': '', 'feature-policy': "camera 'none'"})],
          'missing_permissions_policy')


def test_server_version_disclosed():
    hits = fires('server_version_disclosed', [page(f'{BASE}/', {'server': 'Apache/2.4.41 (Ubuntu)'})],
                 'server_version_disclosed')
    if hits:
        result('server_version_disclosed: quotes the header',
               '2.4.41' in hits[0]['details'], hits[0]['details'])
    fires('server_version_disclosed via x-powered-by',
          [page(f'{BASE}/', {'x-powered-by': 'PHP/8.1.2'})], 'server_version_disclosed')

    # A product name with no version names nothing an attacker can look up.
    quiet('server_version_disclosed', [page(f'{BASE}/')], 'server_version_disclosed')
    quiet('server_version_disclosed for a bare product name',
          [page(f'{BASE}/', {'server': 'cloudflare', 'x-powered-by': 'Express'})],
          'server_version_disclosed')


# ---------------------------------------------------------------------------
# cookies
# ---------------------------------------------------------------------------

GOOD_COOKIE = 'session=abc; Path=/; Secure; HttpOnly; SameSite=Lax'


def test_cookie_without_secure_flag():
    hits = fires('cookie_without_secure_flag',
                 [page(f'{BASE}/', {'set-cookie': 'session=abc; HttpOnly; SameSite=Lax'})],
                 'cookie_without_secure_flag')
    if hits:
        result('cookie_without_secure_flag: names the cookie',
               'session' in hits[0]['details'], hits[0]['details'])
    quiet('cookie_without_secure_flag', [page(f'{BASE}/', {'set-cookie': GOOD_COOKIE})],
          'cookie_without_secure_flag')
    quiet('cookie_without_secure_flag with no cookies at all', [page(f'{BASE}/')],
          'cookie_without_secure_flag')

    # Over http the missing flag changes nothing; the scheme is the problem.
    quiet('cookie_without_secure_flag on an http page',
          [page('http://site.test/', {'strict-transport-security': '',
                                      'set-cookie': 'session=abc; HttpOnly; SameSite=Lax'})],
          'cookie_without_secure_flag')


def test_cookie_without_httponly():
    fires('cookie_without_httponly',
          [page(f'{BASE}/', {'set-cookie': 'session=abc; Secure; SameSite=Lax'})],
          'cookie_without_httponly')
    quiet('cookie_without_httponly', [page(f'{BASE}/', {'set-cookie': GOOD_COOKIE})],
          'cookie_without_httponly')


def test_cookie_without_samesite():
    fires('cookie_without_samesite',
          [page(f'{BASE}/', {'set-cookie': 'session=abc; Secure; HttpOnly'})],
          'cookie_without_samesite')
    quiet('cookie_without_samesite', [page(f'{BASE}/', {'set-cookie': GOOD_COOKIE})],
          'cookie_without_samesite')
    quiet('cookie_without_samesite for SameSite=None',
          [page(f'{BASE}/', {'set-cookie': 'session=abc; Secure; HttpOnly; SameSite=None'})],
          'cookie_without_samesite')


def test_several_cookies_in_one_joined_header():
    # requests joins repeated Set-Cookie headers with ", ", and an Expires
    # date carries a comma of its own. Splitting on every comma would invent
    # a cookie called "21 Oct 2015 07:28:00 GMT".
    joined = ('a=1; Expires=Wed, 21 Oct 2015 07:28:00 GMT; Secure; HttpOnly; SameSite=Lax, '
              'b=2; Secure; HttpOnly')
    hits = find(run([page(f'{BASE}/', {'set-cookie': joined})]), 'cookie_without_samesite')
    names = hits[0]['evidence'].get('found', '') if hits else ''
    result('joined Set-Cookie: splits into real cookies', names == 'b', f'got {names!r}')

    # A list, which is what the crawler stores when urllib3 kept the originals.
    hits = find(run([page(f'{BASE}/', {'set-cookie': ['a=1; Secure; HttpOnly; SameSite=Lax',
                                                      'b=2; Secure; HttpOnly']})]),
                'cookie_without_samesite')
    names = hits[0]['evidence'].get('found', '') if hits else ''
    result('listed Set-Cookie: reads every entry', names == 'b', f'got {names!r}')


def test_one_page_reports_each_header_problem_once():
    bad = page(f'{BASE}/', {'strict-transport-security': '', 'content-security-policy': '',
                            'x-content-type-options': '', 'referrer-policy': '',
                            'permissions-policy': '',
                            'set-cookie': 'a=1, b=2, c=3'})
    issues = run([bad])
    counts = {check: len(find(issues, check)) for check in checks(issues)}
    repeated = {check: count for check, count in counts.items() if count > 1}
    result('each header problem is reported once per page', not repeated, str(repeated))


# ---------------------------------------------------------------------------
# mixed content
# ---------------------------------------------------------------------------

def insecure(*pairs):
    return [{'kind': kind, 'url': url} for kind, url in pairs]


def test_mixed_content_active():
    hits = fires('mixed_content_active',
                 [page(f'{BASE}/', insecure_resources=insecure(
                     ('script', 'http://cdn.test/a.js'),
                     ('stylesheet', 'http://cdn.test/a.css')))], 'mixed_content_active')
    if hits:
        result('mixed_content_active: severity is error', hits[0]['type'] == 'error',
               hits[0]['type'])
        result('mixed_content_active: lists the resources',
               'http://cdn.test/a.js' in hits[0]['evidence'].get('related_urls', []))
    quiet('mixed_content_active', [page(f'{BASE}/')], 'mixed_content_active')

    # An http page loading http resources is not mixed content; its scheme is
    # the finding, and this check would only repeat it on every page.
    quiet('mixed_content_active on an http page',
          [page('http://site.test/', {'strict-transport-security': ''},
                insecure_resources=insecure(('script', 'http://cdn.test/a.js')))],
          'mixed_content_active')


def test_mixed_content_passive():
    hits = fires('mixed_content_passive',
                 [page(f'{BASE}/', insecure_resources=insecure(
                     ('image', 'http://cdn.test/a.png'),
                     ('media', 'http://cdn.test/a.mp4')))], 'mixed_content_passive')
    if hits:
        result('mixed_content_passive: severity is warning', hits[0]['type'] == 'warning',
               hits[0]['type'])
    quiet('mixed_content_passive', [page(f'{BASE}/')], 'mixed_content_passive')

    # An image is passive; it must not also be reported as blocked.
    quiet('mixed_content_active for an image only',
          [page(f'{BASE}/', insecure_resources=insecure(('image', 'http://cdn.test/a.png')))],
          'mixed_content_active')


def test_insecure_form_action():
    hits = fires('insecure_form_action',
                 [page(f'{BASE}/login',
                       insecure_form_actions=['http://site.test/login'])],
                 'insecure_form_action')
    if hits:
        result('insecure_form_action: severity is error', hits[0]['type'] == 'error',
               hits[0]['type'])
    quiet('insecure_form_action', [page(f'{BASE}/login')], 'insecure_form_action')


# ---------------------------------------------------------------------------
# extraction from real markup
# ---------------------------------------------------------------------------

def extract(html, base_url=f'{BASE}/listing'):
    out = {}
    extract_page_resources(BeautifulSoup(html, 'html.parser'), base_url, out)
    return out


def test_extraction_finds_every_insecure_subresource():
    found = extract("""
        <html><head>
          <script src="http://cdn.test/a.js"></script>
          <link rel="stylesheet" href="http://cdn.test/a.css">
          <link rel="next" href="/listing?page=2">
        </head><body>
          <img src="http://cdn.test/a.png">
          <picture><source srcset="http://cdn.test/b.webp 1x"></picture>
          <video poster="http://cdn.test/p.jpg"><source src="http://cdn.test/v.mp4"></video>
          <iframe src="http://cdn.test/f.html"></iframe>
          <object data="http://cdn.test/o.swf"></object>
          <form action="http://site.test/subscribe"></form>
        </body></html>
    """)
    kinds = sorted({entry['kind'] for entry in found['insecure_resources']})
    result('extraction: every kind of subresource',
           kinds == ['iframe', 'image', 'media', 'object', 'script', 'stylesheet'], str(kinds))
    result('extraction: the form action',
           found['insecure_form_actions'] == ['http://site.test/subscribe'],
           str(found['insecure_form_actions']))
    result('extraction: rel=next',
           found['pagination'].get('next') == f'{BASE}/listing?page=2',
           str(found['pagination']))


def test_extraction_leaves_secure_and_non_resource_urls_alone():
    found = extract("""
        <html><body>
          <a href="http://other.test/article">an http link to another site</a>
          <a href="http://site.test/page">an http link to this site</a>
          <img src="https://cdn.test/ok.png">
          <script src="//cdn.test/relative.js"></script>
          <img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
          <img srcset="https://cdn.test/one.png 1x, https://cdn.test/two.png 2x">
        </body></html>
    """)
    result('extraction: an http link to another site is not mixed content',
           found['insecure_resources'] == [], str(found['insecure_resources']))
    result('extraction: no form actions', found['insecure_form_actions'] == [])
    result('extraction: no pagination', found['pagination'] == {})


def test_extraction_resolves_relative_urls():
    found = extract('<html><body><img src="../img/a.png">'
                    '<link rel="prev" href="?page=1"></body></html>',
                    base_url='http://site.test/blog/page/2')
    urls = [entry['url'] for entry in found['insecure_resources']]
    result('extraction: a relative src resolves against the page',
           urls == ['http://site.test/blog/img/a.png'], str(urls))
    result('extraction: a relative rel=prev resolves against the page',
           found['pagination'].get('prev') == 'http://site.test/blog/page/2?page=1',
           str(found['pagination']))


# ---------------------------------------------------------------------------
# pagination
# ---------------------------------------------------------------------------

def paginated(url, next_url=None, prev_url=None, **fields):
    links = {}
    if next_url:
        links['next'] = next_url
    if prev_url:
        links['prev'] = prev_url
    return page(url, pagination=links, **fields)


def listing(count=3):
    """A correct three-page set: next and prev both ways, self-canonical."""
    urls = [f'{BASE}/listing'] + [f'{BASE}/listing?page={n}' for n in range(2, count + 1)]
    pages = []
    for index, url in enumerate(urls):
        pages.append(paginated(url,
                               next_url=urls[index + 1] if index + 1 < len(urls) else None,
                               prev_url=urls[index - 1] if index else None))
    return pages


def inbound(pages):
    """One internal link to each page, so nothing reads as orphaned."""
    return [{'source_url': f'{BASE}/', 'target_url': item['url'], 'is_internal': True,
             'target_status': 200} for item in pages]


def test_a_correct_paginated_set_reports_nothing():
    pages = listing()
    issues = run(pages, inbound(pages))
    result('a correct paginated set reports nothing', not issues,
           ', '.join(f"{i['check']} on {i['url']}" for i in issues))


def test_page_one_is_not_missing_a_prev_link():
    pages = listing()
    issues = run(pages, inbound(pages))
    on_page_one = [i for i in issues if i['url'] == f'{BASE}/listing']
    result('page one is not reported for having no rel=prev', not on_page_one,
           str([i['check'] for i in on_page_one]))


def test_pagination_broken_chain():
    pages = listing()
    pages[1]['status_code'] = 404
    fires('pagination_broken_chain', pages, 'pagination_broken_chain', inbound(pages))

    # A next that points nowhere the crawl ever saw is a typo in the markup.
    fires('pagination_broken_chain for a URL that does not exist',
          [paginated(f'{BASE}/listing', next_url=f'{BASE}/listing?page=99')],
          'pagination_broken_chain')

    quiet('pagination_broken_chain', listing(), 'pagination_broken_chain',
          inbound(listing()))

    # A next page the crawl knows about but never fetched was held back by a
    # depth or count limit, not broken.
    quiet('pagination_broken_chain for an uncrawled but linked page',
          [paginated(f'{BASE}/listing', next_url=f'{BASE}/listing?page=2')],
          'pagination_broken_chain',
          [{'source_url': f'{BASE}/', 'target_url': f'{BASE}/listing?page=2',
            'is_internal': True, 'target_status': None}])


def test_pagination_no_return_link():
    pages = listing()
    pages[1]['pagination'].pop('prev')
    hits = fires('pagination_no_return_link', pages, 'pagination_no_return_link',
                 inbound(pages))
    if hits:
        result('pagination_no_return_link: reported against the page that must change',
               hits[0]['url'] == f'{BASE}/listing?page=2', hits[0]['url'])

    pages = listing()
    pages[1]['pagination']['prev'] = f'{BASE}/somewhere-else'
    fires('pagination_no_return_link for a prev that points elsewhere', pages,
          'pagination_no_return_link', inbound(pages))

    quiet('pagination_no_return_link', listing(), 'pagination_no_return_link',
          inbound(listing()))

    # A trailing slash is the same page, not a different one.
    pages = listing()
    pages[1]['pagination']['prev'] = f'{BASE}/listing/'
    quiet('pagination_no_return_link across a trailing slash', pages,
          'pagination_no_return_link', inbound(pages))

    # The next page was never crawled, so it cannot be asked what it says.
    quiet('pagination_no_return_link for an uncrawled next page',
          [paginated(f'{BASE}/listing', next_url=f'{BASE}/listing?page=2')],
          'pagination_no_return_link',
          [{'source_url': f'{BASE}/', 'target_url': f'{BASE}/listing?page=2',
            'is_internal': True, 'target_status': None}])


def test_pagination_canonical_to_page_one():
    pages = listing()
    pages[1]['canonical_url'] = f'{BASE}/listing'
    pages[2]['canonical_url'] = f'{BASE}/listing'
    hits = fires('pagination_canonical_to_page_one', pages,
                 'pagination_canonical_to_page_one', inbound(pages))
    result('pagination_canonical_to_page_one: both later pages',
           len(hits) == 2, f'{len(hits)} finding(s)')

    quiet('pagination_canonical_to_page_one', listing(),
          'pagination_canonical_to_page_one', inbound(listing()))

    # Page one canonicalising to itself is correct, not a finding.
    quiet('pagination_canonical_to_page_one for page one itself', listing(),
          'pagination_canonical_to_page_one', inbound(listing()))

    # Canonicalising somewhere outside the set is a different problem, and
    # technical_checks already owns it.
    pages = listing()
    pages[1]['canonical_url'] = f'{BASE}/everything'
    quiet('pagination_canonical_to_page_one for a canonical outside the set', pages,
          'pagination_canonical_to_page_one', inbound(pages))


def test_pagination_orphaned_page():
    pages = listing()
    links = inbound(pages)[:1]  # only page one is linked
    hits = fires('pagination_orphaned_page', pages, 'pagination_orphaned_page', links)
    result('pagination_orphaned_page: not page one',
           all(hit['url'] != f'{BASE}/listing' for hit in hits),
           str([hit['url'] for hit in hits]))

    quiet('pagination_orphaned_page', listing(), 'pagination_orphaned_page',
          inbound(listing()))

    # With no link records at all the crawl cannot tell what is orphaned, and
    # guessing would report every paginated page on every crawl.
    quiet('pagination_orphaned_page with no links recorded', listing(),
          'pagination_orphaned_page')

    # A page outside any pagination chain is not this check's business.
    plain = [page(f'{BASE}/lonely')]
    quiet('pagination_orphaned_page for a page with no rel=next or rel=prev', plain,
          'pagination_orphaned_page', [{'source_url': f'{BASE}/',
                                        'target_url': f'{BASE}/other', 'is_internal': True}])


def test_a_pagination_cycle_does_not_hang():
    a = paginated(f'{BASE}/a', next_url=f'{BASE}/b', prev_url=f'{BASE}/b')
    b = paginated(f'{BASE}/b', next_url=f'{BASE}/a', prev_url=f'{BASE}/a')
    try:
        issues = run([a, b], inbound([a, b]))
        result('a prev/next cycle terminates', True, f'{len(issues)} issue(s)')
    except Exception as exc:
        result('a prev/next cycle terminates', False, f'{type(exc).__name__}: {exc}')


# ---------------------------------------------------------------------------
# shape of the output
# ---------------------------------------------------------------------------

def test_grouping_above_the_threshold():
    pages = [page(f'{BASE}/p{n}', {'x-content-type-options': ''}) for n in range(9)]
    issues = find(run(pages), 'missing_x_content_type_options')
    result('grouping: nine pages collapse into one issue', len(issues) == 1, str(len(issues)))
    if issues:
        result('grouping: the issue carries the count and the URLs',
               issues[0]['evidence'].get('found') == '9'
               and len(issues[0]['evidence'].get('related_urls', [])) == 9,
               str(issues[0]['evidence'])[:120])

    few = [page(f'{BASE}/p{n}', {'x-content-type-options': ''}) for n in range(3)]
    issues = find(run(few), 'missing_x_content_type_options')
    result('grouping: three pages stay as three issues', len(issues) == 3, str(len(issues)))


def test_every_issue_is_well_formed():
    pages = [page(f'{BASE}/', {'strict-transport-security': 'max-age=1',
                               'content-security-policy': "script-src 'unsafe-inline' "
                                                          "'unsafe-eval'",
                               'x-content-type-options': '', 'referrer-policy': 'unsafe-url',
                               'permissions-policy': '', 'server': 'nginx/1.18.0',
                               'set-cookie': 'a=1'},
                  insecure_resources=insecure(('script', 'http://cdn.test/a.js'),
                                              ('image', 'http://cdn.test/a.png')),
                  insecure_form_actions=['http://site.test/x'],
                  pagination={'next': f'{BASE}/nowhere'})]
    issues = run(pages)
    missing = [f"{i['check']}:{field}" for i in issues
               for field in ('check', 'type', 'category', 'issue', 'details', 'how_to_fix')
               if not i.get(field)]
    result('every issue carries the mandatory fields', not missing, str(missing))
    result('every issue links to its documentation',
           all(i['docs_url'] == f"/checks#{i['check']}" for i in issues))
    result('every detail is one sentence',
           all(i['details'].count('. ') <= 1 and i['details'].endswith('.') for i in issues),
           str([i['details'] for i in issues if not i['details'].endswith('.')]))
    result('every fix is one sentence',
           all(i['how_to_fix'].endswith('.') for i in issues))
    result('an evidence dict everywhere it helps',
           all(isinstance(i['evidence'], dict) for i in issues))


def test_ids_are_stable_and_specific():
    spoilt = page(f'{BASE}/a', {'x-content-type-options': ''})
    first = find(run([spoilt]), 'missing_x_content_type_options')
    second = find(run([page(f'{BASE}/a', {'x-content-type-options': ''})]),
                  'missing_x_content_type_options')
    result('ids: the same defect keeps its id',
           first[0]['id'] == second[0]['id'], f"{first[0]['id']} vs {second[0]['id']}")

    other = find(run([page(f'{BASE}/b', {'x-content-type-options': ''})]),
                 'missing_x_content_type_options')
    result('ids: a different page gets a different id', first[0]['id'] != other[0]['id'])

    cookies = find(run([page(f'{BASE}/a', {'set-cookie': 'a=1; Secure; HttpOnly'})]),
                   'cookie_without_samesite')
    others = find(run([page(f'{BASE}/a', {'set-cookie': 'b=2; Secure; HttpOnly'})]),
                  'cookie_without_samesite')
    result('ids: a different cookie on one page gets a different id',
           cookies[0]['id'] != others[0]['id'])


def test_checks_can_be_switched_off():
    spoilt = [page(f'{BASE}/', {'x-content-type-options': ''})]
    issues = run(spoilt, skip_checks=('missing_x_content_type_options',))
    result('skip_checks silences one check', not find(issues, 'missing_x_content_type_options'))


def test_junk_input_does_not_raise():
    junk = [
        {'url': f'{BASE}/a', 'content_type': 'text/html', 'status_code': 200,
         'response_headers': {'strict-transport-security': None, 'set-cookie': 17,
                              'content-security-policy': ['default-src *']},
         'insecure_resources': 'not a list', 'insecure_form_actions': {'no': 'pe'},
         'pagination': 'nope', 'canonical_url': None},
        {'url': f'{BASE}/b', 'status_code': 200, 'response_headers': {'server': 'x'},
         'insecure_resources': [None, {'kind': 'script'}, 'junk'],
         'pagination': {'next': None, 'prev': ''}},
        {'url': None},
        'not even a dict',
    ]
    try:
        issues = header_issues(junk, [{'source_url': None}, 'junk'], BASE)
        result('junk: odd pages do not raise', True, f'{len(issues)} issue(s)')
    except Exception as exc:
        result('junk: odd pages do not raise', False, f'{type(exc).__name__}: {exc}')


def test_srcset_urls_may_contain_commas():
    """A URL with commas in it is still one URL.

    Splitting a srcset on commas invents broken images. Cloudflare's resizer
    writes /cdn-cgi/image/width=800,quality=85,format=auto/hero.png, and the
    old parser turned that single URL into three fragments - two of which are
    not URLs - so the crawler reported a broken image on a page whose images
    were fine. Found by crawling ten real SEO sites.
    """
    from src.core.header_checks import _srcset_urls

    cf = ('https://example.com/cdn-cgi/image/width=800,quality=85,format=auto/hero.png 800w, '
          'https://example.com/cdn-cgi/image/width=1600,quality=85,format=auto/hero.png 1600w')
    got = _srcset_urls(cf)
    result('srcset: a Cloudflare resizer URL survives as one URL',
           got == ['https://example.com/cdn-cgi/image/width=800,quality=85,format=auto/hero.png',
                   'https://example.com/cdn-cgi/image/width=1600,quality=85,format=auto/hero.png'],
           str(got))
    result('srcset: no fragment is mistaken for a URL',
           not any(u.startswith(('quality=', 'format=')) for u in got), str(got))

    result('srcset: ordinary candidates still split',
           _srcset_urls('a.png 1x, b.png 2x') == ['a.png', 'b.png'])
    result('srcset: a comma with no space after it still separates',
           _srcset_urls('a.png 1x,b.png 2x') == ['a.png', 'b.png'])
    result('srcset: candidates without descriptors still split',
           _srcset_urls('a.png, b.png') == ['a.png', 'b.png'])
    result('srcset: a query string with commas survives',
           _srcset_urls('/img?w=100,h=50 100w') == ['/img?w=100,h=50'],
           str(_srcset_urls('/img?w=100,h=50 100w')))
    result('srcset: a data URI is skipped without taking the rest with it',
           _srcset_urls('data:image/gif;base64,R0lGODlhAQ== 1x, real.png 2x') == ['real.png'],
           str(_srcset_urls('data:image/gif;base64,R0lGODlhAQ== 1x, real.png 2x')))
    result('srcset: empty is empty', _srcset_urls('') == [] and _srcset_urls(None) == [])


TESTS = (
    test_srcset_urls_may_contain_commas,
    test_a_correctly_served_site_reports_nothing,
    test_pages_with_no_recorded_headers_are_not_judged,
    test_error_pages_and_non_html_are_not_judged,
    test_hsts,
    test_hsts_max_age,
    test_csp_presence,
    test_csp_unsafe_inline,
    test_csp_unsafe_eval,
    test_x_content_type_options,
    test_x_frame_options,
    test_referrer_policy,
    test_insecure_referrer_policy,
    test_permissions_policy,
    test_server_version_disclosed,
    test_cookie_without_secure_flag,
    test_cookie_without_httponly,
    test_cookie_without_samesite,
    test_several_cookies_in_one_joined_header,
    test_one_page_reports_each_header_problem_once,
    test_mixed_content_active,
    test_mixed_content_passive,
    test_insecure_form_action,
    test_extraction_finds_every_insecure_subresource,
    test_extraction_leaves_secure_and_non_resource_urls_alone,
    test_extraction_resolves_relative_urls,
    test_a_correct_paginated_set_reports_nothing,
    test_page_one_is_not_missing_a_prev_link,
    test_pagination_broken_chain,
    test_pagination_no_return_link,
    test_pagination_canonical_to_page_one,
    test_pagination_orphaned_page,
    test_a_pagination_cycle_does_not_hang,
    test_grouping_above_the_threshold,
    test_every_issue_is_well_formed,
    test_ids_are_stable_and_specific,
    test_checks_can_be_switched_off,
    test_junk_input_does_not_raise,
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
