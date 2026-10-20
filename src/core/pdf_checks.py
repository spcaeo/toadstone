"""PDFs, as documents rather than as holes in the crawl.

PDFs are excluded from crawls by default, which is a defensible choice — they
are slow to fetch and most sites have few. It is not a defensible choice for
the sites that have many. A university, a council, a manufacturer with
datasheets, a law firm with filings: on those sites the PDFs *are* the
content, Google indexes them, people land on them from search, and a crawler
that skips them reports on a fraction of the site while looking complete.

Google treats a PDF much like a page. It reads the title, indexes the text,
follows the links, and honours `X-Robots-Tag` on the response. So the same
questions apply: does it have a title, is it indexable, is it so large that
nobody on a phone will wait for it, is it a scan with no text layer at all —
which looks fine to a person and is invisible to a search engine.

This reads the PDF structure directly rather than adding a dependency. That
buys the metadata, the page count and enough text-layer detection to tell a
real document from a scan; it does not buy full text extraction, and the code
says so rather than pretending. Encrypted and malformed files are reported as
what they are instead of crashing a crawl.
"""

import re
import zlib

from src.utils.issue_factory import make_issue

CATEGORY = 'Documents'

# Above this, a PDF is a download rather than something anyone reads in a tab.
HEAVY_BYTES = 10 * 1024 * 1024
# Below this much extractable text per page, it is almost certainly a scan.
MIN_TEXT_CHARS_PER_PAGE = 60

_TITLE = re.compile(rb'/Title\s*\(((?:[^()\\]|\\.)*)\)')
_TITLE_HEX = re.compile(rb'/Title\s*<([0-9A-Fa-f]+)>')
_PAGES = re.compile(rb'/Type\s*/Page[^s]')
_ENCRYPT = re.compile(rb'/Encrypt\b')
_TEXT_OP = re.compile(rb'(?:BT\b|\bTj\b|\bTJ\b)')


def looks_like_pdf(data, content_type=''):
    return bool(data[:5] == b'%PDF-' or 'pdf' in (content_type or '').lower())


def _decode_pdf_string(raw):
    """A PDF literal string as text, handling the two encodings in practice."""
    try:
        value = re.sub(rb'\\(.)', rb'\1', raw)
        if value[:2] in (b'\xfe\xff',):
            return value[2:].decode('utf-16-be', 'replace').strip()
        return value.decode('latin-1', 'replace').strip()
    except Exception:
        return ''


def _streams_text(data, limit=40):
    """Enough decompressed content to tell a text layer from a scan.

    Not full extraction: that needs a font and encoding model this does not
    have. It answers one question - is there any text at all - which is the
    question that distinguishes a usable PDF from a photographed one.
    """
    found = []
    for match in re.finditer(rb'stream\r?\n', data):
        if len(found) >= limit:
            break
        start = match.end()
        end = data.find(b'endstream', start)
        if end == -1:
            break
        chunk = data[start:end]
        try:
            chunk = zlib.decompress(chunk)
        except Exception:
            pass  # uncompressed, or a filter this does not implement
        found.append(chunk)
    return b''.join(found)


def inspect(data, content_type=''):
    """What can be read from a PDF's bytes, with the limits made explicit."""
    report = {
        'is_pdf': looks_like_pdf(data, content_type),
        'bytes': len(data or b''),
        'title': '',
        'pages': 0,
        'encrypted': False,
        'has_text_layer': False,
        'damaged': False,
    }
    if not report['is_pdf'] or not data:
        return report

    if not data[:5] == b'%PDF-':
        # Served as a PDF but does not start like one.
        report['damaged'] = True
        return report

    report['encrypted'] = bool(_ENCRYPT.search(data))
    report['pages'] = len(_PAGES.findall(data)) or 0

    match = _TITLE.search(data)
    if match:
        report['title'] = _decode_pdf_string(match.group(1))
    else:
        hex_match = _TITLE_HEX.search(data)
        if hex_match:
            try:
                raw = bytes.fromhex(hex_match.group(1).decode('ascii'))
                report['title'] = (raw[2:] if raw[:2] == b'\xfe\xff' else raw) \
                    .decode('utf-16-be' if raw[:2] == b'\xfe\xff' else 'latin-1',
                            'replace').strip()
            except Exception:
                report['title'] = ''

    if report['encrypted']:
        # The content is unreadable by design; saying "no text layer" about an
        # encrypted file would be a guess dressed as a finding.
        return report

    body = _streams_text(data)
    report['has_text_layer'] = bool(_TEXT_OP.search(body))
    if b'%%EOF' not in data[-2048:]:
        report['damaged'] = True
    return report


