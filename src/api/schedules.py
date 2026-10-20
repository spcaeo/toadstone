"""The HTTP surface for scheduled crawls.

Thin on purpose: every decision about what a job is and when it fires lives
in `src/core/scheduler.py`, and this only translates it to and from JSON.

    GET    /api/schedules          every job
    POST   /api/schedules          add one
    GET    /api/schedules/<id>     one job
    PATCH  /api/schedules/<id>     change one
    DELETE /api/schedules/<id>     remove one
    POST   /api/schedules/<id>/run fire it now, without waiting for the timer
    GET    /api/schedules/status   is the loop alive, and when did it last tick
"""

from flask import Blueprint, jsonify, request, session

from src.core import scheduler


def create_blueprint(login_required, runner=None, get_scheduler=None):
    """Build the blueprint.

    `runner(job)` starts a crawl and returns its id; `get_scheduler()` returns
    the running Scheduler, if there is one. Both are injected so this module
    neither imports the crawler nor owns the loop's lifetime.
    """
    api = Blueprint('schedules', __name__)

    def _owned(job):
        """A job is visible when it is yours, or when nobody owns it."""
        user_id = session.get('user_id')
        return not user_id or job.get('user_id') in (None, user_id)

    def _public(job):
        out = dict(job)
        out['settings'] = scheduler.job_settings(job)
        return out

    @api.route('/api/schedules')
    @login_required
    def list_schedules():
        jobs = [_public(job) for job in scheduler.list_jobs() if _owned(job)]
        return jsonify({'success': True, 'schedules': jobs, 'count': len(jobs)})

    @api.route('/api/schedules', methods=['POST'])
    @login_required
    def add_schedule():
        payload = request.get_json(silent=True) or {}
        try:
            job_id = scheduler.create_job(
                url=payload.get('url'),
                every_minutes=payload.get('every_minutes'),
                user_id=session.get('user_id'),
                name=payload.get('name') or '',
                settings=payload.get('settings') or {})
        except (ValueError, TypeError) as exc:
            return jsonify({'success': False, 'error': str(exc)}), 400
        return jsonify({'success': True, 'schedule': _public(scheduler.get_job(job_id))}), 201

    @api.route('/api/schedules/<int:job_id>')
    @login_required
    def one_schedule(job_id):
        job = scheduler.get_job(job_id)
        if not job:
            return jsonify({'success': False, 'error': 'No such schedule'}), 404
        if not _owned(job):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        return jsonify({'success': True, 'schedule': _public(job)})

    @api.route('/api/schedules/<int:job_id>', methods=['PATCH', 'PUT'])
    @login_required
    def change_schedule(job_id):
        job = scheduler.get_job(job_id)
        if not job:
            return jsonify({'success': False, 'error': 'No such schedule'}), 404
        if not _owned(job):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        try:
            scheduler.update_job(job_id, **(request.get_json(silent=True) or {}))
        except (ValueError, TypeError) as exc:
            return jsonify({'success': False, 'error': str(exc)}), 400
        return jsonify({'success': True, 'schedule': _public(scheduler.get_job(job_id))})

    @api.route('/api/schedules/<int:job_id>', methods=['DELETE'])
    @login_required
    def remove_schedule(job_id):
        job = scheduler.get_job(job_id)
        if not job:
            return jsonify({'success': False, 'error': 'No such schedule'}), 404
        if not _owned(job):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        scheduler.delete_job(job_id)
        return jsonify({'success': True})

    @api.route('/api/schedules/<int:job_id>/run', methods=['POST'])
    @login_required
    def run_now(job_id):
        job = scheduler.get_job(job_id)
        if not job:
            return jsonify({'success': False, 'error': 'No such schedule'}), 404
        if not _owned(job):
            return jsonify({'success': False, 'error': 'Unauthorized'}), 403
        if runner is None:
            return jsonify({'success': False,
                            'error': 'No crawl runner is wired up'}), 503
        try:
            crawl_id = runner(job)
        except Exception as exc:
            scheduler.record_run(job_id, None, f'{type(exc).__name__}: {exc}')
            return jsonify({'success': False, 'error': str(exc)}), 400
        scheduler.record_run(job_id, crawl_id, '')
        return jsonify({'success': True, 'crawl_id': crawl_id,
                        'status_url': f'/api/crawl_status?crawl_id={crawl_id}'})

    @api.route('/api/schedules/status')
    @login_required
    def status():
        loop = get_scheduler() if get_scheduler else None
        return jsonify({
            'success': True,
            'running': bool(loop and loop.running()),
            'busy': bool(loop and loop.busy),
            'last_tick': str(loop.last_tick) if loop and loop.last_tick else None,
            'tick_seconds': getattr(loop, 'tick_seconds', None),
            'min_interval_minutes': scheduler.MIN_INTERVAL_MINUTES,
        })

    return api
