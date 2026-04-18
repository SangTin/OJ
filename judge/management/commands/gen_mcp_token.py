"""Issue an MCP API token for a user with an explicit scope list.

The plaintext token is printed once. Only its HMAC digest is stored.

Scope validation:

* each scope must resolve to a real ``auth.Permission`` via its
  ``app_label.codename`` string;
* the user must currently hold every listed permission — enforces the
  subset invariant at issuance time. The runtime dispatcher re-checks
  ``user.has_perm`` on every call, so later revocations also take effect.
"""
from datetime import timedelta

from django.contrib.auth.models import Permission, User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from judge.mcp.auth import MCP_GATE_PERM
from judge.models import MCPToken


def _resolve_permission(codename):
    if '.' not in codename:
        raise CommandError(
            f'invalid scope "{codename}" — expected APP_LABEL.CODENAME',
        )
    app_label, name = codename.split('.', 1)
    try:
        return Permission.objects.select_related('content_type').get(
            content_type__app_label=app_label, codename=name,
        )
    except Permission.DoesNotExist:
        raise CommandError(f'permission does not exist: {codename}')


class Command(BaseCommand):
    help = 'Generate an MCP API token for a user with given scopes.'

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument(
            '--name', required=True,
            help='Human-readable label for the token (e.g. "Claude Desktop").',
        )
        parser.add_argument(
            '--scopes', nargs='+', required=True,
            metavar='APP.CODENAME',
            help='Permission codenames this token is allowed to use, '
                 'e.g. judge.mcp_read_problem judge.mcp_read_submission.',
        )
        parser.add_argument(
            '--days', type=int, default=90,
            help='Expiry in days. Default 90. Use --no-expiry to disable.',
        )
        parser.add_argument(
            '--no-expiry', action='store_true', dest='no_expiry',
            help='Issue a non-expiring token.',
        )

    def handle(self, *args, **opts):
        try:
            user = User.objects.get(username=opts['username'])
        except User.DoesNotExist:
            raise CommandError(f'user not found: {opts["username"]}')

        scope_strings = list(dict.fromkeys(opts['scopes']))
        permissions = []
        for scope in scope_strings:
            perm = _resolve_permission(scope)
            if not user.has_perm(scope):
                raise CommandError(
                    f'user "{user.username}" does not currently have permission '
                    f'"{scope}" — token scope must be a subset of user perms',
                )
            permissions.append(perm)

        if not user.has_perm(MCP_GATE_PERM):
            self.stdout.write(self.style.WARNING(
                f'user "{user.username}" does not have {MCP_GATE_PERM}; the '
                f'token will be rejected until that gate permission is granted.',
            ))

        expires_at = None if opts['no_expiry'] else (
            timezone.now() + timedelta(days=opts['days'])
        )

        instance, raw = MCPToken.generate(
            user=user, name=opts['name'],
            permissions=permissions, expires_at=expires_at,
        )

        self.stdout.write(self.style.SUCCESS(
            f'Created MCP token #{instance.id} for {user.username}',
        ))
        self.stdout.write(f'  name:    {instance.name}')
        self.stdout.write(f'  scopes:  {scope_strings}')
        self.stdout.write(f'  expires: {expires_at or "never"}')
        self.stdout.write('')
        self.stdout.write(self.style.WARNING(
            'Copy this token now — it is shown only once:',
        ))
        self.stdout.write(raw)
