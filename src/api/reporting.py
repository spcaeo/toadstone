"""Reporting API: issues, pages, links, the link graph, sitemaps and diffs.

These are the endpoints a developer (or another agent) uses to act on a crawl
without re-fetching the site, as asked for in section 10.2 of the feature
request: filterable, paginated, stable in their ordering, and carrying the
evidence block so a finding can be fixed from the response alone.

    GET  /api/crawls/<id>/issues        ?severity=&check=&category=&url_prefix=
                                        &since_crawl=&status=&format=json|csv|ndjson
                                        &limit=&offset=
    GET  /api/crawls/<id>/pages         ?fields=&format=json|csv|ndjson&limit=&offset=
    GET  /api/crawls/<id>/page          ?url=&baseline=   one URL: row, headings
                                        tree, schema, links both ways, its
                                        issues, and a diff against the previous
                                        crawl
    GET  /api/crawls/<id>/links         ?src=&dst=&anchor=&internal=&format=
    GET  /api/crawls/<id>/graph         nodes + edges, or ?format=csv
    GET  /api/crawls/<id>/sitemaps      per-file summaries and coverage
    GET  /api/crawls/<id>/diff/<other>  {new, fixed, still_open} by stable issue id
    GET  /api/issues/accepted           what this user has muted
    POST /api/issues/<issue_id>/accept  {"reason": "..."}   mute it
    DELETE /api/issues/<issue_id>/accept                    un-mute it

The blueprint is built by main.py, which passes its own login decorator in, so
authentication stays defined in one place.
"""

import csv
import io
import json

from flask import Blueprint, Response, jsonify, request, session

from src import crawl_db

PAGE_FIELDS_DEFAULT = ('url', 'status_code', 'title', 'meta_description', 'h1',
                       'canonical_url', 'word_count', 'main_text_hash', 'depth')
ISSUE_FIELDS = ('id', 'crawl_id', 'url', 'check', 'type', 'category', 'issue',
                'details', 'evidence', 'how_to_fix', 'docs_url', 'first_seen_crawl',
                'status', 'accepted_reason', 'detected_at')
MAX_LIMIT = 100000

# The page-detail endpoint's fixed shapes. Links and issues come back whole
# rather than field-selected: the popup renders all of it.
LINK_FIELDS = ('source_url', 'target_url', 'anchor_text', 'is_internal', 'target_domain',
               'target_status', 'placement', 'rel', 'nofollow', 'link_target',
               'in_nav', 'in_main', 'in_heading')
PAGE_ISSUE_FIELDS = ('id', 'check', 'type', 'category', 'issue', 'details', 'evidence',
                     'how_to_fix', 'docs_url', 'first_seen_crawl', 'status',
                     'accepted_reason', 'detected_at')
# Columns crawled_urls stores as JSON text, and what an absent one should be.
PAGE_JSON_LISTS = ('h2', 'h3', 'json_ld', 'images', 'hreflang', 'schema_org',
                   'redirects', 'linked_from', 'headings', 'heading_text_issues')
PAGE_JSON_DICTS = ('meta_tags', 'og_tags', 'twitter_tags', 'analytics')
# What `page.http` groups together for the popup's response panel.
HTTP_FIELDS = ('status_code', 'content_type', 'size', 'content_length', 'cache_control',
               'last_modified', 'etag', 'ttfb', 'response_time', 'render_time',
               'processing_time', 'javascript_rendered', 'error_type', 'crawled_at')


