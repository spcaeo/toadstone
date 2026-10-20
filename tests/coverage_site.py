#!/usr/bin/env python3
"""A deliberately broken site, crawled for real, to see which checks fire.

Everything else in `tests/` asks "does this check work?" by calling it with a
hand-made page. That answers whether the logic is right and says nothing
about whether the check ever runs — a check can be correct, tested, and
unreachable because the crawler never passes it the field it reads.

So this serves a small site built to be wrong in as many ways as possible,
crawls it with the real crawler, and records every slug that fires. It is the
difference between "148 checks have never been seen live" and knowing which
of them cannot fire versus which simply had nothing to fire on.

    python tests/coverage_site.py            # crawl and report
    python tests/coverage_site.py --expect 40  # fail under that many slugs

The report is the point, not a pass/fail: it prints what fired, so a check
that stops firing after a refactor shows up as a number going down.
"""

import argparse
import http.server
import os
import socketserver
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT = int(os.environ.get('COVERAGE_SITE_PORT', '8971'))
CRAWL_TIMEOUT = int(os.environ.get('COVERAGE_CRAWL_TIMEOUT', '1800'))
BASE = f'http://127.0.0.1:{PORT}'


def page(body, head='', title='A page on the coverage fixture site'):
    return f'''<!doctype html><html lang="en"><head>
<meta charset="utf-8"><title>{title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="A fixture page.">
{head}</head><body>{body}</body></html>'''.encode('utf-8')


FILLER = ('This paragraph exists so the page is not judged thin for reasons '
          'unrelated to what it is testing, and it repeats itself a little. ') * 6

# --- the pages, each wrong in a specific way -------------------------------

PAGES = {
    '/': page(f'''<h1>Home</h1><p>{FILLER}</p>
      <a href="/canonical-elsewhere/">canonical elsewhere</a>
      <a href="/canonical-relative/">relative canonical</a>
      <a href="/canonical-to-redirect/">canonical to a redirect</a>
      <a href="/two-h1/">two h1</a>
      <a href="/empty-heading/">empty heading</a>
      <a href="/skipped-heading/">skipped heading</a>
      <a href="/bad-images/">bad images</a>
      <a href="/broken-link/">broken link</a>
      <a href="/redirect-a/">redirect chain</a>
      <a href="/hreflang/">hreflang</a>
      <a href="/mixed-links/">mixed links</a>
      <a href="/tracked/?utm_source=newsletter">tracked link</a>
      <a href="/big/">big page</a>
      <a href="/bad-jsonld/">bad json-ld</a>
      <a href="/dates/">dates</a>
      <a href="/nofollow/" rel="nofollow">internal nofollow</a>
      <a href="/deep/1/2/3/4/5/">deep page</a>
      <a href="/noindex-in-sitemap/">noindex but in sitemap</a>
      <a href="http://127.0.0.1:{PORT}/mixed-protocol/">http link</a>'''),

    '/canonical-elsewhere/': page(
        f'<h1>Canonical elsewhere</h1><p>{FILLER}</p>',
        head=f'<link rel="canonical" href="{BASE}/somewhere-else/">'),

    '/canonical-relative/': page(
        f'<h1>Relative canonical</h1><p>{FILLER}</p>',
        head='<link rel="canonical" href="/canonical-relative/">'),

    '/canonical-to-redirect/': page(
        f'<h1>Canonical to a redirect</h1><p>{FILLER}</p>',
        head=f'<link rel="canonical" href="{BASE}/redirect-a/">'),

    '/two-h1/': page(f'<h1>First</h1><h1>Second</h1><p>{FILLER}</p>'),

    '/empty-heading/': page(f'<h1>Empty heading below</h1><h2>   </h2><p>{FILLER}</p>'),

    '/skipped-heading/': page(f'<h1>One</h1><h4>Jumped to four</h4><p>{FILLER}</p>'),

    '/bad-images/': page(
        f'''<h1>Images</h1><p>{FILLER}</p>
        <img src="/missing.png">
        <img src="/missing2.png" alt="">
        <img src="/server-error.png" alt="server error">'''),

    '/broken-link/': page(
        f'<h1>Broken link</h1><p>{FILLER}</p><a href="/nowhere-at-all/">gone</a>'),

    '/hreflang/': page(
        f'<h1>hreflang</h1><p>{FILLER}</p>',
        head=f'''<link rel="alternate" hreflang="en-GB" href="{BASE}/hreflang/">
        <link rel="alternate" hreflang="not-a-locale" href="{BASE}/hreflang-xx/">
        <link rel="alternate" hreflang="fr" href="{BASE}/hreflang-fr/">'''),

    '/hreflang-fr/': page(f'<h1>Sans retour</h1><p>{FILLER}</p>'),

    '/mixed-links/': page(
        f'''<h1>Mixed</h1><p>{FILLER}</p>
        <a href="http://localhost:{PORT}/other-host/">another host</a>'''),

    '/tracked/': page(f'<h1>Tracked</h1><p>{FILLER}</p>'),

    '/big/': page('<h1>Big</h1><p>' + ('padding ' * 60000) + '</p>'),

    '/bad-jsonld/': page(
        f'<h1>Bad JSON-LD</h1><p>{FILLER}</p>',
        head='<script type="application/ld+json">{"@type": "Article",}</script>'),

    '/dates/': page(
        f'<h1>Dates</h1><p>{FILLER}</p>',
        head='''<script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Article","headline":"D",
         "datePublished":"2031-01-01","dateModified":"2019-01-01"}</script>'''),

    '/nofollow/': page(f'<h1>Nofollow target</h1><p>{FILLER}</p>'),
    '/deep/1/2/3/4/5/': page(f'<h1>Deep</h1><p>{FILLER}</p>'),
    '/noindex-in-sitemap/': page(f'<h1>Noindex</h1><p>{FILLER}</p>',
                                 head='<meta name="robots" content="noindex">'),
    '/mixed-protocol/': page(f'<h1>Mixed protocol</h1><p>{FILLER}</p>'),
    '/redirect-c/': page(f'<h1>End of the chain</h1><p>{FILLER}</p>'),
    '/sitemap-only/': page(f'<h1>Only in the sitemap</h1><p>{FILLER}</p>'),
}

# AMP, which needs nothing but AMP markup — no credential, no special host.
PAGES['/amp/'] = (
    '<!doctype html><html amp lang="en"><head><title>An AMP page</title>'
    '<meta name="description" content="An AMP page that breaks the rules.">'
    '<style>body{color:red}</style><style>h1{color:blue}</style>'
    '<link rel="stylesheet" href="https://cdn.test/site.css">'
    '<script>var x = 1;</script></head>'
    f'<body><h1>AMP</h1><p style="color:green">{FILLER}</p>'
    '<img src="/a.png"></body></html>'
).encode('utf-8')

