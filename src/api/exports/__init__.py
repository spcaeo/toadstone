"""Exports API: every dataset a crawl holds, in the format the reader needs.

The reporting API (``src/api/reporting.py``) answers questions; this one hands
over the data. Same auth, same filters, same crawl ownership rule - so a link
from the filtered issue list carries its filters straight into the download.

    GET /api/crawls/<id>/export/<dataset>  ?format=csv|json|ndjson|xlsx
                                           &fields=&<dataset filters>
    GET /api/crawls/<id>/export/all        ?format=xlsx|zip
    GET /api/crawls/<id>/report            ?format=html|md&baseline=<crawl id>
    GET /api/export/datasets               the catalogue, with every column

``datasets.py`` holds the twelve datasets and their filters, ``writers.py``
the streaming serialisers, and ``report.py`` the human summary. The blueprint
is built by main.py, which passes its own login decorator in, so
authentication stays defined in one place.
"""

from flask import Blueprint, Response, jsonify, request, session, stream_with_context

from src import crawl_db
from src.api.exports.datasets import DATASETS, filtered
from src.api.exports.report import report_html, report_markdown, summarise
from src.api.exports.writers import (BUNDLE_FORMATS, REPORT_FORMATS, ROW_FORMATS,
                                     XLSX_MIMETYPE, bad_format, csv_chunks, download,
                                     json_chunks, ndjson_chunks, stream_file,
                                     workbook_of_everything, workbook_of_one,
                                     zip_of_csvs)

__all__ = ['create_blueprint', 'DATASETS']


def create_blueprint(login_required):
    """Build the blueprint. `login_required` is main.py's own decorator."""
    api = Blueprint('exports', __name__)

    def owned_crawl(crawl_id):
        """The crawl, or (None, error response) when it is not this user's."""
        crawl = crawl_db.get_crawl_by_id(crawl_id)
        if not crawl:
            return None, (jsonify({'success': False, 'error': 'Crawl not found'}), 404)
        user_id = session.get('user_id')
        if user_id and crawl.get('user_id') not in (None, user_id):
            return None, (jsonify({'success': False, 'error': 'Unauthorized'}), 403)
        return crawl, None

    @api.route('/api/export/datasets')
    @login_required
    def export_catalogue():
        """What can be exported, and what each dataset's columns are."""
        return jsonify({
            'success': True,
            'formats': list(ROW_FORMATS),
            'bundle_formats': list(BUNDLE_FORMATS),
            'report_formats': list(REPORT_FORMATS),
            'datasets': [{'dataset': name,
                          'columns': list(spec['fields']),
                          'derived': not spec.get('stored'),
                          'about': spec['about']}
                         for name, spec in DATASETS.items()],
        })

    @api.route('/api/crawls/<int:crawl_id>/export/all')
    @login_required
    def export_all(crawl_id):
        crawl, error = owned_crawl(crawl_id)
        if error:
            return error

        fmt = (request.args.get('format') or 'xlsx').lower()
        if fmt not in BUNDLE_FORMATS:
            return bad_format(fmt, BUNDLE_FORMATS)

        if fmt == 'zip':
            return Response(stream_with_context(zip_of_csvs(crawl_id, crawl)),
                            mimetype='application/zip',
                            headers=download('zip', crawl_id, 'all'))
        return stream_file(workbook_of_everything(crawl_id, crawl),
                            XLSX_MIMETYPE, download('xlsx', crawl_id, 'all'))

    @api.route('/api/crawls/<int:crawl_id>/export/<dataset>')
    @login_required
    def export_dataset(crawl_id, dataset):
        if dataset == 'all':                     # defensive: routing prefers the
            return export_all(crawl_id)          # static rule, but never assume it

        spec = DATASETS.get(dataset)
        if not spec:
            return jsonify({'success': False,
                            'error': f'Unknown dataset {dataset!r}',
                            'datasets': list(DATASETS)}), 400

        fmt = (request.args.get('format') or 'csv').lower()
        if fmt not in ROW_FORMATS:
            return bad_format(fmt, ROW_FORMATS)

        crawl, error = owned_crawl(crawl_id)
        if error:
            return error

        requested = request.args.get('fields', '')
        fields = tuple(f.strip() for f in requested.split(',') if f.strip()) \
            or spec['fields']

        def rows():
            return filtered(spec['loader'](crawl_id, crawl), spec, request.args)

        if fmt == 'xlsx':
            return stream_file(workbook_of_one(dataset, fields, rows()),
                                XLSX_MIMETYPE, download('xlsx', crawl_id, dataset))
        if fmt == 'ndjson':
            return Response(stream_with_context(ndjson_chunks(fields, rows())),
                            mimetype='application/x-ndjson',
                            headers=download('ndjson', crawl_id, dataset))
        if fmt == 'json':
            return Response(stream_with_context(
                                json_chunks(crawl_id, dataset, fields, rows())),
                            mimetype='application/json',
                            headers=download('json', crawl_id, dataset))
        return Response(stream_with_context(csv_chunks(fields, rows())),
                        mimetype='text/csv',
                        headers=download('csv', crawl_id, dataset))

    @api.route('/api/crawls/<int:crawl_id>/report')
    @login_required
    def crawl_report(crawl_id):
        fmt = (request.args.get('format') or 'html').lower()
        if fmt not in REPORT_FORMATS:
            return bad_format(fmt, REPORT_FORMATS)

        crawl, error = owned_crawl(crawl_id)
        if error:
            return error

        baseline = request.args.get('baseline', type=int)
        summary = summarise(crawl_id, crawl, baseline)

        if fmt == 'md':
            return Response(report_markdown(summary),
                            mimetype='text/markdown',
                            headers=download('md', crawl_id, 'report'))
        return Response(report_html(summary), mimetype='text/html',
                        headers=download('html', crawl_id, 'report', inline=True))

    return api