def create_blueprint(login_required):
    """Build the blueprint. `login_required` is main.py's own decorator."""
    api = Blueprint('reporting', __name__)

    def owned_crawl(crawl_id):
        """The crawl, or (None, error response) when it is not this user's."""
        crawl = crawl_db.get_crawl_by_id(crawl_id)
        if not crawl:
            return None, (jsonify({'success': False, 'error': 'Crawl not found'}), 404)
        user_id = session.get('user_id')
        if user_id and crawl.get('user_id') not in (None, user_id):
            return None, (jsonify({'success': False, 'error': 'Unauthorized'}), 403)
        return crawl, None

    @api.route('/api/crawls/<int:crawl_id>/issues')
    @login_required
    def crawl_issues(crawl_id):
        crawl, error = owned_crawl(crawl_id)
        if error:
            return error

        rows = crawl_db.load_crawl_issues(crawl_id)
        accepted = crawl_db.get_accepted_issues(session.get('user_id'),
                                                crawl.get('base_domain'))
        for row in rows:
            if row.get('id') in accepted:
                row['status'] = 'accepted'
                row['accepted_reason'] = accepted[row['id']].get('reason')

        rows = _filter_issues(rows, request.args)

        since = request.args.get('since_crawl', type=int)
        if since:
            from src.core.crawl_diff import regression_report
            report = regression_report(since, crawl_id)
            new_ids = {issue.get('id') for issue in report['new']}
            rows = [row for row in rows if row.get('id') in new_ids]

        # Stable ordering: severity, then check, then URL
        order = {'error': 0, 'warning': 1, 'info': 2}
        rows.sort(key=lambda r: (order.get(r.get('type'), 3), r.get('check') or '',
                                 r.get('url') or ''))
        return _respond(rows, ISSUE_FIELDS, 'issues', crawl_id)

    @api.route('/api/crawls/<int:crawl_id>/pages')
    @login_required
    def crawl_pages(crawl_id):
        crawl, error = owned_crawl(crawl_id)
        if error:
            return error

        requested = request.args.get('fields', '')
        fields = tuple(f.strip() for f in requested.split(',') if f.strip()) or PAGE_FIELDS_DEFAULT
        rows = crawl_db.load_crawled_urls(crawl_id)

        prefix = request.args.get('url_prefix')
        if prefix:
            rows = [row for row in rows if (row.get('url') or '').startswith(prefix)]
        status = request.args.get('status_code', type=int)
        if status:
            rows = [row for row in rows if row.get('status_code') == status]

        rows.sort(key=lambda r: r.get('url') or '')
        return _respond(rows, fields, 'pages', crawl_id)

    @api.route('/api/crawls/<int:crawl_id>/page')
    @login_required
    def crawl_page_detail(crawl_id):
        """Everything about one URL on one screen, plus how it changed.

        Section 10.3's page detail: the stored row with its headings tree and
        schema blocks, the links pointing in and out of it, every issue filed
        against it, and a diff of this page against the previous crawl.

            GET /api/crawls/<id>/page?url=<encoded>&baseline=<crawl id>

        Every lookup is by (crawl_id, url) against an existing index, so a
        500k-page crawl costs the same as a two-page one: no endpoint below
        loads a whole crawl's rows to answer for a single URL.
        """
        crawl, error = owned_crawl(crawl_id)
        if error:
            return error

        url = request.args.get('url', '').strip()
        if not url:
            return jsonify({'success': False, 'error': 'A url parameter is required'}), 400

        page = _page_row(crawl_id, url)
        if not page:
            return jsonify({'success': False,
                            'error': f'URL not found in crawl {crawl_id}',
                            'crawl_id': crawl_id, 'url': url}), 404

        inbound, outbound = _page_links(crawl_id, url)
        issues = _page_issues(crawl_id, url)

        accepted = crawl_db.get_accepted_issues(session.get('user_id'),
                                                crawl.get('base_domain'))
        for issue in issues:
            if issue.get('id') in accepted:
                issue['status'] = 'accepted'
                issue['accepted_reason'] = accepted[issue['id']].get('reason')

        baseline_id, note = _baseline_for(crawl, request.args.get('baseline'))
        diff = None
        if baseline_id:
            try:
                diff = _page_diff(baseline_id, url, page, inbound + outbound, issues)
            except Exception as exc:  # a broken baseline must not lose the page
                note = f'Diff unavailable: {type(exc).__name__}: {exc}'

        body = {
            'success': True,
            'crawl_id': crawl_id,
            'url': url,
            'page': _shape_page(page),
            'links': {
                'inbound': [{f: row.get(f) for f in LINK_FIELDS} for row in inbound],
                'outbound': [{f: row.get(f) for f in LINK_FIELDS} for row in outbound],
                'counts': {
                    'inbound': len(inbound),
                    'outbound': len(outbound),
                    'outbound_internal': sum(1 for r in outbound if r.get('is_internal')),
                    'outbound_external': sum(1 for r in outbound if not r.get('is_internal')),
                },
            },
            'issues': [{f: issue.get(f) for f in PAGE_ISSUE_FIELDS} for issue in issues],
            'diff': diff,
        }
        if note:
            body['note'] = note
        return jsonify(body)

    @api.route('/api/crawls/<int:crawl_id>/links')
    @login_required
    def crawl_links(crawl_id):
        crawl, error = owned_crawl(crawl_id)
        if error:
            return error

        rows = crawl_db.load_crawl_links(crawl_id)
        src = request.args.get('src')
        dst = request.args.get('dst')
        anchor = request.args.get('anchor')
        internal = request.args.get('internal')
        if src:
            rows = [r for r in rows if (r.get('source_url') or '').startswith(src)]
        if dst:
            rows = [r for r in rows if (r.get('target_url') or '').startswith(dst)]
        if anchor:
            needle = anchor.lower()
            rows = [r for r in rows if needle in (r.get('anchor_text') or '').lower()]
        if internal is not None:
            want = internal.lower() in ('1', 'true', 'yes')
            rows = [r for r in rows if bool(r.get('is_internal')) is want]

        rows.sort(key=lambda r: ((r.get('source_url') or ''), (r.get('target_url') or '')))
        fields = ('source_url', 'target_url', 'anchor_text', 'is_internal', 'target_status',
                  'placement', 'rel', 'nofollow', 'link_target', 'in_nav', 'in_main', 'in_heading')
        return _respond(rows, fields, 'links', crawl_id)

    @api.route('/api/crawls/<int:crawl_id>/graph')
    @login_required
    def crawl_graph(crawl_id):
        crawl, error = owned_crawl(crawl_id)
        if error:
            return error

        from src.core import link_graph
        pages = crawl_db.load_crawled_urls(crawl_id)
        links = crawl_db.load_crawl_links(crawl_id)
        graph = link_graph.build_graph(pages, links, crawl.get('base_url') or '')
        link_graph.click_depth(graph, crawl.get('base_url'))

        if request.args.get('format') == 'csv':
            nodes_csv, edges_csv = link_graph.export(graph, fmt='csv')
            return Response(f'# nodes\n{nodes_csv}\n# edges\n{edges_csv}',
                            mimetype='text/csv',
                            headers={'Content-Disposition':
                                     f'attachment; filename=crawl-{crawl_id}-graph.csv'})

        payload = link_graph.export(graph, fmt='json')
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload['clusters'] = link_graph.cluster_summary(graph)
        return jsonify({'success': True, 'crawl_id': crawl_id, **payload})

    @api.route('/api/crawls/<int:crawl_id>/sitemaps')
    @login_required
    def crawl_sitemaps(crawl_id):
        crawl, error = owned_crawl(crawl_id)
        if error:
            return error
        return jsonify({
            'success': True,
            'crawl_id': crawl_id,
            'files': crawl.get('sitemap_files') or [],
            'coverage': crawl.get('sitemap_coverage') or {},
        })

    @api.route('/api/crawls/<int:crawl_id>/diff/<int:other_id>')
    @login_required
    def crawl_diff(crawl_id, other_id):
        for cid in (crawl_id, other_id):
            _, error = owned_crawl(cid)
            if error:
                return error

        from src.core.crawl_diff import regression_report
        report = regression_report(other_id, crawl_id)
        return jsonify({'success': True, 'from_crawl': other_id, 'to_crawl': crawl_id, **report})

    @api.route('/api/rules')
    @login_required
    def read_rules():
        """The rules file for a domain (or the shared one), plus its problems."""
        from src.core import rules_engine
        domain = request.args.get('domain', '')
        loaded = rules_engine.load_site_rules(domain)
        path = loaded.get('path') or ''
        text = ''
        if path:
            try:
                with open(path) as handle:
                    text = handle.read()
            except OSError as error:
                return jsonify({'success': False, 'error': str(error)}), 500
        return jsonify({'success': True, 'path': path, 'text': text,
                        'rules': [rule.get('raw', {}) for rule in loaded.get('rules', [])],
                        'errors': loaded.get('errors', [])})

    @api.route('/api/rules', methods=['PUT'])
    @login_required
    def write_rules():
        """Save a rules file, refusing one that does not parse."""
        import os
        from src.core import rules_engine

        payload = request.get_json(silent=True) or {}
        text = payload.get('text', '')
        domain = (payload.get('domain') or '').strip()

        loaded = rules_engine.load_rules(text)
        if loaded.get('errors'):
            return jsonify({'success': False, 'errors': loaded['errors']}), 400

        safe = ''.join(c for c in domain if c.isalnum() or c in '.-')
        folder = os.path.join(rules_engine.DATA_DIR, 'rules')
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f'{safe}.yaml') if safe else rules_engine.DEFAULT_RULES_PATH
        try:
            with open(path, 'w') as handle:
                handle.write(text)
        except OSError as error:
            return jsonify({'success': False, 'error': str(error)}), 500
        return jsonify({'success': True, 'path': path, 'rules': len(loaded.get('rules', []))})

    @api.route('/api/rules/preview', methods=['POST'])
    @login_required
    def preview_rules():
        """What a rules file would report against a crawl, before saving it."""
        from src.core import rules_engine

        payload = request.get_json(silent=True) or {}
        crawl_id = payload.get('crawl_id')
        if not crawl_id:
            return jsonify({'success': False, 'error': 'A crawl id is required'}), 400
        _, error = owned_crawl(int(crawl_id))
        if error:
            return error

        loaded = rules_engine.load_rules(payload.get('text', ''))
        if loaded.get('errors'):
            return jsonify({'success': False, 'errors': loaded['errors']}), 400

        pages = crawl_db.load_crawled_urls(int(crawl_id))
        links = crawl_db.load_crawl_links(int(crawl_id))
        results = []
        for rule in loaded.get('rules', []):
            outcome = rules_engine.preview_rule(rule, pages, links, limit=10)
            results.append({'id': rule.get('raw', {}).get('id') or rule.get('id'),
                            'total': outcome['total'],
                            'scanned_pages': outcome['scanned_pages'],
                            'notes': outcome['notes'],
                            'issues': outcome['issues']})
        return jsonify({'success': True, 'crawl_id': crawl_id, 'pages': len(pages),
                        'results': results})

    @api.route('/api/issues/accepted')
    @login_required
    def accepted_issues():
        accepted = crawl_db.get_accepted_issues(session.get('user_id'),
                                                request.args.get('base_domain'))
        return jsonify({'success': True, 'accepted': list(accepted.values())})

    @api.route('/api/issues/<issue_id>/accept', methods=['POST'])
    @login_required
    def accept_issue(issue_id):
        payload = request.get_json(silent=True) or {}
        reason = (payload.get('reason') or '').strip()
        if not reason:
            return jsonify({'success': False, 'error': 'A reason is required'}), 400
        ok = crawl_db.accept_issue(issue_id, session.get('user_id'), reason,
                                   payload.get('base_domain'))
        return jsonify({'success': ok, 'id': issue_id, 'reason': reason})

    @api.route('/api/issues/<issue_id>/accept', methods=['DELETE'])
    @login_required
    def unaccept_issue(issue_id):
        ok = crawl_db.unaccept_issue(issue_id, session.get('user_id'))
        return jsonify({'success': ok, 'id': issue_id})

    return api