# A second AMP page rather than more faults on the first. Giving /amp/ two
# <style amp-custom> blocks would fire amp_multiple_custom_styles, but /amp/
# currently has no amp-custom block at all, and that absence is what some of
# the eight checks it already proves are reading. This one carries both the
# duplicate blocks and a canonical that never links back.
PAGES['/amp-two/'] = (
    '<!doctype html><html amp lang="en"><head><title>A second AMP page</title>'
    '<meta name="description" content="An AMP page wired up one way only.">'
    f'<link rel="canonical" href="{BASE}/not-actually-amp/">'
    '<style amp-custom>body{color:red}</style>'
    '<style amp-custom>h1{color:blue}</style></head>'
    f'<body><h1>AMP two</h1><p>{FILLER}</p></body></html>'
).encode('utf-8')

# A section hub and a page under it that never links back up to it. The hub
# check wants two path segments below any locale, a parent that was crawled,
# and no link to that parent from the child.
PAGES['/tools/'] = page(f'<h1>Tools</h1><p>{FILLER}</p>'
                        '<a href="/tools/hammer/">hammer</a>',
                        title='Tools')
PAGES['/tools/hammer/'] = page(f'<h1>Hammer</h1><p>{FILLER}</p>'
                               '<a href="/">home</a>', title='Hammer')

# A page that spends its own main keyword on an anchor pointing elsewhere.
PAGES['/widget-repair/'] = page(
    f'<h1>Widget Repair</h1><p>{FILLER}</p>'
    '<a href="/tools/">widget repair</a>',
    title='Widget Repair')

# Two pagination sets, named away from the /list/1/ set already here. The
# first canonicalises to its own page one; the second is reachable only by
# following rel="next", which is what the orphan check is about.
PAGES['/series/'] = page(f'<h1>Series</h1><p>{FILLER}</p><a href="/series-2/">page 2</a>',
                         head=f'<link rel="next" href="{BASE}/series-2/">', title='Series')
PAGES['/series-2/'] = page(f'<h1>Series page 2</h1><p>{FILLER}</p>',
                           head=f'<link rel="prev" href="{BASE}/series/">'
                                f'<link rel="canonical" href="{BASE}/series/">',
                           title='Series page 2')
PAGES['/batch/'] = page(f'<h1>Batch</h1><p>{FILLER}</p>',
                        head=f'<link rel="next" href="{BASE}/batch-2/">', title='Batch')
PAGES['/batch-2/'] = page(f'<h1>Batch page 2</h1><p>{FILLER}</p>',
                          head=f'<link rel="prev" href="{BASE}/batch/">', title='Batch page 2')

# One host spelled two ways and reached over two schemes. Neither link
# resolves - there is no TLS here and www.127.0.0.1 is not a name - but both
# checks read the markup, and is_internal strips www before comparing, so
# both links count as internal links to this site.
PAGES['/one-host-two-spellings/'] = page(
    f'<h1>Spellings</h1><p>{FILLER}</p>'
    f'<a href="http://www.127.0.0.1:{PORT}/">same site, with www</a> '
    f'<a href="https://127.0.0.1:{PORT}/">same site, over https</a>',
    title='Spellings')

# Page chrome that exists in the DOM and is never visible, and two dates
# three seconds apart - close enough that they are two reads of one clock
# rather than two events, which is what that check is for.
PAGES['/hidden-chrome/'] = page(
    '<nav style="display:none"><a href="/">home</a></nav>'
    f'<h1>Hidden chrome</h1><p>{FILLER}</p>',
    title='Hidden chrome')

PAGES['/clock-dates/'] = page(
    f'<h1>Clock dates</h1><p>{FILLER}</p>',
    head='<script type="application/ld+json">'
         '{"@context":"https://schema.org","@type":"Article",'
         '"headline":"Two reads of one clock",'
         '"datePublished":"2026-01-01T00:00:00Z",'
         '"dateModified":"2026-01-01T00:00:03Z"}</script>',
    title='Clock dates')

# A page the site shouts about and the web ignores. The check wants ten or
# more internal links in, so the link is injected into ten pages that already
# exist rather than ten new pages being crawled for one slug.
PAGES['/popular/'] = page(f'<h1>Popular internally</h1><p>{FILLER}</p>',
                          title='Popular internally')
for _key in [k for k in list(PAGES) if k != '/popular/'][:10]:
    _body = PAGES[_key]
    if _body.endswith(b'</body></html>'):
        PAGES[_key] = (_body[:-len(b'</body></html>')]
                       + b'<a href="/popular/">popular</a></body></html>')

# Rendered-mode faults: an image served far larger than it is displayed, a
# paragraph styled to look like a heading, text hidden off-screen.
PAGES['/has-amphtml/'] = page(
    f'<h1>Desktop page</h1><p>{FILLER}</p>',
    head=f'<link rel="amphtml" href="{BASE}/not-actually-amp/">')

PAGES['/not-actually-amp/'] = page(f'<h1>Not AMP</h1><p>{FILLER}</p>')

PAGES['/rendered-faults/'] = page(
    f'''<h1>Rendered faults</h1><p>{FILLER}</p>
    <img src="/big.png" alt="oversized" width="40" height="40">
    <p style="font-size:34px;font-weight:700">This is not a heading</p>
    <div style="position:absolute;left:-9999px">Hidden off to the side.</div>
    <div style="width:3000px">Wider than any viewport.</div>''',
    head='<script>console.error("a deliberate console error");</script>')

PAGES['/'] = PAGES['/'][:-len(b'</body></html>')] + (
    '''<a href="/amp/">amp</a>\n    <a href="/has-amphtml/">has amphtml</a>\n    <a href="/not-actually-amp/">amphtml target</a>
    <a href="/rendered-faults/">rendered faults</a>
    <a href="/doc-scan.pdf">scanned pdf</a>
    <a href="/doc-locked.pdf">locked pdf</a>
    <a href="/doc-cut.pdf">truncated pdf</a>
    <a href="/amp-two/">second amp</a>
    <a href="/tools/">tools</a>
    <a href="/widget-repair/">widget repair page</a>
    <a href="/series/">series</a>
    <a href="/batch/">batch</a>
    <a href="/one-host-two-spellings/">spellings</a>
    <a href="/doc-heavy.pdf">heavy pdf</a>
    <a href="/hidden-chrome/">hidden chrome</a>
    <a href="/clock-dates/">clock dates</a>'''
    + ('<img src="/silent.png" alt="a server that never answers">'
       if os.environ.get('COVERAGE_SILENT_IMAGE') else '')
    + '''</body></html>''').encode('utf-8')

# JSON-LD that only exists after JavaScript runs, and JSON-LD that JavaScript
# takes away. The rendered-mode checks compare raw against rendered, so these
# two need real script execution rather than crafted input.
PAGES['/js-jsonld-added/'] = page(
    f'<h1>Injected</h1><p>{FILLER}</p>'
    '<script>var s=document.createElement("script");'
    's.type="application/ld+json";'
    's.textContent=JSON.stringify({"@context":"https://schema.org",'
    '"@type":"Article","headline":"Added after load"});'
    'document.head.appendChild(s);</script>')

