"""List MCP tokens with their status and scopes."""
from django.core.management.base import BaseCommand

from judge.models import MCPToken


class Command(BaseCommand):
    help = 'List MCP API tokens.'

    def add_arguments(self, parser):
        parser.add_argument('--username', help='Filter to this user only.')
        parser.add_argument(
            '--include-revoked', action='store_true',
            help='Include revoked tokens in the output.',
        )

    def handle(self, *args, **opts):
        queryset = MCPToken.objects.select_related('user').order_by('user__username', 'id')
        if opts['username']:
            queryset = queryset.filter(user__username=opts['username'])
        if not opts['include_revoked']:
            queryset = queryset.filter(revoked_at__isnull=True)

        for tok in queryset:
            status = 'active' if tok.is_active else (
                'revoked' if tok.revoked_at else 'expired'
            )
            self.stdout.write(
                f'#{tok.id:<5} {tok.user.username:<20} {status:<8} '
                f'{tok.name!r:<30} scopes={tok.scope_codenames()} '
                f'last_used={tok.last_used or "-"}',
            )
