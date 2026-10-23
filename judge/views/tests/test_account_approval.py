from django.core import mail
from django.test import RequestFactory, TestCase
from django.urls import reverse

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
