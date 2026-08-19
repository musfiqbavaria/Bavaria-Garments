from django.core.management.base import BaseCommand
from django.utils import timezone
from portal.models import SourcingAutoReport,Alert,ActionItem
from portal.views import _sourcing_payload
class Command(BaseCommand):
    def add_arguments(self,p):
        p.add_argument("--slot",required=True,choices=["08:00","13:00","20:00"])
    def handle(self,*args,**opts):
        d=timezone.localdate();s=opts["slot"]
        SourcingAutoReport.objects.update_or_create(report_date=d,slot=s,defaults={
            "summary":_sourcing_payload(),
            "outstanding_alerts":Alert.objects.filter(actioned=False).count(),
            "pending_actions":ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),
            "escalated_items":Alert.objects.filter(actioned=False,level="RED").count()})
        self.stdout.write(self.style.SUCCESS(f"Sourcing report generated {d} {s}"))
