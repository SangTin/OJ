from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext as _, gettext_lazy
from django.views import View
from django.views.decorators.clickjacking import xframe_options_sameorigin

from judge.models import Contest, Organization
from judge.utils.user_import import COLUMNS, ImportFileError, MAX_ROWS, STATUS_ADD, STATUS_CREATE, STATUS_ERROR, \
    STATUS_SKIP, create_accounts, load_credentials, load_rows, read_file, resolve_rows, row_summary, \
    send_credentials_emails, store_credentials, store_rows, template_workbook

__all__ = ['BulkUserImport', 'OrganizationUserImport', 'ContestantImport', 'UserImportDownload',
           'UserImportTemplate']


class BaseUserImportView(LoginRequiredMixin, View):
    """Upload a file, preview what will happen to each row, then confirm.

    Subclasses decide who may use the page (``has_permission``), what happens to rows whose email already has an
    account (``mark_existing``) and what to do with them once confirmed (``apply``).
    """
    template_name = 'user/import/page.html'
    title = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        self.setup_target()
        if not self.has_permission():
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def setup_target(self):
        pass

    def has_permission(self):
        raise NotImplementedError()

    def can_create(self):
        return self.request.user.has_perm('judge.bulk_create_users')

    def new_account_organization(self):
        return None

    def mark_existing(self, row):
        row.status = STATUS_SKIP
        row.note = _('Already has an account: %s') % row.profile.user.username

    def mark(self, rows):
        can_create = self.can_create()
        for row in rows:
            row.note = ''
            if row.error:
                row.status = STATUS_ERROR
            elif row.profile is not None:
                self.mark_existing(row)
            elif can_create:
                row.status = STATUS_CREATE
                row.note = _('New account: %s') % row.username
            else:
                row.status = STATUS_ERROR
                row.error = _('No account uses this email.')
        return rows

    def apply(self, rows):
        pass

    def hidden_fields(self):
        return {}

    def get_context_data(self, **kwargs):
        context = {
            'title': self.title,
            'columns': COLUMNS,
            'max_rows': MAX_ROWS,
            'can_create': self.can_create(),
            'hidden_fields': self.hidden_fields(),
            'STATUS_CREATE': STATUS_CREATE, 'STATUS_ADD': STATUS_ADD,
            'STATUS_SKIP': STATUS_SKIP, 'STATUS_ERROR': STATUS_ERROR,
        }
        context.update(kwargs)
        return context

    def render(self, **kwargs):
        return render(self.request, self.template_name, self.get_context_data(**kwargs))

    def get(self, request, *args, **kwargs):
        return self.render(step='upload')

    def post(self, request, *args, **kwargs):
        if request.POST.get('step') == 'confirm':
            return self.confirm()
        return self.preview()

    def preview(self):
        uploaded = self.request.FILES.get('file')
        if uploaded is None:
            return self.render(step='upload', error=_('Choose a file to upload.'))
        try:
            rows = self.mark(resolve_rows(read_file(uploaded)))
        except ImportFileError as e:
            return self.render(step='upload', error=str(e))
        return self.render(step='preview', rows=rows, summary=row_summary(rows),
                           token=store_rows(self.request.user, rows))

    def confirm(self):
        rows = load_rows(self.request.user, self.request.POST.get('token'))
        if rows is None:
            return self.render(step='upload', error=_('The preview has expired. Please upload the file again.'))
        # Plan again: accounts may have been created since the preview.
        rows = self.mark(resolve_rows(rows))
        created = create_accounts(rows, organization=self.new_account_organization())
        self.apply(rows)

        failed_emails = []
        if created and self.request.POST.get('send_email'):
            failed_emails = send_credentials_emails(created, self.request)
        return self.render(
            step='result', rows=rows, summary=row_summary(rows), created=created,
            emailed=bool(self.request.POST.get('send_email')), failed_emails=failed_emails,
            credentials_url=reverse('user_import_download', args=[store_credentials(self.request.user, created)])
            if created else None,
        )


class BulkUserImport(BaseUserImportView):
    title = gettext_lazy('Import users')

    def has_permission(self):
        return self.can_create()