PAGES['/js-jsonld-removed/'] = page(
    f'<h1>Removed</h1><p>{FILLER}</p>'
    '<script>document.addEventListener("DOMContentLoaded",function(){'
    'var n=document.getElementById("ld");if(n)n.remove();});</script>',
    head='<script id="ld" type="application/ld+json">'
         '{"@context":"https://schema.org","@type":"Article",'
         '"headline":"Here until the script runs"}</script>')

# A canonical pointing at a path robots.txt forbids.
PAGES['/canonical-blocked/'] = page(
    f'<h1>Canonical blocked</h1><p>{FILLER}</p>',
    head=f'<link rel="canonical" href="{BASE}/blocked/secret/">')

# A pagination chain that breaks: page 2 names a next page that 404s.
PAGES['/list/1/'] = page(
    f'<h1>List 1</h1><p>{FILLER}</p><a href="/list/2/">next</a>',
    head=f'<link rel="next" href="{BASE}/list/2/">')
PAGES['/list/2/'] = page(
    f'<h1>List 2</h1><p>{FILLER}</p>',
    head=f'<link rel="prev" href="{BASE}/list/1/">'
         f'<link rel="next" href="{BASE}/list/nowhere/">')

PAGES['/'] = PAGES['/'][:-len(b'</body></html>')] + (
    '''<a href="/js-jsonld-added/">json-ld added</a>
    <a href="/js-jsonld-removed/">json-ld removed</a>
    <a href="/canonical-blocked/">canonical blocked</a>
    <a href="/list/1/">list</a>
    <img src="/missing-entirely.png" alt="an image that is not there">'''
    + '</body></html>').encode('utf-8')

# Accessibility violations axe can see, and a few link-shape faults.
PAGES['/a11y-faults/'] = page(
    f'''<h1>Accessibility</h1><p>{FILLER}</p>
    <form><input type="text" name="q"><button type="submit"></button></form>
    <a href="/chain-c/"><img src="/a.png"></a>
    <a href="/page/1/"></a>''')

# A page whose dates are long past, for the staleness check.
PAGES['/stale/'] = page(
    f'<h1>An old post</h1><p>{FILLER}</p>',
    head='''<script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Article","headline":"Old",
     "datePublished":"2016-03-01","dateModified":"2016-03-02"}</script>''')

# Internal links written against a second hostname for the same site.
PAGES['/host-mix/'] = page(
    f'''<h1>Host mix</h1><p>{FILLER}</p>
    <a href="http://localhost:{PORT}/chain-c/">same site, other hostname</a>
    <a href="http://localhost:{PORT}/page/1/">and again</a>''')

PAGES['/'] = PAGES['/'][:-len(b'</body></html>')] + (
    '''<a href="/a11y-faults/">a11y faults</a>
    <a href="/stale/">stale</a>
    <a href="/host-mix/">host mix</a>''' + '</body></html>').encode('utf-8')

# A LanguageTool endpoint, mocked at this origin. The language checks read
# whatever a LanguageTool server returns, so they need an endpoint rather
# than a credential - the public service is free but rate-limited and would
# make this suite depend on the internet.
import json as _json_lt
from urllib.parse import parse_qs as _parse_qs, urlparse as _urlparse

LT_MATCHES = {'matches': [
    {'message': 'Possible spelling mistake found.',
     'offset': 10, 'length': 7, 'replacements': [{'value': 'service'}],
     'context': {'text': 'Our best servcie for you', 'offset': 10, 'length': 7},
     'rule': {'id': 'MORFOLOGIK_RULE_EN_GB', 'description': 'Possible typo',
              'category': {'id': 'TYPOS', 'name': 'Possible Typo'}}},
    {'message': 'Use "an" instead of "a" before a vowel sound.',
     'offset': 40, 'length': 1, 'replacements': [{'value': 'an'}],
     'context': {'text': 'a engineer', 'offset': 0, 'length': 1},
     'rule': {'id': 'EN_A_VS_AN', 'description': 'a/an',
              'category': {'id': 'GRAMMAR', 'name': 'Grammar'}}},
    {'message': 'Two consecutive spaces.',
     'offset': 60, 'length': 2, 'replacements': [{'value': ' '}],
     'context': {'text': 'words  here', 'offset': 5, 'length': 2},
     'rule': {'id': 'WHITESPACE_RULE', 'description': 'Whitespace',
              'category': {'id': 'PUNCTUATION', 'name': 'Punctuation'}}},
    {'message': 'This sentence does not start with an uppercase letter.',
     'offset': 80, 'length': 1, 'replacements': [{'value': 'We'}],
     'context': {'text': 'we build', 'offset': 0, 'length': 1},
     'rule': {'id': 'UPPERCASE_SENTENCE_START', 'description': 'Casing',
              'category': {'id': 'CASING', 'name': 'Casing'}}},
    {'message': 'Consider a shorter word.',
     'offset': 95, 'length': 11, 'replacements': [{'value': 'use'}],
     'context': {'text': 'utilisation', 'offset': 0, 'length': 11},
     'rule': {'id': 'EN_WORDINESS', 'description': 'Wordiness',
              'category': {'id': 'REDUNDANCY', 'name': 'Redundancy'}}},
]}

PAGES['/prose/'] = page(
    '<h1>Our services</h1>'
    '<p>Our best servcie for you, written by a engineer with words  here. '
    'we build things, and the utilisation of them is straightforward enough '
    'that this paragraph has the length a real page would have.</p>'
    f'<p>{FILLER}</p>')

PAGES['/'] = PAGES['/'][:-len(b'</body></html>')] + (
    '<a href="/prose/">prose</a></body></html>').encode('utf-8')

# A PageSpeed Insights response, shaped as Google's v5 runPagespeed returns
# it. Served here because the live API without a key shares an anonymous
# quota that is routinely exhausted - a real call while writing this came
# back 429 - so leaning on it would make this suite depend on someone else's
# rate limit to tell the truth.
PSI_BODY = {
    'lighthouseResult': {
        'categories': {'performance': {'score': 0.31}},
        'audits': {
            'first-contentful-paint': {'numericValue': 4100},
            'largest-contentful-paint': {'numericValue': 6200},
            'cumulative-layout-shift': {'numericValue': 0.31},
            'max-potential-fid': {'numericValue': 620},
            'speed-index': {'numericValue': 7400},
            'interactive': {'numericValue': 9100},
        },
    },
}