def _filter_issues(rows, args):
    severity = args.get('severity')
    if severity:
        wanted = {s.strip() for s in severity.split(',') if s.strip()}
        rows = [r for r in rows if r.get('type') in wanted]
    for key, field in (('check', 'check'), ('category', 'category'), ('status', 'status')):
        value = args.get(key)
        if value:
            wanted = {v.strip() for v in value.split(',') if v.strip()}
            rows = [r for r in rows if (r.get(field) or '') in wanted]
    prefix = args.get('url_prefix')
    if prefix:
        rows = [r for r in rows if (r.get('url') or '').startswith(prefix)]
    if args.get('include_accepted', 'false').lower() not in ('1', 'true', 'yes'):
        rows = [r for r in rows if r.get('status') != 'accepted']
    return rows


def _respond(rows, fields, name, crawl_id):
    """Serialise rows as JSON, CSV or NDJSON, honouring limit/offset."""
    offset = max(request.args.get('offset', 0, type=int), 0)
    limit = request.args.get('limit', type=int)
    total = len(rows)
    if limit is not None:
        rows = rows[offset:offset + min(limit, MAX_LIMIT)]
    elif offset:
        rows = rows[offset:]

    shaped = [{field: row.get(field) for field in fields} for row in rows]
    fmt = (request.args.get('format') or 'json').lower()

    if fmt == 'ndjson':
        def stream():
            for row in shaped:
                yield json.dumps(row, default=str) + '\n'
        return Response(stream(), mimetype='application/x-ndjson')

    if fmt == 'csv':
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(fields), extrasaction='ignore')
        writer.writeheader()
        for row in shaped:
            writer.writerow({k: (json.dumps(v, default=str) if isinstance(v, (dict, list)) else v)
                             for k, v in row.items()})
        return Response(buffer.getvalue(), mimetype='text/csv',
                        headers={'Content-Disposition':
                                 f'attachment; filename=crawl-{crawl_id}-{name}.csv'})

    return jsonify({'success': True, 'crawl_id': crawl_id, 'total': total,
                    'offset': offset, 'count': len(shaped), name: shaped})


