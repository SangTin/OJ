from django.db import migrations, models
from django.db.models import F, Max


def add_pending_accounts_item(apps, schema_editor):
    NavigationBar = apps.get_model('judge', 'NavigationBar')
    parent = NavigationBar.objects.filter(key='user').first()
    if parent is None or NavigationBar.objects.filter(key='approval').exists():
        return

    # Append as the last child of the Users item. Historical models have no MPTT manager,
    # so make room in the nested set by hand.
    position = parent.rght
    NavigationBar.objects.filter(tree_id=parent.tree_id, rght__gte=position).update(rght=F('rght') + 2)
    NavigationBar.objects.filter(tree_id=parent.tree_id, lft__gt=position).update(lft=F('lft') + 2)
    last_order = NavigationBar.objects.filter(parent=parent).aggregate(order=Max('order'))['order'] or 0
    NavigationBar.objects.create(
        key='approval', label='Pending accounts', path='/users/approval/', regex=r'^/users/approval/',
        order=last_order + 1, permission='judge.approve_user', parent=parent,
        tree_id=parent.tree_id, level=parent.level + 1, lft=position, rght=position + 1,
    )


def remove_pending_accounts_item(apps, schema_editor):
    NavigationBar = apps.get_model('judge', 'NavigationBar')
    item = NavigationBar.objects.filter(key='approval').first()
    if item is None:
        return
    width = item.rght - item.lft + 1
    tree_id, right = item.tree_id, item.rght
    item.delete()
    NavigationBar.objects.filter(tree_id=tree_id, rght__gt=right).update(rght=F('rght') - width)
    NavigationBar.objects.filter(tree_id=tree_id, lft__gt=right).update(lft=F('lft') - width)


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0246_notification_kind_data'),
    ]

    operations = [
        migrations.AddField(
            model_name='navigationbar',
            name='permission',
            field=models.CharField(blank=True, help_text='Only show this item to users with this permission, e.g. judge.approve_user. Leave empty to show it to everyone.', max_length=100, verbose_name='required permission'),
        ),
        migrations.RunPython(add_pending_accounts_item, remove_pending_accounts_item),
    ]
