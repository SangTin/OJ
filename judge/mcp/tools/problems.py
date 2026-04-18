"""MCP read tools for problems.

Design notes:

* ``Problem.get_visible_problems(user)`` gives us the queryset filter
  matching the site's own problem-list view — same rules for public,
  organization-private, suggesting, editor-owned, etc.
* ``Problem.is_accessible_by(user)`` enforces the per-object policy used by
  the problem detail page.
* Test data files (zip, generator, checker) are never exposed. For images
  and PDF statements we return URLs to the existing site routes, which
  inherit the site's access control. Binary fetching of statement assets is
  intentionally out of scope for this initial read-only surface.
"""
from django.db.models import Q

from judge.mcp.dispatch import MCPToolError
from judge.mcp.registry import register_tool
from judge.mcp.serializers import (
    json_text, serialize_problem, serialize_problem_summary,
)
from judge.models import Problem


@register_tool(
    name='get_problem',
    description=(
        'Fetch a problem by its code, including description (markdown), '
        'limits, authors, and statement image URLs. '
        'Test data and checker files are never returned.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'code': {
                'type': 'string',
                'description': 'Problem code (e.g. "a-plus-b").',
            },
        },
        'required': ['code'],
        'additionalProperties': False,
    },
    required_perms=['judge.mcp_read_problem'],
    annotations={'readOnlyHint': True},
)
def get_problem(user, code):
    try:
        problem = Problem.objects.prefetch_related('types', 'authors__user', 'allowed_languages').get(code=code)
    except Problem.DoesNotExist:
        raise MCPToolError(f'problem not found: {code}')
    if not problem.is_accessible_by(user):
        raise MCPToolError('you do not have access to this problem')
    return json_text(serialize_problem(problem))


@register_tool(
    name='list_problems',
    description=(
        'List problems visible to the authenticated user. Returns summaries '
        '(code, name, points, visibility). Supports substring search over '
        'code and name.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'search': {
                'type': 'string',
                'description': 'Substring matched against code or name.',
            },
            'limit': {
                'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20,
            },
            'offset': {
                'type': 'integer', 'minimum': 0, 'default': 0,
            },
        },
        'additionalProperties': False,
    },
    required_perms=['judge.mcp_read_problem'],
    annotations={'readOnlyHint': True},
)
def list_problems(user, search=None, limit=20, offset=0):
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    queryset = Problem.get_visible_problems(user).order_by('code')
    if search:
        queryset = queryset.filter(Q(code__icontains=search) | Q(name__icontains=search))
    total = queryset.count()
    items = [serialize_problem_summary(p) for p in queryset[offset:offset + limit]]
    return json_text({
        'items': items,
        'limit': limit,
        'offset': offset,
        'total': total,
    })
