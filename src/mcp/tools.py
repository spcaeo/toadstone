"""The tools this MCP server exposes, and what each one hands back.

Every tool is a thin call onto an existing Toadstone HTTP route (see
`src/api/reporting.py`, `src/api/exports/` and `src/api/checks.py`), narrowed
in two ways that matter when the caller is a language model rather than a
browser:

    it is trimmed      `GET /api/crawls/<id>/page` returns 52 columns including
                       the page's whole main text. A model does not need the
                       text to fix a missing canonical, and paying for it on
                       every call crowds out the issues.
    it is bounded      every list takes a limit and reports the total it came
                       from, so a 20,000-issue crawl answers in a readable page
                       rather than filling the context window.

Two tools write (`start_crawl`, `accept_issue`) and both go through
`Guardrails` first. Nothing here reaches past the API: crawl ownership is the
API's own check, made against the session the client carries.
"""

import difflib

from src.mcp.client import ApiError
from src.mcp.guardrails import GuardrailError

# Mirrors DATASETS in src/api/exports/datasets.py. Kept as a literal so that
# tools/list works without importing the export stack; tests/mcp_tests.py
# asserts the two still agree.
DATASETS = ('issues', 'pages', 'links', 'graph-nodes', 'graph-edges',
            'sitemap-files', 'sitemap-coverage', 'duplicates', 'redirects',
            'broken-links', 'headings', 'images')
TEXT_FORMATS = ('csv', 'json', 'ndjson')
BINARY_FORMATS = ('xlsx', 'zip')

SEVERITIES = ('error', 'warning', 'info')

CRAWL_FIELDS = ('id', 'base_url', 'base_domain', 'status', 'started_at',
                'completed_at', 'urls_crawled', 'urls_discovered',
                'max_depth_reached', 'can_resume')
# What a page is reduced to. Deliberately excludes main_text, json_ld_raw and
# linked_from: all three are large, and all three are available in full from
# the HTTP API for anyone who actually needs them.
PAGE_FIELDS = ('url', 'status_code', 'content_type', 'title', 'meta_description',
               'h1', 'canonical_url', 'lang', 'word_count', 'main_word_count',
               'depth', 'is_internal', 'javascript_rendered', 'redirects',
               'hreflang', 'main_text_hash')
ISSUE_FIELDS = ('id', 'url', 'check', 'type', 'category', 'issue', 'details',
                'evidence', 'how_to_fix', 'docs_url', 'first_seen_crawl', 'status',
                'accepted_reason')

MAX_LIMIT = 500


