from django.db import migrations
from django.db.models import F, Max


def add_import_users_item(apps, schema_editor):
    NavigationBar = apps.get_model('judge', 'NavigationBar')
    parent = NavigationBar.objects.filter(key='user').first()
    if parent is None or NavigationBar.objects.filter(key='userimport').exists():
        return

    # Append as the last child of the Users item (see 0247 for why the nested set is updated by hand).
    position = parent.rght
    NavigationBar.objects.filter(tree_id=parent.tree_id, rght__gte=position).update(rght=F('rght') + 2)
    NavigationBar.objects.filter(tree_id=parent.tree_id, lft__gt=position).update(lft=F('lft') + 2)
    last_order = NavigationBar.objects.filter(parent=parent).aggregate(order=Max('order'))['order'] or 0
    NavigationBar.objects.create(
        key='userimport', label='Import users', path='/users/import/', regex=r'^/users/import/',
        order=last_order + 1, permission='judge.bulk_create_users', parent=parent,
        tree_id=parent.tree_id, level=parent.level + 1, lft=position, rght=position + 1,
    )


def remove_import_users_item(apps, schema_editor):
    NavigationBar = apps.get_model('judge', 'NavigationBar')
    item = NavigationBar.objects.filter(key='userimport').first()
    if item is None:
        return
    width = item.rght - item.lft + 1
    tree_id, right = item.tree_id, item.rght
    item.delete()
    NavigationBar.objects.filter(tree_id=tree_id, rght__gt=right).update(rght=F('rght') - width)
    NavigationBar.objects.filter(tree_id=tree_id, lft__gt=right).update(lft=F('lft') - width)


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0247_navigationbar_permission'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='profile',
            options={'permissions': (('test_site', 'Shows in-progress development stuff'), ('totp', 'Edit TOTP settings'), ('can_upload_image', 'Can upload image directly to server via martor'), ('high_problem_timelimit', 'Can set high problem timelimit'), ('long_contest_duration', 'Can set long contest duration'), ('create_mass_testcases', 'Can create unlimitted number of testcases for a problem'), ('ban_user', 'Ban users'), ('approve_user', 'Approve user accounts'), ('bulk_create_users', 'Create user accounts in bulk')), 'verbose_name': 'user profile', 'verbose_name_plural': 'user profiles'},
        ),
        migrations.RunPython(add_import_users_item, remove_import_users_item),
    ]
