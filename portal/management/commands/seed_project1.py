import json, os
from datetime import timedelta
from pathlib import Path
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import time
from decimal import Decimal
from portal.models import (DashboardPage,FormDefinition,Department,Employee,MasterOrder,Alert,ActionItem,BarcodeAsset,UserProfile,
                           OrganizationNode,ExchangeRate,AttendanceShift)

class Command(BaseCommand):
    help='Seed the Project 1 registry (95 pages + 650 forms), the organisation tree and starter operational data.'
    def handle(self,*args,**opts):
        base=Path(__file__).resolve().parents[3]
        pages=json.loads((base/'data/page_registry.json').read_text())
        forms=json.loads((base/'data/form_master_650.json').read_text())
        # A registry entry carrying superseded_by is a legacy stub that a later
        # module replaced, so it is registered but disabled: the dashboard stopped
        # offering two of everything, one working and one empty. Re-enable in
        # admin if a page is still wanted. See TECHNICAL_ASSESSMENT.md 3.1.
        for p in pages:
            DashboardPage.objects.update_or_create(page_id=p['id'],defaults={
                'title':p['title'],'slug':p['slug'],'group':p['group'],
                'enabled':not p.get('superseded_by'),
                'superseded_by':p.get('superseded_by','') or ''})
        for f in forms: FormDefinition.objects.update_or_create(form_id=f['id'],defaults={'code':f['code'],'name':f['name'],'department':f['department'],'category':f['category'],'version':f['version'],'status':'ACTIVE','requires_approval':f['requires_approval'],'red_alert_enabled':True})
        depts=['Executive','Administration','HR','Finance','IT','Operations','Merchandising','Sourcing','Planning','Procurement','Stock','Production','Cutting','Print','Embroidery','Sewing','Label','QC','Finishing','Packing','Shipping','Retail','Franchise','Security','Compliance']
        for i,d in enumerate(depts,1): Department.objects.get_or_create(code=f'D{i:02d}',defaults={'name':d})
        # --- organisation tree ---------------------------------------------
        # Scoping filters on OrganizationNode.path, so the sites must exist
        # before any record can be attributed to one. See portal/tenancy.py.
        ireland,_=OrganizationNode.objects.get_or_create(
            name='Ireland',node_type='Country',parent=None,
            defaults={'timezone':'Europe/Dublin'})
        bangladesh,_=OrganizationNode.objects.get_or_create(
            name='Bangladesh',node_type='Country',parent=None,
            defaults={'timezone':'Asia/Dhaka'})
        OrganizationNode.objects.get_or_create(
            name='Rozalia Limited',node_type='Company',parent=ireland)
        bd_company,_=OrganizationNode.objects.get_or_create(
            name='Rozalia Bangladesh',node_type='Company',parent=bangladesh)
        factory,_=OrganizationNode.objects.get_or_create(
            name='Main Factory',node_type='Factory',parent=bd_company)
        OrganizationNode.objects.get_or_create(
            name='Production Unit 1',node_type='Production Unit',parent=factory)
        OrganizationNode.objects.get_or_create(
            name='Main Warehouse',node_type='Warehouse',parent=factory)

        # --- working shifts ------------------------------------------------
        # The attendance engine reads these from the database; without a row it
        # falls back to hardcoded defaults, which is what blocked per-site shifts.
        for code,category,check_in,b1i,b1o,b2i,b2o,check_out,mandatory in [
            ('SHIFT-OP','OPERATOR',time(8,0),time(13,0),time(14,0),None,None,time(17,0),480),
            ('SHIFT-HP','HELPER',time(8,0),time(13,0),time(14,0),None,None,time(17,0),480),
            ('SHIFT-ST','STAFF',time(8,0),time(13,0),time(14,0),time(17,0),time(17,15),time(20,0),660),
        ]:
            AttendanceShift.objects.get_or_create(code=code,defaults={
                'name':f'{category.title()} standard shift','employee_category':category,
                'check_in':check_in,'break1_in':b1i,'break1_out':b1o,
                'break2_in':b2i,'break2_out':b2o,'check_out':check_out,
                'mandatory_minutes':mandatory,'grace_minutes':10,'ot_break_minutes':30,
                'active':True})

        # --- starter exchange rates ----------------------------------------
        # Marked SEED, not MANUAL or AUTO, so Finance can see they are indicative
        # and must be replaced by the daily feed or a reviewed manual entry.
        # Without any rate, consolidated reporting correctly refuses to convert.
        if not ExchangeRate.objects.exists():
            base=os.getenv('BASE_CURRENCY','EUR')
            for quote,rate in [('BDT','131.0000000000'),('USD','1.0900000000'),
                               ('GBP','0.8400000000')]:
                ExchangeRate.objects.get_or_create(
                    base_currency=base,quote_currency=quote,
                    rate_date=timezone.localdate(),
                    defaults={'rate':Decimal(rate),'source':'SEED',
                              'provider':'seed_project1'})

        username=os.getenv('DEFAULT_ADMIN_USERNAME','admin'); email=os.getenv('DEFAULT_ADMIN_EMAIL','admin@example.com'); password=os.getenv('DEFAULT_ADMIN_PASSWORD','ChangeMeNow-2026')
        admin,created=User.objects.get_or_create(username=username,defaults={'email':email,'is_staff':True,'is_superuser':True})
        if created: admin.set_password(password); admin.save()
        # No scope: head office sees every site. Assigning a scope is what
        # narrows a user's view - see portal/tenancy.py.
        UserProfile.objects.get_or_create(user=admin,defaults={'employee_id':'CEO-001','role':'CEO','department':Department.objects.filter(name='Executive').first(),'scope':None})
        if not Employee.objects.exists():
            for idx,(name,role,dept,cost) in enumerate([('CEO User','CEO','Executive',400),('HR Manager','HR Manager','HR',180),('Cutting Operator','Operator','Cutting',45),('Sewing Operator','Operator','Sewing',45),('Packing Helper','Helper','Packing',35)],1):
                # Set category explicitly. It defaults to 'STAFF', so seeding an
                # operator or helper without it leaves the shift ambiguous.
                category={'Operator':'OPERATOR','Helper':'HELPER'}.get(role,'STAFF')
                Employee.objects.create(employee_id=f'ER-{idx:04d}',name=name,role=role,category=category,department=Department.objects.filter(name=dept).first(),daily_cost=cost,scope=factory)
        if not MasterOrder.objects.exists():
            MasterOrder.objects.create(master_order_id='MO-2026-0001',buyer='Demo Buyer',product='Premium Cap',quantity=5000,order_value=125000,currency='USD',scope=factory,confirmed_at=timezone.now(),delivery_due=timezone.now()+timedelta(days=30),status='PRODUCTION')
        Alert.objects.get_or_create(title='Project 1 seed complete',defaults={'message':'Project 1 registry, organisation tree and starter data loaded.','level':'INFO','department':'System','scope':None})
        ActionItem.objects.get_or_create(title='Replace production secrets',defaults={'department':'IT','priority':'HIGH','status':'OPEN'})
        BarcodeAsset.objects.get_or_create(code='BUNDLE-DEMO-0001',defaults={'asset_type':'BUNDLE','reference':'MO-2026-0001','payload':{'style':'Premium Cap','qty':50}})
        # Roles must exist as Django groups before the authorisation layer can
        # resolve them. Idempotent, so safe to call on every seed.
        call_command('sync_roles', verbosity=0)
        self.stdout.write(self.style.SUCCESS(f'Seeded {DashboardPage.objects.count()} pages and {FormDefinition.objects.count()} forms.'))
