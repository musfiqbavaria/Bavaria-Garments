import json, os
from datetime import timedelta
from pathlib import Path
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from portal.models import DashboardPage,FormDefinition,Department,Employee,MasterOrder,Alert,ActionItem,BarcodeAsset,UserProfile

class Command(BaseCommand):
    help='Seed the approved Project 1 registry (70 pages + 650 forms) and starter operational data.'
    def handle(self,*args,**opts):
        base=Path(__file__).resolve().parents[3]
        pages=json.loads((base/'data/page_registry.json').read_text())
        forms=json.loads((base/'data/form_master_650.json').read_text())
        for p in pages: DashboardPage.objects.update_or_create(page_id=p['id'],defaults={'title':p['title'],'slug':p['slug'],'group':p['group'],'enabled':True})
        for f in forms: FormDefinition.objects.update_or_create(form_id=f['id'],defaults={'code':f['code'],'name':f['name'],'department':f['department'],'category':f['category'],'version':f['version'],'status':'ACTIVE','requires_approval':f['requires_approval'],'red_alert_enabled':True})
        depts=['Executive','Administration','HR','Finance','IT','Operations','Merchandising','Sourcing','Planning','Procurement','Stock','Production','Cutting','Print','Embroidery','Sewing','Label','QC','Finishing','Packing','Shipping','Retail','Franchise','Security','Compliance']
        for i,d in enumerate(depts,1): Department.objects.get_or_create(code=f'D{i:02d}',defaults={'name':d})
        username=os.getenv('DEFAULT_ADMIN_USERNAME','admin'); email=os.getenv('DEFAULT_ADMIN_EMAIL','admin@example.com'); password=os.getenv('DEFAULT_ADMIN_PASSWORD','ChangeMeNow-2026')
        admin,created=User.objects.get_or_create(username=username,defaults={'email':email,'is_staff':True,'is_superuser':True})
        if created: admin.set_password(password); admin.save()
        UserProfile.objects.get_or_create(user=admin,defaults={'employee_id':'CEO-001','role':'CEO','department':Department.objects.filter(name='Executive').first()})
        if not Employee.objects.exists():
            for idx,(name,role,dept,cost) in enumerate([('CEO User','CEO','Executive',400),('HR Manager','HR Manager','HR',180),('Cutting Operator','Operator','Cutting',45),('Sewing Operator','Operator','Sewing',45),('Packing Helper','Helper','Packing',35)],1):
                # Set category explicitly. It defaults to 'STAFF', so seeding an
                # operator or helper without it leaves the shift ambiguous.
                category={'Operator':'OPERATOR','Helper':'HELPER'}.get(role,'STAFF')
                Employee.objects.create(employee_id=f'ER-{idx:04d}',name=name,role=role,category=category,department=Department.objects.filter(name=dept).first(),daily_cost=cost)
        if not MasterOrder.objects.exists():
            MasterOrder.objects.create(master_order_id='MO-2026-0001',buyer='Demo Buyer',product='Premium Cap',quantity=5000,order_value=125000,confirmed_at=timezone.now(),delivery_due=timezone.now()+timedelta(days=30),status='PRODUCTION')
        Alert.objects.get_or_create(title='Project 1 seed complete',defaults={'message':'70 pages and 650 forms loaded.','level':'INFO','department':'System'})
        ActionItem.objects.get_or_create(title='Replace production secrets',defaults={'department':'IT','priority':'HIGH','status':'OPEN'})
        BarcodeAsset.objects.get_or_create(code='BUNDLE-DEMO-0001',defaults={'asset_type':'BUNDLE','reference':'MO-2026-0001','payload':{'style':'Premium Cap','qty':50}})
        self.stdout.write(self.style.SUCCESS(f'Seeded {DashboardPage.objects.count()} pages and {FormDefinition.objects.count()} forms.'))
