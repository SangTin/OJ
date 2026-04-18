from django import forms
from django.contrib import admin, messages
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.contrib.auth.models import Permission
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _, ngettext

from judge.models import MCPToken


class MCPTokenAdminForm(forms.ModelForm):
    """Narrow the Permission picker to permissions the owner actually holds.

    This enforces the subset invariant at UI level. Dispatcher re-checks
    ``user.has_perm`` on every call, so even if stale scopes slip through
    (e.g. the user loses a permission later), the token silently loses that
    capability until the permission is re-granted.
    """

    class Meta:
        model = MCPToken
        fields = '__all__'
        widgets = {
            'scopes': FilteredSelectMultiple(_('scopes'), is_stacked=False),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get('instance')
        if instance is not None and instance.user_id:
            user = instance.user
            if not user.is_superuser:
                self.fields['scopes'].queryset = Permission.objects.filter(
                    Q(user=user) | Q(group__user=user),
                ).select_related('content_type').distinct()


@admin.register(MCPToken)
class MCPTokenAdmin(admin.ModelAdmin):
    """Read-oriented admin for MCP tokens.

    Tokens cannot be created from this UI — issuing requires showing the
    plaintext exactly once, which does not fit the Django admin change-form
    flow. Use ``python manage.py gen_mcp_token`` to issue. From here you can
    inspect usage, edit scopes, and revoke.
    """

    form = MCPTokenAdminForm
    list_display = ('id', 'user', 'name', 'status', 'scope_count', 'created',
                    'last_used', 'expires_at', 'revoked_at')
    list_filter = ('created', 'revoked_at', 'expires_at')
    list_select_related = ('user',)
    search_fields = ('user__username', 'name')
    readonly_fields = ('token_hash', 'created', 'last_used')
    autocomplete_fields = ('user',)
    ordering = ('-id',)
    actions = ('revoke_selected',)

    fieldsets = (
        (None, {
            'fields': ('user', 'name', 'scopes'),
        }),
        (_('Lifecycle'), {
            'fields': ('created', 'last_used', 'expires_at', 'revoked_at'),
        }),
        (_('Security'), {
            'fields': ('token_hash',),
            'classes': ('collapse',),
        }),
    )

    def has_add_permission(self, request):
        return False

    @admin.display(description=_('status'))
    def status(self, obj):
        if obj.revoked_at is not None:
            return _('revoked')
        if obj.expires_at is not None and obj.expires_at < timezone.now():
            return _('expired')
        return _('active')

    @admin.display(description=_('scopes'))
    def scope_count(self, obj):
        return obj.scopes.count()

    @admin.action(description=_('Revoke selected tokens'))
    def revoke_selected(self, request, queryset):
        now = timezone.now()
        updated = queryset.filter(revoked_at__isnull=True).update(revoked_at=now)
        if updated:
            self.message_user(
                request,
                ngettext('%d token revoked.', '%d tokens revoked.', updated) % updated,
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                _('No active tokens in selection.'),
                level=messages.WARNING,
            )