def pdf_issues(pages, emit=None):
    """Findings for every crawled PDF. Shaped like every other check."""
    emit = emit or make_issue
    found = []
    for page in pages or []:
        report = page.get('pdf')
        if not report or not report.get('is_pdf'):
            continue
        url = page.get('url', '')
        status = page.get('status_code')
        if status and int(status) >= 400:
            continue  # the status check already said this

        if report.get('damaged'):
            found.append(emit(
                url=url, check='pdf_damaged', severity='error', category=CATEGORY,
                issue='PDF Is Truncated Or Damaged',
                details='This file is served as a PDF but does not end like one. '
                        'It was probably truncated in upload or transfer, and '
                        'readers may refuse to open it.',
                offending_value=url[:120],
                evidence={'found': f'{report.get("bytes", 0)} bytes, no end marker'},
                how_to_fix='Re-upload the file and check it opens.'))
            continue

        if report.get('encrypted'):
            found.append(emit(
                url=url, check='pdf_encrypted', severity='warning', category=CATEGORY,
                issue='PDF Is Password Protected',
                details='Search engines cannot read a protected PDF, so none of '
                        'its content can be indexed and it will not rank for '
                        'anything it says.',
                offending_value=url[:120],
                evidence={'found': 'encrypted'},
                how_to_fix='Publish an unprotected copy if the content is meant '
                           'to be found.'))

        if not report.get('title'):
            found.append(emit(
                url=url, check='pdf_missing_title', severity='warning',
                category=CATEGORY, issue='PDF Has No Title',
                details='Google shows a PDF\'s document title as the search '
                        'result heading. Without one it shows the file name, '
                        'which is usually something like final-v3-web.pdf.',
                offending_value=url[:120],
                evidence={'expected': 'a document title in the PDF properties'},
                how_to_fix='Set the Title field in the document properties '
                           'before exporting.'))

        if not report.get('encrypted') and not report.get('has_text_layer') \
                and report.get('pages'):
            found.append(emit(
                url=url, check='pdf_no_text_layer', severity='error',
                category=CATEGORY, issue='PDF Is A Scan With No Text',
                details='This PDF contains no selectable text, so it is almost '
                        'certainly scanned images. A person can read it and a '
                        'search engine cannot: none of its words are indexable, '
                        'and it is unusable with a screen reader.',
                offending_value=url[:120],
                evidence={'found': f'{report["pages"]} page(s), no text layer'},
                how_to_fix='Run OCR over it, or publish the original export '
                           'rather than a scan.'))

        if report.get('bytes', 0) > HEAVY_BYTES:
            megabytes = report['bytes'] / (1024 * 1024)
            found.append(emit(
                url=url, check='pdf_too_heavy', severity='warning',
                category=CATEGORY, issue='PDF Is Very Large',
                details=f'This file is {megabytes:.1f} MB. On a phone '
                        f'connection that is a long wait before anything '
                        f'appears, and many people will give up.',
                offending_value=f'{megabytes:.1f} MB',
                evidence={'found': f'{megabytes:.1f} MB',
                          'expected': f'under {HEAVY_BYTES // (1024 * 1024)} MB'},
                how_to_fix='Compress the images, or split it into sections.'))
    return found
