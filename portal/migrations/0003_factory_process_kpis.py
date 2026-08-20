from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

class Migration(migrations.Migration):
    dependencies=[('portal','0002_storefront_localization')]
    operations=[migrations.CreateModel(name='FactoryProcessStandard',fields=[
        ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('created_at',models.DateTimeField(auto_now_add=True,db_index=True)),('updated_at',models.DateTimeField(auto_now=True)),('process_name',models.CharField(max_length=180)),('style_reference',models.CharField(blank=True,max_length=120)),('sam',models.DecimalField(decimal_places=4,default=0,max_digits=10)),('smv',models.DecimalField(decimal_places=4,default=0,max_digits=10)),('cost_per_minute',models.DecimalField(decimal_places=4,default=0,max_digits=14)),('effective_from',models.DateField(default=django.utils.timezone.localdate)),('effective_to',models.DateField(blank=True,null=True)),('active',models.BooleanField(db_index=True,default=True)),('department',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='factory_process_standards',to='portal.department')),('factory',models.ForeignKey(limit_choices_to={'node_type':'Factory'},on_delete=django.db.models.deletion.PROTECT,related_name='process_standards',to='portal.organizationnode')),('production_unit',models.ForeignKey(blank=True,limit_choices_to={'node_type':'Production Unit'},null=True,on_delete=django.db.models.deletion.PROTECT,related_name='unit_process_standards',to='portal.organizationnode')),
    ],options={'constraints':[models.UniqueConstraint(fields=('factory','production_unit','department','process_name','style_reference','effective_from'),name='uq_factory_process_standard')]})]
