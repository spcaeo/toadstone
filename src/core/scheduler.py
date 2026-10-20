"""Recurring crawls: a standing instruction, fired on a timer.

Both commercial crawlers schedule; this did not, so every crawl had to be
started by a person watching a browser. That is the difference between a tool
you reach for and a tool that tells you when something broke.

A job is deliberately thin. It holds a URL, an interval and the settings to
crawl with, and when it fires it starts an **ordinary crawl** - the same code
path, the same row in the same table. Everything downstream therefore works
on a scheduled crawl without knowing it was scheduled: the regression diff,
the exports, the webhook, the check reference. A scheduler that invented its
own kind of crawl would have to reimplement all of that, and would drift.

Three decisions worth stating.

**One crawl at a time.** Two crawls of the same site at once would race on
the same rows and halve each other's rate limit. A job whose turn comes while
another is running waits for the next tick rather than queueing up, because a
backlog of stale crawls is worth less than one fresh one.

**A failure reschedules.** A site that is down when the timer fires must not
silently stop being monitored - that is exactly when monitoring matters. The
error is recorded on the job and the next run is set as normal.

**The timer is checked, not trusted.** `next_run_at` is stored, so a process
that was asleep for a day runs each due job once on waking rather than
catching up on twenty-four missed hours.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone

from src import crawl_db

# How often the loop looks for work. A job cannot fire more precisely than
# this, which is why the minimum interval is comfortably larger.
TICK_SECONDS = 30

MIN_INTERVAL_MINUTES = 5


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(value):
    return value.strftime('%Y-%m-%d %H:%M:%S') if value else None


def _parse(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    for shape in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(str(value), shape)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# the job store
# ---------------------------------------------------------------------------

def create_job(url, every_minutes, user_id=None, name='', settings=None):
    """Add a standing instruction. Returns the job id."""
    url = (url or '').strip()
    if not url:
        raise ValueError('a job needs a url')
    every = int(every_minutes or 0)
    if every < MIN_INTERVAL_MINUTES:
        raise ValueError(f'the shortest interval is {MIN_INTERVAL_MINUTES} minutes')
    with crawl_db.get_db() as conn:
        cursor = conn.execute(
            'INSERT INTO scheduled_crawls (user_id, url, name, every_minutes, settings, '
            'enabled, next_run_at) VALUES (?, ?, ?, ?, ?, 1, ?)',
            (user_id, url, name or url, every, json.dumps(settings or {}),
             _iso(_now() + timedelta(minutes=every))))
        return cursor.lastrowid


def list_jobs(user_id=None):
    query = 'SELECT * FROM scheduled_crawls'
    params = []
    if user_id is not None:
        query += ' WHERE user_id = ?'
        params.append(user_id)
    query += ' ORDER BY id'
    with crawl_db.get_db() as conn:
        return [dict(row) for row in conn.execute(query, params).fetchall()]


def get_job(job_id):
    with crawl_db.get_db() as conn:
        row = conn.execute('SELECT * FROM scheduled_crawls WHERE id = ?', (job_id,)).fetchone()
        return dict(row) if row else None


def update_job(job_id, **fields):
    allowed = {'url', 'name', 'every_minutes', 'settings', 'enabled'}
    changes = {k: v for k, v in fields.items() if k in allowed}
    if not changes:
        return False
    if 'settings' in changes and not isinstance(changes['settings'], str):
        changes['settings'] = json.dumps(changes['settings'] or {})
    if 'every_minutes' in changes:
        every = int(changes['every_minutes'])
        if every < MIN_INTERVAL_MINUTES:
            raise ValueError(f'the shortest interval is {MIN_INTERVAL_MINUTES} minutes')
        changes['next_run_at'] = _iso(_now() + timedelta(minutes=every))
    sets = ', '.join(f'{key} = ?' for key in changes)
    with crawl_db.get_db() as conn:
        conn.execute(f'UPDATE scheduled_crawls SET {sets} WHERE id = ?',
                     list(changes.values()) + [job_id])
    return True


def delete_job(job_id):
    with crawl_db.get_db() as conn:
        conn.execute('DELETE FROM scheduled_crawls WHERE id = ?', (job_id,))
    return True


def due_jobs(now=None):
    """Jobs whose turn has come. Never more than one firing per job per tick."""
    now = now or _now()
    with crawl_db.get_db() as conn:
        rows = conn.execute(
            'SELECT * FROM scheduled_crawls WHERE enabled = 1 '
            'AND (next_run_at IS NULL OR next_run_at <= ?) ORDER BY next_run_at',
            (_iso(now),)).fetchall()
    return [dict(row) for row in rows]


def record_run(job_id, crawl_id=None, error='', now=None):
    """Mark a job as having fired, and set when it should fire next.

    A failure reschedules exactly like a success: a site that is down is
    precisely the site worth checking again.
    """
    now = now or _now()
    job = get_job(job_id)
    every = int((job or {}).get('every_minutes') or MIN_INTERVAL_MINUTES)
    with crawl_db.get_db() as conn:
        conn.execute(
            'UPDATE scheduled_crawls SET last_run_at = ?, last_crawl_id = ?, '
            'last_error = ?, next_run_at = ?, runs = COALESCE(runs, 0) + 1 WHERE id = ?',
            (_iso(now), crawl_id, error or '', _iso(now + timedelta(minutes=every)), job_id))


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------

class Scheduler:
    """Fires due jobs, one crawl at a time.

    `runner(job)` starts a crawl and returns its id, or raises. It is injected
    rather than imported so this module never has to know how a crawl is
    started, and so the tests need neither a browser nor a network.
    """

    def __init__(self, runner, tick_seconds=TICK_SECONDS, clock=None):
        self.runner = runner
        self.tick_seconds = tick_seconds
        self.clock = clock or _now
        self._thread = None
        self._stop = threading.Event()
        self.busy = False
        self.last_tick = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name='toadstone-scheduler',
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout=5):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        return True

    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # a bad tick must not end the scheduler
                print(f'Scheduler tick failed: {type(exc).__name__}: {exc}')
            self._stop.wait(self.tick_seconds)

    def tick(self, now=None):
        """One pass. Returns the job ids fired, which is [] or one id."""
        now = now or self.clock()
        self.last_tick = now
        if self.busy:
            return []  # one crawl at a time; the rest wait for the next tick
        due = due_jobs(now)
        if not due:
            return []
        job = due[0]
        self.busy = True
        try:
            crawl_id, error = None, ''
            try:
                crawl_id = self.runner(job)
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'
            record_run(job['id'], crawl_id, error, now)
            return [job['id']]
        finally:
            self.busy = False


def job_settings(job):
    """The stored settings for a job, as a dict, whatever shape they are in."""
    raw = (job or {}).get('settings')
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or '{}')
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}
