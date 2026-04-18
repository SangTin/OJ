from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('judge', '0222_add_sample_fields'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='MCPToken',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Human-readable label, e.g. "Claude Desktop on laptop".', max_length=100, verbose_name='token name')),
                ('token_hash', models.CharField(help_text='HMAC-SHA256 hex digest of the token secret.', max_length=64, verbose_name='token hash')),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='created')),
                ('last_used', models.DateTimeField(blank=True, null=True, verbose_name='last used')),
                ('expires_at', models.DateTimeField(blank=True, null=True, verbose_name='expires at')),
                ('revoked_at', models.DateTimeField(blank=True, null=True, verbose_name='revoked at')),
                ('user', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='mcp_tokens', to=settings.AUTH_USER_MODEL, verbose_name='user')),
                ('scopes', models.ManyToManyField(blank=True, help_text='Permissions this token is allowed to exercise. Must be a subset of the permissions granted to the owning user.', related_name='mcp_tokens', to='auth.permission', verbose_name='scopes')),
            ],
            options={
                'verbose_name': 'MCP API token',
                'verbose_name_plural': 'MCP API tokens',
                'permissions': (
                    ('use_mcp_api', 'Use MCP API'),
                    ('mcp_read_problem', 'MCP: read problems'),
                    ('mcp_read_submission', 'MCP: read submissions'),
                    ('mcp_read_ticket', 'MCP: read tickets'),
                ),
            },
        ),
    ]
