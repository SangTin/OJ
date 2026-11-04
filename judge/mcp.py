"""Stateless MCP transport and deliberately bounded problem/contest editing tools."""
import json
import logging
import math
from urllib.parse import urlsplit

import reversion
from django.conf import settings
from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core import serializers
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.forms import model_to_dict, modelform_factory
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from judge.mcp_auth import json_server_errors
from judge.models import Contest, ContestProblem, Organization, Problem, Submission
from judge.utils.authoring import validate_contest_duration, validate_organization_code, validate_problem_time_limit

logger = logging.getLogger(__name__)
PROTOCOLS = ('2025-03-26', '2025-06-18', '2025-11-25')
STATEMENT_FORMAT = (
    'Follow the statement language explicitly requested in the user prompt. '
    'When editing/importing, preserve the source language unless translation is requested. '
    'If the intended language is unclear, ask the user before composing, translating or publishing. '
    'Do not infer the statement language from these English instructions or their examples. '
    'Write raw Markdown descriptions; use https://commonmark.org/help/ for basic syntax. '
    'Use a space after heading markers (## Input, not ##Input) and blank lines between sections. '
    'Use a single tilde on each side, ~...~, for LaTeX math and variables: ~a+b~, ~n~, ~i~, ~a_i~. '
    'Double tildes ~~...~~ mean strikethrough, not math. Do not wrap math in backticks. '
    'Organize the introduction, task, input, output, constraints and examples according to the user layout '
    'and chosen language; distinguish scoring subtasks from overall constraints. '
    'Preserve named I/O files and do not invent missing limits or requirements. '
    'Keep each sample input/output in a separate fenced code block, with literal values and no math delimiters. '
    'Always use English sample labels: ### Input 1, ### Output 1, ### Input 2, ### Output 2, etc., '
    'regardless of the statement language. Number examples consecutively. '
    'Add missing example numbers in source order without changing sample data or input/output pairing. '
    'Check examples for inconsistencies; explain issues and ask for confirmation before publishing corrections. '
    'Do not wrap the entire statement in a code block. Math does not require is_full_markup. '
    'Preserve LaTeX backslashes through JSON serialization. '
    'After successfully creating a problem or contest, include a clickable Markdown link to the created object '
    'in the user-facing reply using the URL returned by the tool. Resolve relative URLs against the MCP server '
    'origin. Only report creation and its link after tool success; do not invent URLs or claim success on failure.'
)
# User-related relations and executable contest label scripts are intentionally not exposed.
PROBLEM_FIELDS = {
    'code': 'string', 'name': 'string', 'description': 'string', 'source': 'string', 'summary': 'string',
    'group': 'integer', 'types': 'ids', 'allowed_languages': 'ids', 'time_limit': 'number',
    'memory_limit': 'integer', 'points': 'number', 'partial': 'boolean',
    'is_public': 'boolean', 'is_full_markup': 'boolean', 'is_manually_managed': 'boolean',
    'organization': 'nullable_id', 'is_organization_private': 'boolean',
}
CONTEST_FIELDS = {
    'key': 'string', 'name': 'string', 'description': 'string', 'terms': 'string', 'summary': 'string',
    'start_time': 'string', 'end_time': 'string', 'registration_start': 'nullable_string',
    'registration_end': 'nullable_string', 'time_limit': 'nullable_string', 'is_visible': 'boolean',
    'is_private': 'boolean', 'access_code': 'string', 'organization': 'nullable_id',
    'is_organization_private': 'boolean', 'scoreboard_visibility': 'string',
    'use_clarifications': 'boolean', 'hide_problem_tags': 'boolean', 'hide_problem_authors': 'boolean',
    'disallow_virtual': 'boolean', 'problems': 'problems',
}
PROBLEM_ENTRY = {
    'type': 'object', 'additionalProperties': False, 'required': ['code', 'points'],
    'properties': {'code': {'type': 'string'}, 'points': {'type': 'integer', 'minimum': 0},
                   'partial': {'type': 'boolean'}, 'is_pretested': {'type': 'boolean'},
                   'max_submissions': {'type': ['integer', 'null'], 'minimum': 1}},
}


