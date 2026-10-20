"""The original export endpoints, kept working.

These predate `src/api/exports/`, which is the one to use: it has twelve
datasets, streams properly and shares one row format. These two routes
are the shapes the existing UI and anyone's existing scripts already
call, so they keep working unchanged.

Lifted out of main.py, which had reached 2,159 lines against this
project's 1,000-line limit. Behaviour is identical; only the file moved.
"""

import base64
import csv
import io
import json
import time
import xml.etree.ElementTree as ET
from io import BytesIO, StringIO
import zipfile
from datetime import datetime

from flask import Blueprint, Response, jsonify, request, session, stream_with_context


def generate_csv_export(urls, fields):
    """Generate CSV export content"""
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()

    for url_data in urls:
        row = {}
        for field in fields:
            value = url_data.get(field, '')

            # Handle complex data types for CSV
            if field == 'analytics' and isinstance(value, dict):
                analytics_list = []
                if value.get('gtag') or value.get('ga4_id'): analytics_list.append('GA4')
                if value.get('google_analytics'): analytics_list.append('GA')
                if value.get('gtm_id'): analytics_list.append('GTM')
                if value.get('facebook_pixel'): analytics_list.append('FB')
                if value.get('hotjar'): analytics_list.append('HJ')
                if value.get('mixpanel'): analytics_list.append('MP')
                row[field] = ', '.join(analytics_list)
            elif field == 'og_tags' and isinstance(value, dict):
                row[field] = f"{len(value)} tags" if value else ''
            elif field == 'twitter_tags' and isinstance(value, dict):
                row[field] = f"{len(value)} tags" if value else ''
            elif field == 'json_ld' and isinstance(value, list):
                row[field] = f"{len(value)} scripts" if value else ''
            elif field == 'images' and isinstance(value, list):
                row[field] = f"{len(value)} images" if value else ''
            elif field == 'internal_links' and isinstance(value, (int, float)):
                row[field] = f"{int(value)} internal links" if value else '0 internal links'
            elif field == 'external_links' and isinstance(value, (int, float)):
                row[field] = f"{int(value)} external links" if value else '0 external links'
            elif field == 'h2' and isinstance(value, list):
                row[field] = ', '.join(value[:3]) + ('...' if len(value) > 3 else '')
            elif field == 'h3' and isinstance(value, list):
                row[field] = ', '.join(value[:3]) + ('...' if len(value) > 3 else '')
            elif isinstance(value, (dict, list)):
                row[field] = str(value)
            else:
                row[field] = value

        writer.writerow(row)

    return output.getvalue()

def generate_json_export(urls, fields):
    """Generate JSON export content"""
    filtered_urls = []
    for url_data in urls:
        filtered_data = {}
        for field in fields:
            value = url_data.get(field, '')
            # Keep complex data structures intact in JSON
            filtered_data[field] = value
        filtered_urls.append(filtered_data)

    return json.dumps({
        'export_date': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total_urls': len(filtered_urls),
        'fields': fields,
        'data': filtered_urls
    }, indent=2, default=str)

def generate_xml_export(urls, fields):
    """Generate XML export content"""
    root = ET.Element('toadstone_export')
    root.set('export_date', time.strftime('%Y-%m-%d %H:%M:%S'))
    root.set('total_urls', str(len(urls)))

    urls_element = ET.SubElement(root, 'urls')

    for url_data in urls:
        url_element = ET.SubElement(urls_element, 'url')
        for field in fields:
            field_element = ET.SubElement(url_element, field)
            field_element.text = str(url_data.get(field, ''))

    return ET.tostring(root, encoding='unicode')

