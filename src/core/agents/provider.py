"""How this project reaches a language model, and what it does without one.

Three ways, because the right one depends on where Toadstone is running and
none of them suits every install:

``api``    an Anthropic API key from the environment. Works headless and in
           Docker, which is what the scheduler and the container assume, and
           costs money per crawl.
``cli``    shell out to a locally installed Claude Code. No key, no per-crawl
           cost, and only works on a machine where someone has installed and
           signed into it - so not in the container.
``none``   the default. Agent findings are skipped entirely.

The default is ``none`` on purpose. A crawler that silently starts spending
money, or silently starts sending a client's page content to a third party,
is a crawler that has broken someone's trust. Both of those have to be asked
for.

Two rules hold everywhere in this package.

**A provider that cannot run says so.** It returns an error, which the
callers turn into a visible `agent_unavailable` note. An agent that was never
asked must never be indistinguishable from an agent that found nothing - that
is the same failure as a check which passes because it measured nothing.

**Page content leaves the machine.** Whichever provider is chosen, the text
of the pages being audited is sent to it. That is stated here, in the
settings, and in the documentation, because for anyone crawling a client's
site it is a decision rather than a detail.
"""

import json
import os
import subprocess
import urllib.error
import urllib.request

ANTHROPIC_ENDPOINT = 'https://api.anthropic.com/v1/messages'
ANTHROPIC_VERSION = '2023-06-01'

# Opus is the strongest of the current family, but these calls are short
# judgements over one page at a time and Sonnet answers them well for a lot
# less money. An install that wants the stronger model can say so.
DEFAULT_MODEL = 'claude-sonnet-5-5'

DEFAULTS = {
    'mode': 'none',            # none | api | cli
    'model': DEFAULT_MODEL,
    'api_key_env': 'ANTHROPIC_API_KEY',
    'max_tokens': 2000,
    'timeout': 120,
    'cli_command': 'claude',
    'max_pages': 25,           # agents are slow and priced per page
}


class ProviderError(Exception):
    """The model could not be reached. Callers surface this, never swallow it."""


def _config(config=None):
    cfg = dict(DEFAULTS)
    cfg.update(config or {})
    return cfg


def configured(config=None):
    """Is a provider actually usable, as opposed to merely named?"""
    cfg = _config(config)
    if cfg['mode'] == 'api':
        return bool(os.environ.get(cfg['api_key_env']))
    if cfg['mode'] == 'cli':
        return bool(cfg['cli_command'])
    return False


def describe(config=None):
    """What a person should be told about where their page text is going."""
    cfg = _config(config)
    if cfg['mode'] == 'none':
        return 'No model is configured, so agent findings are skipped.'
    if cfg['mode'] == 'api':
        if not os.environ.get(cfg['api_key_env']):
            return (f'Mode is "api" but {cfg["api_key_env"]} is not set, so agent '
                    f'findings are skipped.')
        return (f'Page text is sent to the Anthropic API ({cfg["model"]}) for the '
                f'pages agents look at.')
    return (f'Page text is sent to a local {cfg["cli_command"]} process, which '
            f'forwards it to Anthropic under that installation\'s own account.')


def ask(prompt, config=None, runner=None):
    """One question, one answer, as plain text. Raises ProviderError on failure.

    `runner` is injected by the tests so none of this needs a key or a
    network; nothing else should pass it.
    """
    cfg = _config(config)
    if runner is not None:
        return runner(prompt, cfg)
    if cfg['mode'] == 'api':
        return _ask_api(prompt, cfg)
    if cfg['mode'] == 'cli':
        return _ask_cli(prompt, cfg)
    raise ProviderError('No model is configured')


def _ask_api(prompt, cfg):
    key = os.environ.get(cfg['api_key_env'])
    if not key:
        raise ProviderError(f'{cfg["api_key_env"]} is not set')
    body = json.dumps({
        'model': cfg['model'],
        'max_tokens': cfg['max_tokens'],
        'messages': [{'role': 'user', 'content': prompt}],
    }).encode('utf-8')
    request = urllib.request.Request(ANTHROPIC_ENDPOINT, data=body, headers={
        'content-type': 'application/json',
        'x-api-key': key,
        'anthropic-version': ANTHROPIC_VERSION,
    })
    try:
        with urllib.request.urlopen(request, timeout=cfg['timeout']) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ProviderError('The API key was rejected')
        if exc.code == 429:
            raise ProviderError('Rate limited by the API')
        raise ProviderError(f'The API returned HTTP {exc.code}')
    except Exception as exc:
        raise ProviderError(f'Could not reach the API: {type(exc).__name__}')

    parts = [block.get('text', '') for block in payload.get('content') or []
             if isinstance(block, dict) and block.get('type') == 'text']
    text = '\n'.join(part for part in parts if part).strip()
    if not text:
        raise ProviderError('The model returned nothing')
    return text


def _ask_cli(prompt, cfg):
    try:
        finished = subprocess.run(
            [cfg['cli_command'], '-p', prompt],
            capture_output=True, text=True, timeout=cfg['timeout'])
    except FileNotFoundError:
        raise ProviderError(f'{cfg["cli_command"]} is not installed on this machine')
    except subprocess.TimeoutExpired:
        raise ProviderError(f'{cfg["cli_command"]} did not answer in time')
    except Exception as exc:
        raise ProviderError(f'Could not run {cfg["cli_command"]}: {type(exc).__name__}')
    if finished.returncode != 0:
        detail = (finished.stderr or '').strip()[:160]
        raise ProviderError(f'{cfg["cli_command"]} failed: {detail or finished.returncode}')
    text = (finished.stdout or '').strip()
    if not text:
        raise ProviderError(f'{cfg["cli_command"]} returned nothing')
    return text


def ask_json(prompt, config=None, runner=None):
    """Ask for JSON and insist on getting it.

    Models wrap JSON in prose and fences however firmly they are asked not
    to, so the first balanced object in the reply is taken rather than
    trusting the whole string to parse. A reply with no object at all is a
    provider error, not an empty result: silently returning {} here would
    turn a broken model into a page with nothing wrong.
    """
    text = ask(prompt, config, runner)
    start = text.find('{')
    while start != -1:
        depth, in_string, escaped = 0, False, False
        for index in range(start, len(text)):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == '\\':
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:index + 1])
                    except ValueError:
                        break
        start = text.find('{', start + 1)
    raise ProviderError('The model did not return readable JSON')