# ---------------------------------------------------------------------------
# page detail: narrow reads, the headings tree, and the per-URL diff
# ---------------------------------------------------------------------------

def _page_row(crawl_id, url):
    """The one crawled_urls row for a URL, decoded, or None.

    Goes straight at the (crawl_id, url) index rather than through
    ``crawl_db.load_crawled_urls``, which loads the whole crawl: answering for
    one URL of a 500k-page crawl must not cost 500k rows.
    """
    rows = _fetch_all('SELECT * FROM crawled_urls WHERE crawl_id = ? AND url = ? LIMIT 1',
                      (crawl_id, url))
    return _decode_page(rows[0]) if rows else None


def _page_links(crawl_id, url):
    """(inbound, outbound) link rows for a URL, both by an existing index."""
    inbound = _fetch_all('SELECT * FROM crawl_links WHERE crawl_id = ? AND target_url = ?',
                         (crawl_id, url))
    outbound = _fetch_all('SELECT * FROM crawl_links WHERE crawl_id = ? AND source_url = ?',
                          (crawl_id, url))
    inbound.sort(key=lambda r: (r.get('source_url') or '', r.get('anchor_text') or ''))
    outbound.sort(key=lambda r: (r.get('target_url') or '', r.get('anchor_text') or ''))
    return inbound, outbound