def field_schema(kind):
    if kind == 'ids':
        return {'type': 'array', 'maxItems': 200, 'uniqueItems': True,
                'items': {'type': 'integer', 'minimum': 1}}
    if kind == 'problems':
        return {'type': 'array', 'maxItems': 200, 'items': PROBLEM_ENTRY}
    if kind == 'nullable_id':
        return {'type': ['integer', 'null'], 'minimum': 1}
    if kind == 'nullable_string':
        return {'type': ['string', 'null'], 'maxLength': 100}
    result = {'type': kind}
    if kind == 'string':
        result['maxLength'] = 100000
    return result


def tool_list():
    tools = []
    for resource, fields, identifier in (('problem', PROBLEM_FIELDS, 'code'), ('contest', CONTEST_FIELDS, 'key')):
        for action in ('create', 'edit', 'delete'):
            props, required = {}, []
            if action != 'create':
                props[identifier] = {'type': 'string', 'maxLength': 32, 'minLength': 1}
                required.append(identifier)
            if action != 'delete':
                properties = {key: field_schema(kind) for key, kind in fields.items()}
                if resource == 'problem':
                    properties['description']['description'] = STATEMENT_FORMAT
                if action == 'edit':
                    properties.pop(identifier)
                props['fields'] = {'type': 'object', 'additionalProperties': False, 'properties': properties,
                                   'minProperties': 1}
                if action == 'create':
                    props['fields']['required'] = (['code', 'name', 'group', 'types', 'allowed_languages',
                                                    'time_limit', 'memory_limit', 'points'] if resource == 'problem'
                                                   else ['key', 'name', 'start_time', 'end_time'])
                required.append('fields')
            else:
                props['confirm'] = {'type': 'boolean', 'const': True}
                required.append('confirm')
            tools.append({
                'name': '%s_%s' % (action, resource),
                'description': ('%s a %s. Uses your site permissions. ' % (action.capitalize(), resource)) +
                               ('Problem deletion archives the problem. '
                                'Only contests without participation, submissions or ratings may be deleted.'
                                if action == 'delete' else
                                'Only listed fields are supported; user relations are not editable.') +
                               (' After success, return the user a clickable link using the returned url; '
                                'resolve relative URLs against the MCP server origin.' if action == 'create' else ''),
                'inputSchema': {'type': 'object', 'properties': props, 'required': required,
                                'additionalProperties': False},
                'annotations': {'readOnlyHint': False, 'destructiveHint': action != 'create',
                                'idempotentHint': False, 'openWorldHint': False},
            })
    return tools


TOOLS = {tool['name']: tool for tool in tool_list()}


def validate(value, schema):
    """Validate the small schema vocabulary we advertise, without coercing JSON types."""
    kinds = schema.get('type', [])
    if isinstance(kinds, str):
        kinds = [kinds]
    actual = ('null' if value is None else 'boolean' if type(value) is bool else
              'integer' if type(value) is int else 'number' if type(value) is float else
              'string' if isinstance(value, str) else 'object' if isinstance(value, dict) else
              'array' if isinstance(value, list) else '')
    if actual not in kinds and not (actual == 'integer' and 'number' in kinds):
        raise ValidationError('Invalid JSON type.')
    if 'const' in schema and value is not schema['const']:
        raise ValidationError('Deletion requires confirm=true.')
    if actual == 'object':
        properties = schema.get('properties', {})
        if set(value) - properties.keys() or set(schema.get('required', [])) - value.keys():
            raise ValidationError('Unknown or missing fields.')
        if len(value) < schema.get('minProperties', 0):
            raise ValidationError('At least one field is required.')
        for key, item in value.items():
            validate(item, properties[key])
    elif actual == 'array':
        if len(value) > schema.get('maxItems', 200):
            raise ValidationError('Too many items.')
        for item in value:
            validate(item, schema['items'])
        if schema.get('uniqueItems') and len(set(value)) != len(value):
            raise ValidationError('Duplicate items.')
    elif actual == 'string':
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', 100000):
            raise ValidationError('Invalid string length.')
    elif actual in ('integer', 'number'):
        if not math.isfinite(value) or value < schema.get('minimum', -math.inf):
            raise ValidationError('Invalid number.')


