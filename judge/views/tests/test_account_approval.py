from django.contrib.auth.models import User
from django.core import mail
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from judge.forms import CustomAuthenticationForm
from judge.models import MiscConfig, Notification, Profile
from judge.models.tests.util import create_user
from judge.utils.account_approval import APPROVAL_CONFIG_KEY, approval_required, hold_for_approval, \
    notify_account_approvers


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

    def test_reject_deletes_account_and_emails(self):
        self.client.force_login(self.approver)
        self.action('reject', self.pending)
        self.assertFalse(User.objects.filter(username='pending').exists())
        self.assertEqual([m.to for m in mail.outbox], [['pending@example.com']])

    def test_reject_refuses_accounts_that_logged_in(self):
        User.objects.filter(id=self.pending.id).update(last_login=timezone.now())
        self.client.force_login(self.approver)
        self.action('reject', self.pending)
        self.assertTrue(User.objects.filter(username='pending').exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_cannot_reject_approved_account(self):
        self.client.force_login(self.approver)
        self.assertEqual(self.action('reject', self.approved).status_code, 404)
        self.assertTrue(User.objects.filter(username='approved').exists())
