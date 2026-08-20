from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies=[('portal','0001_initial')]
    operations=[
        migrations.AlterField(model_name='organizationnode',name='node_type',field=models.CharField(choices=[(x,x) for x in ['Global','Country','Company','Factory','Production Unit','Area','Branch','Department','Warehouse','E-commerce Store','Vendor','Franchise','Franchise Retail Store','Retail Store','Online Store','POS']],db_index=True,max_length=40)),
        migrations.CreateModel(name='StorefrontConfiguration',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('created_at',models.DateTimeField(auto_now_add=True,db_index=True)),('updated_at',models.DateTimeField(auto_now=True)),
            ('channel_type',models.CharField(choices=[(x,x) for x in ['E-commerce Store','Online Store','Franchise Retail Store','Retail Store','POS']],db_index=True,max_length=40)),('code',models.SlugField(max_length=80,unique=True)),('domain',models.CharField(blank=True,max_length=255)),('country_code',models.CharField(db_index=True,default='IE',max_length=2)),('default_language',models.CharField(default='en',max_length=12)),('supported_languages',models.JSONField(default=list)),('default_currency',models.CharField(default='EUR',max_length=10)),('supported_currencies',models.JSONField(default=list)),('active',models.BooleanField(db_index=True,default=True)),('scope',models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,related_name='storefront_configuration',to='portal.organizationnode')),
        ]),
        migrations.CreateModel(name='LocalizedContent',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('created_at',models.DateTimeField(auto_now_add=True,db_index=True)),('updated_at',models.DateTimeField(auto_now=True)),('resource_type',models.CharField(db_index=True,max_length=80)),('resource_key',models.CharField(db_index=True,max_length=120)),('field_name',models.CharField(max_length=80)),('language_code',models.CharField(db_index=True,max_length=12)),('value',models.TextField()),('storefront',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='translations',to='portal.storefrontconfiguration')),
            ],options={'constraints':[models.UniqueConstraint(fields=('storefront','resource_type','resource_key','field_name','language_code'),name='uq_storefront_localized_field')]}),
    ]
