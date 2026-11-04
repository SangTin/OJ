import json
from unittest.mock import patch

from django.contrib.admin.models import DELETION, LogEntry
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from judge.mcp import TOOLS, call_tool
from judge.models import Contest, ContestProblem, ContestSubmission, Language, Problem, Submission
from judge.models.tests.util import create_contest, create_contest_participation, create_organization, create_problem, \
    create_problem_group, create_problem_type, create_user


class MCPToolsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = create_user('owner', user_permissions=(
            'edit_own_problem', 'edit_own_contest', 'add_problem', 'add_contest', 'delete_problem', 'delete_contest',
        ))
        cls.other = create_user('other', user_permissions=(
            'edit_own_problem', 'edit_own_contest', 'delete_problem', 'delete_contest',
        ))
        cls.group = create_problem_group('mcpgroup')
        cls.kind = create_problem_type('mcptype')
        cls.language = Language.objects.create(key='MCP', name='MCP', common_name='MCP', ace='text')
        cls.problem = create_problem('mcp_private', authors=('owner',), types=('mcptype',), allowed_languages=('MCP',))
        cls.contest = create_contest('mcp_private', authors=('owner',), start_time=timezone.now(),
                                     end_time=timezone.now() + timezone.timedelta(days=1))

    def problem_fields(self, **kwargs):
        fields = {'code': 'newproblem', 'name': 'Problem', 'group': self.group.pk, 'types': [self.kind.pk],
                  'allowed_languages': [self.language.pk], 'time_limit': 1.0, 'memory_limit': 65536, 'points': 100}
        fields.update(kwargs)
        return fields

    def test_only_six_tools_and_no_user_fields(self):
        self.assertEqual(set(TOOLS), {'create_problem', 'edit_problem', 'delete_problem',
                                      'create_contest', 'edit_contest', 'delete_contest'})
        for tool in TOOLS.values():
            fields = tool['inputSchema']['properties'].get('fields', {}).get('properties', {})
            for name in ('authors', 'curators', 'testers', 'banned_users', 'private_contestants', 'is_staff'):
                self.assertNotIn(name, fields)

    def test_create_edit_and_soft_delete_problem(self):
        result = call_tool(self.owner, 'create_problem', {'fields': self.problem_fields()})
        problem = Problem.objects.get(pk=result['id'])
        self.assertEqual(list(problem.authors.all()), [self.owner.profile])
        self.assertEqual(list(problem.allowed_languages.all()), [self.language])
        call_tool(self.owner, 'edit_problem', {'code': problem.code, 'fields': {'name': 'Changed'}})
        problem.refresh_from_db()
        self.assertEqual(problem.name, 'Changed')
        self.assertEqual(problem.time_limit, 1)
        call_tool(self.owner, 'delete_problem', {'code': problem.code, 'confirm': True})
        self.assertIsNotNone(Problem.objects.get(pk=problem.pk).deleted_at)

    def test_object_permissions_and_delete_confirmation(self):
        for tool, args in (
            ('edit_problem', {'code': self.problem.code, 'fields': {'name': 'Stolen'}}),
            ('delete_problem', {'code': self.problem.code, 'confirm': True}),
            ('edit_contest', {'key': self.contest.key, 'fields': {'name': 'Stolen'}}),
            ('delete_contest', {'key': self.contest.key, 'confirm': True}),
        ):
            with self.subTest(tool=tool), self.assertRaises(PermissionDenied):
                call_tool(self.other, tool, args)
        for confirm in (None, False, 'true', 1):
            with self.subTest(confirm=confirm), self.assertRaises(ValidationError):
                call_tool(self.owner, 'delete_problem', {'code': self.problem.code, 'confirm': confirm})
        self.assertIsNone(Problem.objects.get(pk=self.problem.pk).deleted_at)

    def test_sensitive_fields_and_mass_assignment(self):
        for fields in ({'is_public': True}, {'is_full_markup': True}, {'is_manually_managed': True},
                       {'organization': 1}):
            with self.subTest(fields=fields), self.assertRaises(PermissionDenied):
                call_tool(self.owner, 'edit_problem', {'code': self.problem.code, 'fields': fields})
        for fields in ({'is_visible': True}, {'access_code': 'secret'}, {'is_private': True}):
            with self.subTest(fields=fields), self.assertRaises(PermissionDenied):
                call_tool(self.owner, 'edit_contest', {'key': self.contest.key, 'fields': fields})
        for fields in ({'authors': [self.other.profile.pk]}, {'user_count': 100}, {'code': 'renamed'}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                call_tool(self.owner, 'edit_problem', {'code': self.problem.code, 'fields': fields})

    def test_full_markup_description_cannot_be_edited_without_permission(self):
        Problem.objects.filter(pk=self.problem.pk).update(is_full_markup=True)
        with self.assertRaises(PermissionDenied):
            call_tool(self.owner, 'edit_problem', {'code': self.problem.code, 'fields': {'description': '<script>'}})

    def test_invalid_fields_do_not_create_objects(self):
        for values in ({'memory_limit': -1}, {'time_limit': True}, {'types': [999999]}, {'code': 'UPPERCASE'}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                call_tool(self.owner, 'create_problem', {'fields': self.problem_fields(**values)})
        self.assertFalse(Problem.objects.filter(code='newproblem').exists())

    def test_create_edit_delete_contest(self):
        result = call_tool(self.owner, 'create_contest', {'fields': {
            'key': 'newcontest', 'name': 'Contest', 'start_time': '2027-01-01T00:00:00Z',
            'end_time': '2027-01-02T00:00:00Z',
        }})
        contest = Contest.objects.get(pk=result['id'])
        self.assertEqual(list(contest.authors.all()), [self.owner.profile])
        call_tool(self.owner, 'edit_contest', {'key': contest.key, 'fields': {'name': 'Changed'}})
        with self.assertRaises(ValidationError):
            call_tool(self.owner, 'edit_contest', {
                'key': contest.key, 'fields': {'end_time': '2026-01-01T00:00:00Z'},
            })
        self.assertEqual(Contest.objects.get(pk=contest.pk).name, 'Changed')
        call_tool(self.owner, 'delete_contest', {'key': contest.key, 'confirm': True})
        self.assertFalse(Contest.objects.filter(pk=contest.pk).exists())

    def test_private_problem_cannot_be_taken_into_other_contest(self):
        # Both users can edit their own contest but the other user cannot take the owner's private problem.
        contest = create_contest('othercontest', authors=('other',))
        with self.assertRaises(PermissionDenied):
            call_tool(self.other, 'edit_contest', {'key': contest.key, 'fields': {
                'problems': [{'code': self.problem.code, 'points': 100}],
            }})
        self.assertFalse(ContestProblem.objects.filter(contest=contest).exists())

    def test_problem_list_updates_preserve_rows_and_rescore(self):
        item = ContestProblem.objects.create(contest=self.contest, problem=self.problem, points=50, order=0,
                                             output_prefix_override=18, max_submissions=7)
        with patch('judge.tasks.rescore_contest') as task, self.captureOnCommitCallbacks(execute=True):
            call_tool(self.owner, 'edit_contest', {'key': self.contest.key, 'fields': {
                'problems': [{'code': self.problem.code, 'points': 100, 'partial': False}],
            }})
        item.refresh_from_db()
        self.assertEqual(item.points, 100)
        self.assertFalse(item.partial)
        self.assertEqual(item.output_prefix_override, 18)
        self.assertEqual(item.max_submissions, 7)
        self.assertEqual(ContestProblem.objects.filter(contest=self.contest).count(), 1)
        task.s.assert_called_once_with(self.contest.key)
        task.s.return_value.delay.assert_called_once_with()

    def test_public_problem_organization_change_requires_visibility_permission(self):
        Problem.objects.filter(pk=self.problem.pk).update(is_public=True, is_organization_private=True)
        with self.assertRaises(PermissionDenied):
            call_tool(self.owner, 'edit_problem', {
                'code': self.problem.code, 'fields': {'is_organization_private': False},
            })
        with self.assertRaises(PermissionDenied):
            call_tool(self.owner, 'edit_problem', {
                'code': self.problem.code, 'fields': {'organization': 1},
            })

    def test_removing_problem_with_submissions_rolls_back(self):
        item = ContestProblem.objects.create(contest=self.contest, problem=self.problem, points=50, order=0)
        participation = create_contest_participation(self.contest, self.owner.profile)
        submission = Submission.objects.create(user=self.owner.profile, problem=self.problem,
                                               language=self.language, contest_object=self.contest)
        ContestSubmission.objects.create(submission=submission, problem=item, participation=participation)
        with self.assertRaises(ValidationError):
            call_tool(self.owner, 'edit_contest', {'key': self.contest.key, 'fields': {
                'name': 'Should roll back', 'problems': [],
            }})
        self.assertTrue(ContestProblem.objects.filter(pk=item.pk).exists())
        self.assertTrue(ContestSubmission.objects.filter(submission=submission).exists())
        self.assertEqual(Contest.objects.get(pk=self.contest.pk).name, self.contest.name)

    def test_problem_partial_change_rescores(self):
        with patch('judge.tasks.rescore_problem') as task, self.captureOnCommitCallbacks(execute=True):
            call_tool(self.owner, 'edit_problem', {'code': self.problem.code, 'fields': {'partial': True}})
        task.s.assert_called_once_with(self.problem.pk)

    def test_private_visibility_cannot_publish_by_removing_restrictions(self):
        user = create_user('privateeditor', user_permissions=('edit_own_contest', 'create_private_contest'))
        contest = create_contest('privatescope', authors=('privateeditor',), is_private=True,
                                 start_time=timezone.now(), end_time=timezone.now() + timezone.timedelta(days=1))
        call_tool(user, 'edit_contest', {'key': contest.key, 'fields': {'is_visible': True}})
        with self.assertRaises(PermissionDenied):
            call_tool(user, 'edit_contest', {'key': contest.key, 'fields': {'is_private': False}})
        contest.refresh_from_db()
        self.assertTrue(contest.is_private)

    def test_delete_needs_explicit_model_permission_even_for_editor(self):
        user = create_user('editoronly', user_permissions=('edit_own_problem',))
        problem = create_problem('editoronly', authors=('editoronly',))
        with self.assertRaises(PermissionDenied):
            call_tool(user, 'delete_problem', {'code': problem.code, 'confirm': True})

    def test_invalid_array_elements_are_validation_errors(self):
        with self.assertRaises(ValidationError):
            call_tool(self.owner, 'create_problem', {'fields': self.problem_fields(types=[{}])})

    def test_site_authoring_limits_and_elevated_permissions(self):
        with self.assertRaises(ValidationError):
            call_tool(self.owner, 'edit_problem', {'code': self.problem.code, 'fields': {'time_limit': 30}})
        with self.assertRaises(ValidationError):
            call_tool(self.owner, 'edit_contest', {'key': self.contest.key, 'fields': {
                'start_time': '2027-01-01T00:00:00Z', 'end_time': '2029-01-01T00:00:00Z',
            }})
        elevated = create_user('elevated', user_permissions=(
            'edit_own_problem', 'edit_own_contest', 'high_problem_timelimit', 'long_contest_duration',
        ))
        self.problem.curators.add(elevated.profile)
        self.contest.curators.add(elevated.profile)
        call_tool(elevated, 'edit_problem', {'code': self.problem.code, 'fields': {'time_limit': 30}})
        call_tool(elevated, 'edit_contest', {'key': self.contest.key, 'fields': {
            'start_time': '2027-01-01T00:00:00Z', 'end_time': '2029-01-01T00:00:00Z',
        }})

    def test_organization_prefix_and_quota(self):
        user = create_user('orgcreator', user_permissions=('add_problem', 'create_organization_problem'))
        org = create_organization('mcpschool', admins=('orgcreator',))
        with self.assertRaises(ValidationError):
            call_tool(user, 'create_problem', {'fields': self.problem_fields(organization=org.pk)})
        with override_settings(VNOJ_QUOTA_ENFORCEMENT_ENABLED=True,
                               VNOJ_ORGANIZATION_DEFAULT_MAX_PROBLEMS=1):
            call_tool(user, 'create_problem', {'fields': self.problem_fields(
                code='mcpschool_first', organization=org.pk)})
            with self.assertRaises(ValidationError):
                call_tool(user, 'create_problem', {'fields': self.problem_fields(
                    code='mcpschool_second', organization=org.pk)})
        self.assertFalse(Problem.objects.filter(code='mcpschool_second').exists())

    def test_virtual_restriction_is_superuser_only(self):
        with self.assertRaises(PermissionDenied):
            call_tool(self.owner, 'edit_contest', {'key': self.contest.key, 'fields': {'disallow_virtual': True}})

    def test_deletion_preserves_participation_and_records_audit(self):
        create_contest_participation(self.contest, self.owner.profile)
        with self.assertRaises(PermissionDenied):
            call_tool(self.owner, 'delete_contest', {'key': self.contest.key, 'confirm': True})
        self.assertTrue(Contest.objects.filter(pk=self.contest.pk).exists())
        empty = create_contest('emptydelete', authors=('owner',))
        call_tool(self.owner, 'delete_contest', {'key': empty.key, 'confirm': True})
        entry = LogEntry.objects.get(action_flag=DELETION, object_id=str(empty.pk))
        self.assertEqual(entry.user_id, self.owner.pk)
        self.assertEqual(json.loads(entry.change_message)['source'], 'MCP')

    def test_new_objects_have_date_and_contest_hook(self):
        result = call_tool(self.owner, 'create_problem', {'fields': self.problem_fields()})
        self.assertIsNotNone(Problem.objects.get(pk=result['id']).date)
        with patch('judge.tasks.on_new_contest') as task, self.captureOnCommitCallbacks(execute=True):
            call_tool(self.owner, 'create_contest', {'fields': {
                'key': 'notifycontest', 'name': 'Notify', 'start_time': '2027-01-01T00:00:00Z',
                'end_time': '2027-01-02T00:00:00Z',
            }})
        task.s.assert_called_once_with('notifycontest')


@override_settings(MCP_ENABLED=True)
class MCPProtocolTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = create_user('mcpuser')

    def rpc(self, body, **headers):
        with patch('judge.mcp_auth.authenticate', return_value=(self.user, None)):
            return self.client.post('/mcp', json.dumps(body), content_type='application/json', **headers)

    def test_initialize_list_ping(self):
        response = self.rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                             'params': {'protocolVersion': '2025-06-18'}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['result']['protocolVersion'], '2025-06-18')
        tools = self.rpc({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}).json()['result']['tools']
        self.assertEqual(len(tools), 6)
        self.assertEqual(self.rpc({'jsonrpc': '2.0', 'id': 3, 'method': 'ping'}).json()['result'], {})

    def test_notifications_never_execute_tools(self):
        with patch('judge.mcp.call_tool') as call:
            response = self.rpc({'jsonrpc': '2.0', 'method': 'tools/call',
                                 'params': {'name': 'delete_contest', 'arguments': {'key': 'x', 'confirm': True}}})
        self.assertEqual(response.status_code, 202)
        call.assert_not_called()

    def test_bad_protocol_origin_and_unknown_method(self):
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}
        self.assertEqual(self.rpc(request, HTTP_ORIGIN='https://evil.example').status_code, 403)
        self.assertEqual(self.rpc(request, HTTP_MCP_PROTOCOL_VERSION='2000-01-01').status_code, 400)
        self.assertEqual(self.rpc({**request, 'method': 'unknown'}).json()['error']['code'], -32601)
        self.assertEqual(self.rpc([]).status_code, 400)

    def test_tool_error_is_reported_without_internal_exception(self):
        result = self.rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                           'params': {'name': 'delete_user', 'arguments': {}}}).json()['result']
        self.assertTrue(result['isError'])

    @override_settings(MCP_ENABLED=False)
    def test_disabled_endpoint(self):
        self.assertEqual(self.client.post('/mcp').status_code, 404)
