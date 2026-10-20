#!/usr/bin/env python3
"""Scheduled crawls: when a job fires, when it does not, and what a failure does.

These run against a real SQLite database in a temporary directory, never the
live one, and they inject both the clock and the thing that starts a crawl -
so no browser, no network, and no waiting for wall-clock time to pass.
"""

import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASSED, FAILED = [], []
WORKDIR = tempfile.mkdtemp(prefix='toadstone-scheduler-')
os.environ['LIBRECRAWL_DATA_DIR'] = WORKDIR

from src import crawl_db  # noqa: E402

# Point the database at the temporary directory. This must happen before any
# table is touched: the live database is in use by a running crawler, and
# reading it from here has corrupted it before.
LIVE_DB = crawl_db.DB_FILE
crawl_db.DB_FILE = os.path.join(WORKDIR, 'test.db')
assert crawl_db.DB_FILE != LIVE_DB, 'refusing to run against the live database'

from src.core import scheduler  # noqa: E402

NOW = datetime(2026, 10, 18, 12, 0, 0)


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


def fresh():
    """An empty jobs table for each test."""
    with crawl_db.get_db() as conn:
        conn.execute('DELETE FROM scheduled_crawls')


def test_creating_and_listing():
    fresh()
    job_id = scheduler.create_job('https://site.test/', 60, name='Nightly')
    result('create: a job comes back with an id', isinstance(job_id, int) and job_id > 0,
           str(job_id))

    job = scheduler.get_job(job_id)
    result('create: the url and interval are stored',
           job['url'] == 'https://site.test/' and job['every_minutes'] == 60, str(job))
    result('create: a job is enabled and scheduled when made',
           job['enabled'] == 1 and job['next_run_at'], str(job.get('next_run_at')))
    result('create: it appears in the list', len(scheduler.list_jobs()) == 1, 'ok')

    for bad, why in ((0, 'zero'), (1, 'one minute'), (-5, 'negative')):
        try:
            scheduler.create_job('https://site.test/', bad)
            result(f'create: {why} is refused', False, 'it was accepted')
        except ValueError:
            result(f'create: {why} is refused as too frequent', True)
    try:
        scheduler.create_job('', 60)
        result('create: a job with no url is refused', False, 'it was accepted')
    except ValueError:
        result('create: a job with no url is refused', True)


def test_updating_and_deleting():
    fresh()
    job_id = scheduler.create_job('https://site.test/', 60)
    scheduler.update_job(job_id, name='Renamed', enabled=0)
    job = scheduler.get_job(job_id)
    result('update: a changed field sticks', job['name'] == 'Renamed' and job['enabled'] == 0,
           str((job['name'], job['enabled'])))
    result('update: an unknown field is ignored rather than written',
           scheduler.update_job(job_id, nonsense='x') is False, 'ok')

    scheduler.update_job(job_id, settings={'render': True})
    result('update: settings round-trip as a dict',
           scheduler.job_settings(scheduler.get_job(job_id)) == {'render': True},
           str(scheduler.job_settings(scheduler.get_job(job_id))))

    try:
        scheduler.update_job(job_id, every_minutes=1)
        result('update: too short an interval is refused', False, 'it was accepted')
    except ValueError:
        result('update: too short an interval is refused', True)

    scheduler.delete_job(job_id)
    result('delete: the job is gone', scheduler.get_job(job_id) is None, 'ok')


def test_which_jobs_are_due():
    fresh()
    soon = scheduler.create_job('https://site.test/soon', 60)
    later = scheduler.create_job('https://site.test/later', 60)
    scheduler.record_run(soon, crawl_id=1, now=NOW - timedelta(minutes=90))
    scheduler.record_run(later, crawl_id=2, now=NOW - timedelta(minutes=10))

    due = [j['id'] for j in scheduler.due_jobs(NOW)]
    result('due: a job past its interval is due', soon in due, str(due))
    result('due: a job inside its interval is not', later not in due, str(due))

    scheduler.update_job(soon, enabled=0)
    result('due: a disabled job is never due',
           soon not in [j['id'] for j in scheduler.due_jobs(NOW)], 'ok')


def test_a_tick_fires_one_job_and_reschedules():
    fresh()
    fired = []

    def runner(job):
        fired.append(job['id'])
        return 4242

    job_id = scheduler.create_job('https://site.test/', 60)
    scheduler.record_run(job_id, now=NOW - timedelta(minutes=90))

    sched = scheduler.Scheduler(runner, clock=lambda: NOW)
    result('tick: a due job fires', sched.tick(NOW) == [job_id], str(fired))
    result('tick: the crawl it started is recorded',
           scheduler.get_job(job_id)['last_crawl_id'] == 4242,
           str(scheduler.get_job(job_id)['last_crawl_id']))
    result('tick: the run is counted', scheduler.get_job(job_id)['runs'] == 2,
           str(scheduler.get_job(job_id)['runs']))

    # Having just run, it must not run again on the next tick.
    result('tick: a job that just ran is not due again',
           sched.tick(NOW) == [], str(fired))
    result('tick: and the next run is an interval away',
           scheduler._parse(scheduler.get_job(job_id)['next_run_at']) == NOW + timedelta(minutes=60),
           str(scheduler.get_job(job_id)['next_run_at']))


