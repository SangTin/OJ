"""Registry for MCP tools.

Each tool declares the Django permission codenames it requires. The
dispatcher gates each call on two independent checks:

    * every required perm appears in the presented token's ``scopes`` list
      (the token is not allowed to do more than it was issued for), and
    * ``user.has_perm(perm)`` returns True for the owning user (the token
      cannot exceed the user's current permissions — subset invariant).

Object-level authorization (e.g. ``Problem.is_accessible_by``) remains the
tool body's responsibility.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

__all__ = ['MCPTool', 'TOOL_REGISTRY', 'register_tool']


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: Dict[str, Any]
    required_perms: List[str]
    handler: Callable[..., List[Dict[str, Any]]]
    # Future: annotations (readOnlyHint, destructiveHint, openWorldHint...)
    annotations: Dict[str, Any] = field(default_factory=dict)


TOOL_REGISTRY: Dict[str, MCPTool] = {}


def get_required_perms():
    """Return the union of all permission codenames required by registered tools.

    Used by the token issuance form to show only MCP-relevant permissions
    instead of the user's full permission set.
    """
    perms = set()
    for tool in TOOL_REGISTRY.values():
        perms.update(tool.required_perms)
    return perms


def register_tool(name, description, input_schema, required_perms, annotations=None):
    """Decorator: register a function as an MCP tool.

    The handler is called with ``user`` as a keyword argument plus the
    validated tool arguments. It must return a list of MCP content blocks
    (dicts with at least a ``type`` key).
    """
    def decorator(fn):
        if name in TOOL_REGISTRY:
            raise RuntimeError(f'MCP tool already registered: {name}')
        TOOL_REGISTRY[name] = MCPTool(
            name=name,
            description=description,
            input_schema=input_schema,
            required_perms=list(required_perms),
            handler=fn,
            annotations=dict(annotations or {}),
        )
        return fn
    return decorator