# The same shape with every core vital in the middle band rather than the bad
# one. Up to three pages get a PageSpeed call - the homepage and two top-level
# pages - so the homepage keeps the poor numbers that already prove five slugs
# and everything else gets these, which is the only way both bands can appear
# in one crawl. LCP 3.2s (good 2.5, poor 4.0), CLS 0.18 (0.1/0.25), FID 320ms
# (200/500). Diagnostics stay poor: they only speak up when they are bad.
PSI_MID = {
    'lighthouseResult': {
        'categories': {'performance': {'score': 0.31}},
        'audits': {
            'first-contentful-paint': {'numericValue': 4100},
            'largest-contentful-paint': {'numericValue': 3200},
            'cumulative-layout-shift': {'numericValue': 0.18},
            'max-potential-fid': {'numericValue': 320},
            'speed-index': {'numericValue': 7400},
            'interactive': {'numericValue': 9100},
        },
    },
}

# Desktop, where the same page is fine. The crawl asks for both strategies,
# and the divergence check wants a gap of 30 points or more between them.
PSI_DESKTOP = {
    'lighthouseResult': {
        'categories': {'performance': {'score': 0.92}},
        'audits': {
            'first-contentful-paint': {'numericValue': 900},
            'largest-contentful-paint': {'numericValue': 1400},
            'cumulative-layout-shift': {'numericValue': 0.02},
            'max-potential-fid': {'numericValue': 80},
            'speed-index': {'numericValue': 1100},
            'interactive': {'numericValue': 1600},
        },
    },
}

# The sitemaps.org size limits, in their own mode. A sitemap of 50,001 URLs
# and an index of 51 files would crowd every other check out of the ordinary
# run - the crawler would queue tens of thousands of URLs it is never going
# to fetch - so COVERAGE_SITEMAP_LIMITS=1 swaps them in and keeps the crawl
# itself tiny. The 50MB file limit is deliberately not exercised: half a
# gigabyte of fixture traffic to reach one slug is a bad trade.
_LIMITS = os.environ.get('COVERAGE_SITEMAP_LIMITS', '')
LIMITS_MODE = _LIMITS in ('1', '2', '3')
# Mode 3 is the 50MB file limit. It is its own run because it is the one
# fixture here that costs real bandwidth: 50,000 entries with long paths,
# about 52MB over loopback. Cheap enough once, far too expensive to put in
# the run that every other check shares.
BYTES_MODE = _LIMITS == '3'
# Mode 2 points robots at one well-formed sitemap that lists nothing at all.
# It has to be its own run: sitemap_empty fires only when *no* sitemap on the
# site names a single URL, so it cannot share a run with any of the others.
EMPTY_MODE = _LIMITS == '2'

def _oversized_by_bytes_sitemap():
    """Past the 50MB limit without going past the 50,000-URL one.

    Exactly 50,000 entries - the limit is "more than", so this does not trip
    the count check - each with a path long enough to carry the file over
    50MB on its own.
    """
    head = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    filler = 'a' * 1000
    body = ''.join(f'<url><loc>{BASE}/p/{n}/{filler}</loc></url>'
                   for n in range(50000))
    return (head + body + '</urlset>').encode('utf-8')


def _oversized_sitemap():
    head = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    body = ''.join(f'<url><loc>{BASE}/p/{n}</loc></url>'
                   for n in range(50001))
    return (head + body + '</urlset>').encode('utf-8')

def _crowded_index():
    head = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    body = ''.join(f'<sitemap><loc>{BASE}/child-{n}.xml</loc></sitemap>'
                   for n in range(51))
    return (head + body + '</sitemapindex>').encode('utf-8')

TINY_SITEMAP = (b'<?xml version="1.0" encoding="UTF-8"?>'
                b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                b'</urlset>')

# A WordPress REST API, mocked at the same origin. The CMS checks compare
# what the CMS holds against what the site serves, so they need a real
# endpoint to read rather than a credential.
import json as _json

WP_ROOT = {'namespace': 'wp/v2', 'routes': {'/wp/v2/posts': {}, '/wp/v2/pages': {}}}

WP_POSTS = [
    {   # title and description over the limits, placeholder text, stray space,
        # structured data in the CMS that never reaches the page, and an
        # author nobody expects.
        'id': 1, 'type': 'post', 'status': 'publish', 'author': 7,
        'link': f'{BASE}/cms-post/',
        'title': {'rendered': 'A title that runs well past the sixty character limit '
                              'that search results will ever show anyone at all'},
        'excerpt': {'rendered': 'Lorem ipsum dolor sit amet, a placeholder excerpt that '
                                'was never replaced before publishing and also runs past '
                                'the hundred and sixty characters a description should '
                                'keep itself within.'},
        'content': {'rendered': '<p>  Leading space and a body. </p>'
                                '<script type="application/ld+json">'
                                '{"@context":"https://schema.org","@type":"FAQPage"}</script>'},
        'date_gmt': '2026-01-01T00:00:00', 'modified_gmt': '2026-01-02T00:00:00',
        # The SEO plugin's own fields, which the length and whitespace checks
        # read instead of the post title and excerpt. Without them those two
        # checks had nothing to look at, whatever the post said.
        'yoast_head_json': {
            'title': '  A Yoast title past the sixty character limit  with a doubled space',
            'description': 'A Yoast meta description written to run past the one hundred '
                           'and sixty character limit, so the length check has something '
                           'real to find in the CMS rather than on the page.',
        },
    },
    {   # published in the CMS, absent from the sitemap and never crawled
        'id': 2, 'type': 'post', 'status': 'publish', 'author': 7,
        'link': f'{BASE}/cms-missing/',
        'title': {'rendered': 'A published post the site never serves'},
        'excerpt': {'rendered': 'Short.'},
        'content': {'rendered': '<p>Body.</p>'},
        'date_gmt': '2026-01-01T00:00:00', 'modified_gmt': '2026-01-01T00:00:00',
    },
]

WP_USERS = {7: {'id': 7, 'name': 'Someone Else', 'slug': 'someone-else'}}

# The page the CMS says exists, served without the structured data the CMS holds.
PAGES['/cms-post/'] = page(
    f'<h1>A CMS post</h1><p>{FILLER}</p>',
    head='<meta name="description" content="Short.">')

PAGES['/'] = PAGES['/'][:-len(b'</body></html>')] + (
    '<a href="/cms-post/">cms post</a></body></html>').encode('utf-8')

# A sitemap that is wrong in as many documented ways as one file can be.
SITEMAP = f'''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>{BASE}/</loc><lastmod>2024-01-01</lastmod></url>
  <url><loc>{BASE}/</loc><lastmod>2024-01-01</lastmod></url>
  <url><loc>{BASE}/sitemap-only/</loc></url>
  <url><loc>{BASE}/noindex-in-sitemap/</loc><lastmod>2099-01-01</lastmod></url>
  <url><loc>{BASE}/canonical-elsewhere/</loc><lastmod>not-a-date</lastmod></url>
  <url><loc>{BASE}/gone-from-the-site/</loc></url>
  <url><loc>{BASE}/server-error-page/</loc></url>
  <url><loc>{BASE}/redirect-a/</loc></url>
  <url><loc>{BASE}/tracked</loc></url>
  <url><loc>{BASE}/batch-2/</loc></url>
  <url><loc>{BASE}/blocked/secret/</loc></url>
  <url><loc>{BASE}/stale/</loc><lastmod>2016-03-02</lastmod></url>
  <url><loc>http://localhost:{PORT}/other-host/</loc></url>
</urlset>'''.encode('utf-8')

