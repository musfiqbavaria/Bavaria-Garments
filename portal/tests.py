from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone
from .models import DashboardPage,FormDefinition,StockItem,Alert,Employee,AttendanceEvent
from .services import apply_stock_scan,record_variance,calculate_attendance_day

class RegistryTests(TestCase):
    def test_registry_models_exist(self):
        self.assertEqual(DashboardPage.objects.count(),0)
        self.assertEqual(FormDefinition.objects.count(),0)

class OperationalRulesTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user(username='tester',password='x')
        self.item=StockItem.objects.create(sku='FAB-1',name='Fabric',category='Raw Material',qty=Decimal('100'),reserved_qty=Decimal('10'))
    def test_stock_out_requires_scan_and_respects_available(self):
        with self.assertRaises(ValueError):
            apply_stock_scan(item=self.item,direction='OUT',quantity=1,barcode='',reference='MO-1',user=self.user)
        with self.assertRaises(ValueError):
            apply_stock_scan(item=self.item,direction='OUT',quantity=91,barcode='B1',reference='MO-1',user=self.user)
        apply_stock_scan(item=self.item,direction='OUT',quantity=20,barcode='B1',reference='MO-1',user=self.user)
        self.item.refresh_from_db(); self.assertEqual(self.item.qty,Decimal('80'))
    def test_bdt_variance_over_ten_creates_red_alert(self):
        record_variance(reference='PO-1',expected=100,actual=111,department='Finance',currency='BDT',user=self.user)
        self.assertTrue(Alert.objects.filter(level='RED',reference='PO-1').exists())
    def test_operator_schedule(self):
        emp=Employee.objects.create(employee_id='OP-1',name='Operator',role='Operator')
        day=timezone.localdate()
        tz=timezone.get_current_timezone()
        from datetime import datetime,time
        AttendanceEvent.objects.create(employee=emp,event='CHECK_IN',occurred_at=timezone.make_aware(datetime.combine(day,time(8,0)),tz))
        AttendanceEvent.objects.create(employee=emp,event='BREAK_IN',occurred_at=timezone.make_aware(datetime.combine(day,time(13,0)),tz))
        AttendanceEvent.objects.create(employee=emp,event='BREAK_OUT',occurred_at=timezone.make_aware(datetime.combine(day,time(14,0)),tz))
        AttendanceEvent.objects.create(employee=emp,event='CHECK_OUT',occurred_at=timezone.make_aware(datetime.combine(day,time(17,0)),tz))
        summary=calculate_attendance_day(emp,day)
        self.assertEqual(summary.scheduled_minutes,480); self.assertEqual(summary.worked_minutes,480)
