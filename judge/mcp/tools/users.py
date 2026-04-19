"""MCP read tools for user profiles.

The authentication layer already gates these tools on the ``mcp_read_user``
scope plus the owning user's Django permission. No additional per-user
object-level authorization is required beyond banned/unlisted visibility:
profiles are inherently public on DMOJ, and these tools follow the same
visibility semantics as the web user list.
"""
from django.db.models import Q

from judge.mcp.dispatch import MCPToolError
from judge.mcp.registry import register_tool
from judge.mcp.serializers import json_text, serialize_user_detail, serialize_user_summary
from judge.models import Profile


def _visible_queryset(request_user):
    queryset = Profile.objects.select_related('user').prefetch_related('organizations', 'badges')
    if request_user.is_staff:
        return queryset
    return queryset.filter(is_unlisted=False, user__is_active=True)


def _is_profile_visible(profile, request_user):
    if request_user.is_staff or profile.user_id == request_user.id:
        return True
    return not profile.is_unlisted and not profile.is_banned


@register_tool(
    name='get_user',
    description=(
        'Fetch a user profile by username, including public profile fields, '
        'organizations, badges, and rank information.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'username': {
                'type': 'string',
                'description': 'Exact username to fetch.',
            },
        },
        'required': ['username'],
        'additionalProperties': False,
    },
    required_perms=['judge.mcp_read_user'],
    annotations={'readOnlyHint': True},
)
def get_user(user, username):
    try:
        profile = (
            Profile.objects.select_related('user')
            .prefetch_related('organizations', 'badges')
            .get(user__username=username)
        )
    except Profile.DoesNotExist:
        raise MCPToolError('user not found')

    if not _is_profile_visible(profile, user):
        raise MCPToolError('user not found')

    return json_text(serialize_user_detail(profile, viewer=user))


@register_tool(
    name='list_users',
    description=(
        'List user profiles with optional search, organization filter, and '
        'ordering by performance points, rating, problem count, or username.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'search': {
                'type': 'string',
                'description': 'Substring matched against username or first name.',
            },
            'organization': {
                'type': 'string',
                'description': 'Organization slug to filter by.',
            },
            'order_by': {
                'type': 'string',
                'enum': ['-performance_points', '-rating', '-problem_count', 'username'],
                'default': '-performance_points',
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
    required_perms=['judge.mcp_read_user'],
    annotations={'readOnlyHint': True},
)
def list_users(user, search=None, organization=None, order_by='-performance_points', limit=20, offset=0):
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))

    queryset = _visible_queryset(user)
    if search:
        queryset = queryset.filter(
            Q(user__username__icontains=search) | Q(user__first_name__icontains=search),
        )
    if organization:
        queryset = queryset.filter(organizations__slug=organization)

    order_map = {
        '-performance_points': '-performance_points',
        '-rating': '-rating',
        '-problem_count': '-problem_count',
        'username': 'user__username',
    }
    queryset = queryset.order_by(order_map[order_by], 'id').distinct()

    total = queryset.count()
    items = [serialize_user_summary(profile) for profile in queryset[offset:offset + limit]]
    return json_text({
        'items': items,
        'limit': limit,
        'offset': offset,
        'total': total,
    })


@register_tool(
    name='me',
    description='Fetch the current authenticated user profile.',
    input_schema={
        'type': 'object',
        'properties': {},
        'additionalProperties': False,
    },
    required_perms=['judge.mcp_read_user'],
    annotations={'readOnlyHint': True},
)
def me(user):
    try:
        profile = (
            Profile.objects.select_related('user')
            .prefetch_related('organizations', 'badges')
            .get(user=user)
        )
    except Profile.DoesNotExist:
        raise MCPToolError('user not found')
    return json_text(serialize_user_detail(profile, viewer=user))