def _page_issues(crawl_id, url):
    """This URL's issues, shaped exactly as ``load_crawl_issues`` shapes them."""
    rows = _fetch_all('SELECT * FROM crawl_issues WHERE crawl_id = ? AND url = ?',
                      (crawl_id, url))
    issues = []
    for issue in rows:
        issue['row_id'] = issue.get('id')
        issue['id'] = issue.pop('issue_key', None)
        issue['check'] = issue.pop('check_slug', None)
        try:
            issue['evidence'] = json.loads(issue['evidence']) if issue.get('evidence') else {}
        except (TypeError, ValueError):
            issue['evidence'] = {}
        issues.append(issue)
    order = {'error': 0, 'warning': 1, 'info': 2}
    issues.sort(key=lambda i: (order.get(i.get('type'), 3), i.get('check') or ''))
    return issues


def _fetch_all(query, params):
    """Run one narrow query and hand back plain dicts. Never raises."""
    try:
        with crawl_db.get_db() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
    except Exception as error:
        print(f'Page detail query failed: {error}')
        return []


def _decode_page(row):
    """Decode a crawled_urls row's JSON columns, as load_crawled_urls does."""
    for field in PAGE_JSON_LISTS + PAGE_JSON_DICTS:
        if row.get(field):
            try:
                row[field] = json.loads(row[field])
            except (TypeError, ValueError):
                row[field] = [] if field in PAGE_JSON_LISTS else {}
    return row


