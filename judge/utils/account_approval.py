import logging

from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext as _

from judge.models import MiscConfig, Profile, make_notification
from judge.notification_actions import NotificationAction, NotificationActionError, NotificationActionHandler, \
    register, resolve_notifications

logger = logging.getLogger('judge.account_approval')

# MiscConfig key toggled from the site settings page; '1' means new accounts need approval.
APPROVAL_CONFIG_KEY = 'require_account_approval'


def approval_required():
    return MiscConfig.objects.filter(key=APPROVAL_CONFIG_KEY, value='1').exists()


def account_approvers():
    can_approve = (Q(user__is_superuser=True) |
                   Q(user__user_permissions__codename='approve_user') |
                   Q(user__groups__permissions__codename='approve_user'))
    return Profile.objects.filter(can_approve, user__is_active=True).distinct()


def hold_for_approval(profile):
    """Mark a newly registered profile as unapproved (without saving) if approval is required.

    Returns whether the profile now needs approval; pass it to `notify_account_approvers` once saved.
    """
    if not approval_required():
        return False
    profile.is_approved = False
    return True


def notify_account_approvers(profile):
    # Notifications are stored as text, so write the title in the site language rather than the registrant's.
    with translation.override(settings.LANGUAGE_CODE):
        title = _('New account awaiting approval: %s') % profile.user.username
    make_notification(
        account_approvers(),
        title=title,
        url=reverse('account_approval'),
        kind=AccountApprovalNotification.kind,
        data={'profile': profile.id, 'username': profile.user.username},
    )


def _email_user(user, subject, message):
    if not user.email:
        return False
    try:
        user.email_user(subject, message)
    except Exception:
        logger.exception('Failed to send account approval email to %s', user.username)
        return False
    return True


def send_approval_email(profile, request):
    user = profile.user
    subject = _('Your account on %s has been approved') % settings.SITE_NAME
    message = _('Hello %(username)s,\n\n'
                'Your account on %(site)s has been approved by an administrator. '
                'You can now log in at %(url)s') % {
        'username': user.username,
        'site': settings.SITE_LONG_NAME,
        'url': request.build_absolute_uri(reverse('auth_login')),
    }
    return _email_user(user, subject, message)


def send_rejection_email(user):
    subject = _('Your registration on %s was not approved') % settings.SITE_NAME
    message = _('Hello %(username)s,\n\n'
                'Your registration on %(site)s was not approved by an administrator, '
                'and the account has been locked.') % {
        'username': user.username,
        'site': settings.SITE_LONG_NAME,
    }
    return _email_user(user, subject, message)


def pending_profiles():
    # Rejected accounts are banned and stay unapproved; they leave the list until an admin unbans them.
    return Profile.objects.filter(is_approved=False, ban_reason__isnull=True).select_related('user')


def finish_approval(profile, request):
    """Run after a profile got approved anywhere. Returns whether the user could be emailed."""
    resolve_notifications(AccountApprovalNotification.kind, profile=profile.id)
    logger.info('%s approved account %s', request.user.username, profile.user.username)
    return send_approval_email(profile, request)


def approve_profile(profile, request):
    profile.approve()
    return finish_approval(profile, request)


def reject_profile(profile, request):
    # Lock the account instead of deleting it, so nothing it may own is lost; an admin can unban it later.
    profile.ban_user(_('Registration was not approved.'))
    send_rejection_email(profile.user)
    resolve_notifications(AccountApprovalNotification.kind, profile=profile.id)
    logger.info('%s rejected and locked account %s', request.user.username, profile.user.username)


@register
class AccountApprovalNotification(NotificationActionHandler):
    kind = 'account_approval'

    def get_title(self, notification):
        if 'username' in notification.data:
            return _('New account awaiting approval: %s') % notification.data['username']

    def get_profile(self, notification):
        return pending_profiles().filter(id=notification.data.get('profile')).first()

    def get_actions(self, notification, request):
        if not request.user.has_perm('judge.approve_user') or self.get_profile(notification) is None:
            return []
        return [
            NotificationAction('approve', _('Approve')),
            NotificationAction('reject', _('Reject'), style='danger',
                               confirm=_('Reject and lock this account?')),
        ]

    def perform(self, notification, action, request):
        profile = self.get_profile(notification)
        if profile is None:
            raise NotificationActionError(_('This account has already been handled.'))
        if action == 'approve':
            approve_profile(profile, request)
            return _('Account %s was approved.') % profile.user.username
        reject_profile(profile, request)
        return _('Account %s was rejected and locked.') % profile.user.username
