from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.translation import gettext as _, gettext_lazy, ngettext
from django.views.generic import ListView, View

from judge.utils.account_approval import approve_profile, pending_profiles, reject_profile
from judge.utils.views import TitleMixin

__all__ = ['PendingAccountList', 'AccountApprovalAction']


class AccountApprovalPermissionMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_perm('judge.approve_user'):
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)


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
            profile = get_object_or_404(pending_profiles(), id=request.POST.get('id'))
            reject_profile(profile, request)
            messages.success(request, _('Account %s was rejected and locked.') % profile.user.username)
        elif action == 'reject_all':
            profiles = list(pending_profiles())
            for profile in profiles:
                reject_profile(profile, request)
            messages.success(request, ngettext('%d account was rejected and locked.',
                                               '%d accounts were rejected and locked.',
                                               len(profiles)) % len(profiles))
        return HttpResponseRedirect(reverse('account_approval'))

    def approve(self, request, queryset):
        profiles = list(queryset)
        for profile in profiles:
            approve_profile(profile, request)
        messages.success(request, ngettext('%d account was approved.', '%d accounts were approved.',
                                           len(profiles)) % len(profiles))