def require(user, permission):
    if not user.has_perm('judge.' + permission):
        raise PermissionDenied('Permission denied.')


def check_fields(user, obj, fields, resource):
    if resource == 'problem':
        permissions = {'is_public': 'change_public_visibility', 'is_full_markup': 'problem_full_markup',
                       'is_manually_managed': 'change_manually_managed'}
        if (obj.is_public and any(field in fields for field in ('organization', 'is_organization_private'))):
            require(user, 'change_public_visibility')
        if obj.is_full_markup and 'description' in fields:
            require(user, 'problem_full_markup')
    else:
        if 'disallow_virtual' in fields and not user.is_superuser:
            raise PermissionDenied('Only superusers may change disallow_virtual.')
        permissions = {'access_code': 'contest_access_code',
                       'is_private': 'create_private_contest', 'is_organization_private': 'create_private_contest',
                       'organization': 'create_private_contest'}
        if 'is_visible' in fields and not user.has_perm('judge.change_contest_visibility'):
            require(user, 'create_private_contest')
            if not (fields.get('is_private', obj.is_private) or
                    fields.get('is_organization_private', obj.is_organization_private)):
                raise PermissionDenied('Global visibility requires change_contest_visibility.')
        # Removing private restrictions would also publish an already visible contest globally.
        if (fields.get('is_visible', obj.is_visible) and
                not fields.get('is_private', obj.is_private) and
                not fields.get('is_organization_private', obj.is_organization_private) and
                any(field in fields for field in ('is_visible', 'is_private', 'is_organization_private'))):
            require(user, 'change_contest_visibility')
    for field, permission in permissions.items():
        if field in fields:
            require(user, permission)
    if 'organization' in fields or 'is_organization_private' in fields:
        if resource == 'problem':
            require(user, 'create_organization_problem')
        org_id = fields.get('organization', obj.organization_id)
        if org_id:
            org = Organization.objects.filter(pk=org_id).first()
            if org is None or not (org.is_admin(user.profile) or user.has_perm('judge.edit_all_organization')):
                raise PermissionDenied('Organization is not manageable by you.')
        if fields.get('is_organization_private', obj.is_organization_private) and not org_id:
            raise ValidationError('Organization-private resources require an organization.')


def contest_problems(user, entries):
    result, seen = [], set()
    for order, entry in enumerate(entries):
        problem = Problem.objects.filter(code=entry['code'], deleted_at__isnull=True).first()
        # Private statements may only be placed into another contest by an editor.
        if problem is None or not (problem.is_accessible_by(user) and
                                   (problem.is_public and not problem.is_organization_private or
                                    problem.is_editable_by(user))):
            raise PermissionDenied('A referenced problem is not available for this contest.')
        if problem.pk in seen:
            raise ValidationError('Duplicate contest problem.')
        seen.add(problem.pk)
        values = dict(entry)
        values.pop('code')
        item = ContestProblem(problem=problem, order=order, **values)
        # The contest is attached after its own validation/save.
        item.full_clean(exclude=['contest'])
        result.append(item)
    return result


