"""MCP JSON-RPC 2.0 endpoint.

Implements a minimal subset of MCP Streamable HTTP, non-streaming mode:
a single POST with a JSON-RPC request body returns a JSON-RPC response.

We do NOT use ``mcp.server.*`` transports because those require an ASGI
runtime and this project is WSGI-only. Types-only usage of the SDK is kept
for future migration; see ``dmoj/wsgi_async.py``. When the project moves to
ASGI, this view can be replaced with ``FastMCP.streamable_http_app()``.
"""
import json
import logging

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from judge.mcp.auth import MCPAuthError, authenticate_request
from judge.mcp.dispatch import MCPToolError, call_tool, list_tools_spec

__all__ = ['mcp_endpoint']

logger = logging.getLogger('judge.mcp')

PROTOCOL_VERSION = '2024-11-05'
SERVER_INFO = {'name': 'dmoj-mcp', 'version': '0.1.0'}

# JSON-RPC 2.0 reserved error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _ensure_tools_loaded():
    """Import tool modules so their @register_tool side effects run."""
    from judge.mcp import tools  # noqa: F401


def _rpc_error(rpc_id, code, message, data=None):
    err = {'code': code, 'message': message}
    if data is not None:
        err['data'] = data
    return JsonResponse({'jsonrpc': '2.0', 'id': rpc_id, 'error': err})


def _rpc_result(rpc_id, result):
    return JsonResponse({'jsonrpc': '2.0', 'id': rpc_id, 'result': result})


def _auth_failure(message):
    resp = HttpResponse(message, status=401)
    resp['WWW-Authenticate'] = 'Bearer realm="MCP"'
    return resp


@csrf_exempt
@require_POST
def mcp_endpoint(request):
    _ensure_tools_loaded()

    try:
        auth = authenticate_request(request)
    except MCPAuthError as e:
        return _auth_failure(str(e))

    try:
        body = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return _rpc_error(None, PARSE_ERROR, 'parse error')

    if not isinstance(body, dict) or body.get('jsonrpc') != '2.0':
        return _rpc_error(body.get('id') if isinstance(body, dict) else None,
                          INVALID_REQUEST, 'invalid JSON-RPC 2.0 envelope')

    rpc_id = body.get('id')
    method = body.get('method')
    params = body.get('params') or {}

    # Notifications (no id) should get 202 Accepted and no body per spec.
    is_notification = rpc_id is None

    try:
        if method == 'initialize':
            result = {
                'protocolVersion': PROTOCOL_VERSION,
                'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': SERVER_INFO,
            }
            return _rpc_result(rpc_id, result)

        if method in ('notifications/initialized', 'notifications/cancelled'):
            return HttpResponse(status=202)

        if method == 'ping':
            return _rpc_result(rpc_id, {})

        if method == 'tools/list':
            return _rpc_result(rpc_id, {'tools': list_tools_spec(auth)})

        if method == 'tools/call':
            name = params.get('name')
            arguments = params.get('arguments') or {}
            if not isinstance(name, str):
                return _rpc_error(rpc_id, INVALID_PARAMS, 'missing tool name')
            try:
                content = call_tool(name, arguments, auth)
                return _rpc_result(rpc_id, {'content': content, 'isError': False})
            except MCPToolError as e:
                return _rpc_result(rpc_id, {
                    'content': [{'type': 'text', 'text': e.message}],
                    'isError': True,
                })
            except TypeError as e:
                # bad arguments for handler signature
                return _rpc_error(rpc_id, INVALID_PARAMS, str(e))

        if is_notification:
            return HttpResponse(status=202)
        return _rpc_error(rpc_id, METHOD_NOT_FOUND, f'method not found: {method}')

    except Exception:
        logger.exception('MCP internal error (method=%s, user=%s)',
                         method, auth.user.username)
        return _rpc_error(rpc_id, INTERNAL_ERROR, 'internal error')