def generate_links_csv_export(links):
    """Generate CSV export for links data"""
    output = StringIO()
    fieldnames = ['source_url', 'target_url', 'anchor_text', 'is_internal', 'target_domain', 'target_status', 'placement']
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for link in links:
        row = {
            'source_url': link.get('source_url', ''),
            'target_url': link.get('target_url', ''),
            'anchor_text': link.get('anchor_text', ''),
            'is_internal': 'Yes' if link.get('is_internal') else 'No',
            'target_domain': link.get('target_domain', ''),
            'target_status': link['target_status'] if link.get('target_status') is not None else 'Not crawled',
            'placement': link.get('placement', 'body')
        }
        writer.writerow(row)

    return output.getvalue()

def generate_links_json_export(links):
    """Generate JSON export for links data"""
    return json.dumps(links, indent=2)

def issue_matches_exclusion(issue, exclusion_patterns):
    """Check whether a single issue's URL matches any exclusion pattern"""
    from fnmatch import fnmatch
    from urllib.parse import urlparse

    path = urlparse(issue.get('url', '')).path

    for pattern in exclusion_patterns:
        if not pattern.strip() or pattern.strip().startswith('#'):
            continue

        if '*' in pattern:
            if fnmatch(path, pattern):
                return True
        elif path == pattern or path.startswith(pattern.rstrip('*')):
            return True

    return False

def generate_issues_csv_export(issues):
    """Generate CSV export for issues data"""
    output = StringIO()
    fieldnames = ['url', 'type', 'category', 'issue', 'details']
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for issue in issues:
        row = {
            'url': issue.get('url', ''),
            'type': issue.get('type', ''),
            'category': issue.get('category', ''),
            'issue': issue.get('issue', ''),
            'details': issue.get('details', '')
        }
        writer.writerow(row)

    return output.getvalue()

def generate_issues_json_export(issues):
    """Generate JSON export for issues data"""
    # Group issues by URL for better organization
    issues_by_url = {}
    for issue in issues:
        url = issue.get('url', '')
        if url not in issues_by_url:
            issues_by_url[url] = []
        issues_by_url[url].append({
            'type': issue.get('type', ''),
            'category': issue.get('category', ''),
            'issue': issue.get('issue', ''),
            'details': issue.get('details', '')
        })

# Module level so other code can read them; they were constants before the
# move and indenting them into the factory made them closure variables.
XLSX_MIMETYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
_XLSX_CELL_LIMIT = 32767  # Excel's per-cell character limit
LINK_EXPORT_FIELDS = ['source_url', 'target_url', 'anchor_text', 'is_internal', 'target_domain', 'target_status', 'placement']
ISSUE_EXPORT_FIELDS = ['url', 'type', 'category', 'issue', 'details']