def _shape_page(page):
    """The stored row, plus the headings tree and a grouped HTTP block."""
    shaped = dict(page)
    flat = shaped.get('headings') or _headings_from_columns(shaped)
    shaped['headings_flat'] = flat
    shaped['headings'] = _headings_tree(flat)
    for field in PAGE_JSON_LISTS:
        if shaped.get(field) in (None, ''):
            shaped[field] = []
    for field in PAGE_JSON_DICTS:
        if shaped.get(field) in (None, ''):
            shaped[field] = {}
    shaped['http'] = {field: page.get(field) for field in HTTP_FIELDS}
    shaped['http']['redirects'] = shaped['redirects']
    return shaped


def _headings_from_columns(page):
    """A flat heading list for rows crawled before `headings` was stored.

    h1/h2/h3 are all that was kept then, and their interleaving is lost, so
    this is a fallback ordering (every h1, then every h2, then every h3) rather
    than document order.
    """
    flat = []

    def add(level, text):
        if text:
            flat.append({'level': level, 'text': text, 'is_visible': True,
                         'dom_order': len(flat)})

    add(1, page.get('h1'))
    for level in (2, 3):
        value = page.get(f'h{level}')
        for text in (value if isinstance(value, list) else [value]):
            add(level, text)
    return flat


def _headings_tree(flat):
    """Nest a flat h1-h6 list by level, in document order.

    A heading is a child of the closest preceding heading of a lower level, so
    a page that jumps h2 -> h4 still nests rather than dropping the h4, and a
    page that opens on an h3 keeps it at the root.
    """
    tree = []
    stack = []
    for heading in sorted(flat or [], key=lambda h: h.get('dom_order') or 0):
        try:
            level = int(heading.get('level') or 1)
        except (TypeError, ValueError):
            level = 1
        node = {'level': level, 'text': heading.get('text') or '',
                'is_visible': heading.get('is_visible', True),
                'dom_order': heading.get('dom_order'), 'children': []}
        while stack and stack[-1]['level'] >= level:
            stack.pop()
        (stack[-1]['children'] if stack else tree).append(node)
        stack.append(node)
    return tree


def _baseline_for(crawl, requested):
    """(baseline crawl id, note). An unusable baseline costs the diff, not the page."""
    from src.core.crawl_diff import previous_crawl

    if requested not in (None, ''):
        try:
            baseline_id = int(requested)
        except (TypeError, ValueError):
            return None, f'Baseline "{requested}" is not a crawl id; no diff was computed.'
        if baseline_id == crawl.get('id'):
            return None, 'Baseline is this crawl itself; no diff was computed.'
        baseline = crawl_db.get_crawl_by_id(baseline_id)
        if not baseline:
            return None, f'Baseline crawl {baseline_id} does not exist; no diff was computed.'
        user_id = session.get('user_id')
        if user_id and baseline.get('user_id') not in (None, user_id):
            return None, (f'Baseline crawl {baseline_id} belongs to another account; '
                          'no diff was computed.')
        return baseline_id, None

    previous = previous_crawl(crawl.get('base_domain'), crawl.get('id'), crawl.get('user_id'))
    if not previous:
        return None, 'No earlier completed crawl of this domain to compare against.'
    return previous['id'], None


