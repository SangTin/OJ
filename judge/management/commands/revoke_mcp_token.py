"""Revoke an MCP API token by its ID, rendering it unusable immediately."""
from django.core.management.base import BaseCommand, CommandError

from judge.models import MCPToken


class Command(BaseCommand):
    help = 'Revoke an MCP API token by ID.'

    def add_arguments(self, parser):
        parser.add_argument('token_id', type=int)

    def handle(self, *args, **opts):
        try:
            token = MCPToken.objects.get(id=opts['token_id'])
        except MCPToken.DoesNotExist:
            raise CommandError(f'token not found: {opts["token_id"]}')

        if token.revoked_at is not None:
            self.stdout.write(self.style.WARNING(
                f'token #{token.id} already revoked at {token.revoked_at}',
            ))
            return

        token.revoke()
        self.stdout.write(self.style.SUCCESS(
            f'revoked MCP token #{token.id} (user={token.user.username}, '
            f'name={token.name!r})',
        ))
