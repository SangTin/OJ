"""MCP read tools for contests.

Authorization layers (in order):

1. ``required_perms`` on each tool are enforced by the MCP dispatcher, which
   checks both token scope and the owning user's Django permission.
2. Queryset-level filtering for contest lists uses
   ``Contest.get_visible_contests(user)`` so the MCP surface matches the
   site's own contest-list visibility rules.
3. Detail and standings access apply per-object policy via
   ``Contest.is_accessible_by(user)`` and ``Contest.can_see_full_scoreboard(user)``.

Standings also respect contest freezing. Non-editor users see frozen values
when the contest is currently frozen; editors keep seeing live values, matching
the site's ranking view.
"""
from django.db.models import Prefetch
from django.utils import timezone

from judge.mcp.dispatch import MCPToolError
from judge.mcp.registry import register_tool
from judge.mcp.serializers import (
    json_text, serialize_contest_detail, serialize_contest_standings_row,
    serialize_contest_summary,
)
from judge.models import Contest, ContestParticipation, ContestProblem


@register_tool(
    name='list_contests',
    description=(
        'List contests visible to the authenticated user, filterable by '
        'status (upcoming / running / ended) and organization slug.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'status': {
                'type': 'string',
                'enum': ['upcoming', 'running', 'ended'],
            },
            'organization': {
                'type': 'string',
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
    required_perms=['judge.mcp_read_contest'],
    annotations={'readOnlyHint': True},
)
def list_contests(user, status=None, organization=None, limit=20, offset=0):
    """List visible contests with optional status and organization filters."""
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))

    queryset = Contest.get_visible_contests(user).order_by('-end_time')
    now = timezone.now()

    if status == 'upcoming':
        queryset = queryset.filter(start_time__gt=now)
    elif status == 'running':
        queryset = queryset.filter(start_time__lte=now, end_time__gt=now)
    elif status == 'ended':
        queryset = queryset.filter(end_time__lte=now)

    if organization:
        queryset = queryset.filter(organization__slug=organization)

    total = queryset.count()
    items = [serialize_contest_summary(contest) for contest in queryset[offset:offset + limit]]
    return json_text({
        'items': items,
        'limit': limit,
        'offset': offset,
        'total': total,
    })


@register_tool(
    name='get_contest',
    description='Fetch a contest by key, including its problem list.',
    input_schema={
        'type': 'object',
        'properties': {
            'key': {
                'type': 'string',
            },
        },
        'required': ['key'],
        'additionalProperties': False,
    },
    required_perms=['judge.mcp_read_contest'],
    annotations={'readOnlyHint': True},
)
def get_contest(user, key):
    """Fetch one contest with its ordered contest problem list."""
    try:
        contest = Contest.objects.prefetch_related(
            Prefetch(
                'contest_problems',
                queryset=ContestProblem.objects.select_related('problem').order_by('order'),
            ),
        ).get(key=key)
    except Contest.DoesNotExist:
        raise MCPToolError(f'contest not found: {key}')

    if not contest.is_accessible_by(user):
        raise MCPToolError('you do not have access to this contest')

    return json_text(serialize_contest_detail(contest))


@register_tool(
    name='get_contest_standings',
    description=(
        'Fetch contest standings (top N live participants) ordered by score, '
        "cumulative time, tiebreaker. Respects the contest's frozen state "
        'for non-editor users.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'key': {
                'type': 'string',
            },
            'limit': {
                'type': 'integer', 'minimum': 1, 'maximum': 200, 'default': 50,
            },
            'offset': {
                'type': 'integer', 'minimum': 0, 'default': 0,
            },
        },
        'required': ['key'],
        'additionalProperties': False,
    },
    required_perms=['judge.mcp_read_contest'],
    annotations={'readOnlyHint': True},
)
def get_contest_standings(user, key, limit=50, offset=0):
    """Fetch the contest scoreboard for live participations."""
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))

    try:
        contest = Contest.objects.get(key=key)
    except Contest.DoesNotExist:
        raise MCPToolError(f'contest not found: {key}')

    if not contest.is_accessible_by(user):
        raise MCPToolError('no access to this contest')
    if not contest.can_see_full_scoreboard(user):
        raise MCPToolError('you cannot view the full scoreboard yet')

    use_frozen = contest.is_frozen and (
        not user.is_authenticated or user.profile.id not in contest.editor_ids
    )

    queryset = contest.users.filter(virtual=ContestParticipation.LIVE).select_related('user__user')
    if use_frozen:
        queryset = queryset.order_by('-frozen_score', 'frozen_cumtime', 'frozen_tiebreaker')
    else:
        queryset = queryset.order_by('-score', 'cumtime', 'tiebreaker')

    total = queryset.count()
    items = []
    for idx, participation in enumerate(queryset[offset:offset + limit], start=offset + 1):
        items.append(serialize_contest_standings_row(participation, rank=idx, use_frozen=use_frozen))

    return json_text({
        'items': items,
        'limit': limit,
        'offset': offset,
        'total': total,
        'frozen': use_frozen,
    })