def create_blueprint(login_required, get_or_create_crawler, get_session_settings,
                     filter_issues_by_exclusion_patterns):
    """Build the blueprint. The three callables come from main.py."""
    api = Blueprint('legacy_exports', __name__)

    @api.route('/api/export_stream')
    @login_required
    def export_stream():
        """Stream export data directly as a file download — avoids loading full export into memory"""
        from flask import Response

        try:
            export_format = request.args.get('format', 'csv')
            fields_param = request.args.get('fields', 'url,status_code,title')
            # Restrict field names to identifier-like strings — they become XML tag
            # names and CSV headers, so arbitrary query input must not pass through
            export_fields = [f.strip() for f in fields_param.split(',')
                             if f.strip() and f.strip().replace('_', '').isalnum() and not f.strip()[0].isdigit()]
            data_type = request.args.get('type', 'urls')  # urls, links, issues

            # Get data from current crawler (already in memory from active or loaded crawl)
            crawler = get_or_create_crawler()
            timestamp = int(time.time())

            if data_type == 'links':
                links = crawler.link_manager.all_links if crawler.link_manager else []
                # Update link statuses from crawled URLs
                if links and crawler.crawl_results:
                    status_lookup = {u['url']: u.get('status_code') for u in crawler.crawl_results}
                    for link in links:
                        target = link.get('target_url')
                        if target in status_lookup:
                            link['target_status'] = status_lookup[target]

                if export_format == 'json':
                    return Response(
                        _stream_links_json(links),
                        mimetype='application/json',
                        headers={'Content-Disposition': f'attachment; filename=toadstone_links_{timestamp}.json'}
                    )
                elif export_format == 'xml':
                    return Response(
                        _stream_links_xml(links),
                        mimetype='application/xml',
                        headers={'Content-Disposition': f'attachment; filename=toadstone_links_{timestamp}.xml'}
                    )
                elif export_format == 'xlsx':
                    return Response(
                        _links_xlsx(links),
                        mimetype=XLSX_MIMETYPE,
                        headers={'Content-Disposition': f'attachment; filename=toadstone_links_{timestamp}.xlsx'}
                    )
                else:
                    return Response(
                        _stream_links_csv(links),
                        mimetype='text/csv',
                        headers={'Content-Disposition': f'attachment; filename=toadstone_links_{timestamp}.csv'}
                    )

            elif data_type == 'issues':
                issues = crawler.issue_detector.get_issues() if crawler.issue_detector else []
                # Apply exclusion patterns
                settings_manager = get_session_settings()
                current_settings = settings_manager.get_settings()
                exclusion_text = current_settings.get('issueExclusionPatterns', '')
                exclusion_patterns = [p.strip() for p in exclusion_text.split('\n') if p.strip()]
                issues = filter_issues_by_exclusion_patterns(issues, exclusion_patterns)

                if export_format == 'json':
                    return Response(
                        _stream_issues_json(issues),
                        mimetype='application/json',
                        headers={'Content-Disposition': f'attachment; filename=toadstone_issues_{timestamp}.json'}
                    )
                elif export_format == 'xml':
                    return Response(
                        _stream_issues_xml(issues),
                        mimetype='application/xml',
                        headers={'Content-Disposition': f'attachment; filename=toadstone_issues_{timestamp}.xml'}
                    )
                elif export_format == 'xlsx':
                    return Response(
                        _issues_xlsx(issues),
                        mimetype=XLSX_MIMETYPE,
                        headers={'Content-Disposition': f'attachment; filename=toadstone_issues_{timestamp}.xlsx'}
                    )
                else:
                    return Response(
                        _stream_issues_csv(issues),
                        mimetype='text/csv',
                        headers={'Content-Disposition': f'attachment; filename=toadstone_issues_{timestamp}.csv'}
                    )

            else:
                # URLs export
                urls = crawler.crawl_results
                if not urls:
                    return jsonify({'success': False, 'error': 'No data to export'}), 404

                # Remove special fields from regular export
                regular_fields = [f for f in export_fields if f not in ['issues_detected', 'links_detailed']]
                if not regular_fields:
                    return jsonify({'success': False, 'error': 'No fields selected'}), 400

                if export_format == 'json':
                    return Response(
                        _stream_urls_json(urls, regular_fields),
                        mimetype='application/json',
                        headers={'Content-Disposition': f'attachment; filename=toadstone_export_{timestamp}.json'}
                    )
                elif export_format == 'xml':
                    return Response(
                        _stream_urls_xml(urls, regular_fields),
                        mimetype='application/xml',
                        headers={'Content-Disposition': f'attachment; filename=toadstone_export_{timestamp}.xml'}
                    )
                elif export_format == 'xlsx':
                    return Response(
                        _urls_xlsx(urls, regular_fields),
                        mimetype=XLSX_MIMETYPE,
                        headers={'Content-Disposition': f'attachment; filename=toadstone_export_{timestamp}.xlsx'}
                    )
                else:
                    return Response(
                        _stream_urls_csv(urls, regular_fields),
                        mimetype='text/csv',
                        headers={'Content-Disposition': f'attachment; filename=toadstone_export_{timestamp}.csv'}
                    )

        except Exception as e:
            import traceback
            traceback.print_exc()
            return jsonify({'success': False, 'error': str(e)}), 500


    def _format_csv_value(value, field):
        """Format a value for CSV export (same logic as generate_csv_export)"""
        if field == 'analytics' and isinstance(value, dict):
            parts = []
            if value.get('gtag') or value.get('ga4_id'): parts.append('GA4')
            if value.get('google_analytics'): parts.append('GA')
            if value.get('gtm_id'): parts.append('GTM')
            if value.get('facebook_pixel'): parts.append('FB')
            if value.get('hotjar'): parts.append('HJ')
            if value.get('mixpanel'): parts.append('MP')
            return ', '.join(parts)
        elif field in ('og_tags', 'twitter_tags') and isinstance(value, dict):
            return f"{len(value)} tags" if value else ''
        elif field == 'json_ld' and isinstance(value, list):
            return f"{len(value)} scripts" if value else ''
        elif field == 'images' and isinstance(value, list):
            return f"{len(value)} images" if value else ''
        elif field == 'internal_links' and isinstance(value, (int, float)):
            return f"{int(value)} internal links" if value else '0 internal links'
        elif field == 'external_links' and isinstance(value, (int, float)):
            return f"{int(value)} external links" if value else '0 external links'
        elif field in ('h2', 'h3') and isinstance(value, list):
            return ', '.join(value[:3]) + ('...' if len(value) > 3 else '')
        elif isinstance(value, (dict, list)):
            return str(value)
        return value




    def _xlsx_cell(value):
        """Coerce an export value into something openpyxl will accept."""
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
        text = ILLEGAL_CHARACTERS_RE.sub('', str(value))
        return text[:_XLSX_CELL_LIMIT]


    def _build_xlsx(sheet_title, headers, rows):
        """Build an .xlsx workbook in memory: one sheet, a header row, then `rows`
        (iterables of cell values). Write-only mode streams rows to a temp file,
        so memory stays flat for large crawls."""
        from openpyxl import Workbook
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet(title=sheet_title)
        sheet.append(list(headers))
        for row in rows:
            sheet.append([_xlsx_cell(v) for v in row])
        buffer = BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()




    def _link_export_row(link):
        return {
            'source_url': link.get('source_url', ''),
            'target_url': link.get('target_url', ''),
            'anchor_text': link.get('anchor_text', ''),
            'is_internal': 'Yes' if link.get('is_internal') else 'No',
            'target_domain': link.get('target_domain', ''),
            'target_status': link['target_status'] if link.get('target_status') is not None else 'Not crawled',
            'placement': link.get('placement', 'body')
        }


    def _issue_export_row(issue):
        return {
            'url': issue.get('url', ''),
            'type': issue.get('type', ''),
            'category': issue.get('category', ''),
            'issue': issue.get('issue', ''),
            'details': str(issue.get('details', ''))
        }


    def _urls_xlsx(urls, fields):
        return _build_xlsx('URLs', fields,
                           ([_format_csv_value(u.get(f, ''), f) for f in fields] for u in urls))


    def _links_xlsx(links):
        return _build_xlsx('Links', LINK_EXPORT_FIELDS,
                           ([_link_export_row(l)[f] for f in LINK_EXPORT_FIELDS] for l in links))


    def _issues_xlsx(issues):
        return _build_xlsx('Issues', ISSUE_EXPORT_FIELDS,
                           ([_issue_export_row(i)[f] for f in ISSUE_EXPORT_FIELDS] for i in issues))


    def _stream_urls_csv(urls, fields):
        """Generator that yields CSV rows one at a time"""
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        yield output.getvalue()
        output.truncate(0)
        output.seek(0)

        for url_data in urls:
            row = {}
            for field in fields:
                value = url_data.get(field, '')
                row[field] = _format_csv_value(value, field)
            writer.writerow(row)
            yield output.getvalue()
            output.truncate(0)
            output.seek(0)


    def _stream_urls_json(urls, fields):
        """Generator that yields JSON array items one at a time"""
        yield '{\n  "export_date": "' + time.strftime('%Y-%m-%d %H:%M:%S') + '",\n'
        yield '  "total_urls": ' + str(len(urls)) + ',\n'
        yield '  "fields": ' + json.dumps(fields) + ',\n'
        yield '  "data": [\n'
        for i, url_data in enumerate(urls):
            filtered = {f: url_data.get(f, '') for f in fields}
            prefix = ',\n' if i > 0 else ''
            yield prefix + '    ' + json.dumps(filtered, default=str)
        yield '\n  ]\n}\n'


    def _stream_urls_xml(urls, fields):
        """Generator that yields XML elements one at a time"""
        yield '<?xml version="1.0" encoding="UTF-8"?>\n'
        yield '<toadstone_export export_date="' + time.strftime('%Y-%m-%d %H:%M:%S') + '" total_urls="' + str(len(urls)) + '">\n'
        yield '  <urls>\n'
        for url_data in urls:
            yield '    <url>\n'
            for field in fields:
                escaped = str(url_data.get(field, '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                yield '      <' + field + '>' + escaped + '</' + field + '>\n'
            yield '    </url>\n'
        yield '  </urls>\n'
        yield '</toadstone_export>\n'


    def _stream_records_xml(root_tag, list_tag, item_tag, total_attr, total, records, fields):
        """Generator yielding an XML document of flat records, one element per field
        (same shape as _stream_urls_xml)."""
        yield '<?xml version="1.0" encoding="UTF-8"?>\n'
        yield f'<{root_tag} export_date="{time.strftime("%Y-%m-%d %H:%M:%S")}" {total_attr}="{total}">\n'
        yield f'  <{list_tag}>\n'
        for record in records:
            yield f'    <{item_tag}>\n'
            for field in fields:
                escaped = str(record.get(field, '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                yield f'      <{field}>{escaped}</{field}>\n'
            yield f'    </{item_tag}>\n'
        yield f'  </{list_tag}>\n'
        yield f'</{root_tag}>\n'


    def _stream_links_xml(links):
        return _stream_records_xml('toadstone_links', 'links', 'link', 'total_links', len(links),
                                   (_link_export_row(link) for link in links), LINK_EXPORT_FIELDS)


    def _stream_issues_xml(issues):
        return _stream_records_xml('toadstone_issues', 'issues', 'issue', 'total_issues', len(issues),
                                   (_issue_export_row(issue) for issue in issues), ISSUE_EXPORT_FIELDS)


    def _stream_links_csv(links):
        """Generator that yields link CSV rows one at a time"""
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=LINK_EXPORT_FIELDS)
        writer.writeheader()
        yield output.getvalue()
        output.truncate(0)
        output.seek(0)

        for link in links:
            writer.writerow(_link_export_row(link))
            yield output.getvalue()
            output.truncate(0)
            output.seek(0)


    def _stream_links_json(links):
        """Generator that yields link JSON array items one at a time"""
        yield '{\n  "export_date": "' + time.strftime('%Y-%m-%d %H:%M:%S') + '",\n'
        yield '  "total_links": ' + str(len(links)) + ',\n'
        yield '  "data": [\n'
        for i, link in enumerate(links):
            entry = {
                'source_url': link.get('source_url', ''),
                'target_url': link.get('target_url', ''),
                'anchor_text': link.get('anchor_text', ''),
                'is_internal': link.get('is_internal', False),
                'target_domain': link.get('target_domain', ''),
                'target_status': link['target_status'] if link.get('target_status') is not None else 'Not crawled',
                'placement': link.get('placement', 'body')
            }
            prefix = ',\n' if i > 0 else ''
            yield prefix + '    ' + json.dumps(entry, default=str)
        yield '\n  ]\n}\n'


    def _stream_issues_csv(issues):
        """Generator that yields issue CSV rows one at a time"""
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=ISSUE_EXPORT_FIELDS)
        writer.writeheader()
        yield output.getvalue()
        output.truncate(0)
        output.seek(0)

        for issue in issues:
            writer.writerow(_issue_export_row(issue))
            yield output.getvalue()
            output.truncate(0)
            output.seek(0)


    def _stream_issues_json(issues):
        """Generator that yields issue JSON array items one at a time"""
        yield '{\n  "export_date": "' + time.strftime('%Y-%m-%d %H:%M:%S') + '",\n'
        yield '  "total_issues": ' + str(len(issues)) + ',\n'
        yield '  "data": [\n'
        for i, issue in enumerate(issues):
            entry = {
                'url': issue.get('url', ''),
                'type': issue.get('type', ''),
                'category': issue.get('category', ''),
                'issue': issue.get('issue', ''),
                'details': issue.get('details', '')
            }
            prefix = ',\n' if i > 0 else ''
            yield prefix + '    ' + json.dumps(entry, default=str)
        yield '\n  ]\n}\n'



    @api.route('/api/export_data', methods=['POST'])
    @login_required
    def export_data():
        try:
            data = request.get_json()
            export_format = data.get('format', 'csv')
            export_fields = data.get('fields', ['url', 'status_code', 'title'])
            local_data = data.get('localData', {})

            # Use local data if provided (from loaded crawl), otherwise get from crawler
            if local_data and local_data.get('urls'):
                urls = local_data.get('urls', [])
                links = local_data.get('links', [])
                issues = local_data.get('issues', [])
            else:
                # Get current crawl results
                crawler = get_or_create_crawler()
                crawl_data = crawler.get_status()
                urls = crawl_data.get('urls', [])
                links = crawl_data.get('links', [])
                issues = crawl_data.get('issues', [])

            if not urls:
                return jsonify({'success': False, 'error': 'No data to export'})

            # Update link statuses from crawled URLs (fixes missing status codes in exports)
            if links and urls:
                status_lookup = {url_data['url']: url_data.get('status_code') for url_data in urls}
                for link in links:
                    target_url = link.get('target_url')
                    if target_url in status_lookup:
                        link['target_status'] = status_lookup[target_url]

            # Apply current issue exclusion patterns (works for loaded crawls too)
            if issues:
                settings_manager = get_session_settings()
                current_settings = settings_manager.get_settings()
                exclusion_patterns_text = current_settings.get('issueExclusionPatterns', '')
                exclusion_patterns = [p.strip() for p in exclusion_patterns_text.split('\n') if p.strip()]
                issues = filter_issues_by_exclusion_patterns(issues, exclusion_patterns)

            # Collect files to export based on special field selections
            files_to_export = []

            # Check for special export fields and prepare them as separate files
            has_issues_export = 'issues_detected' in export_fields
            has_links_export = 'links_detailed' in export_fields

            # Remove special fields from regular export fields
            regular_fields = [f for f in export_fields if f not in ['issues_detected', 'links_detailed']]


            # Generate issues export if requested
            if has_issues_export:
                if export_format == 'csv':
                    issues_content = generate_issues_csv_export(issues)
                    issues_mimetype = 'text/csv'
                    issues_filename = f'toadstone_issues_{int(time.time())}.csv'
                elif export_format == 'json':
                    issues_content = generate_issues_json_export(issues)
                    issues_mimetype = 'application/json'
                    issues_filename = f'toadstone_issues_{int(time.time())}.json'
                elif export_format == 'xml':
                    issues_content = ''.join(_stream_issues_xml(issues))
                    issues_mimetype = 'application/xml'
                    issues_filename = f'toadstone_issues_{int(time.time())}.xml'
                elif export_format == 'xlsx':
                    issues_content = base64.b64encode(_issues_xlsx(issues)).decode('ascii')
                    issues_mimetype = XLSX_MIMETYPE
                    issues_filename = f'toadstone_issues_{int(time.time())}.xlsx'
                else:
                    issues_content = generate_issues_csv_export(issues)
                    issues_mimetype = 'text/csv'
                    issues_filename = f'toadstone_issues_{int(time.time())}.csv'

                files_to_export.append({
                    'content': issues_content,
                    'mimetype': issues_mimetype,
                    'filename': issues_filename,
                    **({'encoding': 'base64'} if export_format == 'xlsx' else {})
                })

            # Generate links export if requested
            if has_links_export:
                if export_format == 'csv':
                    links_content = generate_links_csv_export(links)
                    links_mimetype = 'text/csv'
                    links_filename = f'toadstone_links_{int(time.time())}.csv'
                elif export_format == 'json':
                    links_content = generate_links_json_export(links)
                    links_mimetype = 'application/json'
                    links_filename = f'toadstone_links_{int(time.time())}.json'
                elif export_format == 'xml':
                    links_content = ''.join(_stream_links_xml(links))
                    links_mimetype = 'application/xml'
                    links_filename = f'toadstone_links_{int(time.time())}.xml'
                elif export_format == 'xlsx':
                    links_content = base64.b64encode(_links_xlsx(links)).decode('ascii')
                    links_mimetype = XLSX_MIMETYPE
                    links_filename = f'toadstone_links_{int(time.time())}.xlsx'
                else:
                    links_content = generate_links_csv_export(links)
                    links_mimetype = 'text/csv'
                    links_filename = f'toadstone_links_{int(time.time())}.csv'

                files_to_export.append({
                    'content': links_content,
                    'mimetype': links_mimetype,
                    'filename': links_filename,
                    **({'encoding': 'base64'} if export_format == 'xlsx' else {})
                })

            # Generate regular export if there are regular fields
            if regular_fields:
                if export_format == 'csv':
                    regular_content = generate_csv_export(urls, regular_fields)
                    regular_mimetype = 'text/csv'
                    regular_filename = f'toadstone_export_{int(time.time())}.csv'
                elif export_format == 'json':
                    regular_content = generate_json_export(urls, regular_fields)
                    regular_mimetype = 'application/json'
                    regular_filename = f'toadstone_export_{int(time.time())}.json'
                elif export_format == 'xml':
                    regular_content = generate_xml_export(urls, regular_fields)
                    regular_mimetype = 'application/xml'
                    regular_filename = f'toadstone_export_{int(time.time())}.xml'
                elif export_format == 'xlsx':
                    regular_content = base64.b64encode(_urls_xlsx(urls, regular_fields)).decode('ascii')
                    regular_mimetype = XLSX_MIMETYPE
                    regular_filename = f'toadstone_export_{int(time.time())}.xlsx'
                else:
                    return jsonify({'success': False, 'error': 'Unsupported export format'})

                files_to_export.append({
                    'content': regular_content,
                    'mimetype': regular_mimetype,
                    'filename': regular_filename,
                    **({'encoding': 'base64'} if export_format == 'xlsx' else {})
                })

            # Handle special case where only special fields are selected but no data
            if not files_to_export:
                if has_issues_export and not issues:
                    return jsonify({'success': False, 'error': 'No issues data to export'})
                elif has_links_export and not links:
                    return jsonify({'success': False, 'error': 'No links data to export'})
                else:
                    return jsonify({'success': False, 'error': 'No data to export'})

            # Return multiple files if we have more than one, otherwise single file
            if len(files_to_export) > 1:
                return jsonify({
                    'success': True,
                    'multiple_files': True,
                    'files': files_to_export
                })
            else:
                # Single file
                file_data = files_to_export[0]
                return jsonify({
                    'success': True,
                    'content': file_data['content'],
                    'mimetype': file_data['mimetype'],
                    'filename': file_data['filename']
                })

        except Exception as e:
            return jsonify({'success': False, 'error': str(e)})

    return api
