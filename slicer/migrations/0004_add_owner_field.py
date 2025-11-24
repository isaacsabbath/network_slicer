from django.db import migrations, models
from django.conf import settings


def backfill_owner(apps, schema_editor):
    NetworkSlice = apps.get_model('slicer', 'NetworkSlice')
    User = apps.get_model(settings.AUTH_USER_MODEL.split('.')[0], settings.AUTH_USER_MODEL.split('.')[1])
    try:
        admin = User.objects.filter(is_staff=True).order_by('id').first()
    except Exception:
        admin = None
    for sl in NetworkSlice.objects.filter(owner__isnull=True):
        if admin:
            sl.owner_id = admin.id
            sl.save(update_fields=['owner'])

class Migration(migrations.Migration):

    dependencies = [
        ('slicer', '0003_update_slice_types_and_add_device_guestcredential'),
    ]

    operations = [
        migrations.AddField(
            model_name='networkslice',
            name='owner',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.CASCADE, related_name='slices', to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(backfill_owner, migrations.RunPython.noop),
    ]
