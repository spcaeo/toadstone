"""The fixer agent: turn a crawl's issues into a brief a coding agent can work from.

`src/core/agents/fixer.py` groups a crawl's findings by cause — forty pages
missing a canonical is one template to fix, not forty edits — and optionally
hands that brief to a coding agent, keeping what comes back.

Like the log analyser before it, nothing called it. The agents package
docstring lists four kinds; `__init__` imported one. This is the route that
makes two of them real.

    GET  /api/fixer/brief?crawl_id=N    the brief, as text, with no model involved
    POST /api/fixer/run                 hand the brief to the configured agent

The brief endpoint deliberately needs no model: it is useful on its own,
costs nothing, and lets someone read exactly what would be sent before
anything is sent. `run` proposes changes and returns them. It never writes to
a repository and never commits — the caller decides what to do with the
report, which is the whole reason the agent is allowed to see the code at all.
"""

from flask import Blueprint, jsonify, request

from src.core.agents import fixer


def create_blueprint(login_required, load_crawl_issues, get_config):
    """Build the blueprint.

    `load_crawl_issues(crawl_id)` returns a crawl's issues; `get_config()`
    returns the current crawl settings, so the agent mode and model are
    whatever the person configured rather than a second copy of the defaults.
    """
    api = Blueprint('fixer_routes', __name__)

    def _agent_config():
        config = get_config() or {}
        return {
            'mode': config.get('agent_mode', 'none'),
            'model': config.get('agent_model'),
            'cli_command': config.get('agent_cli_command'),
        }

    @api.route('/api/fixer/brief')
    @login_required
    def brief():
        crawl_id = request.args.get('crawl_id', type=int)
        if not crawl_id:
            return jsonify({'success': False,
                            'error': 'crawl_id is required.'}), 400
        issues = load_crawl_issues(crawl_id) or []
        worth_fixing = fixer.fixable(issues)
        text = fixer.brief(worth_fixing, request.args.get('repo_path', ''),
                           crawl_id, request.args.get('base_url', ''))
        return jsonify({
            'success': True,
            'crawl_id': crawl_id,
            'issues_total': len(issues),
            'issues_fixable': len(worth_fixing),
            'brief': text,
            'note': ('Nothing has been sent anywhere. This is what would be '
                     'sent if you ran the fixer.'),
        })

    @api.route('/api/fixer/run', methods=['POST'])
    @login_required
    def run():
        payload = request.get_json(silent=True) or {}
        crawl_id = payload.get('crawl_id')
        if not crawl_id:
            return jsonify({'success': False,
                            'error': 'crawl_id is required.'}), 400

        issues = load_crawl_issues(int(crawl_id)) or []
        record = fixer.run(fixer.fixable(issues),
                           payload.get('repo_path', ''),
                           crawl_id,
                           payload.get('base_url', ''),
                           config=_agent_config())
        # `ran: False` is an ordinary answer, not an error: no model
        # configured and nothing fixable are both normal states, and the
        # brief is still worth returning. as_json() returns a string for
        # storing beside a crawl, so the record itself goes in the response.
        return jsonify({'success': True, 'crawl_id': crawl_id, **record})

    return api
