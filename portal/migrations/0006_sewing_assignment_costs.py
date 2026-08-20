from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies=[('portal','0005_barcode_scan_event_and_types')]
    operations=[
        migrations.AddField(model_name='sewingbundleassignment',name='machine_cost_per_minute',field=models.DecimalField(decimal_places=4,default=0,max_digits=12)),
        migrations.AddField(model_name='sewingbundleassignment',name='labour_cost_per_minute',field=models.DecimalField(decimal_places=4,default=0,max_digits=12)),
        migrations.AddField(model_name='sewingbundleassignment',name='machine_cost',field=models.DecimalField(decimal_places=2,default=0,max_digits=14)),
        migrations.AddField(model_name='sewingbundleassignment',name='labour_cost',field=models.DecimalField(decimal_places=2,default=0,max_digits=14)),
        migrations.AddField(model_name='sewingbundleassignment',name='total_process_cost',field=models.DecimalField(decimal_places=2,default=0,max_digits=14)),
    ]