class Tools:
    """The tool implementations, bound to one client and one set of guardrails."""

    def __init__(self, client, guardrails, settings):
        self.client = client
        self.guardrails = guardrails
        self.settings = settings

    # -- crawls -------------------------------------------------------------

    def list_crawls(self, limit=20, status=None):
        limit = _bounded(limit, 20)
        body = self.client.get_json('/api/crawls/list',
                                    {'limit': limit, 'status': status})
        crawls = [_pick(row, CRAWL_FIELDS) for row in body.get('crawls') or []]
        return {'total': body.get('total'), 'count': len(crawls), 'crawls': crawls}

    def crawl_status(self, crawl_id):
        return self.client.get_json('/api/crawl_status',
                                    {'crawl_id': _crawl_id(crawl_id)})

    def start_crawl(self, url, render=False, max_urls=None, max_depth=None):
        self.guardrails.check_write('start_crawl')
        url = self.guardrails.check_host(url)
        payload = {'url': url, 'render': bool(render),
                   'max_urls': self.guardrails.check_max_urls(max_urls)}
        depth = self.guardrails.check_max_depth(max_depth)
        if depth is not None:
            payload['max_depth'] = depth

        body = self.client.post_json('/api/crawls', payload)
        return {'crawl_id': body.get('crawl_id'),
                'url': url,
                'max_urls': payload['max_urls'],
                'render': payload['render'],
                'next': 'Poll crawl_status with this crawl_id until status is '
                        '"completed", then call list_issues.'}

    # -- issues -------------------------------------------------------------

    def list_issues(self, crawl_id, severity=None, check=None, category=None,
                    url_prefix=None, status=None, since_crawl=None,
                    include_accepted=False, limit=50, offset=0):
        crawl_id = _crawl_id(crawl_id)
        limit = _bounded(limit, 50)
        severity = _csv(severity)
        for value in severity.split(','):
            if value and value not in SEVERITIES:
                raise ToolError(f'severity must be one of {", ".join(SEVERITIES)}; '
                                f'got {value!r}.')
        params = {'severity': severity or None, 'check': _csv(check) or None,
                  'category': _csv(category) or None, 'url_prefix': url_prefix or None,
                  'status': _csv(status) or None, 'since_crawl': since_crawl or None,
                  'include_accepted': 'true' if include_accepted else None,
                  'limit': limit, 'offset': max(int(offset or 0), 0)}
        body = self.client.get_json(f'/api/crawls/{crawl_id}/issues', params)
        issues = [_pick(row, ISSUE_FIELDS) for row in body.get('issues') or []]
        return {'crawl_id': crawl_id, 'total': body.get('total'),
                'offset': body.get('offset'), 'count': len(issues),
                'by_severity': _tally(issues, 'type'),
                'by_check': _tally(issues, 'check'),
                'issues': issues}

    def issue_detail(self, crawl_id, issue_id):
        """One issue by its stable id, with the page it sits on.

        The HTTP API has no `GET /api/issues/<id>`: an issue id is stable
        across crawls but not unique across them, so a lookup needs the crawl
        it belongs to. This fetches that crawl's issues, finds the id, and
        attaches the page summary from the page-detail endpoint.
        """
        crawl_id = _crawl_id(crawl_id)
        issue_id = (issue_id or '').strip()
        if not issue_id:
            raise ToolError('An issue_id is required.')

        body = self.client.get_json(f'/api/crawls/{crawl_id}/issues',
                                    {'include_accepted': 'true'})
        match = next((row for row in body.get('issues') or []
                      if row.get('id') == issue_id), None)
        if match is None:
            raise ToolError(
                f'No issue {issue_id!r} in crawl {crawl_id}. Issue ids are stable '
                'per (url, check, offending value) but are scoped to a crawl; '
                'list_issues for this crawl to find the right one.')

        detail = {'crawl_id': crawl_id, 'issue': _pick(match, ISSUE_FIELDS)}
        try:
            page = self.page_detail(crawl_id, match.get('url'), include_links=False)
            detail['page'] = page.get('page')
            detail['page_issue_count'] = page.get('issue_count')
        except (ApiError, ToolError) as error:
            detail['page'] = None
            detail['page_note'] = str(error)
        return detail

    def accept_issue(self, issue_id, reason, base_domain=None):
        self.guardrails.check_write('accept_issue')
        issue_id = (issue_id or '').strip()
        reason = (reason or '').strip()
        if not issue_id:
            raise ToolError('An issue_id is required.')
        if not reason:
            raise ToolError('A reason is required: accepting an issue without one '
                            'leaves nobody able to tell why it was muted.')
        body = self.client.post_json(f'/api/issues/{issue_id}/accept',
                                     {'reason': reason, 'base_domain': base_domain})
        return {'accepted': bool(body.get('success')), 'issue_id': issue_id,
                'reason': reason,
                'note': 'Accepted issues are hidden from list_issues unless '
                        'include_accepted is true.'}

    # -- pages and diffs ----------------------------------------------------

    def page_detail(self, crawl_id, url, baseline=None, include_links=True,
                    link_limit=25):
        crawl_id = _crawl_id(crawl_id)
        url = (url or '').strip()
        if not url:
            raise ToolError('A url is required.')

        body = self.client.get_json(f'/api/crawls/{crawl_id}/page',
                                    {'url': url, 'baseline': baseline})
        page = body.get('page') or {}
        issues = [_pick(row, ISSUE_FIELDS) for row in body.get('issues') or []]
        shaped = {
            'crawl_id': crawl_id,
            'url': body.get('url'),
            'page': _page_summary(page),
            'issue_count': len(issues),
            'issues': issues,
            'link_counts': (body.get('links') or {}).get('counts'),
            'diff': _diff_summary(body.get('diff')),
        }
        if body.get('note'):
            shaped['note'] = body['note']
        if include_links:
            links = body.get('links') or {}
            limit = _bounded(link_limit, 25)
            shaped['links'] = {
                'inbound': (links.get('inbound') or [])[:limit],
                'outbound': (links.get('outbound') or [])[:limit],
                'truncated': max(len(links.get('inbound') or []),
                                 len(links.get('outbound') or [])) > limit,
            }
        return shaped

    def regression_diff(self, baseline_crawl_id, candidate_crawl_id, limit=25):
        baseline = _crawl_id(baseline_crawl_id)
        candidate = _crawl_id(candidate_crawl_id)
        if baseline == candidate:
            raise ToolError('baseline_crawl_id and candidate_crawl_id are the same '
                            f'crawl ({baseline}); there is nothing to compare.')
        limit = _bounded(limit, 25)
        body = self.client.get_json(f'/api/crawls/{candidate}/diff/{baseline}')

        sections = {}
        for name in ('new', 'fixed', 'still_open'):
            rows = body.get(name) or []
            sections[name] = {
                'total': len(rows),
                'returned': min(len(rows), limit),
                'by_check': _tally(rows, 'check'),
                'issues': [_pick(row, ISSUE_FIELDS) for row in rows[:limit]],
            }
        return {'baseline_crawl': baseline, 'candidate_crawl': candidate,
                'counts': body.get('counts'),
                'note': 'Issues are matched by their stable id, which is a hash of '
                        '(url, check, offending value). Two crawls of one site served '
                        'from different hosts therefore read as entirely new and '
                        'entirely fixed.',
                **sections}

    # -- exports and reference ----------------------------------------------

    def export_dataset(self, crawl_id, dataset, format='csv', limit=None,
                       severity=None, check=None, url_prefix=None, status_code=None):
        crawl_id = _crawl_id(crawl_id)
        dataset = (dataset or '').strip()
        if dataset not in DATASETS:
            raise ToolError(f'Unknown dataset {dataset!r}. The datasets are: '
                            f'{", ".join(DATASETS)}.')
        fmt = (format or 'csv').strip().lower()
        if fmt in BINARY_FORMATS:
            raise ToolError(
                f'{fmt} is a binary download and cannot be returned through MCP. '
                f'Use csv, json or ndjson here, or fetch '
                f'{self.settings.base_url}/api/crawls/{crawl_id}/export/{dataset}'
                f'?format={fmt} over HTTP.')
        if fmt not in TEXT_FORMATS:
            raise ToolError(f'format must be one of {", ".join(TEXT_FORMATS)}; '
                            f'got {fmt!r}.')

        params = {'format': fmt, 'severity': _csv(severity) or None,
                  'check': _csv(check) or None, 'url_prefix': url_prefix or None,
                  'status_code': status_code}
        body = self.client.get_text(f'/api/crawls/{crawl_id}/export/{dataset}',
                                    params, limit=self.settings.max_bytes)
        text = body['text']
        truncated = body['truncated']
        if limit and fmt == 'csv':
            text, truncated = _head_lines(text, _bounded(limit, 100) + 1, truncated)
        elif limit and fmt == 'ndjson':
            text, truncated = _head_lines(text, _bounded(limit, 100), truncated)

        return {'crawl_id': crawl_id, 'dataset': dataset, 'format': fmt,
                'truncated': truncated, 'data': text}

    def check_reference(self, check=None):
        body = self.client.get_json('/api/checks')
        rows = body.get('checks') or []
        if not check:
            return {'count': len(rows),
                    'note': 'Built from what this install has actually reported, so a '
                            'check that has never fired here is not listed.',
                    'checks': rows}

        slug = check.strip()
        match = next((row for row in rows if row.get('check') == slug), None)
        if match:
            return {'check': match}
        near = difflib.get_close_matches(slug, [row.get('check') or '' for row in rows],
                                         n=5, cutoff=0.5)
        raise ToolError(
            f'No check {slug!r} has been reported in this install.'
            + (f' Did you mean: {", ".join(near)}?' if near else
               ' Call check_reference with no argument for the list.'))


