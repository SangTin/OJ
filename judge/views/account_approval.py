import logging

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext as _, gettext_lazy, ngettext
from django.views.generic import ListView, View

from judge.models import Profile
from judge.utils.account_approval import send_approval_email, send_rejection_email
from judge.utils.views import TitleMixin

__all__ = ['PendingAccountList', 'AccountApprovalAction']

logger = logging.getLogger('judge.account_approval')


class AccountApprovalPermissionMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_perm('judge.approve_user'):
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)


def pending_profiles():
    return Profile.objects.filter(is_approved=False).select_related('user')


class PendingAccountList(AccountApprovalPermissionMixin, TitleMixin, ListView):
    template_name = 'user/account-approval.html'
    context_object_name = 'profiles'
    title = gettext_lazy('Pending accounts')

    def get_queryset(self):
        return pending_profiles().prefetch_related('organizations').order_by('user__date_joined')


class AccountApprovalAction(AccountApprovalPermissionMixin, View):
    http_method_names = ['post']

    def post(self, request):
        action = request.POST.get('action')
        if action == 'approve':
            self.approve(request, pending_profiles().filter(id=request.POST.get('id')))
        elif action == 'approve_all':
            self.approve(request, pending_profiles())
        elif action == 'reject':
            self.reject(request, get_object_or_404(pending_profiles(), id=request.POST.get('id')))
        return HttpResponseRedirect(reverse('account_approval'))

    def approve(self, request, queryset):
        profiles = list(queryset)
        for profile in profiles:
            profile.approve()
            send_approval_email(profile, request)
            logger.info('%s approved account %s', request.user.username, profile.user.username)
        messages.success(request, ngettext('%d account was approved.', '%d accounts were approved.',
                                           len(profiles)) % len(profiles))

    def reject(self, request, profile):
        user = profile.user
        # Only accounts that never logged in are deleted; anything else may already own submissions or other data.
        if user.last_login is not None:
            messages.error(request, _('%s has logged in before and cannot be rejected here. '
                                      'Handle this account in the admin instead.') % user.username)
            return
        send_rejection_email(user)
        with transaction.atomic():
            user.delete()
        logger.info('%s rejected and deleted account %s', request.user.username, user.username)
        messages.success(request, _('Account %s was rejected and removed.') % user.username)
