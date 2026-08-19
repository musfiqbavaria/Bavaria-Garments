from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from portal.models import FinalQCAutoReport, Alert, ActionItem
from portal.views import _final_qc_auto_report_payload

class Command(BaseCommand):
    help="Generate the Final QC automatic report for 08:00, 13:00 or 20:00 Bangladesh-local slot."

    def add_arguments(self, parser):
        parser.add_argument("--slot", required=True, choices=["08:00","13:00","20:00"])

    def handle(self, *args, **options):
        slot=options["slot"]
        today=timezone.localdate()
        payload=_final_qc_auto_report_payload(today)
        report,_=FinalQCAutoReport.objects.update_or_create(
            report_date=today,slot=slot,
            defaults={
                "summary":payload,
                "outstanding_alerts":Alert.objects.filter(actioned=False).count(),
                "pending_actions":ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),
                "escalated_items":Alert.objects.filter(actioned=False,level="RED").count(),
            }
        )
        self.stdout.write(self.style.SUCCESS(f"Final QC auto report generated: {today} {slot} (id={report.id})"))