class ToolError(Exception):
    """A tool argument the caller can fix. Reported as a tool error, not a crash."""


# ---------------------------------------------------------------------------
# the schemas tools/list hands to the client
# ---------------------------------------------------------------------------

def _schema(properties, required=()):
    return {'type': 'object', 'properties': properties,
            'required': list(required), 'additionalProperties': False}


_STRING = {'type': 'string'}
_INT = {'type': 'integer'}
_BOOL = {'type': 'boolean'}


def _crawl_id_prop(name='crawl_id'):
    return {name: {'type': 'integer',
                   'description': 'A crawl id, as list_crawls reports it.'}}


SPECS = (
    {
        'name': 'list_crawls',
        'write': False,
        'description': 'Recent crawls in this Toadstone, newest first: id, URL, '
                       'status and page counts. Start here when you do not have a '
                       'crawl id.',
        'schema': _schema({
            'limit': dict(_INT, description='How many crawls to return (default 20).'),
            'status': dict(_STRING, description="Only crawls in this state, e.g. "
                                                "'completed' or 'running'."),
        }),
    },
    {
        'name': 'crawl_status',
        'write': False,
        'description': 'Progress of one crawl: status, pages done, pages found and '
                       'an ETA. Poll this after start_crawl.',
        'schema': _schema(_crawl_id_prop(), ['crawl_id']),
    },
    {
        'name': 'start_crawl',
        'write': True,
        'description': 'Start a crawl and return its id. Writes, so it is refused '
                       'unless this server was started with writes enabled, and the '
                       'target host must be on the allow-list.',
        'schema': _schema({
            'url': dict(_STRING, description='The http(s) URL to start from.'),
            'render': dict(_BOOL, description='Render with a headless browser. '
                                              'Much slower; needed for JS-built pages.'),
            'max_urls': dict(_INT, description='Stop after this many URLs. Defaults '
                                               'to the server cap, and may not exceed it.'),
            'max_depth': dict(_INT, description='Maximum click depth from the start URL.'),
        }, ['url']),
    },
    {
        'name': 'list_issues',
        'write': False,
        'description': 'Issues from one crawl, filterable by severity, check, '
                       'category and URL prefix, with a tally by severity and by '
                       'check. Each issue carries its stable id, its evidence and '
                       'how to fix it.',
        'schema': _schema({
            **_crawl_id_prop(),
            'severity': dict(_STRING, description="'error', 'warning' or 'info'; "
                                                  'comma-separate for several.'),
            'check': dict(_STRING, description='Check slug, e.g. '
                                               "'missing_meta_description'; "
                                               'comma-separate for several.'),
            'category': dict(_STRING, description="Category, e.g. 'Technical'."),
            'url_prefix': dict(_STRING, description='Only URLs starting with this.'),
            'status': dict(_STRING, description="'open' or 'accepted'."),
            'since_crawl': dict(_INT, description='Only issues new since this crawl id.'),
            'include_accepted': dict(_BOOL, description='Include muted issues '
                                                        '(default false).'),
            'limit': dict(_INT, description='How many issues to return (default 50).'),
            'offset': dict(_INT, description='Skip this many first.'),
        }, ['crawl_id']),
    },
    {
        'name': 'issue_detail',
        'write': False,
        'description': 'One issue by its stable id, with a summary of the page it '
                       'was found on. The crawl id is required: an issue id is '
                       'stable across crawls, so it alone does not identify one row.',
        'schema': _schema({**_crawl_id_prop(),
                           'issue_id': dict(_STRING, description='The stable issue id.')},
                          ['crawl_id', 'issue_id']),
    },
    {
        'name': 'page_detail',
        'write': False,
        'description': 'Everything known about one URL in one crawl: the stored '
                       'row, its issues, inbound and outbound links, and how it '
                       'changed against the previous crawl of the site.',
        'schema': _schema({
            **_crawl_id_prop(),
            'url': dict(_STRING, description='The exact URL, as the crawl stored it.'),
            'baseline': dict(_INT, description='Crawl id to diff against. Defaults '
                                               'to the previous crawl of this domain.'),
            'include_links': dict(_BOOL, description='Include link rows, not just '
                                                     'counts (default true).'),
            'link_limit': dict(_INT, description='Link rows per direction (default 25).'),
        }, ['crawl_id', 'url']),
    },
    {
        'name': 'regression_diff',
        'write': False,
        'description': 'What changed between two crawls: issues that are new, '
                       'fixed, and still open, matched by stable issue id.',
        'schema': _schema({
            'baseline_crawl_id': dict(_INT, description='The earlier crawl.'),
            'candidate_crawl_id': dict(_INT, description='The crawl being judged.'),
            'limit': dict(_INT, description='Issues per section (default 25).'),
        }, ['baseline_crawl_id', 'candidate_crawl_id']),
    },
    {
        'name': 'export_dataset',
        'write': False,
        'description': 'Export one dataset from a crawl as text. Datasets: '
                       + ', '.join(DATASETS) + '. Formats: csv, json, ndjson '
                       '(xlsx and zip are downloads, not MCP results).',
        'schema': _schema({
            **_crawl_id_prop(),
            'dataset': dict(_STRING, enum=list(DATASETS), description='Which dataset.'),
            'format': dict(_STRING, enum=list(TEXT_FORMATS),
                           description='Serialisation (default csv).'),
            'limit': dict(_INT, description='Rows to keep, for csv and ndjson.'),
            'severity': dict(_STRING, description='Filter, where the dataset has '
                                                  'a severity column.'),
            'check': dict(_STRING, description='Filter, where the dataset has a '
                                               'check column.'),
            'url_prefix': dict(_STRING, description='Only rows whose URL starts '
                                                    'with this.'),
            'status_code': dict(_INT, description='Only rows with this HTTP status.'),
        }, ['crawl_id', 'dataset']),
    },
    {
        'name': 'check_reference',
        'write': False,
        'description': 'What a check means, how severe it is and how to fix it. '
                       'With no argument, the whole reference for this install.',
        'schema': _schema({'check': dict(_STRING, description='A check slug.')}),
    },
    {
        'name': 'accept_issue',
        'write': True,
        'description': 'Mute one issue with a reason, so it stops being reported '
                       'as open. Writes, so it is refused on a read-only server.',
        'schema': _schema({
            'issue_id': dict(_STRING, description='The stable issue id.'),
            'reason': dict(_STRING, description='Why this is acceptable. Required.'),
            'base_domain': dict(_STRING, description='Scope the acceptance to one '
                                                     'domain.'),
        }, ['issue_id', 'reason']),
    },
)