class OrganizationUserImport(BaseUserImportView):
    def setup_target(self):
        self.organization = get_object_or_404(Organization, slug=self.kwargs['slug'])
        self.title = _('Import users into %s') % self.organization.name

    def is_org_admin(self):
        return self.organization.is_admin(self.request.profile) or \
            self.request.user.has_perm('judge.edit_all_organization')

    def has_permission(self):
        return self.is_org_admin()

    def can_create(self):
        return self.is_org_admin()

    def new_account_organization(self):
        return self.organization

    def mark_existing(self, row):
        if self.organization in row.profile.organizations.all():
            row.status = STATUS_SKIP
            row.note = _('Already a member: %s') % row.profile.user.username
        elif self.request.user.has_perm('judge.edit_all_organization'):
            row.status = STATUS_ADD
            row.note = _('Existing account: %s') % row.profile.user.username
        else:
            # Organization admins can't pull other people's accounts into their organization.
            row.status = STATUS_SKIP
            row.note = _('Already has an account: %s (left unchanged)') % row.profile.user.username

    def apply(self, rows):
        joined = [row.profile for row in rows if row.status == STATUS_ADD]
        if joined:
            self.organization.members.add(*joined)
            self.organization.on_user_changes()

    def get_context_data(self, **kwargs):
        return super().get_context_data(organization=self.organization, **kwargs)


# Shown in an iframe on the contest form, so it must be framable by the site itself.
@method_decorator(xframe_options_sameorigin, name='dispatch')
class ContestantImport(BaseUserImportView):
    """Popup used from the contest create/edit form to fill the private contestants field.

    It creates the missing accounts right away but doesn't touch the contest: the contestants are handed back to
    the form, which saves them with the rest of the contest. ``current`` holds the ids already in the field.
    """
    template_name = 'user/import/popup.html'
    title = gettext_lazy('Add contestants from a file')

    def setup_target(self):
        self.contest = None
        self.organization = None
        if 'contest' in self.kwargs:
            self.contest = get_object_or_404(Contest, key=self.kwargs['contest'])
            if self.contest.is_organization_private:
                self.organization = self.contest.organization
        elif 'slug' in self.kwargs:
            self.organization = get_object_or_404(Organization, slug=self.kwargs['slug'])

        raw = self.request.POST.get('current', self.request.GET.get('current', ''))
        self.current = {int(pk) for pk in raw.split(',') if pk.isdigit()}

    def is_org_admin(self):
        return self.organization is not None and (
            self.organization.is_admin(self.request.profile) or
            self.request.user.has_perm('judge.edit_all_organization'))

    def has_permission(self):
        if self.contest is not None:
            return self.contest.is_editable_by(self.request.user)
        if self.organization is not None:
            return self.is_org_admin()
        return self.request.user.has_perm('judge.add_contest')

    def can_create(self):
        return super().can_create() or self.is_org_admin()

    def new_account_organization(self):
        return self.organization

    def mark_existing(self, row):
        username = row.profile.user.username
        if row.profile.id in self.current:
            row.status = STATUS_SKIP
            row.note = _('Already in the list: %s') % username
        elif self.organization is not None and self.organization not in row.profile.organizations.all():
            # Only members can see an organization's private contest.
            row.status = STATUS_ERROR
            row.error = _('%(user)s is not a member of %(org)s.') % {'user': username, 'org': self.organization.name}
        else:
            row.status = STATUS_ADD
            row.note = _('Existing account: %s') % username

    def hidden_fields(self):
        return {'current': ','.join(map(str, sorted(self.current)))}

    def get_context_data(self, **kwargs):
        rows = kwargs.get('rows') or []
        if kwargs.get('step') == 'result':
            kwargs['contestants'] = [{'id': row.profile.id, 'text': row.profile.user.username} for row in rows
                                     if row.status in (STATUS_CREATE, STATUS_ADD)]
        return super().get_context_data(organization=self.organization, **kwargs)


class UserImportDownload(LoginRequiredMixin, View):
    def get(self, request, token):
        data = load_credentials(request.user, token)
        if data is None:
            raise Http404()
        response = HttpResponse(data, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="accounts.xlsx"'
        return response


class UserImportTemplate(LoginRequiredMixin, View):
    def get(self, request):
        response = HttpResponse(template_workbook(),
                                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = 'attachment; filename="users_template.xlsx"'
        return response