# A chain of ordinary pages, so depth is measured in clicks rather than in
# path segments: /deep/1/2/3 is one click from the homepage, not three.
for _n in range(1, 10):
    PAGES[f'/chain/{_n}/'] = page(
        f'<h1>Chain {_n}</h1><p>{FILLER}</p>'
        + (f'<a href="/chain/{_n + 1}/">next</a>' if _n < 9 else ''))

PAGES['/banned-claim/'] = page(
    f'<h1>Claims</h1><p>We offer guaranteed results for every client.</p><p>{FILLER}</p>')

PAGES['/heading-whitespace/'] = page(
    f'<h1>  Leading and trailing  </h1><p>{FILLER}</p>')

# A canonical chain: A says B, B says C.
PAGES['/chain-a/'] = page(f'<h1>Chain A</h1><p>{FILLER}</p>',
                          head=f'<link rel="canonical" href="{BASE}/chain-b/">')
PAGES['/chain-b/'] = page(f'<h1>Chain B</h1><p>{FILLER}</p>',
                          head=f'<link rel="canonical" href="{BASE}/chain-c/">')
PAGES['/chain-c/'] = page(f'<h1>Chain C</h1><p>{FILLER}</p>')

# Page weight: the bands are 1MB and 3MB.
PAGES['/heavy-moderate/'] = page('<h1>Moderate</h1><p>' + ('w ' * 700_000) + '</p>')
PAGES['/heavy-large/'] = page('<h1>Large</h1><p>' + ('w ' * 1_900_000) + '</p>')

# Pagination: a chain that breaks, one page that never links back, and one
# that canonicalises to page one.
PAGES['/page/1/'] = page(
    f'<h1>Listing, page 1</h1><p>{FILLER}</p><a href="/page/2/">next</a>',
    head=f'<link rel="next" href="{BASE}/page/2/">')
PAGES['/page/2/'] = page(
    f'<h1>Listing, page 2</h1><p>{FILLER}</p><a href="/page/3/">next</a>',
    head=f'<link rel="next" href="{BASE}/page/3/">')
PAGES['/page/3/'] = page(
    f'<h1>Listing, page 3</h1><p>{FILLER}</p>',
    head=f'<link rel="canonical" href="{BASE}/page/1/">')

# Tracking parameters on internal links, which should be a finding about the
# links rather than about the pages.
PAGES['/tracked-links/'] = page(
    f'''<h1>Tracked links</h1><p>{FILLER}</p>
    <a href="/page/1/?utm_source=a&utm_medium=b">one</a>
    <a href="/page/2/?utm_source=c">two</a>
    <a href="/chain-c/?gclid=xyz">three</a>''')

PAGES['/'] = PAGES['/'][:-len(b'</body></html>')] + (
    f'''<a href="/chain/1/">chain</a>
    <a href="/heading-whitespace/">heading whitespace</a>\n    <a href="/banned-claim/">banned claim</a>
    <a href="/chain-a/">canonical chain</a>\n    <a href="/chain-b/">canonical chain middle</a>
    <a href="/heavy-moderate/">moderate</a>
    <a href="/heavy-large/">large</a>
    <a href="/page/1/">listing</a>
    <a href="/tracked-links/">tracked links</a>
    <a href="/hreflang-fr/">fr</a></body></html>''').encode('utf-8')

# A sitemap index pointing at a child that is empty and a child on another
# protocol, so the index-level checks have something to find.
SITEMAP_INDEX = f'''<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>{BASE}/sitemap.xml</loc></sitemap>
  <sitemap><loc>{BASE}/sitemap-empty.xml</loc></sitemap>
  <sitemap><loc>{BASE}/sitemap-nested.xml</loc></sitemap>
  <sitemap><loc>https://127.0.0.1:{PORT}/sitemap-https.xml</loc></sitemap>
</sitemapindex>'''.encode('utf-8')

SITEMAP_EMPTY = b'''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>'''

# An index inside an index: search engines follow only one level.
SITEMAP_NESTED = f'''<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>{BASE}/sitemap.xml</loc></sitemap>
</sitemapindex>'''.encode('utf-8')

ROBOTS = f'User-agent: *\nDisallow: /blocked/\nSitemap: {BASE}/sitemap-index.xml\n'.encode()

