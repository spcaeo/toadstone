#!/usr/bin/env python3
"""The Claude bridge: what it will run, for whom, and what it refuses.

This is the one component in the project whose whole job is to run a program
on behalf of something else, so the tests are mostly about the refusals. A
real server is started on an ephemeral loopback port and driven over HTTP,
with a harmless stand-in for the CLI, so nothing here needs Claude installed
or a network.

The test that matters most is the injection one. The bridge takes text from
another process and passes it to a subprocess; if that ever went through a
shell, a prompt could run commands on the host. It does not, and the test
proves it by trying.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from http.server import ThreadingHTTPServer  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'tools'))

import claude_bridge  # noqa: E402

PASSED, FAILED = [], []
TOKEN = 'test-token-not-a-real-one'


def result(name, ok, detail=''):
    (PASSED if ok else FAILED).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")


class running_bridge:
    """A real bridge on a loopback port, with /bin/echo standing in for the CLI."""

    def __init__(self, command='/bin/echo', timeout=10):
        self.command, self.timeout = command, timeout

    def __enter__(self):
        handler = claude_bridge.make_handler(TOKEN, self.command, self.timeout)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def url(self, path='/ask'):
        return f'http://127.0.0.1:{self.port}{path}'

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def fake_cli(body, _kept=[]):
    """An executable that behaves like the CLI, for the failure cases.

    Written rather than borrowed from /bin, because the obvious candidates are
    not portable: macOS has no /bin/false or /bin/true, and /bin/sleep rejects
    the -p flag, so a test built on them exercises "command not found" while
    claiming to exercise something else. That is exactly how this suite first
    reported a passing test for a failing CLI.
    """
    handle, path = tempfile.mkstemp(suffix='.py')
    os.write(handle, ('#!' + sys.executable + '\n'
                      'import sys, time\n' + body).encode('utf-8'))
    os.close(handle)
    os.chmod(path, 0o700)
    _kept.append(path)
    return path


def call(bridge, body=None, token=TOKEN, path='/ask', method='POST', raw=None):
    """Returns (status, payload). Never raises for an HTTP error status."""
    data = raw if raw is not None else (
        json.dumps(body).encode('utf-8') if body is not None else None)
    headers = {'Content-Type': 'application/json'}
    if token is not None:
        headers['Authorization'] = f'Bearer {token}'
    request = urllib.request.Request(bridge.url(path), data=data,
                                     headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode('utf-8'))
        except Exception:
            return exc.code, {}


# ---------------------------------------------------------------------------
# who may ask
# ---------------------------------------------------------------------------

def test_a_request_without_the_token_is_refused():
    with running_bridge() as bridge:
        status, _ = call(bridge, {'prompt': 'hello'}, token=None)
        result('no token is refused', status == 401, str(status))
        status, _ = call(bridge, {'prompt': 'hello'}, token='')
        result('an empty token is refused', status == 401, str(status))
        status, _ = call(bridge, {'prompt': 'hello'}, token='wrong-token')
        result('the wrong token is refused', status == 401, str(status))
        status, _ = call(bridge, {'prompt': 'hello'}, token=TOKEN[:-1])
        result('a nearly-right token is refused', status == 401, str(status))


def test_the_right_token_gets_an_answer():
    with running_bridge() as bridge:
        status, payload = call(bridge, {'prompt': 'hello'})
        result('the right token is answered', status == 200, str(status))
        result('and the answer is the CLI\'s stdout',
               'hello' in (payload.get('text') or ''), str(payload))


def test_nothing_else_is_exposed():
    with running_bridge() as bridge:
        for path, method in (('/', 'GET'), ('/run', 'POST'), ('/ask', 'GET'),
                             ('/../etc/passwd', 'GET')):
            status, _ = call(bridge, {'prompt': 'x'}, path=path, method=method)
            if status != 404:
                result('only /ask and /health exist', False,
                       f'{method} {path} returned {status}')
                return
        result('only /ask and /health exist, and nothing else answers', True)


def test_health_says_nothing_useful_to_a_stranger():
    with running_bridge() as bridge:
        status, payload = call(bridge, path='/health', method='GET', token=None)
        result('health works without a token, for liveness', status == 200)
        result('and reveals nothing but liveness', payload == {'ok': True},
               str(payload))


# ---------------------------------------------------------------------------
# what it will run
# ---------------------------------------------------------------------------

def test_a_prompt_cannot_run_a_command():
    """The bridge hands caller-supplied text to a subprocess. If that ever
    reached a shell, a prompt would be remote code execution on the host."""
    marker = os.path.join(tempfile.gettempdir(), f'bridge-pwned-{os.getpid()}')
    if os.path.exists(marker):
        os.unlink(marker)
    attacks = [
        f'hello; touch {marker}',
        f'hello && touch {marker}',
        f'hello $(touch {marker})',
        f'hello `touch {marker}`',
        f'hello | touch {marker}',
        f'hello\ntouch {marker}',
    ]
    with running_bridge() as bridge:
        for attack in attacks:
            status, payload = call(bridge, {'prompt': attack})
            if status != 200:
                result('a prompt cannot run a command', False,
                       f'{attack!r} gave {status}')
                return
            if marker in (payload.get('text') or '') is False:
                pass
    created = os.path.exists(marker)
    if created:
        os.unlink(marker)
    result('no shell metacharacter in a prompt ran anything', not created,
           'a prompt created a file on the host')

    # and the text arrived intact rather than being mangled on the way
    with running_bridge() as bridge:
        _, payload = call(bridge, {'prompt': 'a; b && c $(d)'})
        result('the prompt reaches the CLI verbatim',
               'a; b && c $(d)' in (payload.get('text') or ''), str(payload))


def test_the_cli_is_called_with_exactly_two_arguments():
    """-p and the prompt. Anything else would let a caller pass flags."""
    seen = {}
    real_run = subprocess.run

    def spy(args, **kwargs):
        seen['args'] = args
        seen['shell'] = kwargs.get('shell', False)
        return real_run(['/bin/echo', 'ok'], **kwargs)

    subprocess.run = spy
    try:
        claude_bridge.ask_claude('the prompt', 'claude', 10)
    finally:
        subprocess.run = real_run
    result('the CLI is run as an argument list, never a string',
           isinstance(seen.get('args'), list), str(seen.get('args')))
    result('shell=True is never used', seen.get('shell') is False)
    result('exactly the command, -p and the prompt',
           seen.get('args') == ['claude', '-p', 'the prompt'], str(seen.get('args')))


def test_an_oversized_prompt_is_refused():
    with running_bridge() as bridge:
        huge = json.dumps({'prompt': 'x' * (claude_bridge.MAX_PROMPT_BYTES + 100)})
        status, _ = call(bridge, raw=huge.encode('utf-8'))
        result('a prompt over the cap is refused', status == 413, str(status))


def test_a_malformed_request_is_refused_not_crashed():
    with running_bridge() as bridge:
        status, _ = call(bridge, raw=b'not json at all')
        result('a body that is not JSON is refused', status == 400, str(status))
        status, _ = call(bridge, {'nothing': 'here'})
        result('a body with no prompt is refused', status == 400, str(status))
        status, _ = call(bridge, {'prompt': '   '})
        result('a blank prompt is refused', status == 400, str(status))
        status, _ = call(bridge, {'prompt': 12345})
        result('a prompt that is not a string is refused', status == 400, str(status))
        status, _ = call(bridge, raw=b'')
        result('an empty body is refused', status == 413, str(status))


# ---------------------------------------------------------------------------
# failing honestly
# ---------------------------------------------------------------------------

def test_a_missing_cli_says_so():
    with running_bridge(command='/nonexistent/claude') as bridge:
        status, payload = call(bridge, {'prompt': 'hello'})
        result('a missing CLI is a 502, not a 200', status == 502, str(status))
        result('and the message names the problem',
               'not installed' in (payload.get('error') or ''), str(payload))


def test_a_failing_cli_is_not_reported_as_an_answer():
    failing = fake_cli('sys.stderr.write("boom\\n"); sys.exit(3)')
    with running_bridge(command=failing) as bridge:
        status, payload = call(bridge, {'prompt': 'hello'})
        result('a CLI that exits non-zero is an error, not empty success',
               status == 502 and 'text' not in payload, str(payload))
        result('and its stderr is passed on rather than swallowed',
               'boom' in (payload.get('error') or ''), str(payload))


def test_a_silent_cli_is_an_error():
    silent = fake_cli('sys.exit(0)')
    with running_bridge(command=silent) as bridge:
        status, payload = call(bridge, {'prompt': 'hello'})
        result('a CLI that prints nothing is an error',
               status == 502 and 'returned nothing' in (payload.get('error') or ''),
               str(payload))


def test_a_hanging_cli_is_given_up_on():
    hanging = fake_cli('time.sleep(30)')
    with running_bridge(command=hanging, timeout=1) as bridge:
        status, payload = call(bridge, {'prompt': 'hello'})
        result('a CLI that hangs is abandoned, not waited on forever',
               status == 502 and 'did not answer' in (payload.get('error') or ''),
               str(payload))


def test_calls_are_serialised():
    """A crawl with forty pages worth judging must not start forty CLIs."""
    slow = fake_cli('time.sleep(1); print("done")')
    with running_bridge(command=slow, timeout=10) as bridge:
        started = time.time()
        results = []

        def one():
            # 1s each; serialised they take >= 2s, in parallel about 1s
            results.append(call(bridge, {'prompt': 'hello'}))

        threads = [threading.Thread(target=one) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - started
        result('two calls run one after another, not at once', elapsed >= 1.9,
               f'{elapsed:.2f}s for two 1s calls')
        result('and both of them got an answer',
               len(results) == 2 and all(r[0] == 200 for r in results), str(results))


# ---------------------------------------------------------------------------
# the token file
# ---------------------------------------------------------------------------

def test_the_token_file_is_owner_only():
    handle, path = tempfile.mkstemp()
    os.close(handle)
    os.unlink(path)
    saved = os.environ.pop('CLAUDE_BRIDGE_TOKEN', None)
    try:
        token = claude_bridge.load_or_create_token(path)
        result('a token is generated when there is none', len(token) > 20)
        mode = os.stat(path).st_mode & 0o777
        result('the token file is readable only by its owner', mode == 0o600,
               oct(mode))
        again = claude_bridge.load_or_create_token(path)
        result('the same token is reused on the next run', again == token)
    finally:
        if saved is not None:
            os.environ['CLAUDE_BRIDGE_TOKEN'] = saved
        if os.path.exists(path):
            os.unlink(path)


def test_the_environment_wins_over_the_file():
    os.environ['CLAUDE_BRIDGE_TOKEN'] = 'from-the-environment'
    try:
        result('an explicitly set token is used as given',
               claude_bridge.load_or_create_token('/nonexistent/path')
               == 'from-the-environment')
    finally:
        os.environ.pop('CLAUDE_BRIDGE_TOKEN', None)


TESTS = (
    test_a_request_without_the_token_is_refused,
    test_the_right_token_gets_an_answer,
    test_nothing_else_is_exposed,
    test_health_says_nothing_useful_to_a_stranger,
    test_a_prompt_cannot_run_a_command,
    test_the_cli_is_called_with_exactly_two_arguments,
    test_an_oversized_prompt_is_refused,
    test_a_malformed_request_is_refused_not_crashed,
    test_a_missing_cli_says_so,
    test_a_failing_cli_is_not_reported_as_an_answer,
    test_a_silent_cli_is_an_error,
    test_a_hanging_cli_is_given_up_on,
    test_calls_are_serialised,
    test_the_token_file_is_owner_only,
    test_the_environment_wins_over_the_file,
)


def main():
    for test in TESTS:
        print(f'\n{test.__name__}:')
        try:
            test()
        except Exception as exc:  # a raised test is a failed test
            result(test.__name__, False, f'raised {type(exc).__name__}: {exc}')

    print('\n' + '=' * 64)
    print(f'  {len(PASSED)} passed, {len(FAILED)} failed')
    if FAILED:
        print('  failed: ' + ', '.join(FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
