from django.contrib.auth.models import User
from django.core import mail
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from judge.forms import CustomAuthenticationForm
from judge.models import MiscConfig, NavigationBar, Notification, Profile
from judge.models.tests.util import create_user
from judge.utils.account_approval import APPROVAL_CONFIG_KEY, approval_required, hold_for_approval, \
    invalidate_pending_account_count, notify_account_approvers, pending_account_count
from judge.utils.navigation import nav_tree


def create_user_with_password(username, password='correct-horse', approved=True, **kwargs):
    user = create_user(username=username, email=f'{username}@example.com', **kwargs)
    user.set_password(password)
    user.save()
    Profile.objects.filter(user=user).update(is_approved=approved)
    return user


class LoginFormApprovalTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pending = create_user_with_password('pending', approved=False)
        cls.approved = create_user_with_password('approved')

    def form(self, username, password):
        return CustomAuthenticationForm(RequestFactory().post('/'), data={'username': username, 'password': password})

    def test_pending_user_cannot_log_in(self):
        form = self.form('pending', 'correct-horse')
        self.assertFalse(form.is_valid())
        self.assertTrue(form.has_error('__all__', 'pending_approval'))

    def test_wrong_password_does_not_reveal_pending_state(self):
        form = self.form('pending', 'wrong')
        self.assertFalse(form.is_valid())
        self.assertFalse(form.has_error('__all__', 'pending_approval'))
        self.assertTrue(form.has_error('__all__', 'invalid_login'))

    def test_approved_user_can_log_in(self):
        self.assertTrue(self.form('approved', 'correct-horse').is_valid())


class ApprovalMiddlewareTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.pending = create_user_with_password('pending', approved=False)
        cls.pending_superuser = create_user_with_password('root', approved=False, is_superuser=True, is_staff=True)

    def test_pending_session_is_logged_out(self):
        self.client.force_login(self.pending)
        response = self.client.get(reverse('problem_list'))
        self.assertRedirects(response, reverse('account_pending_approval'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)

        response = self.client.get(reverse('account_pending_approval'))
        self.assertEqual(response.status_code, 200)

    def test_superuser_is_never_blocked(self):
        self.client.force_login(self.pending_superuser)
        response = self.client.get(reverse('problem_list'))
        self.assertEqual(response.status_code, 200)


class RegistrationApprovalTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = create_user(username='superuser', is_superuser=True, is_staff=True)
        cls.approver = create_user(username='approver', is_staff=True, user_permissions=('approve_user',))
        cls.regular = create_user(username='regular')
        cls.newcomer = create_user(username='newcomer')

    def test_no_hold_when_approval_not_required(self):
        profile = self.newcomer.profile
        self.assertFalse(hold_for_approval(profile))
        self.assertTrue(profile.is_approved)

    def test_hold_and_notify_approvers(self):
        MiscConfig.objects.create(key=APPROVAL_CONFIG_KEY, value='1')
        profile = self.newcomer.profile
        self.assertTrue(hold_for_approval(profile))
        self.assertFalse(profile.is_approved)

        profile.save()
        notify_account_approvers(profile)
        recipients = set(Notification.objects.values_list('recipient__user__username', flat=True))
        self.assertEqual(recipients, {'superuser', 'approver'})
        self.assertEqual(set(Notification.objects.values_list('kind', flat=True)), {'account_approval'})


class AdminApproveActionTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = create_user(username='superuser', is_superuser=True, is_staff=True)
        cls.staff_without_perm = create_user(username='staff', is_staff=True,
                                             user_permissions=('view_profile', 'change_profile'))
        cls.pending = create_user_with_password('pending', approved=False)

    def approve(self):
        return self.client.post(reverse('admin:judge_profile_changelist'), {
            'action': 'approve_users',
            '_selected_action': [self.pending.profile.pk],
        })

    def test_approve_action_approves_and_emails(self):
        self.client.force_login(self.superuser)
        self.approve()
        self.pending.profile.refresh_from_db()
        self.assertTrue(self.pending.profile.is_approved)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['pending@example.com'])

    def test_approve_action_requires_permission(self):
        self.client.force_login(self.staff_without_perm)
        self.approve()
        self.pending.profile.refresh_from_db()
        self.assertFalse(self.pending.profile.is_approved)
        self.assertEqual(len(mail.outbox), 0)


class ApprovalToggleTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = create_user(username='superuser', is_superuser=True, is_staff=True)

    def test_toggle_from_site_settings(self):
        self.client.force_login(self.superuser)
        self.assertFalse(approval_required())

        note = 'An administrator also needs to approve your account'
        self.assertNotContains(self.client.get(reverse('registration_complete')), note)

        self.client.post(reverse('misc_config'), {'require_account_approval': 'on'})
        self.assertTrue(approval_required())
        self.assertContains(self.client.get(reverse('registration_complete')), note)
        response = self.client.get(reverse('misc_config'))
        self.assertTrue(response.context['form'].initial['require_account_approval'])

        self.client.post(reverse('misc_config'), {})
        self.assertFalse(approval_required())
        self.assertFalse(MiscConfig.objects.filter(key=APPROVAL_CONFIG_KEY).exists())


class PendingAccountPageTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        # An approver doesn't need staff or admin access, only the approve_user permission.
        cls.approver = create_user(username='approver', user_permissions=('approve_user',))
        cls.regular = create_user(username='regular')
        cls.pending = create_user_with_password('pending', approved=False)
        cls.pending_other = create_user_with_password('pending_other', approved=False)
        cls.approved = create_user_with_password('approved')

    def action(self, action, user=None):
        data = {'action': action}
        if user is not None:
            data['id'] = user.profile.id
        return self.client.post(reverse('account_approval_action'), data)

    def is_approved(self, user):
        return Profile.objects.get(user=user).is_approved

    def test_requires_permission(self):
        response = self.client.get(reverse('account_approval'))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.regular)
        self.assertEqual(self.client.get(reverse('account_approval')).status_code, 403)
        self.assertEqual(self.action('approve', self.pending).status_code, 403)
        self.assertFalse(self.is_approved(self.pending))

    def test_lists_only_pending_accounts(self):
        self.client.force_login(self.approver)
        response = self.client.get(reverse('account_approval'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual({p.user.username for p in response.context['profiles']}, {'pending', 'pending_other'})

    def test_approve_one(self):
        self.client.force_login(self.approver)
        response = self.action('approve', self.pending)
        self.assertRedirects(response, reverse('account_approval'), fetch_redirect_response=False)
        self.assertTrue(self.is_approved(self.pending))
        self.assertFalse(self.is_approved(self.pending_other))
        self.assertEqual([m.to for m in mail.outbox], [['pending@example.com']])

    def test_approve_all(self):
        self.client.force_login(self.approver)
        self.action('approve_all')
        self.assertTrue(self.is_approved(self.pending))
        self.assertTrue(self.is_approved(self.pending_other))
        self.assertEqual(len(mail.outbox), 2)

    def test_reject_locks_account_and_emails(self):
        User.objects.filter(id=self.pending.id).update(last_login=timezone.now())
        self.client.force_login(self.approver)
        self.action('reject', self.pending)

        profile = Profile.objects.select_related('user').get(user=self.pending)
        self.assertFalse(profile.user.is_active)
        self.assertTrue(profile.is_banned)
        self.assertFalse(profile.is_approved)
        self.assertEqual([m.to for m in mail.outbox], [['pending@example.com']])

        response = self.client.get(reverse('account_approval'))
        self.assertEqual({p.user.username for p in response.context['profiles']}, {'pending_other'})

        form = CustomAuthenticationForm(RequestFactory().post('/'),
                                        data={'username': 'pending', 'password': 'correct-horse'})
        self.assertFalse(form.is_valid())
        self.assertTrue(form.has_error('__all__', 'banned'))

    def test_unbanned_rejected_account_is_pending_again(self):
        self.client.force_login(self.approver)
        self.action('reject', self.pending)
        Profile.objects.get(user=self.pending).unban_user()

        response = self.client.get(reverse('account_approval'))
        self.assertIn('pending', {p.user.username for p in response.context['profiles']})

    def test_cannot_reject_approved_account(self):
        self.client.force_login(self.approver)
        self.assertEqual(self.action('reject', self.approved).status_code, 404)
        self.assertTrue(User.objects.filter(username='approved').exists())


class ApprovalNotificationActionTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.approver = create_user(username='approver', user_permissions=('approve_user',))
        cls.other_approver = create_user(username='other_approver', user_permissions=('approve_user',))
        cls.regular = create_user(username='regular')
        cls.pending = create_user_with_password('pending', approved=False)

    def setUp(self):
        notify_account_approvers(self.pending.profile)

    def notification(self, user):
        return Notification.objects.get(recipient__user=user)

    def act(self, user, action):
        return self.client.post(reverse('notification_action'), {'id': self.notification(user).id, 'action': action})

    def ajax_actions(self):
        response = self.client.get(reverse('notification_ajax'), {'status': 'all'})
        return [[a['name'] for a in n['actions']] for n in response.json()['notifications']]

    def test_notification_carries_actions(self):
        notification = self.notification(self.approver)
        self.assertEqual(notification.kind, 'account_approval')
        self.assertEqual(notification.data, {'profile': self.pending.profile.id, 'username': 'pending'})
        self.client.force_login(self.approver)
        self.assertEqual(self.ajax_actions(), [['approve', 'reject']])

    def test_title_is_rendered_when_displayed(self):
        # The stored title is only a fallback; the handler renders it in the viewer's language.
        Notification.objects.update(title='stored title')
        self.client.force_login(self.approver)
        titles = [n['title'] for n in self.client.get(reverse('notification_ajax')).json()['notifications']]
        self.assertEqual(titles, ['New account awaiting approval: pending'])
        self.assertContains(self.client.get(reverse('notification_list')), 'New account awaiting approval: pending')

    def test_approve_from_notification_resolves_it_for_everyone(self):
        self.client.force_login(self.approver)
        response = self.act(self.approver, 'approve')
        self.assertEqual(response.status_code, 200)
        self.assertIn('pending', response.json()['message'])
        self.assertTrue(Profile.objects.get(user=self.pending).is_approved)
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(Notification.objects.filter(read=False).exists())

        # The other approver's notification no longer offers actions, and acting on it fails cleanly.
        self.client.force_login(self.other_approver)
        self.assertEqual(self.ajax_actions(), [[]])
        self.assertEqual(self.act(self.other_approver, 'reject').status_code, 400)
        self.assertTrue(Profile.objects.get(user=self.pending).is_approved)

    def test_reject_from_notification(self):
        self.client.force_login(self.approver)
        self.assertEqual(self.act(self.approver, 'reject').status_code, 200)
        self.assertTrue(Profile.objects.get(user=self.pending).is_banned)

    def test_needs_permission_and_ownership(self):
        self.client.force_login(self.regular)
        # Someone else's notification.
        self.assertEqual(self.act(self.approver, 'approve').status_code, 404)

        # A recipient who lost the permission gets no actions.
        self.approver.user_permissions.clear()
        self.client.force_login(User.objects.get(id=self.approver.id))
        self.assertEqual(self.act(self.approver, 'approve').status_code, 400)
        self.assertFalse(Profile.objects.get(user=self.pending).is_approved)

    def test_notification_page_shows_buttons(self):
        self.client.force_login(self.approver)
        response = self.client.get(reverse('notification_list'))
        self.assertContains(response, 'data-action="approve"')
        self.assertContains(response, 'data-action="reject"')


class RejectAllTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.approver = create_user(username='approver', user_permissions=('approve_user',))
        cls.pending = create_user_with_password('pending', approved=False)
        cls.pending_other = create_user_with_password('pending_other', approved=False)

    def test_reject_all(self):
        self.client.force_login(self.approver)
        self.client.post(reverse('account_approval_action'), {'action': 'reject_all'})
        for user in (self.pending, self.pending_other):
            self.assertTrue(Profile.objects.get(user=user).is_banned)
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(list(self.client.get(reverse('account_approval')).context['profiles']), [])


class PendingAccountNavigationTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = NavigationBar.objects.create(key='user', label='Users', path='/users/', regex=r'^/user', order=1)
        NavigationBar.objects.create(key='approval', label='Pending accounts', path='/users/approval/',
                                     regex=r'^/users/approval/', order=1, parent=users,
                                     permission='judge.approve_user')
        cls.approver = create_user(username='approver', user_permissions=('approve_user',))
        cls.regular = create_user(username='regular')
        create_user_with_password('pending', approved=False)
        create_user_with_password('pending_other', approved=False)

    def setUp(self):
        invalidate_pending_account_count()

    def tree(self, user):
        return {node.key: (node.badge, [child.key for child in node.get_children()])
                for node in nav_tree(NavigationBar.objects.all(), user)}

    def test_item_and_badge_only_for_approvers(self):
        self.assertEqual(self.tree(self.approver), {'user': (2, ['approval'])})
        self.assertEqual(self.tree(self.regular), {'user': (0, [])})

        self.client.force_login(self.approver)
        response = self.client.get(reverse('problem_list'))
        self.assertContains(response, 'href="/users/approval/" class="nav-approval')
        self.assertContains(response, '<span class="nav-badge">2</span>')

        self.client.force_login(self.regular)
        response = self.client.get(reverse('problem_list'))
        self.assertNotContains(response, 'nav-approval')
        self.assertNotContains(response, 'nav-badge')

    def test_count_follows_approvals(self):
        self.assertEqual(pending_account_count(), 2)
        self.client.force_login(self.approver)
        self.client.post(reverse('account_approval_action'), {'action': 'approve_all'})
        self.assertEqual(pending_account_count(), 0)
        self.assertEqual(self.tree(self.approver), {'user': (0, ['approval'])})