# Response headers chosen to trip every header check at once.
BAD_HEADERS = {
    'Strict-Transport-Security': 'max-age=100',
    'Content-Security-Policy': "default-src 'self' 'unsafe-eval' 'unsafe-inline'",
    'Referrer-Policy': 'unsafe-url',
    'Set-Cookie': 'sid=abc; Path=/',
    'Server': 'CoverageFixture/1.2.3',
    'X-Powered-By': 'Fixture 9.9',
}


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _send(self, code, body, ctype='text/html; charset=utf-8', extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for key, value in {**BAD_HEADERS, **(extra or {})}.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        if self.path.split('?')[0] == '/v2/check':
            try:
                self.rfile.read(int(self.headers.get('Content-Length') or 0))
            except Exception:
                pass
            return self._send(200, _json_lt.dumps(LT_MATCHES).encode('utf-8'),
                              'application/json')
        return self._send(404, page('<h1>Not found</h1>'))

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/pagespeedonline/v5/runPagespeed':
            query = _parse_qs(_urlparse(self.path).query)
            asked = query.get('url', [''])[0]
            at_root = _urlparse(asked).path in ('', '/')
            body = PSI_BODY if at_root else PSI_MID
            if query.get('strategy', ['mobile'])[0] == 'desktop':
                body = PSI_DESKTOP
            return self._send(200, _json_lt.dumps(body).encode('utf-8'),
                              'application/json')
        if path == '/v2/check':
            return self._send(200, _json_lt.dumps(LT_MATCHES).encode('utf-8'),
                              'application/json')
        if path.startswith('/wp-json/wp/v2'):
            tail = path[len('/wp-json/wp/v2'):].strip('/')
            if tail in ('', 'posts'):
                body = _json.dumps(WP_ROOT if tail == '' else WP_POSTS)
            elif tail == 'pages':
                body = '[]'
            elif tail.startswith('users/'):
                body = _json.dumps(WP_USERS.get(int(tail.split('/')[1]), {}))
            else:
                body = '[]'
            return self._send(200, body.encode('utf-8'), 'application/json',
                              {'X-WP-Total': '2', 'X-WP-TotalPages': '1'})
        if path == '/robots.txt':
            if LIMITS_MODE:
                if EMPTY_MODE:
                    maps = [f'Sitemap: {BASE}/child-0.xml']
                elif BYTES_MODE:
                    maps = [f'Sitemap: {BASE}/sitemap-heavy.xml']
                else:
                    maps = [f'Sitemap: {BASE}/sitemap-big.xml',
                            f'Sitemap: {BASE}/sitemap-crowded.xml']
                body = '\n'.join(['User-agent: *'] + maps + ['']).encode()
                return self._send(200, body, 'text/plain')
            return self._send(200, ROBOTS, 'text/plain')
        if path == '/sitemap.xml':
            return self._send(200, SITEMAP, 'application/xml')
        if path == '/sitemap-index.xml':
            return self._send(200, SITEMAP_INDEX, 'application/xml')
        if path == '/sitemap-empty.xml':
            return self._send(200, SITEMAP_EMPTY, 'application/xml')
        if path.startswith('/child-') and path.endswith('.xml'):
            return self._send(200, TINY_SITEMAP, 'application/xml')
        if path == '/sitemap-big.xml':
            return self._send(200, _oversized_sitemap(), 'application/xml')
        if path == '/sitemap-heavy.xml':
            return self._send(200, _oversized_by_bytes_sitemap(), 'application/xml')
        if path == '/sitemap-crowded.xml':
            return self._send(200, _crowded_index(), 'application/xml')
        if path == '/sitemap-nested.xml':
            return self._send(200, SITEMAP_NESTED, 'application/xml')
        if path == '/redirect-a/':
            return self._send(301, b'', extra={'Location': f'{BASE}/redirect-b/'})
        if path == '/redirect-b/':
            return self._send(302, b'', extra={'Location': f'{BASE}/redirect-c/'})
        if path.endswith('.pdf'):
            from coverage_gap_tests import build_pdf
            kinds = {'/doc-scan.pdf': dict(title=None, pages=3, text=False),
                     '/doc-locked.pdf': dict(title='Locked', encrypted=True, text=False),
                     '/doc-cut.pdf': dict(title='Cut', truncated=True),
                     '/doc-heavy.pdf': dict(title='Heavy')}
            body = build_pdf(**kinds.get(path, {}))
            if path == '/doc-heavy.pdf':
                # Over the 10MB line. The padding is a comment placed just
                # before %%EOF, not after it: a file that does not end in the
                # end marker reads as damaged, and the damaged branch returns
                # before anything asks how big it is. Sitting after the xref
                # and trailer, it moves no offsets.
                pad = b'%' + b'A' * (11 * 1024 * 1024) + b'\n'
                head, sep, tail = body.rpartition(b'%%EOF')
                body = head + pad + sep + tail
            return self._send(200, body, 'application/pdf')
        if path == '/silent.png':
            # Accepts the connection and answers nothing. An earlier version
            # of this fixture had a handler like this, meant to trigger
            # broken_image; it was reverted after the run fell from 96 checks
            # to 21, which was blamed on the handler. COVERAGE_SILENT_IMAGE=1
            # puts it back so that blame can be checked now the harness waits
            # for the crawl instead of timing out underneath it.
            import time as _t
            _t.sleep(90)
            return
        if path == '/big.png':
            # A 1200x1200 PNG displayed at 40x40.
            import base64, zlib, struct
            def chunk(tag, data):
                c = tag + data
                return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c))
            w = h = 1200
            raw = b''.join(b'\x00' + b'\xff\x00\x00' * w for _ in range(h))
            png = (b'\x89PNG\r\n\x1a\n'
                   + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
                   + chunk(b'IDAT', zlib.compress(raw))
                   + chunk(b'IEND', b''))
            return self._send(200, png, 'image/png')
        if path in ('/server-error.png', '/server-error-page/'):
            return self._send(503, b'busy', 'text/plain')
        if path in PAGES:
            return self._send(200, PAGES[path])
        return self._send(404, page('<h1>Not found</h1>'))

    def log_message(self, *args):
        pass


def _analyse_fixture_log(pages):
    """Run the log checks over a log written to disagree with this crawl.

    Googlebot's user agent from 127.0.0.1, which reverse DNS will not
    confirm; a dozen requests to a page that only ever 404s; one path the
    crawl never found; and enough unreadable lines to be worth saying so.
    """
    from src.core import log_analysis

    ua = ('Mozilla/5.0 (compatible; Googlebot/2.1; '
          '+http://www.google.com/bot.html)')

    def line(path, status, minute):
        return (f'127.0.0.1 - - [18/Oct/2026:10:{minute:02d}:00 +0000] '
                f'"GET {path} HTTP/1.1" {status} 120 "-" "{ua}"')

    lines = [line('/', 200, 0), line('/sitemap-only/', 200, 1)]
    # Ten or more hits, never a 200: budget spent on an error.
    lines += [line('/gone-from-the-site/', 404, n) for n in range(2, 14)]
    # A path nothing on the site links to, so the crawl never saw it.
    lines.append(line('/an-old-url/', 200, 14))
    # Above the one-in-five ratio that means the format is not understood.
    lines += ['this line is not an access log entry'] * 6

    base = ''
    for page_row in pages:
        url = (page_row or {}).get('url') or ''
        if url.startswith(('http://', 'https://')):
            base = '/'.join(url.split('/')[:3])
            break

    parsed = log_analysis.parse_lines(lines)
    found = log_analysis.log_issues(parsed, pages, base)
    print(f'log analysis: {len(found)} issue(s) from {parsed.get("total", 0)} lines')


