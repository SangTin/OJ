"""MCP API token management — dedicated page at ``/mcp/tokens/``.

Separate from the main profile edit flow so the settings area is not
inflated by a feature only MCP users ever touch. Linked from the profile
edit sidebar when the user holds ``judge.use_mcp_api``.
"""
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.forms import widgets
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST
from django.views.generic import FormView

from judge.mcp.auth import MCP_GATE_PERM
from judge.mcp.registry import get_required_perms
from judge.models import MCPToken
from judge.utils.views import TitleMixin

__all__ = ['MCPTokenForm', 'MCPTokenManageView', 'mcp_token_revoke']


class MCPTokenForm(forms.Form):
    """Issuance form constrained to the current user's permission set."""

    name = forms.CharField(
        max_length=100, label=_('Token name'),
        help_text=_('A label to recognize this token later (e.g. "Laptop").'),
    )
    scopes = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.none(),
        label=_('Scopes'),
        help_text=_('Permissions this token is allowed to exercise. '
                    'Only permissions you currently hold are listed.'),
        widget=forms.CheckboxSelectMultiple,
    )
    expiration = forms.ChoiceField(
        choices=[
            ('7', _('7 days')),
            ('30', _('30 days')),
            ('60', _('60 days')),
            ('90', _('90 days')),
            ('custom', _('Custom')),
            ('0', _('No expiration')),
        ],
        initial='30',
        label=_('Expiration'),
    )
    custom_expiration = forms.DateField(
        required=False,
        label=_('Custom expiration date'),
        widget=widgets.DateInput(attrs={'type': 'date'}),
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Set dynamic choices for expiration with dates
        now = timezone.now()
        def fmt(d): return d.strftime('%b %d, %Y')
        self.fields['expiration'].choices = [
            ('7', _('7 days (%s)') % fmt(now + timedelta(days=7))),
            ('30', _('30 days (%s)') % fmt(now + timedelta(days=30))),
            ('60', _('60 days (%s)') % fmt(now + timedelta(days=60))),
            ('90', _('90 days (%s)') % fmt(now + timedelta(days=90))),
            ('custom', _('Custom')),
            ('0', _('No expiration')),
        ]

        # Ensure tool modules are imported so TOOL_REGISTRY is populated.
        import judge.mcp.tools  # noqa: F401

        # Only show permissions that at least one MCP tool actually requires.
        tool_perms = get_required_perms()  # e.g. {'judge.view_problem', ...}
        perm_q = Q()
        for codename in tool_perms:
            app_label, code = codename.split('.', 1)
            perm_q |= Q(content_type__app_label=app_label, codename=code)

        if not perm_q:
            qs = Permission.objects.none()
        elif user.is_superuser:
            qs = Permission.objects.filter(perm_q)
        else:
            qs = Permission.objects.filter(
                perm_q, Q(user=user) | Q(group__user=user),
            ).distinct()

        self.fields['scopes'].queryset = (
            qs.select_related('content_type')
              .order_by('content_type__app_label', 'codename')
        )

    def clean(self):
        cleaned = super().clean()
        expiration = cleaned.get('expiration')
        custom_date = cleaned.get('custom_expiration')
        if expiration == 'custom' and not custom_date:
            self.add_error(
                'custom_expiration',
                _('Enter a date for custom expiration, or choose a preset.'),
            )
        if custom_date and custom_date <= timezone.now().date():
            self.add_error(
                'custom_expiration',
                _('Expiration date must be in the future.'),
            )
        return cleaned


class MCPTokenManageView(LoginRequiredMixin, TitleMixin, FormView):
    template_name = 'user/mcp-tokens.html'
    form_class = MCPTokenForm
    success_url = reverse_lazy('mcp_token_manage')

    def get_title(self):
        return _('MCP API tokens')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.has_perm(MCP_GATE_PERM):
            raise PermissionDenied(
                _('You need the "%s" permission to manage MCP tokens.') % MCP_GATE_PERM,
            )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        expiration_type = form.cleaned_data['expiration']
        expires_at = None
        
        if expiration_type == 'custom':
            custom_date = form.cleaned_data.get('custom_expiration')
            if custom_date:
                expires_at = timezone.make_aware(timezone.datetime.combine(custom_date, timezone.datetime.min.time()))
        elif expiration_type != '0':
            expiration_days = int(expiration_type)
            expires_at = timezone.now() + timedelta(days=expiration_days)

        instance, raw = MCPToken.generate(
            user=self.request.user,
            name=form.cleaned_data['name'],
            permissions=list(form.cleaned_data['scopes']),
            expires_at=expires_at,
        )
        messages.success(
            self.request,
            _('Token "%(name)s" created. Copy it now — it will not be shown again:') % {
                'name': instance.name,
            },
        )
        messages.info(self.request, raw, extra_tags='mcp-token-secret')
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['tokens'] = (
            MCPToken.objects
            .filter(user=self.request.user, revoked_at__isnull=True)
            .prefetch_related('scopes__content_type')
            .order_by('-id')
        )
        context['show_form'] = bool(self.request.GET.get('new')) or context.get('form').errors
        context['now'] = timezone.now()
        return context


@require_POST
@login_required
def mcp_token_revoke(request, pk):
    token = get_object_or_404(MCPToken, pk=pk)
    if token.user_id != request.user.id:
        raise Http404()
    if token.revoked_at is None:
        token.revoke()
        messages.success(request, _('Token "%s" revoked.') % token.name)
    else:
        messages.info(request, _('Token "%s" was already revoked.') % token.name)
    return HttpResponseRedirect(reverse('mcp_token_manage'))
