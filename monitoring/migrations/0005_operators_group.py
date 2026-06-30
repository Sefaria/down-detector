"""
Create an 'Operators' group with least-privilege admin permissions.

Members can author incidents and maintenance, force-resolve outages, and read
the health-check history — but cannot delete records or manage users. Assign
staff to this group instead of making them superusers.
"""
from django.db import migrations


# (app_label, codename) permissions granted to Operators.
OPERATOR_PERMS = [
    ("monitoring", "add_message"),
    ("monitoring", "change_message"),
    ("monitoring", "view_message"),
    ("monitoring", "add_maintenance"),
    ("monitoring", "change_maintenance"),
    ("monitoring", "view_maintenance"),
    ("monitoring", "change_outage"),   # required for the force-resolve action
    ("monitoring", "view_outage"),
    ("monitoring", "view_healthcheck"),
]


def create_operators_group(apps, schema_editor):
    from django.apps import apps as global_apps
    from django.contrib.auth.management import create_permissions

    # Model permissions are normally created by a post_migrate signal that
    # hasn't fired yet during this migration; create them now so we can grant
    # them on a fresh database.
    create_permissions(global_apps.get_app_config("monitoring"), verbosity=0)

    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    group, _ = Group.objects.get_or_create(name="Operators")
    perms = []
    for app_label, codename in OPERATOR_PERMS:
        try:
            perms.append(
                Permission.objects.get(
                    codename=codename, content_type__app_label=app_label
                )
            )
        except Permission.DoesNotExist:
            pass
    group.permissions.set(perms)


def remove_operators_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name="Operators").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("monitoring", "0004_alter_maintenance_description_and_more"),
        ("auth", "0001_initial"),
        ("contenttypes", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_operators_group, remove_operators_group),
    ]
