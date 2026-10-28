from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0248_bulk_create_users'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='must_change_password',
            field=models.BooleanField(default=False, help_text='The user is sent to the password change page until they change their password.', verbose_name='must change password'),
        ),
    ]
