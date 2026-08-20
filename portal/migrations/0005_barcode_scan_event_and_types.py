from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('portal', '0004_sewing_bundle_traceability'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='barcodeasset', name='asset_type',
            field=models.CharField(choices=[(x, x) for x in ['ORDER','STYLE','BUNDLE','PART','OPERATION','MACHINE','OPERATOR','HELPER','MATERIAL','STOCK','PRODUCT','CARTON','EMPLOYEE','ASSET','DOCUMENT']], max_length=30),
        ),
        migrations.CreateModel(
            name='BarcodeScanEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('scan_type', models.CharField(choices=[(x, x) for x in ['ORDER','STYLE','BUNDLE','PART','OPERATION','MACHINE','OPERATOR','HELPER','MATERIAL','STOCK','PRODUCT','CARTON','EMPLOYEE','ASSET','DOCUMENT']], db_index=True, max_length=30)),
                ('scanned_code', models.CharField(db_index=True, max_length=180)),
                ('expected_code', models.CharField(blank=True, db_index=True, max_length=180)),
                ('result', models.CharField(choices=[('ACCEPTED','Accepted'),('BLOCKED','Blocked'),('WARNING','Warning')], db_index=True, max_length=20)),
                ('reason', models.CharField(blank=True, max_length=255)),
                ('workstation', models.CharField(blank=True, max_length=80)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('assignment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='barcode_scan_events', to='portal.sewingbundleassignment')),
                ('bundle', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='barcode_scan_events', to='portal.cuttingbundle')),
                ('performed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='barcode_scan_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering':['-created_at']},
        ),
    ]
