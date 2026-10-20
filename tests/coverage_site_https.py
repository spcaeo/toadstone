#!/usr/bin/env python3
"""The same idea as coverage_site.py, over TLS.

Nine checks cannot fire against an http:// origin, and saying so is not the
same as knowing they work. HSTS, the Secure cookie flag, mixed content and
an insecure form action are all questions that only make sense once a page is
served over https, so this serves one — with a certificate generated here and
trusted only by this process, via REQUESTS_CA_BUNDLE.

Nothing is installed into a system trust store and the certificate lives in a
temporary directory that is removed on the way out.

    python tests/coverage_site_https.py
"""

import datetime
import http.server
import os
import ssl
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PORT = int(os.environ.get('COVERAGE_HTTPS_PORT', '8983'))
PLAIN_PORT = PORT + 1          # serves plain HTTP, for a deliberate TLS failure
CRAWL_TIMEOUT = int(os.environ.get('COVERAGE_CRAWL_TIMEOUT', '1800'))
EXPECT_AT_LEAST = int(os.environ.get('COVERAGE_HTTPS_EXPECT', '28'))
BASE = f'https://127.0.0.1:{PORT}'

FILLER = ('Words enough that these pages are not judged thin for reasons that '
          'have nothing to do with what they are testing here. ') * 6


def make_cert(directory):
    """A certificate for 127.0.0.1, trusted by this process alone."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import ipaddress

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, '127.0.0.1')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(hours=2))
            .add_extension(x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
                 x509.DNSName('localhost')]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                           critical=True)
            .sign(key, hashes.SHA256()))

    cert_path = os.path.join(directory, 'cert.pem')
    key_path = os.path.join(directory, 'key.pem')
    with open(cert_path, 'wb') as handle:
        handle.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, 'wb') as handle:
        handle.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
    return cert_path, key_path


def page(body, head=''):
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>A page served over TLS</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="A fixture page served over TLS for coverage.">
{head}</head><body>{body}</body></html>'''.encode('utf-8')


PAGES = {
    '/': page(f'''<h1>Home</h1><p>{FILLER}</p>
        <a href="/mixed/">mixed content</a>
        <a href="/insecure-form/">insecure form</a>
        <a href="/no-hsts/">no hsts</a>
        <a href="http://127.0.0.1:{PLAIN_PORT}/">a plain http link</a>'''),

    # Active mixed content is a script; passive is an image. They are
    # different severities for a good reason, so the fixture has both.
    '/mixed/': page(
        f'''<h1>Mixed content</h1><p>{FILLER}</p>
        <img src="http://127.0.0.1:{PLAIN_PORT}/pixel.png" alt="passive">''',
        head=f'<script src="http://127.0.0.1:{PLAIN_PORT}/app.js"></script>'),

    '/insecure-form/': page(
        f'''<h1>Insecure form</h1><p>{FILLER}</p>
        <form action="http://127.0.0.1:{PLAIN_PORT}/submit" method="post">
          <input name="email"><button>Send</button></form>'''),

    '/no-hsts/': page(f'<h1>No HSTS on this one</h1><p>{FILLER}</p>'),
}

SITEMAP = f'''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{BASE}/</loc></url>
  <url><loc>{BASE}/mixed/</loc></url>
  <url><loc>http://127.0.0.1:{PLAIN_PORT}/also-here/</loc></url>
</urlset>'''.encode('utf-8')

ROBOTS = f'User-agent: *\nSitemap: {BASE}/sitemap.xml\n'.encode()

# max-age well under the usual six-month recommendation, and a cookie with
# neither Secure nor SameSite.
TLS_HEADERS = {
    'Strict-Transport-Security': 'max-age=600',
    'Set-Cookie': 'sid=abc; Path=/; HttpOnly',
}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _send(self, code, body, ctype='text/html; charset=utf-8', extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        headers = dict(TLS_HEADERS)
        if self.path.startswith('/no-hsts'):
            headers.pop('Strict-Transport-Security', None)
        for key, value in {**headers, **(extra or {})}.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/robots.txt':
            return self._send(200, ROBOTS, 'text/plain')
        if path == '/sitemap.xml':
            return self._send(200, SITEMAP, 'application/xml')
        if path in PAGES:
            return self._send(200, PAGES[path])
        return self._send(404, page('<h1>Not found</h1>'))

    def log_message(self, *args):
        pass


class PlainHandler(Handler):
    """Plain HTTP on the neighbouring port, for the mixed-content targets."""

    def _send(self, code, body, ctype='text/html; charset=utf-8', extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_GET(self):
        return self._send(200, page('<h1>Plain</h1><p>Served without TLS.</p>'))


def run():
    log = os.environ.get('TOADSTONE_SLUG_LOG')
    if not log:
        print('Set TOADSTONE_SLUG_LOG to a file path first.')
        return 2
    open(log, 'w').close()

    workdir = tempfile.mkdtemp(prefix='toadstone-tls-')
    cert_path, key_path = make_cert(workdir)
    # Trusted for this process only; nothing touches a system trust store.
    os.environ['REQUESTS_CA_BUNDLE'] = cert_path

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)

    http.server.ThreadingHTTPServer.allow_reuse_address = True
    secure = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    secure.socket = context.wrap_socket(secure.socket, server_side=True)
    plain = http.server.ThreadingHTTPServer(('127.0.0.1', PLAIN_PORT), PlainHandler)
    for server in (secure, plain):
        threading.Thread(target=server.serve_forever, daemon=True).start()

    from src.crawler import WebCrawler
    crawler = WebCrawler()
    crawler.config.update({
        'max_urls': 30, 'max_depth': 6, 'delay': 0, 'crawl_images': True,
        'audit_sitemaps': True, 'header_checks': True, 'analyse_link_graph': True,
        'enable_javascript': False, 'audit_cms': False, 'amp_checks': False,
        'accessibility_checks': False, 'rendered_checks': False,
        'crawl_external_links': True,
    })
    print(f'crawling {BASE} ...')
    crawler.start_crawl(BASE + '/')
    # Wait for the crawl, rather than ninety seconds and then whatever has
    # happened by then. The sibling harness had the same cap and reported a
    # quarter of its checks as a pass when a crawl outgrew it: the servers
    # below were shut down mid-crawl and every post-fetch phase was lost.
    deadline = time.time() + CRAWL_TIMEOUT
    while time.time() < deadline:
        if not crawler.is_running and crawler.crawl_results:
            break
        time.sleep(0.5)
    else:
        print(f'\nWARNING: the crawl was still running after {CRAWL_TIMEOUT}s. '
              f'Everything below is partial.')
    for server in (secure, plain):
        server.shutdown()

    fired = sorted({line.strip() for line in open(log) if line.strip()})
    print(f'\npages crawled : {len(crawler.crawl_results)}')
    print(f'checks fired  : {len(fired)}\n')
    for slug in fired:
        print(' ', slug)

    import shutil
    shutil.rmtree(workdir, ignore_errors=True)
    os.environ.pop('REQUESTS_CA_BUNDLE', None)

    # A floor, so a run that loses most of what it measures cannot pass.
    if len(fired) < EXPECT_AT_LEAST:
        print(f'\nFAIL: expected at least {EXPECT_AT_LEAST} distinct checks, '
              f'got {len(fired)}')
        return 1
    print('\nOK')
    return 0


if __name__ == '__main__':
    sys.exit(run())