@transaction.atomic
def call_tool(user, name, arguments):
    tool = TOOLS.get(name)
    if tool is None:
        raise ValidationError('Unknown tool.')
    validate(arguments, tool['inputSchema'])
    action, resource = name.split('_', 1)
    model = Problem if resource == 'problem' else Contest
    identifier = 'code' if resource == 'problem' else 'key'
    if action == 'create':
        require(user, 'add_' + resource)
        obj = model()
    else:
        queryset = model.objects.select_for_update()
        if resource == 'problem':
            queryset = queryset.filter(deleted_at__isnull=True)
        obj = queryset.filter(**{identifier: arguments[identifier]}).first()
        # Do not distinguish nonexistent resources from ones this user cannot edit.
        if obj is None or not obj.is_editable_by(user):
            raise PermissionDenied('Resource does not exist or is not editable by you.')
    with reversion.create_revision():
        reversion.set_user(user)
        reversion.set_comment('MCP: ' + name)
        if action == 'delete':
            require(user, 'delete_' + resource)
            key = getattr(obj, identifier)
            if resource == 'problem':
                obj.mark_as_deleted()
            else:
                if obj.ratings.exists():
                    raise PermissionDenied('Rated contests cannot be deleted.')
                if obj.users.exists() or Submission.objects.filter(contest_object=obj).exists():
                    raise PermissionDenied('Contests with participation or submissions cannot be deleted through MCP.')
                # Retain an audit snapshot even though deleting the object does not auto-create a Version.
                LogEntry.objects.log_action(
                    user_id=user.pk, content_type_id=ContentType.objects.get_for_model(obj).pk,
                    object_id=obj.pk, object_repr=str(obj), action_flag=DELETION,
                    change_message=json.dumps({'source': 'MCP', 'action': name,
                                               'snapshot': json.loads(serializers.serialize(
                                                   'json', [obj], fields=tuple(
                                                       field.name for field in obj._meta.fields if field.name not in
                                                       ('access_code', 'ranking_access_code'))))}),
                )
                obj.delete()
            return {identifier: key, 'deleted': True}
        original_organization_id = obj.organization_id
        fields = dict(arguments['fields'])
        check_fields(user, obj, fields, resource)
        entries = fields.pop('problems', None)
        problems = contest_problems(user, entries) if entries is not None else None
        allowed = list(PROBLEM_FIELDS if resource == 'problem' else CONTEST_FIELDS)
        if 'problems' in allowed:
            allowed.remove('problems')
        if action == 'edit':
            allowed.remove(identifier)
        form_class = modelform_factory(model, fields=allowed)
        values = model_to_dict(obj, fields=allowed)
        values.update(fields)
        form = form_class(data=values, instance=obj)
        if not form.is_valid():
            raise ValidationError(form.errors.as_json())
        if resource == 'problem':
            validate_problem_time_limit(user, obj.time_limit)
        else:
            validate_contest_duration(user, obj.start_time, obj.end_time)
        if obj.organization_id:
            # Serialize quota admission checks for concurrent MCP creates/moves into the same organization.
            organization = Organization.objects.select_for_update().get(pk=obj.organization_id)
            validate_organization_code(getattr(obj, identifier), organization, contest=resource == 'contest')
            if (resource == 'problem' and settings.VNOJ_QUOTA_ENFORCEMENT_ENABLED and
                    (action == 'create' or original_organization_id != obj.organization_id) and
                    not organization.can_create_problem()):
                raise ValidationError('Organization problem or storage quota exceeded.')
        if resource == 'problem' and (action == 'create' or obj.is_public and obj.date is None):
            obj.date = timezone.now()
        if resource == 'contest' and action == 'create' and settings.VNOJ_OFFICIAL_CONTEST_MODE:
            obj.disallow_virtual = True
            obj.push_announcements = True
        needs_problem_rescore = resource == 'problem' and obj.pk and any(
            field in form.changed_data for field in ('partial', 'is_public', 'is_organization_private'))
        saved = form.save()
        if needs_problem_rescore:
            from judge.tasks import rescore_problem
            transaction.on_commit(rescore_problem.s(saved.pk).delay)
        if action == 'create':
            saved.authors.add(user.profile)
            if resource == 'contest':
                from judge.tasks import on_new_contest
                transaction.on_commit(on_new_contest.s(saved.key).delay)
        if problems is not None:
            # Preserve existing rows and submissions for unchanged problems. Removing rows with
            # submissions is forbidden, because replacing the through table would delete scores.
            existing = {item.problem_id: item for item in saved.contest_problems.all()}
            retained = {item.problem_id for item in problems}
            removed = saved.contest_problems.exclude(problem_id__in=retained)
            if removed.filter(submission__isnull=False).exists():
                raise ValidationError('Cannot remove a contest problem with submissions.')
            removed.delete()
            for item, entry in zip(problems, entries):
                old = existing.get(item.problem_id)
                if old:
                    old.order = item.order
                    for field, value in entry.items():
                        if field != 'code':
                            setattr(old, field, value)
                    item = old
                item.contest = saved
                item.full_clean()
                item.save()
            saved.save()  # Trigger standard contest cache invalidation after relation updates.
            from judge.tasks import rescore_contest
            transaction.on_commit(rescore_contest.s(saved.key).delay)
        return {'id': saved.pk, identifier: getattr(saved, identifier), 'url': saved.get_absolute_url()}


