"""Server log analysis, joined to a crawl.

`src/core/log_analysis.py` reads Apache, Nginx, W3C and JSON access logs,
verifies that a request claiming to be Googlebot really came from Google, and
compares what search engines actually requested against what the crawler
found. It has four formats, DNS verification and thirty-six tests.

Until this file existed, nothing called it. No route, no crawl hook, no UI —
the module was referenced by its own test file and by nothing else, which
meant a capability the project claimed was, in practice, unreachable. This is
the smallest honest way to make it real: upload a log, pick a crawl, get the
findings.

    POST /api/logs/analyse     a log file plus a crawl id
    GET  /api/logs/formats     what it can read, for the upload form

The log is read, analysed and discarded. It is never written to disk and
never stored in the crawl database: an access log is a list of who visited a
site and from where, and it belongs to whoever uploaded it, not to us.
"""

from flask import Blueprint, jsonify, request

from src.core import log_analysis

# Enough to be useful, bounded so one upload cannot exhaust memory. A log
# larger than this should be sampled or split by the person who has it.
MAX_BYTES = 64 * 1024 * 1024
MAX_LINES = 500_000


def create_blueprint(login_required, load_crawl_pages, save_crawl_issues=None):
    """Build the blueprint.

    `load_crawl_pages(crawl_id)` returns the crawled rows for a crawl, or an
    empty list. `save_crawl_issues(crawl_id, issues)` writes findings onto a
    crawl, and is optional: without it the route still answers, it simply
    cannot keep what it found. Both are injected so this file never imports
    the database directly and can be tested with stubs.
    """
    api = Blueprint('logs', __name__)

    @api.route('/api/logs/formats')
    @login_required
    def formats():
        return jsonify({
            'success': True,
            'formats': [
                {'id': 'combined', 'name': 'Apache / Nginx combined',
                 'example': '127.0.0.1 - - [18/Oct/2026:10:00:00 +0000] '
                            '"GET /a HTTP/1.1" 200 120 "-" "Mozilla/5.0"'},
                {'id': 'w3c', 'name': 'W3C extended (IIS)',
                 'example': '#Fields: date time cs-uri-stem sc-status'},
                {'id': 'json', 'name': 'JSON lines',
                 'example': '{"remote_addr": "127.0.0.1", "request": "GET /a", '
                            '"status": 200, "user_agent": "Mozilla/5.0"}'},
            ],
            'max_bytes': MAX_BYTES,
            'note': ('The file is read, analysed and discarded. Nothing is '
                     'written to disk or stored with the crawl.'),
        })

    @api.route('/api/logs/analyse', methods=['POST'])
    @login_required
    def analyse():
        upload = request.files.get('log')
        if upload is None:
            return jsonify({'success': False,
                            'error': 'No log file was uploaded.'}), 400

        raw = upload.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return jsonify({
                'success': False,
                'error': f'That log is larger than {MAX_BYTES // (1024 * 1024)}MB. '
                         f'Split it, or upload a recent slice of it.'}), 413
        if not raw:
            return jsonify({'success': False, 'error': 'That file is empty.'}), 400

        text = raw.decode('utf-8', 'replace')
        lines = text.splitlines()[:MAX_LINES]

        crawl_id = request.form.get('crawl_id', type=int)
        pages = load_crawl_pages(crawl_id) if crawl_id else []
        base_url = ''
        for page in pages:
            url = page.get('url') or ''
            if url.startswith(('http://', 'https://')):
                base_url = '/'.join(url.split('/')[:3])
                break

        parsed = log_analysis.parse_lines(lines)
        issues = log_analysis.log_issues(parsed, pages, base_url)

        # Keeping the findings is opt-in, and off by default. A log tells you
        # things no crawl can - which pages search engines actually fetch,
        # which they never ask for - and those belong beside the crawl's own
        # issues rather than in a response someone has to keep open. But
        # writing to a crawl is not something a read-only look should do
        # silently, so the caller asks for it.
        kept = False
        if issues and save_crawl_issues and crawl_id and \
                str(request.form.get('keep', '')).lower() in ('1', 'true', 'yes'):
            try:
                save_crawl_issues(crawl_id, issues)
                kept = True
            except Exception as exc:
                return jsonify({
                    'success': False,
                    'error': f'The log was read but the findings could not be '
                             f'saved to crawl {crawl_id} ({type(exc).__name__}).',
                    'issues': issues,
                }), 500

        return jsonify({
            'success': True,
            'lines_read': parsed.get('total', 0),
            'lines_unparsed': parsed.get('unparsed', 0),
            'requests_understood': len(parsed.get('entries') or []),
            'crawl_id': crawl_id,
            'pages_compared': len(pages),
            'issues': issues,
            'kept': kept,
            'truncated': len(text.splitlines()) > MAX_LINES,
        })

    return api