WRITE_TOOLS = frozenset(spec['name'] for spec in SPECS if spec['write'])


def tool_list(settings):
    """The tools/list payload. Read-only servers say so in the description."""
    listed = []
    for spec in SPECS:
        description = spec['description']
        if spec['write'] and settings.read_only:
            description += ' NOTE: this server is currently running read-only, ' \
                           'so calling it will be refused.'
        listed.append({'name': spec['name'], 'description': description,
                       'inputSchema': spec['schema']})
    return listed


def call(tools, name, arguments):
    """Dispatch one tool call. Unknown names and bad arguments raise ToolError."""
    spec = next((s for s in SPECS if s['name'] == name), None)
    if spec is None:
        raise ToolError(f'Unknown tool {name!r}. Available: '
                        + ', '.join(s['name'] for s in SPECS) + '.')
    if not isinstance(arguments, dict):
        raise ToolError(f'Arguments for {name} must be an object, got '
                        f'{type(arguments).__name__}.')

    allowed = set(spec['schema']['properties'])
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise ToolError(f'{name} does not take {", ".join(unknown)}. It takes: '
                        + ', '.join(sorted(allowed)) + '.')
    missing = [key for key in spec['schema']['required'] if arguments.get(key) in (None, '')]
    if missing:
        raise ToolError(f'{name} needs {", ".join(missing)}.')

    return getattr(tools, name)(**arguments)


