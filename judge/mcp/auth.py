"""Bearer-token authentication for the MCP endpoint.

Deliberately separate from ``judge.middleware.APIMiddleware`` so that MCP
tokens have an independent lifecycle (issue/revoke/expire per token instead
of one-per-user), independent scope list, and cannot be used to hit any
non-MCP URL.
"""
import re

from judge.models import MCPToken

__all__ = ['MCPAuthError', 'MCPAuthContext', 'authenticate_request']

TOKEN_PATTERN = re.compile(r'^Bearer ([A-Za-z0-9_-]{48})$')

# Gate permission every MCP call must satisfy, in addition to per-tool perms.
# Admin can grant/revoke this to toggle a user's MCP access without touching
# individual tokens.
MCP_GATE_PERM = 'judge.use_mcp_api'


class MCPAuthError(Exception):
    """Raised when Bearer authentication fails. Message is safe to surface."""


class MCPAuthContext:
    """Per-request authenticated principal.

    Holds the authenticated user, the MCPToken row, and a pre-materialized
    frozenset of scope codenames so the dispatcher avoids N+1 queries when
    a single tool call lists multiple required permissions.
    """

    __slots__ = ('user', 'token', 'scopes')

    def __init__(self, user, token, scopes):
        self.user = user
        self.token = token
        self.scopes = scopes

    def has_scope(self, codename):
        return codename in self.scopes


def authenticate_request(request):
    """Resolve the request's Authorization header to an ``MCPAuthContext``.

    Raises ``MCPAuthError`` on any failure. The caller is responsible for
    turning that into a 401 response.
    """
    header = request.headers.get('authorization', '')
    if not header:
        raise MCPAuthError('missing Authorization header')

    match = TOKEN_PATTERN.match(header)
    if not match:
        raise MCPAuthError('malformed Authorization header')

    token = MCPToken.verify(match.group(1))
    if token is None:
        raise MCPAuthError('invalid or expired token')

    user = token.user
    if not user.is_active:
        raise MCPAuthError('user is inactive')
    if not user.has_perm(MCP_GATE_PERM):
        raise MCPAuthError(f'user lacks {MCP_GATE_PERM}')

    token.touch()
    return MCPAuthContext(user=user, token=token, scopes=frozenset(token.scope_codenames()))
