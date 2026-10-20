#!/usr/bin/env python3
"""The three gaps the capability matrix still listed as open.

Signing in to the site being crawled, structured data that is not JSON-LD,
and PDFs. No network: the auth tests drive a fake session, the markup tests
use real-world shapes, and the PDF tests build actual PDF bytes.

Each of these existed as a row reading "no" in a comparison table, which is
a bad reason to build something and a good reason to test it properly: a
feature added to close a gap on a chart is exactly the kind that gets a
cheerful implementation and no adversarial reading.
"""

import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core import microdata, pdf_checks, site_auth  # noqa: E402
from src.core.site_auth import AuthError  # noqa: E402

PASSED, FAILED = [], []


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def raises(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except AuthError as exc:
        return str(exc)
    return None


class FakeCookies(dict):
    def set(self, name, value):
        self[name] = value


class FakeResponse:
    def __init__(self, status=200, text=''):
        self.status_code = status
        self.text = text


class FakeSession:
    """Just enough of requests.Session to drive the auth code."""

    def __init__(self, post_response=None, get_response=None, sets_cookie=True):
        self.auth = None
        self.cookies = FakeCookies()
        self.headers = {}
        self.posted = []
        self._post_response = post_response or FakeResponse()
        self._get_response = get_response or FakeResponse(200, '<h1>Dashboard</h1>')
        self._sets_cookie = sets_cookie

    def post(self, url, data=None, timeout=None, allow_redirects=None):
        self.posted.append((url, data))
        if self._sets_cookie:
            self.cookies.set('session', 'abc')
        return self._post_response

    def get(self, url, timeout=None, allow_redirects=None):
        return self._get_response


# ---------------------------------------------------------------------------
# signing in to the site being crawled
# ---------------------------------------------------------------------------

def test_http_auth_is_set_on_the_session():
    session = FakeSession()
    described = site_auth.apply(session, {
        'auth_mode': 'basic', 'auth_username': 'bob', 'auth_password': 'hunter2'})
    result('basic auth is attached to the session', session.auth is not None)
    result('and described without the password',
           'bob' in described and 'hunter2' not in described, described)

    session = FakeSession()
    site_auth.apply(session, {'auth_mode': 'digest', 'auth_username': 'bob',
                              'auth_password': 'hunter2'})
    result('digest auth is attached too', session.auth is not None)


def test_incomplete_credentials_say_what_is_missing():
    for config in ({'auth_mode': 'basic', 'auth_username': 'bob'},
                   {'auth_mode': 'basic', 'auth_password': 'x'}):
        message = raises(site_auth.apply, FakeSession(), config)
        if not message or 'username and a password' not in message:
            result('incomplete HTTP credentials are refused', False, repr(message))
            return
    result('incomplete HTTP credentials are refused with a useful message', True)

    message = raises(site_auth.apply, FakeSession(), {'auth_mode': 'wizardry'})
    result('an unknown mode names the modes that exist',
           message is not None and 'basic' in message, repr(message))


def test_a_form_login_posts_and_keeps_cookies():
    session = FakeSession()
    described = site_auth.apply(session, {
        'auth_mode': 'form', 'auth_login_url': 'https://site.test/login',
        'auth_username': 'bob', 'auth_password': 'hunter2',
        'auth_username_field': 'log', 'auth_password_field': 'pwd',
        'auth_extra_fields': 'rememberme=forever&redirect_to=/'})
    url, payload = session.posted[0]
    result('the form is posted to the configured URL',
           url == 'https://site.test/login')
    result('the configured field names are used, not guessed',
           payload.get('log') == 'bob' and payload.get('pwd') == 'hunter2',
           str(sorted(payload)))
    result('extra fields are sent too',
           payload.get('rememberme') == 'forever'
           and payload.get('redirect_to') == '/')
    result('the cookies it returns are kept',
           'session' in session.cookies, str(dict(session.cookies)))
    result('and the password is not in the description',
           'hunter2' not in described, described)


def test_a_login_that_sets_no_cookie_is_a_failure():
    """The nastiest failure: HTTP 200, nothing kept, every page is the login."""
    session = FakeSession(sets_cookie=False)
    message = raises(site_auth.apply, session, {
        'auth_mode': 'form', 'auth_login_url': 'https://site.test/login',
        'auth_username': 'bob', 'auth_password': 'x'})
    result('a 200 that sets no cookie is refused, not accepted',
           message is not None and 'no cookies' in message, repr(message))


def test_rejected_credentials_are_reported_as_such():
    session = FakeSession(post_response=FakeResponse(403))
    message = raises(site_auth.apply, session, {
        'auth_mode': 'form', 'auth_login_url': 'https://site.test/login',
        'auth_username': 'bob', 'auth_password': 'x'})
    result('a 403 from the login form says the credentials were rejected',
           message is not None and 'rejected' in message, repr(message))

    session = FakeSession(post_response=FakeResponse(503))
    message = raises(site_auth.apply, session, {
        'auth_mode': 'form', 'auth_login_url': 'https://site.test/login',
        'auth_username': 'bob', 'auth_password': 'x'})
    result('a 5xx blames the site, not the credentials',
           message is not None and 'may be down' in message, repr(message))


def test_pasted_cookies_are_parsed_the_way_a_browser_shows_them():
    session = FakeSession()
    site_auth.apply(session, {
        'auth_mode': 'cookies',
        'auth_cookies': 'sessionid=abc123; csrftoken=xyz;  stray  ; k=v=v'})
    result('name=value pairs are read', session.cookies.get('sessionid') == 'abc123'
           and session.cookies.get('csrftoken') == 'xyz')
    result('a value containing = survives', session.cookies.get('k') == 'v=v')
    result('a fragment with no = is skipped', 'stray' not in session.cookies)

    message = raises(site_auth.apply, FakeSession(),
                     {'auth_mode': 'cookies', 'auth_cookies': 'nonsense'})
    result('cookies that parse to nothing are refused',
           message is not None, repr(message))


def test_a_login_page_behind_the_login_is_caught():
    """Without this the crawl succeeds and reports the login page 400 times."""
    login_html = '<form><input type="password" name="pwd"></form>'
    session = FakeSession(get_response=FakeResponse(200, login_html))
    message = raises(site_auth.verify, session, 'https://site.test/',
                     {'auth_mode': 'form'})
    result('a login page served after signing in is caught',
           message is not None and 'still looks like a login' in message,
           repr(message))

    session = FakeSession(get_response=FakeResponse(200, '<h1>Your account</h1>'))
    result('a real page after signing in passes',
           site_auth.verify(session, 'https://site.test/', {'auth_mode': 'form'}))


def test_verification_can_be_told_what_to_look_for():
    session = FakeSession(get_response=FakeResponse(200, '<h1>Welcome back</h1>'))
    result('the expected text passing is a pass',
           site_auth.verify(session, 'https://site.test/',
                            {'auth_mode': 'form', 'auth_expect_text': 'Welcome back'}))
    message = raises(site_auth.verify, session, 'https://site.test/',
                     {'auth_mode': 'form', 'auth_expect_text': 'Sign out'})
    result('the expected text missing is a failure',
           message is not None and 'Sign out' in message, repr(message))

    session = FakeSession(get_response=FakeResponse(401, ''))
    message = raises(site_auth.verify, session, 'https://site.test/',
                     {'auth_mode': 'basic'})
    result('still being refused after signing in is caught',
           message is not None and '401' in message, repr(message))


def test_the_password_never_reaches_a_stored_config():
    """A crawl config is written to the database and travels with exports."""
    config = {
        'auth_mode': 'form', 'auth_username': 'bob',
        'auth_password': 'hunter2', 'auth_cookies': 'sessionid=abc',
        'google_refresh_token': 'r-1', 'max_depth': 5,
        'nested': {'auth_password': 'also-secret', 'keep': 'me'},
    }
    safe = site_auth.redacted(config)
    blob = repr(safe)
    for secret in ('hunter2', 'sessionid=abc', 'r-1', 'also-secret'):
        if secret in blob:
            result('no credential survives into a stored config', False,
                   f'{secret!r} leaked')
            return
    result('no credential survives into a stored config', True)
    result('but ordinary settings are untouched',
           safe['max_depth'] == 5 and safe['auth_username'] == 'bob'
           and safe['nested']['keep'] == 'me', str(safe))
    result('and a set password still reads as set, so the UI can say so',
           safe['auth_password'] == site_auth.REDACTED)
    result('the original is not mutated', config['auth_password'] == 'hunter2')


def test_no_auth_configured_does_nothing_at_all():
    session = FakeSession()
    result('no auth mode leaves the session alone',
           site_auth.apply(session, {}) == '' and session.auth is None)
    result('and verify is a no-op', site_auth.verify(session, 'x', {}) == '')
    result('configured() agrees', not site_auth.configured({'auth_mode': 'none'})
           and site_auth.configured({'auth_mode': 'form'}))


# ---------------------------------------------------------------------------
# structured data that is not JSON-LD
# ---------------------------------------------------------------------------

PRODUCT_MICRODATA = '''
<div itemscope itemtype="http://schema.org/Product">
  <h1 itemprop="name">Blue Widget</h1>
  <img itemprop="image" src="/w.png">
  <meta itemprop="sku" content="BW-1">
  <div itemprop="offers" itemscope itemtype="http://schema.org/Offer">
    <meta itemprop="price" content="19.99">
    <span itemprop="priceCurrency">GBP</span>
  </div>
</div>'''


def test_microdata_is_read_at_all():
    nodes = microdata.microdata_nodes(PRODUCT_MICRODATA)
    types = [n['@type'] for n in nodes]
    result('both items are found, not just the outer one',
           types == ['Product', 'Offer'], str(types))
    product = nodes[0]
    result('text properties are read', product.get('name') == 'Blue Widget')
    result('an image takes its src, not its tag text',
           product.get('image') == '/w.png', str(product.get('image')))
    result('a meta takes its content attribute',
           product.get('sku') == 'BW-1', str(product.get('sku')))


def test_a_nested_item_does_not_leak_into_its_parent():
    """A Product silently acquiring its Offer's price is the classic bug, and
    the first version of this had it."""
    product = microdata.microdata_nodes(PRODUCT_MICRODATA)[0]
    result('the parent does not absorb the nested item\'s properties',
           'price' not in product and 'priceCurrency' not in product,
           str(sorted(product)))
    offer = microdata.microdata_nodes(PRODUCT_MICRODATA)[1]
    result('and the nested item keeps them',
           offer.get('price') == '19.99' and offer.get('priceCurrency') == 'GBP',
           str(offer))


def test_bare_attributes_are_not_invisible():
    """`itemscope` carries no value. Requiring name=value found nothing."""
    nodes = microdata.microdata_nodes(
        '<div itemscope itemtype="https://schema.org/Thing">'
        '<span itemprop="name">x</span></div>')
    result('an item declared with a valueless itemscope is found',
           len(nodes) == 1 and nodes[0]['@type'] == 'Thing', str(nodes))


def test_type_urls_become_plain_type_names():
    for markup, expected in (
            ('<div itemscope itemtype="http://schema.org/Recipe"></div>', 'Recipe'),
            ('<div itemscope itemtype="https://schema.org/Recipe/"></div>', 'Recipe'),
            ('<div itemscope itemtype="Recipe"></div>', 'Recipe')):
        nodes = microdata.microdata_nodes(markup)
        if not nodes or nodes[0]['@type'] != expected:
            result('a type URL becomes the bare type name', False,
                   f'{markup} gave {nodes}')
            return
    result('a type URL becomes the bare type name, in every form', True)


def test_rdfa_is_read():
    nodes = microdata.rdfa_nodes(
        '<div vocab="http://schema.org/" typeof="Recipe">'
        '<h2 property="name">Soup</h2>'
        '<time property="cookTime" datetime="PT30M">30 min</time>'
        '<span property="recipeIngredient">Water</span>'
        '<span property="recipeIngredient">Salt</span></div>')
    result('an RDFa item is found', len(nodes) == 1 and nodes[0]['@type'] == 'Recipe',
           str(nodes))
    node = nodes[0]
    result('a time takes its datetime attribute', node.get('cookTime') == 'PT30M')
    result('a repeated property becomes a list',
           node.get('recipeIngredient') == ['Water', 'Salt'],
           str(node.get('recipeIngredient')))


def test_the_three_syntaxes_come_back_together():
    page = {
        'json_ld': [{'@type': 'Article', 'headline': 'A'}],
        'microdata': [{'@type': 'Product', 'name': 'B'}],
        'rdfa': [{'@type': 'Recipe', 'name': 'C'}],
    }
    types = sorted(n['@type'] for n in microdata.all_nodes(page))
    result('all three syntaxes are offered to a check that treats them alike',
           types == ['Article', 'Product', 'Recipe'], str(types))
    result('a page with none of them yields nothing rather than raising',
           microdata.all_nodes({}) == [])


def test_markup_with_no_structured_data_finds_none():
    """A parser that invents nodes is worse than one that finds none."""
    found = microdata.extract('<html><body><p>Just a page.</p>'
                              '<div class="itemprop">not markup</div></body></html>')
    result('plain markup produces no nodes',
           found['microdata'] == [] and found['rdfa'] == [], str(found))


def test_broken_markup_does_not_raise():
    for markup in ('<div itemscope itemtype=', '<div itemscope><span itemprop>',
                   '', None, '<div itemscope itemtype="X"><span itemprop="a">'):
        try:
            microdata.extract(markup or '')
        except Exception as exc:
            result('malformed markup does not raise', False,
                   f'{markup!r}: {type(exc).__name__}')
            return
    result('malformed markup does not raise', True)


# ---------------------------------------------------------------------------
# PDFs
# ---------------------------------------------------------------------------

def build_pdf(title=None, pages=1, text=True, encrypted=False,
              truncated=False, padding=0):
    """A small but genuinely-shaped PDF, so the reader is read, not mocked."""
    body = b'%PDF-1.4\n'
    if title is not None:
        body += b'1 0 obj\n<< /Title (' + title.encode('latin-1') + b') >>\nendobj\n'
    if encrypted:
        body += b'trailer\n<< /Encrypt 9 0 R >>\n'
    for n in range(pages):
        body += b'%d 0 obj\n<< /Type /Page /Parent 1 0 R >>\nendobj\n' % (n + 10)
    content = b'BT /F1 12 Tf (Hello) Tj ET' if text else b'q 100 0 0 100 0 0 cm /Im1 Do Q'
    compressed = zlib.compress(content)
    body += b'5 0 obj\n<< /Length %d /Filter /FlateDecode >>\nstream\n' % len(compressed)
    body += compressed + b'\nendstream\nendobj\n'
    if padding:
        body += b'%' + b'p' * padding + b'\n'
    if not truncated:
        body += b'trailer\n<< /Root 1 0 R >>\n%%EOF\n'
    return body


def test_a_real_pdf_is_read():
    report = pdf_checks.inspect(build_pdf(title='Annual Report 2026', pages=3))
    result('it is recognised as a PDF', report['is_pdf'])
    result('the document title is read',
           report['title'] == 'Annual Report 2026', repr(report['title']))
    result('the page count is read', report['pages'] == 3, str(report['pages']))
    result('a text layer is detected', report['has_text_layer'])
    result('and it is not reported as damaged', not report['damaged'])


def test_a_scan_is_told_apart_from_a_document():
    """The finding that matters most: it looks perfect to a person."""
    scan = pdf_checks.inspect(build_pdf(title='Scanned', pages=2, text=False))
    result('a PDF with no text layer is identified', not scan['has_text_layer'])
    issues = pdf_checks.pdf_issues([{'url': 'https://site.test/s.pdf',
                                     'status_code': 200, 'pdf': scan}])
    slugs = {i['check'] for i in issues}
    result('and it is reported', 'pdf_no_text_layer' in slugs, str(slugs))
    finding = next(i for i in issues if i['check'] == 'pdf_no_text_layer')
    result('as an error, because none of its words can be indexed',
           finding['type'] == 'error', finding['type'])


def test_a_missing_title_is_reported():
    report = pdf_checks.inspect(build_pdf(title=None))
    issues = pdf_checks.pdf_issues([{'url': 'https://site.test/a.pdf',
                                     'status_code': 200, 'pdf': report}])
    result('a PDF with no title is reported',
           'pdf_missing_title' in {i['check'] for i in issues})
    titled = pdf_checks.inspect(build_pdf(title='Has One'))
    issues = pdf_checks.pdf_issues([{'url': 'https://site.test/b.pdf',
                                     'status_code': 200, 'pdf': titled}])
    result('and one with a title is not',
           'pdf_missing_title' not in {i['check'] for i in issues})


def test_encryption_is_reported_without_guessing_at_the_contents():
    report = pdf_checks.inspect(build_pdf(title='Secret', encrypted=True, text=False))
    result('an encrypted PDF is identified', report['encrypted'])
    issues = pdf_checks.pdf_issues([{'url': 'https://site.test/e.pdf',
                                     'status_code': 200, 'pdf': report}])
    slugs = {i['check'] for i in issues}
    result('it is reported as protected', 'pdf_encrypted' in slugs, str(slugs))
    result('but no claim is made about its text layer, which cannot be read',
           'pdf_no_text_layer' not in slugs, str(slugs))


def test_a_truncated_file_is_reported_once_and_stops_there():
    report = pdf_checks.inspect(build_pdf(title='Cut Off', truncated=True))
    issues = pdf_checks.pdf_issues([{'url': 'https://site.test/t.pdf',
                                     'status_code': 200, 'pdf': report}])
    slugs = [i['check'] for i in issues]
    result('a truncated PDF is reported', slugs == ['pdf_damaged'], str(slugs))


def test_a_heavy_pdf_is_reported_with_its_size():
    heavy = build_pdf(title='Big', padding=pdf_checks.HEAVY_BYTES + 1000)
    report = pdf_checks.inspect(heavy)
    issues = pdf_checks.pdf_issues([{'url': 'https://site.test/h.pdf',
                                     'status_code': 200, 'pdf': report}])
    finding = next((i for i in issues if i['check'] == 'pdf_too_heavy'), None)
    result('an oversized PDF is reported', finding is not None)
    # offending_value feeds the issue id rather than staying a field, so the
    # size a person reads comes from the evidence.
    size_text = (finding or {}).get('evidence', {}).get('found', '')
    result('and the finding names the actual size',
           'MB' in size_text, repr(size_text))


def test_things_that_are_not_pdfs_are_left_alone():
    report = pdf_checks.inspect(b'<html><body>not a pdf</body></html>', 'text/html')
    result('HTML is not treated as a PDF', not report['is_pdf'])
    result('and produces no findings',
           pdf_checks.pdf_issues([{'url': 'x', 'pdf': report}]) == [])
    result('a page with no pdf report at all is skipped',
           pdf_checks.pdf_issues([{'url': 'x'}]) == [])

    # served as a PDF but is not one
    report = pdf_checks.inspect(b'<html>oops</html>', 'application/pdf')
    result('a file served as a PDF that is not one is reported as damaged',
           report['is_pdf'] and report['damaged'], str(report))


def test_a_broken_pdf_does_not_raise():
    for data in (b'', b'%PDF-', b'%PDF-1.4\nstream\n', b'%PDF-1.4\x00\xff\xfe',
                 b'%PDF-1.4\n/Title <zzz>\n'):
        try:
            pdf_checks.inspect(data, 'application/pdf')
        except Exception as exc:
            result('a malformed PDF does not raise', False,
                   f'{data[:20]!r}: {type(exc).__name__}: {exc}')
            return
    result('a malformed PDF does not raise', True)


def test_a_404_pdf_is_not_audited_for_its_contents():
    report = pdf_checks.inspect(b'')
    report['is_pdf'] = True
    issues = pdf_checks.pdf_issues([{'url': 'https://site.test/gone.pdf',
                                     'status_code': 404, 'pdf': report}])
    result('a PDF that returned 404 produces no content findings',
           issues == [], str(issues))



def test_a_non_html_url_is_not_sent_to_the_browser():
    """With JavaScript rendering on, every PDF was reported as unreachable.

    Chromium cannot render a PDF, a zip or a font. Sending one there produced
    a row with status 0, no content type and no size - a perfectly healthy
    file recorded as a dead URL, and before the no-response fix, ten invented
    content findings on top of that. Every PDF check was dead for anyone
    crawling with rendering enabled, which is a common setting.

    The renderer has always known which URLs it should not take. Nothing
    asked it.
    """
    from src.core.js_renderer import JavaScriptRenderer

    renderer = JavaScriptRenderer.__new__(JavaScriptRenderer)
    for url in ('https://site.test/a.pdf', 'https://site.test/x.zip',
                'https://site.test/f.woff2', 'https://site.test/i.png',
                'https://site.test/s.css', 'https://site.test/d.docx'):
        if renderer.should_use_javascript(url):
            result('non-HTML URLs are kept away from the browser', False, url)
            return
    result('non-HTML URLs are kept away from the browser', True)

    for url in ('https://site.test/', 'https://site.test/page',
                'https://site.test/a.html', 'https://site.test/p?x=1'):
        if not renderer.should_use_javascript(url):
            result('ordinary pages still go to the browser', False, url)
            return
    result('ordinary pages still go to the browser', True)


TESTS = (
    test_a_non_html_url_is_not_sent_to_the_browser,
    test_http_auth_is_set_on_the_session,
    test_incomplete_credentials_say_what_is_missing,
    test_a_form_login_posts_and_keeps_cookies,
    test_a_login_that_sets_no_cookie_is_a_failure,
    test_rejected_credentials_are_reported_as_such,
    test_pasted_cookies_are_parsed_the_way_a_browser_shows_them,
    test_a_login_page_behind_the_login_is_caught,
    test_verification_can_be_told_what_to_look_for,
    test_the_password_never_reaches_a_stored_config,
    test_no_auth_configured_does_nothing_at_all,
    test_microdata_is_read_at_all,
    test_a_nested_item_does_not_leak_into_its_parent,
    test_bare_attributes_are_not_invisible,
    test_type_urls_become_plain_type_names,
    test_rdfa_is_read,
    test_the_three_syntaxes_come_back_together,
    test_markup_with_no_structured_data_finds_none,
    test_broken_markup_does_not_raise,
    test_a_real_pdf_is_read,
    test_a_scan_is_told_apart_from_a_document,
    test_a_missing_title_is_reported,
    test_encryption_is_reported_without_guessing_at_the_contents,
    test_a_truncated_file_is_reported_once_and_stops_there,
    test_a_heavy_pdf_is_reported_with_its_size,
    test_things_that_are_not_pdfs_are_left_alone,
    test_a_broken_pdf_does_not_raise,
    test_a_404_pdf_is_not_audited_for_its_contents,
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