class _CannedResponse:
    """What urlopen returns, as far as google_auth is concerned."""

    def __init__(self, payload):
        self._body = _json_lt.dumps(payload).encode('utf-8')

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _google_checks(pages, base):
    """Search Console and GA4, stubbed at the transport and nowhere else.

    These nine checks were filed as credential-blocked all day, which was
    wrong in the same way four other "needs an external input" calls were
    wrong today. What they need is a server that answers like Google, and
    `collect()` already takes an injected opener for exactly that.

    The stub replaces urlopen and nothing else: the OAuth refresh runs, the
    request bodies are built by the real code, the responses are parsed by
    the real parsers, and the findings come out of the real `issues()`. The
    field names and enums in these payloads are not invented either - they
    are the ones `tools/verify_google.py` checks against Google's published
    Discovery documents, which is the part a live call would not tell us
    any better. What is still untested is Google itself being on the other
    end, and that waits on one consent click.
    """
    from src.core import analytics, google_auth, search_console

    crawled = [p.get('url') for p in pages
               if (p or {}).get('status_code') == 200 and p.get('url')]
    if len(crawled) < 3:
        print('google: not enough crawled pages to judge')
        return
    shown, buried, quiet = crawled[0], crawled[1], crawled[2]
    unknown = base + '/a-page-google-knows-and-we-do-not/'

    def search_analytics(body):
        if body.get('dimensions') == ['page']:
            return {'rows': [
                # 2,400 impressions and 6 clicks: seen often, clicked rarely.
                {'keys': [shown], 'clicks': 6, 'impressions': 2400,
                 'ctr': 0.0025, 'position': 18.4},
                {'keys': [buried], 'clicks': 40, 'impressions': 900,
                 'ctr': 0.044, 'position': 7.1},
                # A page with impressions that the crawl never reached.
                {'keys': [unknown], 'clicks': 3, 'impressions': 210,
                 'ctr': 0.014, 'position': 22.0},
            ]}
        # query + page: two pages splitting one query between them.
        return {'rows': [
            {'keys': ['widget repair', shown], 'clicks': 4,
             'impressions': 300, 'ctr': 0.013, 'position': 11.0},
            {'keys': ['widget repair', buried], 'clicks': 2,
             'impressions': 180, 'ctr': 0.011, 'position': 14.0},
        ]}

    def inspection(body):
        # The budget is spent only on crawled pages with no impressions, so
        # the override has to go on one of those. Putting it on a page that
        # has performance data means it is never inspected at all, which is
        # how this check stayed quiet on the first run.
        url = body.get('inspectionUrl') or ''
        if url == quiet:
            # Indexed, but under an address its author did not choose.
            status = {'verdict': 'PASS', 'coverageState': 'Submitted and indexed',
                      'googleCanonical': shown, 'userCanonical': quiet}
        else:
            status = {'verdict': 'FAIL',
                      'coverageState': 'Crawled - currently not indexed',
                      'googleCanonical': '', 'userCanonical': ''}
        return {'inspectionResult': {'indexStatusResult': status}}

    def run_report():
        def row(path, sessions, bounce):
            return {'dimensionValues': [{'value': path}],
                    'metricValues': [{'value': str(sessions)},
                                     {'value': str(int(sessions * 0.1))},
                                     {'value': str(bounce)},
                                     {'value': '14.2'}]}
        return {'rows': [
            # Over the 30-session floor and past the 85% bounce threshold.
            row(shown[len(base):] or '/', 420, 0.93),
            row(buried[len(base):] or '/', 95, 0.41),
            # Traffic on a path nothing on the site links to.
            row('/a-page-google-knows-and-we-do-not/', 60, 0.5),
        ]}

    def opener(request, timeout=None):
        url = request.full_url
        body = {}
        if request.data and 'oauth2' not in url:
            body = _json_lt.loads(request.data.decode('utf-8'))
        if 'oauth2' in url or url.endswith('/token'):
            return _CannedResponse({'access_token': 'stub-token',
                                    'expires_in': 3600, 'token_type': 'Bearer'})
        if 'urlInspection' in url:
            return _CannedResponse(inspection(body))
        if 'searchanalytics' in url.lower():
            return _CannedResponse(search_analytics(body))
        if 'analyticsdata' in url:
            return _CannedResponse(run_report())
        return _CannedResponse({})

    credentials = google_auth.Credentials('stub-client', 'stub-secret',
                                          'stub-refresh', opener=opener)
    gsc = search_console.collect(credentials, base + '/', pages,
                                 config={'inspect_budget': 3}, opener=opener)
    ga = analytics.collect(credentials, '123456', pages, base,
                           config=None, opener=opener)
    print(f'google (stubbed transport): {len(gsc.get("issues") or [])} Search Console '
          f'and {len(ga.get("issues") or [])} Analytics issue(s)'
          + (f' [gsc error: {gsc.get("error")}]' if gsc.get('error') else '')
          + (f' [ga error: {ga.get("error")}]' if ga.get('error') else ''))


def _link_metrics_and_field_data(pages, base):
    """Moz and CrUX, stubbed at the transport, over this crawl's own pages.

    Four link-metrics checks and the CrUX field vitals were filed as
    needing a paid subscription and a Google key. The subscription is what
    a *live* call needs; what these checks need is a provider that answers,
    and `collect()` and `fetch_crux()` both take an injected opener. The
    endpoints and failure shapes were already checked against the real
    services without a key - Moz answers a POST with 401, Ahrefs with 401,
    Majestic with HTTP 200 and the error in the body - so what is stubbed
    here is only the success path that money would unlock.
    """
    import urllib.error
    from src.core import link_metrics, pagespeed_checks

    live = [p for p in pages
            if (p or {}).get('status_code') == 200 and p.get('url')]
    if not live:
        print('link metrics: no crawled pages')
        return

    # A page the web rates and this site buries, and one the site shouts
    # about that nobody outside links to. Picked from the crawl rather than
    # named, so this keeps working as the fixture grows.
    deep = max(live, key=lambda p: _n(p.get('depth')))
    hub = max(live, key=lambda p: _n(p.get('linked_from_count')
                                     or len(p.get('linked_from') or [])))
    spammy = live[0]

    rows = {
        deep['url']: dict(page=deep['url'], root_domains_to_page=18,
                          external_pages_to_page=44, spam_score=3,
                          page_authority=31, domain_authority=28),
        hub['url']: dict(page=hub['url'], root_domains_to_page=0,
                         external_pages_to_page=0, spam_score=1,
                         page_authority=12, domain_authority=28),
        spammy['url']: dict(page=spammy['url'], root_domains_to_page=2,
                            external_pages_to_page=5, spam_score=61,
                            page_authority=9, domain_authority=28),
    }

    def moz_opener(request, timeout=None):
        asked = _json_lt.loads(request.data.decode('utf-8')).get('targets') or []
        return _CannedResponse({'results': [rows[u] for u in asked if u in rows]})

    def refusing_opener(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {}, None)

    config = {'moz_access_id': 'stub-id', 'moz_secret_key': 'stub-secret'}
    good = link_metrics.collect(pages, config, opener=moz_opener)
    found = link_metrics.metric_issues(pages, good)
    # And the other half of the contract: a provider that will not answer
    # must produce a visible note, not silence.
    refused = link_metrics.collect(pages, config, opener=refusing_opener)
    found += link_metrics.metric_issues(pages, refused)
    print(f'link metrics (stubbed transport): {len(found)} issue(s), '
          f'refusal reported as "{(refused.get("error") or "")[:40]}"')

    # CrUX: real-user vitals, which outrank the lab numbers when both exist.
    def crux_opener(request, timeout=None):
        return _CannedResponse({'record': {
            'collectionPeriod': {'firstDate': {'year': 2026, 'month': 9, 'day': 1}},
            'metrics': {
                'largest_contentful_paint': {'percentiles': {'p75': 6100}},
                'cumulative_layout_shift': {'percentiles': {'p75': 0.34}},
                'interaction_to_next_paint': {'percentiles': {'p75': 640}},
                'first_contentful_paint': {'percentiles': {'p75': 4300}},
            }}})

    crux = pagespeed_checks.fetch_crux(base + '/', 'stub-key', opener=crux_opener)
    field = pagespeed_checks.field_issues(base + '/', crux)
    print(f'crux field data (stubbed transport): {len(field)} issue(s)')