# ---------------------------------------------------------------------------
# shaping helpers
# ---------------------------------------------------------------------------

def _pick(row, fields):
    return {field: row.get(field) for field in fields if field in row}


def _page_summary(page):
    """The page row cut down to what a fix needs, plus its headings tree."""
    summary = _pick(page, PAGE_FIELDS)
    summary['http'] = page.get('http') or {}
    summary['headings'] = page.get('headings') or []
    summary['heading_text_issues'] = page.get('heading_text_issues') or []
    summary['image_count'] = len(page.get('images') or [])
    summary['images_missing_alt'] = sum(
        1 for image in page.get('images') or []
        if isinstance(image, dict) and not (image.get('alt') or '').strip())
    summary['schema_types'] = sorted({
        block.get('@type') for block in page.get('json_ld') or []
        if isinstance(block, dict) and isinstance(block.get('@type'), str)})
    return summary


def _diff_summary(diff):
    """The page diff with its issue lists reduced to counts and ids."""
    if not diff:
        return None
    summary = {key: diff.get(key) for key in
               ('baseline_crawl', 'introduced', 'status_changed', 'title_changed',
                'meta_description_changed', 'canonical_changed', 'word_count_delta',
                'text_changed', 'similarity')}
    for key in ('issues_new', 'issues_fixed', 'issues_still_open'):
        rows = diff.get(key) or []
        summary[key] = {'count': len(rows),
                        'checks': sorted({row.get('check') for row in rows
                                          if row.get('check')})}
    summary['links_added'] = len(diff.get('links_added') or [])
    summary['links_removed'] = len(diff.get('links_removed') or [])
    return summary


def _tally(rows, field):
    counts = {}
    for row in rows:
        key = row.get(field) or 'unknown'
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _bounded(value, default):
    """A caller's limit, defaulted when absent and capped at MAX_LIMIT."""
    if value in (None, ''):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ToolError(f'limit must be a whole number, got {value!r}.')
    if number < 1:
        raise ToolError('limit must be at least 1.')
    return min(number, MAX_LIMIT)


def _csv(value):
    """Normalise a single value or a list into the API's comma-separated form."""
    if value in (None, ''):
        return ''
    if isinstance(value, (list, tuple)):
        return ','.join(str(v).strip() for v in value if str(v).strip())
    return ','.join(part.strip() for part in str(value).split(',') if part.strip())


def _head_lines(text, keep, truncated):
    lines = text.splitlines()
    if len(lines) <= keep:
        return text, truncated
    return '\n'.join(lines[:keep]) + '\n', True


def _crawl_id(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ToolError(f'crawl_id must be a whole number, got {value!r}.')
    if number < 1:
        raise ToolError(f'crawl_id must be positive, got {number}.')
    return number


__all__ = ['Tools', 'ToolError', 'GuardrailError', 'SPECS', 'DATASETS',
           'WRITE_TOOLS', 'tool_list', 'call']
