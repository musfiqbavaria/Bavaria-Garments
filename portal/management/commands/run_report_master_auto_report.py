from django.core.management.base import BaseCommand
from django.utils import timezone
from portal.models import ReportSnapshot
from portal.views import _report_master_summary,_report_master_auto_reports

class Command(BaseCommand):
    help="Generate the central Report Master snapshot for 08:00, 13:00 or 20:00."

    def add_arguments(self,p):
        p.add_argument("--slot",required=True,choices=["08:00","13:00","20:00"])

    def handle(self,*args,**opts):
        slot=opts["slot"]
        today=timezone.localdate()
        payload=_report_master_summary(today)
        payload["slot"]=slot
        payload["department_reports"]=_report_master_auto_reports(today)
        snap=ReportSnapshot.objects.create(
            snapshot_type=f"REPORT_MASTER_{slot}",
            generated_at=timezone.now(),
            data=payload
        )
        self.stdout.write(self.style.SUCCESS(f"Report Master snapshot generated: {today} {slot} id={snap.id}"))
