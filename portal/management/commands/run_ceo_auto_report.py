from django.core.management.base import BaseCommand
from django.utils import timezone
from portal.models import CEOAutoReport
from portal.views import _ceo_summary
class Command(BaseCommand):
    def add_arguments(self,p):
        p.add_argument("--slot",required=True,choices=["08:00","13:00","20:00"])
    def handle(self,*args,**opts):
        d=timezone.localdate();s=opts["slot"];payload=_ceo_summary(d)
        CEOAutoReport.objects.update_or_create(report_date=d,slot=s,defaults={
            "summary":payload,"outstanding_alerts":payload["alerts"],
            "pending_actions":payload["pending_actions"],"escalated_items":payload["red_alerts"]})
        self.stdout.write(self.style.SUCCESS(f"CEO report generated {d} {s}"))