def _page_diff(baseline_id, url, page, links, issues):
    """How this one page changed between the baseline crawl and this one.

    ``diff_pages`` and ``diff_links`` are pure over row lists, so this feeds
    them just this URL's rows: the same differ src/core/crawl_diff.py uses for
    a whole crawl, narrowed at its input rather than its output, which is what
    keeps the endpoint cheap on a large crawl.

    The issue sections apply that module's identity rule - an issue is the same
    issue in both crawls when its stable id matches - to the two crawls' rows
    for this URL alone. ``regression_report`` answers the same question for a
    whole crawl, and loading two crawls' entire issue tables to report on one
    page would undo the point of this endpoint.
    """
    from src.core.crawl_diff import diff_links, diff_pages

    baseline = crawl_db.get_crawl_by_id(baseline_id) or {}
    before = _page_row(baseline_id, url)
    before_issues = _page_issues(baseline_id, url)

    current_ids = {issue.get('id') for issue in issues if issue.get('id')}
    before_ids = {issue.get('id') for issue in before_issues if issue.get('id')}

    diff = {
        'baseline_crawl': baseline_id,
        'baseline_started_at': baseline.get('started_at'),
        'baseline_completed_at': baseline.get('completed_at'),
        'introduced': before is None,
        'status_changed': None,
        'title_changed': None,
        'meta_description_changed': None,
        'canonical_changed': None,
        'word_count_delta': None,
        'text_changed': False,
        'similarity': None,
        'removed_sentences': [],
        'added_sentences': [],
        'links_removed': [],
        'links_added': [],
        'issues_new': _shape_issues(issues),
        'issues_fixed': _shape_issues([i for i in before_issues
                                       if i.get('id') not in current_ids]),
        'issues_still_open': [],
    }

    if before is None:
        # A page that did not exist in the baseline has removed nothing and
        # every issue on it is new: say so rather than reporting empty deltas.
        return diff

    diff['status_changed'] = _field_change(before, page, 'status_code')
    diff['title_changed'] = _field_change(before, page, 'title')
    diff['meta_description_changed'] = _field_change(before, page, 'meta_description')
    diff['canonical_changed'] = _field_change(before, page, 'canonical_url')
    diff['word_count_delta'] = (page.get('word_count') or 0) - (before.get('word_count') or 0)

    page_diff = diff_pages([before], [page])
    if page_diff['text_changed']:
        changed = page_diff['text_changed'][0]
        diff['text_changed'] = True
        diff['similarity'] = changed.get('similarity')
        diff['removed_sentences'] = changed.get('removed_sentences') or []
        diff['added_sentences'] = changed.get('added_sentences') or []

    # Both crawls' link rows are restricted to the edges touching this URL, and
    # an edge is in one restriction exactly when it is in the other's crawl, so
    # the comparison is the same one diff_links makes over a whole crawl.
    before_in, before_out = _page_links(baseline_id, url)
    link_diff = diff_links(before_in + before_out, links)
    diff['links_removed'] = _tag_direction(link_diff['links_removed'], url)
    diff['links_added'] = _tag_direction(link_diff['links_added'], url)

    diff['issues_new'] = _shape_issues([i for i in issues if i.get('id') not in before_ids])
    diff['issues_still_open'] = _shape_issues([i for i in issues if i.get('id') in before_ids])
    return diff


def _field_change(before, after, field):
    """{'from': old, 'to': new} when a field moved, else None."""
    was, now = before.get(field), after.get(field)
    if was == now or (was in (None, '') and now in (None, '')):
        return None
    return {'from': was, 'to': now}


def _tag_direction(edges, url):
    """Say which side of this page each changed link sat on."""
    for edge in edges:
        edge['direction'] = 'outbound' if edge.get('source_url') == url else 'inbound'
    return edges


def _shape_issues(issues):
    return [{field: issue.get(field) for field in PAGE_ISSUE_FIELDS} for issue in issues]