def test_one_crawl_at_a_time():
    """Two crawls of one site at once race on the same rows."""
    fresh()
    for n in range(3):
        job_id = scheduler.create_job(f'https://site.test/{n}', 60)
        scheduler.record_run(job_id, now=NOW - timedelta(minutes=90))

    fired = []
    sched = scheduler.Scheduler(lambda job: fired.append(job['id']) or 1, clock=lambda: NOW)
    result('serial: three due jobs produce one firing, not three',
           len(sched.tick(NOW)) == 1, str(fired))

    sched.busy = True
    result('serial: nothing fires while a crawl is already running',
           sched.tick(NOW) == [], str(fired))
    sched.busy = False
    result('serial: the next tick picks up where it left off',
           len(sched.tick(NOW)) == 1, str(fired))


def test_a_failure_does_not_stop_the_job():
    """A site that is down is exactly the site worth checking again."""
    fresh()
    job_id = scheduler.create_job('https://site.test/', 60)
    scheduler.record_run(job_id, now=NOW - timedelta(minutes=90))

    def runner(job):
        raise RuntimeError('the site is down')

    sched = scheduler.Scheduler(runner, clock=lambda: NOW)
    result('failure: the tick still reports the job as fired', sched.tick(NOW) == [job_id], 'ok')

    job = scheduler.get_job(job_id)
    result('failure: the error is recorded on the job',
           'the site is down' in (job['last_error'] or ''), str(job['last_error']))
    result('failure: the job is still enabled', job['enabled'] == 1, 'ok')
    result('failure: and is scheduled to run again',
           scheduler._parse(job['next_run_at']) == NOW + timedelta(minutes=60),
           str(job['next_run_at']))


def test_a_sleeping_process_does_not_catch_up():
    """Waking after a day should run each job once, not twenty-four times."""
    fresh()
    job_id = scheduler.create_job('https://site.test/', 60)
    scheduler.record_run(job_id, now=NOW - timedelta(hours=24))

    fired = []
    sched = scheduler.Scheduler(lambda job: fired.append(job['id']) or 1, clock=lambda: NOW)
    sched.tick(NOW)
    sched.tick(NOW)
    result('catch-up: a day of missed runs fires once, not twenty-four times',
           len(fired) == 1, str(len(fired)))


def test_the_thread_starts_and_stops():
    fresh()
    sched = scheduler.Scheduler(lambda job: 1, tick_seconds=0.05)
    result('thread: it is not running before start', not sched.running(), 'ok')
    sched.start()
    result('thread: it runs after start', sched.running(), 'ok')
    result('thread: starting twice is a no-op', sched.start() is False, 'ok')
    sched.stop()
    result('thread: it stops', not sched.running(), 'ok')


def test_a_broken_tick_does_not_end_the_scheduler():
    fresh()
    sched = scheduler.Scheduler(lambda job: 1, tick_seconds=0.05)
    calls = []

    def exploding(now=None):
        calls.append(1)
        raise RuntimeError('boom')

    sched.tick = exploding
    sched.start()
    deadline = __import__('time').time() + 1.0
    while len(calls) < 2 and __import__('time').time() < deadline:
        __import__('time').sleep(0.02)
    running = sched.running()
    sched.stop()
    result('resilience: a tick that raises does not kill the loop',
           len(calls) >= 2 and running, f'{len(calls)} ticks, running={running}')


TESTS = (
    test_creating_and_listing,
    test_updating_and_deleting,
    test_which_jobs_are_due,
    test_a_tick_fires_one_job_and_reschedules,
    test_one_crawl_at_a_time,
    test_a_failure_does_not_stop_the_job,
    test_a_sleeping_process_does_not_catch_up,
    test_the_thread_starts_and_stops,
    test_a_broken_tick_does_not_end_the_scheduler,
)


def main():
    if 'data/users.db' in crawl_db.DB_FILE or 'data\\users.db' in crawl_db.DB_FILE:
        print('  refusing to run: the database was not redirected away from the live one')
        return 1
    try:
        crawl_db.init_crawl_tables()
    except Exception as exc:
        print(f'  could not prepare the test database: {exc}')
        return 1

    for test in TESTS:
        print(f'\n{test.__name__}:')
        try:
            test()
        except Exception as exc:  # a raised test is a failed test
            result(test.__name__, False, f'raised {type(exc).__name__}: {exc}')

    shutil.rmtree(WORKDIR, ignore_errors=True)
    print('\n' + '=' * 64)
    print(f'  {len(PASSED)} passed, {len(FAILED)} failed')
    if FAILED:
        print('  failed: ' + ', '.join(FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
