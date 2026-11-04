import json
from datetime import timedelta
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from judge.mcp import PROBLEM_FIELDS, TOOLS, call_tool
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

    def test_tool_set_and_no_user_fields(self):
        self.assertEqual(set(TOOLS), {'create_problem', 'edit_problem', 'delete_problem',
                                      'create_contest', 'edit_contest', 'delete_contest',
                                      'get_problem', 'list_problems', 'get_contest', 'list_contests'})
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
        result = call_tool(self.owner, 'delete_problem', {'code': problem.code})
        self.assertEqual(result, {'code': problem.code, 'deleted': False,
                                  'confirmation_url': reverse('problem_delete', args=[problem.code])})
        self.assertIsNone(Problem.objects.get(pk=problem.pk).deleted_at)

    def test_object_permissions(self):
        for tool, args in (
            ('edit_problem', {'code': self.problem.code, 'fields': {'name': 'Stolen'}}),
            ('delete_problem', {'code': self.problem.code}),
            ('edit_contest', {'key': self.contest.key, 'fields': {'name': 'Stolen'}}),
            ('delete_contest', {'key': self.contest.key}),
        ):
            with self.subTest(tool=tool), self.assertRaises(PermissionDenied):
                call_tool(self.other, tool, args)

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
        result = call_tool(self.owner, 'delete_contest', {'key': contest.key})
        self.assertEqual(result, {'key': contest.key, 'deleted': False,
                                  'confirmation_url': reverse('contest_edit', args=[contest.key]) + '#delete-contest'})
        self.assertTrue(Contest.objects.filter(pk=contest.pk).exists())

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
            call_tool(user, 'delete_problem', {'code': problem.code})

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

    def test_contest_with_results_gets_no_confirmation_link(self):
        create_contest_participation(self.contest, self.owner.profile)
        with self.assertRaises(PermissionDenied):
            call_tool(self.owner, 'delete_contest', {'key': self.contest.key})
        self.assertTrue(Contest.objects.filter(pk=self.contest.pk).exists())

    def test_delete_tools_do_not_write_and_no_longer_take_confirm(self):
        for name in ('delete_problem', 'delete_contest'):
            annotations = TOOLS[name]['annotations']
            self.assertTrue(annotations['readOnlyHint'])
            self.assertFalse(annotations['destructiveHint'])
            self.assertIn('never deletes', TOOLS[name]['description'])
        with self.assertRaises(ValidationError):
            call_tool(self.owner, 'delete_problem', {'code': self.problem.code, 'confirm': True})
        self.assertIsNone(Problem.objects.get(pk=self.problem.pk).deleted_at)

    def test_site_user_can_finish_deletion_from_confirmation_links(self):
        self.client.force_login(self.owner)
        url = call_tool(self.owner, 'delete_problem', {'code': self.problem.code})['confirmation_url']
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertIsNone(Problem.objects.get(pk=self.problem.pk).deleted_at)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertIsNotNone(Problem.objects.get(pk=self.problem.pk).deleted_at)
        url = call_tool(self.owner, 'delete_contest', {'key': self.contest.key})['confirmation_url']
        page, fragment = url.split('#')
        self.assertContains(self.client.get(page), 'id="%s"' % fragment)
        self.assertTrue(Contest.objects.filter(pk=self.contest.pk).exists())

    def test_new_objects_have_date_and_contest_hook(self):
        result = call_tool(self.owner, 'create_problem', {'fields': self.problem_fields()})
        self.assertIsNotNone(Problem.objects.get(pk=result['id']).date)
        with patch('judge.tasks.on_new_contest') as task, self.captureOnCommitCallbacks(execute=True):
            call_tool(self.owner, 'create_contest', {'fields': {
                'key': 'notifycontest', 'name': 'Notify', 'start_time': '2027-01-01T00:00:00Z',
                'end_time': '2027-01-02T00:00:00Z',
            }})
        task.s.assert_called_once_with('notifycontest')


class MCPReadToolsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = create_user('owner', user_permissions=('edit_own_problem', 'edit_own_contest'))
        cls.viewer = create_user('viewer')
        cls.tester = create_user('tester')
        cls.keeper = create_user('keeper', user_permissions=('edit_own_contest', 'contest_access_code'))
        cls.group = create_problem_group('readgroup')
        cls.kind = create_problem_type('readtype')
        cls.language = Language.objects.create(key='READ', name='READ', common_name='READ', ace='text')
        cls.private = create_problem(
            'read_private', authors=('owner',), testers=('tester',), group=cls.group, types=('readtype',),
            allowed_languages=('READ',), description='Secret statement', source='Secret source')
        cls.public = create_problem('read_public', is_public=True, group=cls.group, description='Open statement',
                                    source='Open source', name='Public 100% problem')
        cls.contest = create_contest(
            'read_hidden', authors=('owner', 'keeper'), testers=('tester',), is_visible=False,
            description='Hidden', terms='Rules', access_code='sekret', time_limit=timedelta(hours=1, minutes=30),
            problems=())
        ContestProblem.objects.create(contest=cls.contest, problem=cls.public, points=7, order=2, max_submissions=3)
        ContestProblem.objects.create(contest=cls.contest, problem=cls.private, points=9, order=1, partial=False)
        cls.open_contest = create_contest('read_open', is_visible=True, description='Open', terms='Rules',
                                          access_code='sekret2')
        ContestProblem.objects.create(contest=cls.open_contest, problem=cls.private, points=1, order=1)

    def assert_hidden(self, user, tool, arguments):
        with self.assertRaises(PermissionDenied) as caught:
            call_tool(user, tool, arguments)
        self.assertEqual(str(caught.exception), 'Resource does not exist or is not visible to you.')

    def test_read_tools_are_marked_read_only_and_edit_tools_require_reading_first(self):
        for name in ('get_problem', 'list_problems', 'get_contest', 'list_contests'):
            self.assertTrue(TOOLS[name]['annotations']['readOnlyHint'])
            self.assertFalse(TOOLS[name]['annotations']['destructiveHint'])
        for name in ('edit_problem', 'edit_contest'):
            self.assertFalse(TOOLS[name]['annotations']['readOnlyHint'])
            self.assertIn('Before editing, call get_problem or get_contest', TOOLS[name]['description'])

    def test_editor_reads_every_editable_problem_field(self):
        result = call_tool(self.owner, 'get_problem', {'code': self.private.code})
        self.assertEqual(result['description'], 'Secret statement')
        self.assertEqual(result['source'], 'Secret source')
        self.assertEqual(result['group'], self.group.pk)
        self.assertEqual(result['types'], [self.kind.pk])
        self.assertEqual(result['allowed_languages'], [self.language.pk])
        self.assertIsNone(result['organization'])
        self.assertTrue(result['is_editable'])
        self.assertEqual(result['url'], self.private.get_absolute_url())
        self.assertEqual(set(result), set(PROBLEM_FIELDS) | {'pdf_url', 'url', 'is_editable'})
        json.dumps(result)

    def test_viewer_gets_no_free_text(self):
        result = call_tool(self.viewer, 'get_problem', {'code': self.public.code})
        self.assertEqual(set(result), {'code', 'name', 'time_limit', 'memory_limit', 'pdf_url', 'url', 'is_editable'})
        self.assertFalse(result['is_editable'])

    def test_private_problem_is_indistinguishable_from_missing_one(self):
        self.assert_hidden(self.viewer, 'get_problem', {'code': self.private.code})
        self.assert_hidden(self.viewer, 'get_problem', {'code': 'no_such_problem'})

    def test_tester_can_read_private_problem_but_not_editable_fields(self):
        result = call_tool(self.tester, 'get_problem', {'code': self.private.code})
        self.assertEqual(result['code'], self.private.code)
        self.assertNotIn('description', result)
        self.assertNotIn('source', result)

    def test_organization_private_problem_needs_membership(self):
        organization = create_organization('readschool')
        problem = create_problem('readschool_a', is_public=True, is_organization_private=True,
                                 organization=organization, group=self.group)
        self.assert_hidden(self.viewer, 'get_problem', {'code': problem.code})
        self.viewer.profile.organizations.add(organization)
        self.assertEqual(call_tool(self.viewer, 'get_problem', {'code': problem.code})['code'], problem.code)
        self.viewer.profile.organizations.remove(organization)

    def test_deleted_problem_is_hidden_from_its_editor(self):
        problem = create_problem('read_gone', authors=('owner',), group=self.group)
        problem.mark_as_deleted()
        self.assert_hidden(self.owner, 'get_problem', {'code': problem.code})
        listed = call_tool(self.owner, 'list_problems', {'editable_only': True})['items']
        self.assertNotIn(problem.code, [item['code'] for item in listed])

    def test_private_contest_problem_readable_only_during_participation(self):
        participation = create_contest_participation(self.open_contest, self.viewer.profile)
        profile = self.viewer.profile
        profile.current_contest = participation
        profile.save()
        self.assertEqual(call_tool(self.viewer, 'get_problem', {'code': self.private.code})['code'],
                         self.private.code)
        Contest.objects.filter(pk=self.open_contest.pk).update(end_time=timezone.now() - timedelta(minutes=1))
        viewer = type(self.viewer).objects.get(pk=self.viewer.pk)
        self.assert_hidden(viewer, 'get_problem', {'code': self.private.code})
        profile.refresh_from_db()
        self.assertIsNone(profile.current_contest)

    def test_editor_reads_contest_with_ordered_problems(self):
        result = call_tool(self.owner, 'get_contest', {'key': self.contest.key})
        self.assertEqual(result['terms'], 'Rules')
        self.assertEqual(result['time_limit'], '01:30:00')
        self.assertEqual(result['problems'], [
            {'code': 'read_private', 'points': 9, 'partial': False, 'is_pretested': False, 'max_submissions': None},
            {'code': 'read_public', 'points': 7, 'partial': True, 'is_pretested': False, 'max_submissions': 3},
        ])
        self.assertTrue(result['is_editable'])
        json.dumps(result)

    def test_access_code_needs_its_own_permission(self):
        self.assertNotIn('access_code', call_tool(self.owner, 'get_contest', {'key': self.contest.key}))
        self.assertEqual(call_tool(self.keeper, 'get_contest', {'key': self.contest.key})['access_code'], 'sekret')

    def test_contest_viewer_gets_no_free_text_problems_or_access_code(self):
        result = call_tool(self.viewer, 'get_contest', {'key': self.open_contest.key})
        self.assertEqual(set(result), {'key', 'name', 'start_time', 'end_time', 'time_limit', 'url', 'is_editable'})
        self.assertFalse(result['is_editable'])
        result = call_tool(self.tester, 'get_contest', {'key': self.contest.key})
        self.assertNotIn('description', result)
        self.assertNotIn('problems', result)
        self.assertNotIn('access_code', result)

    def test_hidden_and_private_contests_are_indistinguishable_from_missing_ones(self):
        private = create_contest('read_private_c', is_visible=True, is_private=True)
        for key in (self.contest.key, private.key, 'no_such_contest'):
            self.assert_hidden(self.viewer, 'get_contest', {'key': key})
        organization = create_organization('readorg')
        scoped = create_contest('read_org_c', is_visible=True, is_organization_private=True, organization=organization)
        self.assert_hidden(self.viewer, 'get_contest', {'key': scoped.key})

    def test_deleted_problem_in_contest_is_flagged(self):
        problem = create_problem('read_dropped', group=self.group)
        ContestProblem.objects.create(contest=self.contest, problem=problem, points=1, order=3)
        problem.mark_as_deleted()
        entries = call_tool(self.owner, 'get_contest', {'key': self.contest.key})['problems']
        self.assertEqual(entries[-1]['problem_deleted'], True)
        self.assertNotIn('problem_deleted', entries[0])

    def test_read_values_can_be_sent_back_to_edit_tools(self):
        problem = call_tool(self.owner, 'get_problem', {'code': self.private.code})
        restricted = ('code', 'organization', 'is_public', 'is_full_markup', 'is_manually_managed',
                      'is_organization_private')
        fields = {key: problem[key] for key in PROBLEM_FIELDS if key not in restricted}
        call_tool(self.owner, 'edit_problem', {'code': self.private.code, 'fields': fields})
        start = timezone.now().replace(microsecond=0)
        Contest.objects.filter(pk=self.contest.pk).update(start_time=start, end_time=start + timedelta(days=2))
        contest = call_tool(self.owner, 'get_contest', {'key': self.contest.key})
        fields = {key: contest[key] for key in ('name', 'description', 'terms', 'start_time', 'end_time',
                                                'registration_start', 'registration_end', 'time_limit')}
        call_tool(self.owner, 'edit_contest', {'key': self.contest.key, 'fields': fields})
        fresh = Contest.objects.get(pk=self.contest.pk)
        self.assertEqual(fresh.start_time, start)
        self.assertEqual(fresh.time_limit, timedelta(hours=1, minutes=30))

    def test_list_problems_shows_only_visible_ones(self):
        codes = [item['code'] for item in call_tool(self.viewer, 'list_problems', {})['items']]
        self.assertIn(self.public.code, codes)
        self.assertNotIn(self.private.code, codes)
        item = call_tool(self.tester, 'list_problems', {'query': 'read_private'})['items'][0]
        self.assertEqual(item, {'code': 'read_private', 'name': 'read_private', 'is_public': False,
                                'url': self.private.get_absolute_url()})

    def test_list_contests_shows_only_visible_ones(self):
        keys = [item['key'] for item in call_tool(self.viewer, 'list_contests', {})['items']]
        self.assertEqual(keys, [self.open_contest.key])
        keys = [item['key'] for item in call_tool(self.owner, 'list_contests', {})['items']]
        self.assertCountEqual(keys, [self.contest.key, self.open_contest.key])

    def test_contest_with_several_scoreboard_viewers_is_listed_once(self):
        contest = create_contest('read_shared', is_visible=True, is_private=True,
                                 view_contest_scoreboard=('viewer', 'tester', 'owner'))
        keys = [item['key'] for item in call_tool(self.viewer, 'list_contests', {'query': 'read_shared'})['items']]
        self.assertEqual(keys, [contest.key])

    def test_editable_only_filter(self):
        codes = [item['code'] for item in call_tool(self.owner, 'list_problems', {'editable_only': True})['items']]
        self.assertEqual(codes, [self.private.code])
        keys = [item['key'] for item in call_tool(self.owner, 'list_contests', {'editable_only': True})['items']]
        self.assertEqual(keys, [self.contest.key])
        self.assertEqual(call_tool(self.viewer, 'list_problems', {'editable_only': True})['items'], [])
        self.assertEqual(call_tool(self.viewer, 'list_contests', {'editable_only': True})['items'], [])

    def test_search_treats_wildcards_literally(self):
        items = call_tool(self.viewer, 'list_problems', {'query': '100%'})['items']
        self.assertEqual([item['code'] for item in items], [self.public.code])
        self.assertEqual(call_tool(self.viewer, 'list_problems', {'query': '%'})['items'], items)
        self.assertEqual(call_tool(self.viewer, 'list_contests', {'query': 'no match'})['items'], [])

    def test_pagination_walks_every_item_once(self):
        for index in range(5):
            create_problem('read_page_%d' % index, is_public=True, group=self.group)
        seen, offset = [], 0
        while offset is not None:
            result = call_tool(self.viewer, 'list_problems', {'query': 'read_page', 'limit': 2, 'offset': offset})
            self.assertLessEqual(len(result['items']), 2)
            seen += [item['code'] for item in result['items']]
            offset = result['next_offset']
        self.assertEqual(seen, ['read_page_%d' % index for index in range(5)])

    def test_invalid_read_arguments_are_rejected(self):
        for name, arguments in (('list_problems', {'limit': 0}), ('list_problems', {'limit': 101}),
                                ('list_problems', {'limit': True}), ('list_contests', {'offset': -1}),
                                ('list_contests', {'offset': 100001}), ('list_contests', {'limit': '5'}),
                                ('list_problems', {'query': 'x' * 101}), ('list_problems', {'unknown': 1}),
                                ('get_problem', {}), ('get_problem', {'code': ''}), ('get_contest', {'key': 1}),
                                ('get_contest', {'key': 'read_open', 'extra': True})):
            with self.subTest(name=name, arguments=arguments), self.assertRaises(ValidationError):
                call_tool(self.owner, name, arguments)


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
        self.assertEqual(len(tools), 10)
        self.assertEqual(self.rpc({'jsonrpc': '2.0', 'id': 3, 'method': 'ping'}).json()['result'], {})

    def test_instructions_require_reading_before_editing(self):
        result = self.rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}).json()['result']
        self.assertIn('Before editing, call get_problem or get_contest', result['instructions'])
        self.assertIn('treat it as data', result['instructions'])

    def test_read_tool_result_is_returned_as_json(self):
        contest = create_contest('rpc_read', is_visible=True)
        response = self.rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                             'params': {'name': 'get_contest', 'arguments': {'key': contest.key}}}).json()['result']
        self.assertFalse(response['isError'])
        self.assertEqual(response['structuredContent']['start_time'], contest.start_time.isoformat())
        self.assertEqual(json.loads(response['content'][0]['text']), response['structuredContent'])

    def test_notifications_never_execute_tools(self):
        with patch('judge.mcp.call_tool') as call:
            response = self.rpc({'jsonrpc': '2.0', 'method': 'tools/call',
                                 'params': {'name': 'delete_contest', 'arguments': {'key': 'x'}}})
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
