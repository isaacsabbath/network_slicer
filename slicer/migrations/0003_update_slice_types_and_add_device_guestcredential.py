# Generated manually by Copilot on 2025-11-13
from django.db import migrations, models
import uuid


def forwards(apps, schema_editor):
    NetworkSlice = apps.get_model('slicer', 'NetworkSlice')
    # No data migration required beyond possible mapping. Keep existing values if any.
    # If older values exist (EMBB/URLLC/MMTC), map to closest new types.
    mapping = {
        'URLLC': 'GAMING',
        'EMBB': 'CORP',
        'MMTC': 'IOT',
    }
    for obj in NetworkSlice.objects.all():
        if obj.slice_type in mapping:
            obj.slice_type = mapping[obj.slice_type]
            obj.save(update_fields=['slice_type'])


def backwards(apps, schema_editor):
    NetworkSlice = apps.get_model('slicer', 'NetworkSlice')
    mapping = {
        'GAMING': 'URLLC',
        'CORP': 'EMBB',
        'IOT': 'MMTC',
    }
    for obj in NetworkSlice.objects.all():
        if obj.slice_type in mapping:
            obj.slice_type = mapping[obj.slice_type]
            obj.save(update_fields=['slice_type'])


class Migration(migrations.Migration):

    dependencies = [
        ('slicer', '0002_networkslice_ssid_name_networkslice_vlan_id_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='networkslice',
            name='slice_type',
            field=models.CharField(max_length=6, choices=[
                ('CORP', 'Corporate'),
                ('GUEST', 'Guest'),
                ('IOT', 'IoT'),
                ('GAMING', 'Gaming'),
            ]),
        ),
        migrations.CreateModel(
            name='Device',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('mac_address', models.CharField(max_length=17, unique=True)),
                ('device_type', models.CharField(max_length=50, blank=True, null=True)),
                ('hostname', models.CharField(max_length=100, blank=True, null=True)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('last_seen', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('slice', models.ForeignKey(to='slicer.networkslice', on_delete=models.SET_NULL, null=True, blank=True, related_name='devices')),
            ],
        ),
        migrations.CreateModel(
            name='GuestCredential',
            fields=[
                ('id', models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
                ('code', models.CharField(max_length=32, unique=True)),
                ('expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('used', models.BooleanField(default=False)),
                ('slice', models.ForeignKey(to='slicer.networkslice', on_delete=models.CASCADE, related_name='guest_credentials')),
            ],
        ),
        migrations.RunPython(forwards, backwards),
    ]