def rpc_error(request_id, code, message, status=200):
    return JsonResponse({'jsonrpc': '2.0', 'id': request_id, 'error': {'code': code, 'message': message}},
                        status=status)


@csrf_exempt
@json_server_errors
def endpoint(request):
    from judge.mcp_auth import authenticate

    if not getattr(settings, 'MCP_ENABLED', False):
        return HttpResponse(status=404)
    # Browser cross-origin requests must match the canonical origin, even when authenticated.
    origin = request.headers.get('Origin')
    if origin:
        expected = getattr(settings, 'MCP_RESOURCE_URL', '').rstrip('/')
        parsed = urlsplit(expected or request.build_absolute_uri('/'))
        allowed = getattr(settings, 'MCP_ALLOWED_ORIGINS', ())
        if origin not in allowed and origin != '%s://%s' % (parsed.scheme, parsed.netloc):
            return HttpResponse(status=403)
    user, failure = authenticate(request)
    if failure is not None:
        return failure
    if request.method != 'POST':
        response = HttpResponse(status=405)
        response['Allow'] = 'POST'
        return response
    if request.content_type != 'application/json':
        return HttpResponse(status=415)
    try:
        length = int(request.META.get('CONTENT_LENGTH') or 0)
    except ValueError:
        return HttpResponse(status=400)
    if length < 0:
        return HttpResponse(status=400)
    if length > 1048576:
        return HttpResponse(status=413)
    if request.headers.get('MCP-Protocol-Version', PROTOCOLS[0]) not in PROTOCOLS:
        return rpc_error(None, -32600, 'Unsupported protocol version.', 400)
    if len(request.body) > 1048576:
        return HttpResponse(status=413)
    try:
        payload = json.loads(request.body, parse_constant=lambda value: None)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return rpc_error(None, -32700, 'Parse error.', 400)
    if not isinstance(payload, dict) or payload.get('jsonrpc') != '2.0':
        return rpc_error(None, -32600, 'Invalid request.', 400)
    request_id = payload.get('id')
    if request_id is not None and type(request_id) not in (str, int):
        return rpc_error(None, -32600, 'Invalid request id.', 400)
    method = payload.get('method')
    params = payload.get('params', {})
    if not isinstance(method, str) or not isinstance(params, dict):
        return rpc_error(request_id, -32600, 'Invalid request.', 400)
    if 'id' not in payload:
        # A notification must never execute a mutating tool.
        return HttpResponse(status=202)
    if method == 'initialize':
        version = params.get('protocolVersion')
        result = {'protocolVersion': version if version in PROTOCOLS else PROTOCOLS[-1],
                  'capabilities': {'tools': {'listChanged': False}},
                  'serverInfo': {'name': 'VNOJ', 'version': '1.0'},
                  'instructions': STATEMENT_FORMAT}
    elif method == 'ping':
        result = {}
    elif method == 'tools/list':
        result = {'tools': list(TOOLS.values())}
    elif method == 'tools/call':
        if set(params) - {'name', 'arguments', '_meta'} or not isinstance(params.get('name'), str):
            return rpc_error(request_id, -32602, 'Invalid tool parameters.')
        try:
            output = call_tool(user, params['name'], params.get('arguments', {}))
        except (ValidationError, PermissionDenied) as exc:
            message = '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            result = {'content': [{'type': 'text', 'text': message}], 'isError': True}
        except IntegrityError:
            result = {'content': [{'type': 'text', 'text': 'Conflicting resource. No changes were saved.'}],
                      'isError': True}
        except Exception:
            logger.exception('MCP tool failed: %s', params['name'])
            result = {'content': [{'type': 'text', 'text': 'Operation failed. Check the resource before retrying.'}],
                      'isError': True}
        else:
            result = {'content': [{'type': 'text', 'text': json.dumps(output)}], 'structuredContent': output,
                      'isError': False}
    else:
        return rpc_error(request_id, -32601, 'Method not found.')
    response = JsonResponse({'jsonrpc': '2.0', 'id': request_id, 'result': result})
    response['Cache-Control'] = 'no-store'
    return response


# Authenticate OAuth inside this endpoint, before session/API middleware can redirect or trust cookies.
endpoint.short_circuit_middleware = True
