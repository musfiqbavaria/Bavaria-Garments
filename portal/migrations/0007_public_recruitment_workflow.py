from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

class Migration(migrations.Migration):
    dependencies=[('portal','0006_sewing_assignment_costs'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.CreateModel(name='HRVacancy',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('created_at',models.DateTimeField(auto_now_add=True,db_index=True)),('updated_at',models.DateTimeField(auto_now=True)),('reference',models.CharField(max_length=80,unique=True)),('title',models.CharField(max_length=180)),('location',models.CharField(default='Limerick, Ireland',max_length=180)),('employment_type',models.CharField(default='Full Time',max_length=80)),('description',models.TextField()),('requirements',models.TextField(blank=True)),('closing_date',models.DateField(blank=True,null=True)),('status',models.CharField(choices=[('DRAFT','Draft'),('PUBLISHED','Published'),('ON_HOLD','On_Hold'),('CLOSED','Closed')],db_index=True,default='DRAFT',max_length=20)),('created_by',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,to=settings.AUTH_USER_MODEL)),('department',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='vacancies',to='portal.department')),
        ]),
        migrations.AddField(model_name='hrrecruitment',name='application_reference',field=models.CharField(blank=True,db_index=True,max_length=80,null=True,unique=True)),
        migrations.AddField(model_name='hrrecruitment',name='vacancy',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='applications',to='portal.hrvacancy')),
        migrations.AddField(model_name='hrrecruitment',name='country',field=models.CharField(blank=True,max_length=80)),
        migrations.AddField(model_name='hrrecruitment',name='address',field=models.TextField(blank=True)),
        migrations.AddField(model_name='hrrecruitment',name='cover_letter',field=models.TextField(blank=True)),
        migrations.AddField(model_name='hrrecruitment',name='consent',field=models.BooleanField(default=False)),
        migrations.AddField(model_name='hrrecruitment',name='submitted_at',field=models.DateTimeField(db_index=True,default=django.utils.timezone.now)),
        migrations.AddField(model_name='hrrecruitment',name='supporting_document',field=models.FileField(blank=True,upload_to='hr/recruitment/supporting/%Y/%m/')),
        migrations.AddField(model_name='hrrecruitment',name='approval',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='recruitment_activation_requests',to='portal.approvalrequest')),
        migrations.AddField(model_name='hrrecruitment',name='hiring_approval',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='recruitment_hiring_requests',to='portal.approvalrequest')),
        migrations.AddField(model_name='hrrecruitment',name='employee',field=models.OneToOneField(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='recruitment_record',to='portal.employee')),
        migrations.AddField(model_name='hrrecruitment',name='portal_user',field=models.OneToOneField(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='recruitment_record',to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='hrrecruitment',name='portal_permission_at',field=models.DateTimeField(blank=True,null=True)),
        migrations.AddField(model_name='hrrecruitment',name='mobile_app_activated_at',field=models.DateTimeField(blank=True,null=True)),
        migrations.AddField(model_name='hrrecruitment',name='reviewed_by',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='recruitment_reviews',to=settings.AUTH_USER_MODEL)),
        migrations.AlterField(model_name='hrrecruitment',name='status',field=models.CharField(choices=[(x,x.replace('_',' ').title()) for x in ['PENDING_APPROVAL','ACTIVE','ON_HOLD','SCREENING','INTERVIEW','SELECTION','DOCUMENT_VERIFICATION','HIRING_APPROVAL','HIRED','REJECTED','CLOSED','OPEN','SHORTLISTED','OFFERED']],db_index=True,default='PENDING_APPROVAL',max_length=30)),
    ]