def _n(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _judge_with_a_stub_runner(pages):
    """The analyst path, with the model's answer supplied rather than asked for.

    This one is weaker than the rest of this file and should be read that
    way. Everything else here is proven by making a real crawl of a real
    server produce a real finding. An analyst's verdict comes from a model,
    and a fixture cannot have one without either a paid key or someone's
    subscription, so the runner is stubbed to return the low score that the
    check is about. What that proves is the plumbing - verdict to issue to
    slug to the UI - and not that any model would score the page this way.
    """
    from src.core.agents import analysts

    def runner(prompt, config):
        return ('{"score": 2, "missing": ["no first-hand detail", '
                '"no author credentials"], "evidence": "generic copy"}')

    # COVERAGE_AGENT_CLI runs the real model instead, which is how this was
    # proven once for real: a 404-word page of repeated boilerplate scored
    # 0/10 - "none of it shows the writer has done any plumbing work: no
    # specifics, no jobs, no people, no data" - and the check fired. It is
    # opt-in because every run of it spends someone's subscription, and a
    # suite that costs money per execution will stop being run.
    real = os.environ.get('COVERAGE_AGENT_CLI')
    chosen = [p for p in pages if (p.get('status_code') == 200)][:1]
    found = analysts.issues(
        chosen, which=('experience',),
        config={'mode': 'cli', 'cli_command': real or 'stub', 'timeout': 180},
        runner=None if real else runner)
    print(f'agent analysts ({"real model" if real else "stubbed verdict"}): '
          f'{len(found)} issue(s)')


def run(expect=0):
    log = os.environ.get('TOADSTONE_SLUG_LOG')
    if not log:
        print('Set TOADSTONE_SLUG_LOG to a file path first — that is what '
              'records which checks fired.')
        return 2
    open(log, 'w').close()

    # Threaded, not socketserver.TCPServer: the crawler makes concurrent
    # requests and a single-threaded server with HTTP/1.1 keep-alive
    # deadlocks against it, which looks exactly like a hung crawler.
    # A rules file for the run, so the user-rules mechanism is exercised
    # rather than assumed. Written only if the install has none, and removed
    # afterwards: this is the operator's directory, not the test's.
    rules_path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'data', 'rules.yaml')
    wrote_rules = False
    if not os.path.exists(rules_path):
        os.makedirs(os.path.dirname(rules_path), exist_ok=True)
        with open(rules_path, 'w', encoding='utf-8') as handle:
            handle.write(
                'rules:\n'
                '  - id: coverage-banned-claim\n'
                '    severity: warning\n'
                '    forbid: ["guaranteed results"]\n')
        wrote_rules = True

    http.server.ThreadingHTTPServer.allow_reuse_address = True
    server = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    from src.crawler import WebCrawler
    crawler = WebCrawler()
    crawler.config.update({
        'max_urls': 3 if LIMITS_MODE else 120, 'max_depth': 2 if LIMITS_MODE else 12, 'delay': 0, 'crawl_images': True,
        'audit_sitemaps': True,
        'analyse_link_graph': True, 'header_checks': True, 'amp_checks': True,
        'audit_cms': True, 'cms_expected_author': 'Jane Doe',
        'spell_check': True,
        'enable_pagespeed': True,
        'pagespeed_endpoint': f'{BASE}/pagespeedonline/v5/runPagespeed',
        'spell_check_endpoint': f'{BASE}/v2/check', 'accessibility_checks': True,
        'rendered_checks': True, 'crawl_pdfs': True, 'enable_duplication_check': True,
        'enable_javascript': True,
        # One rendering worker, not two, so this run measures the same thing
        # every time. With two it alternates between 84 pages / 164 checks
        # and 77 / 162, and the pages that go missing are always the tail of
        # the one sequential link chain here - /chain/3/ onwards, discovered
        # only by following the chain a link at a time. With one worker it
        # gives 84/164 every time. No error, timeout or render failure is
        # logged in the short runs, and a minimal reproduction (the same
        # chain plus forty sibling pages, two workers) does not reproduce
        # it, so the mechanism is NOT established - only that concurrency
        # is involved. COVERAGE_JS_WORKERS=2 reproduces the flake for
        # anyone who wants to chase it.
        'js_max_concurrent_pages': int(os.environ.get('COVERAGE_JS_WORKERS', '1')),
        # An agent command that is not there. agent_unavailable is the note a
        # crawl makes when a configured model cannot be reached, and the only
        # honest way to reach it is to configure one that cannot be. Nothing
        # is spawned: the name does not resolve to a binary.
        'agent_mode': 'cli',
        'agent_cli_command': '/nonexistent/toadstone-no-such-agent',
        'agent_max_pages': 1,
    })
    print(f'crawling the fixture site at {BASE} ...')
    crawler.start_crawl(BASE + '/')
    # Wait for the crawl to actually finish. This was two minutes, which was
    # enough until it wasn't: three more pages took the crawl past the cap,
    # the server below was shut down while PageSpeed was still calling it,
    # and every phase after the fetch - duplication, sitemap audit, technical,
    # header, language, CMS - never ran. The run then reported 27 checks
    # instead of 125 and still printed OK, because --expect defaults to zero.
    # Nothing was wrong with the pages; the harness had pulled the floor out.
    deadline = time.time() + CRAWL_TIMEOUT
    while time.time() < deadline:
        if not crawler.is_running and crawler.crawl_results:
            break
        time.sleep(0.5)
    else:
        print(f'\nWARNING: the crawl was still running after {CRAWL_TIMEOUT}s. '
              f'Everything below is partial.')
    server.shutdown()

    if wrote_rules:
        try:
            os.unlink(rules_path)
        except OSError:
            pass

    # Server logs, against the pages this crawl actually found. The log
    # checks compare what search engines requested with what the crawler
    # reached, so they need a crawl on one side and a log on the other -
    # not a credential, which is why they sat unproven for so long. The
    # log is synthetic and never touches disk.
    _analyse_fixture_log(crawler.crawl_results)
    _judge_with_a_stub_runner(crawler.crawl_results)
    _google_checks(crawler.crawl_results, BASE)
    _link_metrics_and_field_data(crawler.crawl_results, BASE)

    fired = sorted({line.strip() for line in open(log) if line.strip()})
    print(f'\npages crawled : {len(crawler.crawl_results)}')
    print(f'checks fired  : {len(fired)}\n')
    for slug in fired:
        print(' ', slug)

    if expect and len(fired) < expect:
        print(f'\nFAIL: expected at least {expect} distinct checks, got {len(fired)}')
        return 1
    print('\nOK')
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    # Not zero. A run that silently lost three quarters of its checks printed
    # OK for as long as this defaulted to no floor at all.
    # 164 is what a healthy run gives. The floor sat at 120, which is far
    # enough below that to let a whole subsystem fall over unnoticed: an
    # undefined name took every PageSpeed check out - 164 to 154 - and the
    # run still passed. A floor only catches what it is close to.
    parser.add_argument('--expect', type=int, default=0 if LIMITS_MODE else 160,
                        help='fail if fewer than this many distinct checks fire')
    sys.exit(run(parser.parse_args().expect))
