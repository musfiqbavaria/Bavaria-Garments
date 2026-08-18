from django.core.management.base import BaseCommand
from django.utils import timezone
from portal.models import ShippingAutoReport,Alert,ActionItem
from portal.views import _shipping_payload
class Command(BaseCommand):
    def add_arguments(self,p):
        p.add_argument("--slot",required=True,choices=["08:00","13:00","20:00"])
    def handle(self,*args,**opts):
        d=timezone.localdate(); s=opts["slot"]; payload=_shipping_payload(d)
        ShippingAutoReport.objects.update_or_create(report_date=d,slot=s,defaults={
            "summary":payload,
            "outstanding_alerts":Alert.objects.filter(actioned=False).count(),
            "pending_actions":ActionItem.objects.exclude(status="COMPLETED").count(),
            "escalated_items":Alert.objects.filter(actioned=False,level="RED").count()})
        self.stdout.write(self.style.SUCCESS(f"Shipping report generated {d} {s}"))
