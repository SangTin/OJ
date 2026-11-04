"""Authoring constraints shared by website forms and MCP."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def validate_problem_time_limit(user, time_limit):
    if time_limit and time_limit > settings.VNOJ_PROBLEM_TIMELIMIT_LIMIT and not (
            user and user.has_perm('judge.high_problem_timelimit')):
        raise ValidationError(_('You cannot set time limit higher than %d seconds')
                              % settings.VNOJ_PROBLEM_TIMELIMIT_LIMIT, 'problem_timelimit_too_long')


def validate_contest_duration(user, start_time, end_time):
    if start_time and end_time and (end_time - start_time).days > settings.VNOJ_CONTEST_DURATION_LIMIT and not (
            user and user.has_perm('judge.long_contest_duration')):
        raise ValidationError(_('Contest duration cannot be longer than %d days')
                              % settings.VNOJ_CONTEST_DURATION_LIMIT, 'contest_duration_too_long')


def validate_organization_code(code, organization, contest=False):
    prefix = ''.join(character for character in organization.slug.lower() if character.isalnum()) + '_'
    if not code.startswith(prefix):
        message = _('Contest id must starts with `%s`') if contest else _('Problem id code must starts with `%s`')
        raise ValidationError(message % (prefix,), 'contest_id_invalid_prefix' if contest else
                              'problem_id_invalid_prefix')
