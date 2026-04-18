"""MCP read tools for submissions.

Authorization layers (in order):

1.  ``required_perms`` check in the dispatcher — token scope AND user perm.
2.  Queryset-level filter via ``Submission.get_visible_submissions(user)`` —
    mirrors the site's submission-list view, including the contest
    hidden-scoreboard rule.
3.  Detail-level check via ``Submission.can_see_detail(user)`` — gates the
    source-code payload specifically.
"""
from judge.mcp.dispatch import MCPToolError
from judge.mcp.registry import register_tool
from judge.mcp.serializers import json_text, serialize_submission
from judge.models import Contest, Submission


_COMMON_SELECT_RELATED = (
    'user__user', 'problem', 'language', 'contest_object',
)


@register_tool(
    name='get_submission',
    description=(
        'Fetch a submission by ID. Source code is included only if the user '
        'is authorized to see it (problem owner, own submission, '
        'source_visibility rules, or contest editor).'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'id': {'type': 'integer', 'minimum': 1},
        },
        'required': ['id'],
        'additionalProperties': False,
    },
    required_perms=['judge.mcp_read_submission'],
    annotations={'readOnlyHint': True},
)
def get_submission(user, id):
    queryset = Submission.get_visible_submissions(user).select_related(*_COMMON_SELECT_RELATED)
    try:
        submission = queryset.get(id=id)
    except Submission.DoesNotExist:
        raise MCPToolError(f'submission not found or not visible: {id}')
    include_source = submission.can_see_detail(user)
    return json_text(serialize_submission(submission, include_source=include_source))


@register_tool(
    name='list_submissions',
    description=(
        'List submissions visible to the authenticated user, optionally '
        'filtered by problem code, username, contest key, language key, or '
        'result (e.g. "AC", "WA"). Source code is never included; use '
        'get_submission for individual source retrieval.'
    ),
    input_schema={
        'type': 'object',
        'properties': {
            'problem_code': {'type': 'string'},
            'username': {'type': 'string'},
            'contest_code': {
                'type': 'string',
                'description': 'When set, restricts listing to this contest '
                               'and applies the contest scoreboard rules.',
            },
            'language_key': {'type': 'string'},
            'result': {
                'type': 'string',
                'description': 'SUBMISSION_RESULT code, e.g. AC, WA, TLE.',
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
    required_perms=['judge.mcp_read_submission'],
    annotations={'readOnlyHint': True},
)
def list_submissions(user, problem_code=None, username=None, contest_code=None,
                     language_key=None, result=None, limit=20, offset=0):
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))

    contest = None
    if contest_code:
        try:
            contest = Contest.objects.get(key=contest_code)
        except Contest.DoesNotExist:
            raise MCPToolError(f'contest not found: {contest_code}')

    queryset = (
        Submission.get_visible_submissions(user, contest=contest)
        .order_by('-id')
        .select_related(*_COMMON_SELECT_RELATED)
    )

    if problem_code:
        queryset = queryset.filter(problem__code=problem_code)
    if username:
        queryset = queryset.filter(user__user__username=username)
    if language_key:
        queryset = queryset.filter(language__key=language_key)
    if result:
        queryset = queryset.filter(result=result)

    total = queryset.count()
    items = [serialize_submission(s, include_source=False)
             for s in queryset[offset:offset + limit]]
    return json_text({
        'items': items,
        'limit': limit,
        'offset': offset,
        'total': total,
    })
