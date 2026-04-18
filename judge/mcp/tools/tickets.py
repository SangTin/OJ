"""MCP read tools for tickets (issues).

Visibility rules mirror ``judge.views.ticket.TicketList``:

* users with ``judge.change_ticket`` see every ticket (admin bypass);
* other users see tickets they created, are assigned to, or raised against
  a problem they can edit (``judge.utils.tickets.filter_visible_tickets``).

For linked items we expose the ContentType label and object id. When the
link resolves to a Problem, the problem code is included for convenience
— but only if the caller is allowed to see that problem per
``Problem.is_accessible_by``.
"""
from django.contrib.contenttypes.models import ContentType

from judge.mcp.dispatch import MCPToolError
from judge.mcp.registry import register_tool
from judge.mcp.serializers import json_text
from judge.models import Problem, Ticket
from judge.utils.tickets import filter_visible_tickets, own_ticket_filter

_problem_ct_id = None


def _get_problem_ct_id():
    global _problem_ct_id
    if _problem_ct_id is None:
        _problem_ct_id = ContentType.objects.get_for_model(Problem).id
    return _problem_ct_id


def _visible_queryset(user):
    queryset = Ticket.objects.select_related('user__user').prefetch_related('assignees__user')
    if user.has_perm('judge.change_ticket'):
        return queryset
    return filter_visible_tickets(queryset, user)


def _linked_problem_code(ticket, user, problem_ct_id):
    """Return the linked problem's code if the user may see it, else None."""
    if ticket.content_type_id != problem_ct_id:
        return None
    try:
        problem = Problem.objects.only('code', 'is_public', 'is_organization_private',
                                       'organization_id').get(id=ticket.object_id)
    except Problem.DoesNotExist:
        return None
    if not problem.is_accessible_by(user):
        return None
    return problem.code


def _serialize_ticket_summary(ticket, user, problem_ct_id):
    return {
        'id': ticket.id,
        'title': ticket.title,
        'creator': ticket.user.user.username,
        'assignees': [a.user.username for a in ticket.assignees.all()],
        'time': ticket.time.isoformat() if ticket.time else None,
        'is_open': ticket.is_open,
        'is_contributive': ticket.is_contributive,
        'linked_type': ticket.content_type.model,
        'linked_id': ticket.object_id,
        'problem_code': _linked_problem_code(ticket, user, problem_ct_id),
    }


def _serialize_ticket_detail(ticket, user, problem_ct_id, include_notes):
    data = _serialize_ticket_summary(ticket, user, problem_ct_id)
    data['messages'] = [
        {
            'id': m.id,
            'user': m.user.user.username,
            'body': m.body,
            'time': m.time.isoformat() if m.time else None,
        }
        for m in ticket.messages.select_related('user__user').order_by('time')
    ]
    if include_notes:
        data['notes'] = ticket.notes
    return data


@register_tool(
    name='list_tickets',
    description=(
        'List tickets (issues) visible to the authenticated user. Users with '
        'judge.change_ticket see all; others see own, assigned, or tickets on '
        'problems they can edit. Supports filtering by open status, '
        'ownership, linked problem, creator, and assignee.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'is_open': {
                'type': 'boolean',
                'description': 'If true: only open tickets. If false: only closed.',
            },
            'mine': {
                'type': 'boolean',
                'description': 'Restrict to tickets where the user is creator '
                               'or assignee.',
            },
            'problem_code': {
                'type': 'string',
                'description': 'Only tickets linked to this problem.',
            },
            'creator': {
                'type': 'string',
                'description': 'Filter by ticket creator username.',
            },
            'assignee': {
                'type': 'string',
                'description': 'Filter by assignee username.',
            },
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 20},
            'offset': {'type': 'integer', 'minimum': 0, 'default': 0},
        },
        'additionalProperties': False,
    },
    required_perms=['judge.mcp_read_ticket'],
    annotations={'readOnlyHint': True},
)
def list_tickets(user, is_open=None, mine=None, problem_code=None,
                 creator=None, assignee=None, limit=20, offset=0):
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))

    queryset = _visible_queryset(user).order_by('-id')

    problem_ct_id = _get_problem_ct_id()

    if is_open is not None:
        queryset = queryset.filter(is_open=bool(is_open))
    if mine:
        queryset = queryset.filter(own_ticket_filter(user.profile.id))
    if problem_code:
        try:
            problem = Problem.objects.only('id').get(code=problem_code)
        except Problem.DoesNotExist:
            raise MCPToolError(f'problem not found: {problem_code}')
        queryset = queryset.filter(content_type_id=problem_ct_id, object_id=problem.id)
    if creator:
        queryset = queryset.filter(user__user__username=creator)
    if assignee:
        queryset = queryset.filter(assignees__user__username=assignee)

    queryset = queryset.distinct()
    items = [_serialize_ticket_summary(t, user, problem_ct_id)
             for t in queryset[offset:offset + limit]]
    return json_text({'items': items, 'limit': limit, 'offset': offset})


@register_tool(
    name='get_ticket',
    description=(
        'Fetch a ticket by ID, including its message thread. Admin-only '
        'notes are returned only to users with judge.change_ticket.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'id': {'type': 'integer', 'minimum': 1},
        },
        'required': ['id'],
        'additionalProperties': False,
    },
    required_perms=['judge.mcp_read_ticket'],
    annotations={'readOnlyHint': True},
)
def get_ticket(user, id):
    queryset = _visible_queryset(user)
    try:
        ticket = queryset.select_related('content_type').get(id=id)
    except Ticket.DoesNotExist:
        raise MCPToolError(f'ticket not found or not visible: {id}')
    include_notes = user.has_perm('judge.change_ticket')
    return json_text(_serialize_ticket_detail(ticket, user, _get_problem_ct_id(), include_notes))
