import logging

from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.utils.translation import gettext as _

from judge.models import MiscConfig, Profile, make_notification

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
    make_notification(
        account_approvers(),
        title=_('New account awaiting approval: %s') % profile.user.username,
        url=reverse('account_approval'),
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
                'and the account has been removed.') % {
        'username': user.username,
        'site': settings.SITE_LONG_NAME,
    }
    return _email_user(user, subject, message)
