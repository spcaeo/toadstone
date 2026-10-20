"""The MCP protocol itself: JSON-RPC 2.0 over whichever transport carries it.

Written against the protocol rather than against the official `mcp` SDK. The
SDK is not in `requirements.txt` and is not installed in the image this app
ships in, and a server that only runs where someone happened to `pip install
mcp` is not a feature of Toadstone. The protocol surface a tools-only server
needs is four methods, and they are all here:

    initialize                 version handshake, capabilities, server name
    notifications/initialized  acknowledged by saying nothing
    tools/list                 the catalogue from src/mcp/tools.py
    tools/call                 run one, and report failures as tool results
    ping                       liveness

`handle` takes a decoded request and returns a decoded response, or None for a
notification, so stdio and HTTP share every line of this. Protocol-level
errors (a malformed frame, an unknown method) come back as JSON-RPC errors; a
tool that refused or failed comes back as a *successful* response carrying
`isError: true`, which is what the specification asks for and what lets a
model read the refusal and correct itself instead of seeing a transport fault.
"""

import json
import traceback

from src.mcp.client import ApiError
from src.mcp.guardrails import GuardrailError
from src.mcp.tools import ToolError, call as call_tool, tool_list

PROTOCOL_VERSION = '2025-06-18'
SUPPORTED_VERSIONS = ('2025-06-18', '2025-03-26', '2024-11-05')
SERVER_NAME = 'toadstone'
SERVER_VERSION = '1.0.0'

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class Server:
    """One MCP session over one `Tools` instance."""

    def __init__(self, tools, settings):
        self.tools = tools
        self.settings = settings

    # -- framing ------------------------------------------------------------

    def handle_text(self, line):
        """Decode one JSON frame, handle it, and encode the reply (or None)."""
        try:
            message = json.loads(line)
        except ValueError as error:
            return json.dumps(_error(None, PARSE_ERROR, f'Invalid JSON: {error}'))
        response = self.handle(message)
        return None if response is None else json.dumps(response, default=str)

    def handle(self, message):
        """One decoded request in, one decoded response out (None = notification)."""
        if isinstance(message, list):
            # Batches were removed in 2025-06-18 and this server never needed
            # them; say so rather than silently answering the first element.
            return _error(None, INVALID_REQUEST,
                          'Batched requests are not supported; send one request '
                          'per message.')
        if not isinstance(message, dict):
            return _error(None, INVALID_REQUEST,
                          f'A request must be an object, got {type(message).__name__}.')

        request_id = message.get('id')
        method = message.get('method')
        params = message.get('params')
        if params is None:
            params = {}
        # An empty list is falsy, so `params or {}` would wave `[]` through as
        # if it had not been sent at all.
        if not isinstance(params, dict):
            return _error(request_id, INVALID_PARAMS,
                          f'params must be an object, got {type(params).__name__}.')

        if method is None:
            # A response, not a request. Nothing sends us requests, so there is
            # nothing to correlate it to; ignore it rather than answer it.
            return None
        if not isinstance(method, str):
            return _error(request_id, INVALID_REQUEST, 'method must be a string.')

        try:
            result = self._dispatch(method, params, request_id)
        except _Rejected as rejection:
            return _error(request_id, rejection.code, rejection.message)
        except Exception as error:  # a bug here must not kill the session
            traceback.print_exc()
            return _error(request_id, INTERNAL_ERROR,
                          f'{type(error).__name__}: {error}')

        if request_id is None:
            return None
        return {'jsonrpc': '2.0', 'id': request_id, 'result': result}

    # -- methods ------------------------------------------------------------

    def _dispatch(self, method, params, request_id):
        if method == 'initialize':
            return self._initialize(params)
        if method in ('notifications/initialized', 'notifications/cancelled',
                      'notifications/progress'):
            return None
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return {'tools': tool_list(self.settings)}
        if method == 'tools/call':
            return self._call(params)
        if method in ('resources/list', 'prompts/list'):
            # Declared unsupported in `initialize`, but clients still ask.
            raise _Rejected(METHOD_NOT_FOUND,
                            f'This server exposes tools only; {method} is not '
                            'supported.')
        raise _Rejected(METHOD_NOT_FOUND, f'Unknown method {method!r}.')

    def _initialize(self, params):
        wanted = params.get('protocolVersion')
        version = wanted if wanted in SUPPORTED_VERSIONS else PROTOCOL_VERSION
        return {
            'protocolVersion': version,
            'capabilities': {'tools': {'listChanged': False}},
            'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION},
            'instructions': _instructions(self.settings),
        }

    def _call(self, params):
        name = params.get('name')
        if not isinstance(name, str) or not name:
            raise _Rejected(INVALID_PARAMS, 'tools/call needs a tool name.')
        arguments = params.get('arguments')
        if arguments is None:
            arguments = {}

        try:
            payload = call_tool(self.tools, name, arguments)
        except (ToolError, GuardrailError) as refusal:
            return _tool_error(_refusal_payload(refusal))
        except ApiError as error:
            return _tool_error({'error': error.message, 'status': error.status,
                                'path': error.path,
                                'hint': _api_hint(error)})
        except TypeError as error:
            # A wrong argument type that got past the schema check lands here.
            return _tool_error({'error': f'{name} rejected its arguments: {error}'})
        except Exception as error:
            traceback.print_exc()
            return _tool_error({'error': f'{name} failed: {type(error).__name__}: '
                                         f'{error}'})

        return {'content': [{'type': 'text',
                             'text': json.dumps(payload, indent=2, default=str)}],
                'isError': False}


class _Rejected(Exception):
    """A JSON-RPC-level error: the request itself was not answerable."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _refusal_payload(refusal):
    if isinstance(refusal, GuardrailError):
        return dict(refusal.as_dict(), guardrails_in_force=True)
    return {'error': str(refusal)}


def _api_hint(error):
    if error.status == 403:
        return 'That crawl belongs to another account.'
    if error.status == 404:
        return 'Call list_crawls to see which crawl ids exist.'
    if error.status == 401:
        return ('Toadstone did not accept this session. Set TOADSTONE_MCP_USERNAME '
                'and TOADSTONE_MCP_PASSWORD, or run Toadstone in local mode.')
    if error.status is None:
        return 'Toadstone is not reachable; check that it is running.'
    return ''


def _tool_error(payload):
    return {'content': [{'type': 'text', 'text': json.dumps(payload, indent=2,
                                                            default=str)}],
            'isError': True}


def _error(request_id, code, message):
    return {'jsonrpc': '2.0', 'id': request_id,
            'error': {'code': code, 'message': message}}


def _instructions(settings):
    guards = settings.describe()
    return (
        'Toadstone is an SEO crawler. Crawls have numeric ids; issues have ids '
        'that are stable across crawls, so the same defect keeps its id until it '
        'is fixed.\n\n'
        'Start with list_crawls to find a crawl id, then list_issues for what is '
        'wrong, page_detail for one URL, and regression_diff to compare two '
        'crawls. check_reference explains any check slug.\n\n'
        f'Guardrails: {guards["mode"]}; crawls allowed against {guards["allowed_hosts"]}; '
        f'at most {guards["max_urls_per_crawl"]} URLs per crawl.'
    )


def build(settings, client):
    """A ready `Server` for these settings over this client."""
    from src.mcp.guardrails import Guardrails
    from src.mcp.tools import Tools

    guardrails = Guardrails(settings, client)
    return Server(Tools(client, guardrails, settings), settings)
