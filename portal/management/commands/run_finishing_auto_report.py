from django.core.management.base import BaseCommand
from django.utils import timezone
from portal.models import FinishingAutoReport,Alert,ActionItem
from portal.views import _finishing_payload
class Command(BaseCommand):
    def add_arguments(self,p):p.add_argument("--slot",required=True,choices=["08:00","13:00","20:00"])
    def handle(self,*a,**o):
        d=timezone.localdate();s=o["slot"];payload=_finishing_payload(d)
        FinishingAutoReport.objects.update_or_create(report_date=d,slot=s,defaults={"summary":payload,"outstanding_alerts":Alert.objects.filter(actioned=False).count(),"pending_actions":ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),"escalated_items":Alert.objects.filter(actioned=False,level="RED").count()})
        self.stdout.write(self.style.SUCCESS(f"Finishing report generated {d} {s}"))
