"""Report records that have no organisation site assigned.

Until every row carries a scope, TENANCY_STRICT must stay off, because unscoped
records are visible to every site. This command shows exactly what is left to
assign, so the decision to switch strict mode on is evidence-based.

    python manage.py report_unscoped
    python manage.py report_unscoped --fail-if-any     for CI

Run it before enabling TENANCY_STRICT=1.
"""
from django.core.management.base import BaseCommand

from portal.tenancy import scoped_models, unscoped


class Command(BaseCommand):
    help = 'List models and row counts that have no organisation scope assigned.'

    def add_arguments(self, parser):
        parser.add_argument('--fail-if-any', action='store_true',
                            help='Exit non-zero if any unscoped rows remain.')

    def handle(self, *args, **options):
        # A field named "scope" is not the marker: UserProfile.scope records the
        # site a user is assigned to, not a scoped record.
        models = scoped_models()
        rows = []
        # unscoped() so the report itself is never narrowed by an active scope.
        with unscoped():
            for model in models:
                total = model.all_objects.count()
                missing = model.all_objects.filter(scope__isnull=True).count()
                if missing:
                    rows.append((model.__name__, missing, total))

        if not rows:
            self.stdout.write(self.style.SUCCESS(
                f'All rows across {len(models)} scoped model(s) have a site '
                f'assigned. TENANCY_STRICT can safely be enabled.'))
            return

        width = max(len(name) for name, _m, _t in rows)
        self.stdout.write(f'{"model".ljust(width)}  unscoped / total')
        self.stdout.write('-' * (width + 20))
        for name, missing, total in rows:
            self.stdout.write(f'{name.ljust(width)}  {missing:>8} / {total}')
        total_missing = sum(m for _n, m, _t in rows)
        self.stdout.write(self.style.WARNING(
            f'\n{total_missing} row(s) across {len(rows)} model(s) have no site. '
            f'They are visible to every scope while TENANCY_STRICT is off.'))
        if options['fail_if_any']:
            raise SystemExit(1)
