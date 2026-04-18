"""Tool-call dispatch with permission gating.

The JSON-RPC view calls ``call_tool`` which enforces the three-layer policy
described in ``registry`` before invoking the tool handler.
"""
from django.core.exceptions import PermissionDenied

from judge.mcp.registry import TOOL_REGISTRY

__all__ = ['MCPToolError', 'call_tool', 'list_tools_spec']


class MCPToolError(Exception):
    """Business-logic error raised during a tool call.

    Surfaced to the client as ``content=[TextContent(...)], isError=True``.
    Never leaks tracebacks or internal object identities.
    """

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _check_scopes(tool, auth):
    for perm in tool.required_perms:
        if perm not in auth.scopes:
            raise MCPToolError(f'token lacks scope: {perm}')
        if not auth.user.has_perm(perm):
            raise MCPToolError(f'user lacks permission: {perm}')


def call_tool(name, arguments, auth):
    """Dispatch a tool call. Returns a list of content blocks (dicts).

    ``auth`` is an ``MCPAuthContext`` produced by ``judge.mcp.auth``.
    Does not catch unexpected exceptions — the view logs and wraps them.
    """
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        raise MCPToolError(f'unknown tool: {name}')

    _check_scopes(tool, auth)

    try:
        return tool.handler(user=auth.user, **(arguments or {}))
    except PermissionDenied as e:
        raise MCPToolError(str(e) or 'permission denied')


def list_tools_spec():
    """Return the JSON-RPC ``tools/list`` payload."""
    return [
        {
            'name': t.name,
            'description': t.description,
            'inputSchema': t.input_schema,
            **({'annotations': t.annotations} if t.annotations else {}),
        }
        for t in TOOL_REGISTRY.values()
    ]
