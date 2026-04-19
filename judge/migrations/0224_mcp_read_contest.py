from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0223_mcp_token'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='mcptoken',
            options={
                'verbose_name': 'MCP API token',
                'verbose_name_plural': 'MCP API tokens',
                'permissions': (
                    ('use_mcp_api', 'Use MCP API'),
                    ('mcp_read_problem', 'MCP: read problems'),
                    ('mcp_read_submission', 'MCP: read submissions'),
                    ('mcp_read_ticket', 'MCP: read tickets'),
                    ('mcp_read_contest', 'MCP: read contests'),
                ),
            },
        ),
    ]
