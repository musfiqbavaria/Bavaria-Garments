"""Generate every department's scheduled report row for one slot.

Replaces the ten separate run_*_auto_report commands, which between them covered
only 9 of the 16 report models and which nothing ever invoked. Celery beat calls
portal.tasks.generate_department_reports on the same registry; this command is
for running a slot by hand - a catch-up after an outage, or a check during
deployment. See portal/reporting.py and SITE_AUDIT_FINDINGS.md A8.
"""
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from portal.reporting import REPORTS, SLOTS, generate_all


class Command(BaseCommand):
    help = "Write every department's auto-report row for a daily slot."

    def add_arguments(self, parser):
        parser.add_argument('--slot', required=True, choices=list(SLOTS))
        parser.add_argument(
            '--date', default=None,
            help='ISO date to generate for. Defaults to today in TIME_ZONE.')

    def handle(self, *args, **options):
        on_date = None
        if options['date']:
            try:
                on_date = date.fromisoformat(options['date'])
            except ValueError as exc:
                raise CommandError(f'--date must be ISO YYYY-MM-DD: {exc}') from exc

        written, failed = generate_all(options['slot'], on_date)
        self.stdout.write(self.style.SUCCESS(
            f"{len(written)} of {len(REPORTS)} department reports written for "
            f"{on_date or 'today'} {options['slot']}"))
        if failed:
            # A non-zero exit so a deploy or cron wrapper notices.
            raise CommandError(
                f"{len(failed)} report(s) failed: {', '.join(failed)}. "
                'See the portal.reporting log for the cause.')
