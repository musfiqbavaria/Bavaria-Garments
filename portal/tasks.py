import logging

from celery import shared_task
from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

from .models import ReportSnapshot, Alert, ActionItem, MasterOrder

logger = logging.getLogger('portal.tasks')


@shared_task
def scheduled_report_snapshot():
    data = {
        'alerts': Alert.objects.filter(actioned=False).count(),
        'red_alerts': Alert.objects.filter(actioned=False, level='RED').count(),
        # ActionItem.OPEN_STATUSES is the single definition of "not finished".
        # This previously excluded 'DONE', a value nothing ever wrote, so the
        # snapshot counted completed items forever.
        'actions': ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),
        'open_orders': MasterOrder.objects.exclude(
            status__in=['DELIVERED', 'COMPLETED']).count(),
    }
    ReportSnapshot.objects.create(snapshot_type='SCHEDULED',
                                  generated_at=timezone.now(), data=data)
    return data


@shared_task
def refresh_exchange_rates():
    """Pull the day's rates. No-op unless EXCHANGE_RATE_API_URL is configured."""
    if not getattr(settings, 'EXCHANGE_RATE_API_URL', ''):
        logger.info('exchange rate feed not configured; skipping')
        return {'fetched': False, 'reason': 'EXCHANGE_RATE_API_URL not set'}
    try:
        call_command('fetch_exchange_rates', verbosity=0)
        return {'fetched': True}
    except Exception as exc:                      # noqa: BLE001 - reported below
        # fetch_exchange_rates already raises a RED Alert on a transport failure.
        # Log here too so the worker's own logs show it, and let Celery record
        # the task as failed rather than reporting a successful no-op.
        logger.exception('exchange rate refresh failed')
        raise


@shared_task
def purge_expired_audit_logs():
    """Trim AuditLog to the configured retention window.

    AuditMiddleware writes one row per authenticated request with no retention,
    so the table grew without bound on the hot path
    (TECHNICAL_ASSESSMENT.md 6.7). FileAccessLog and ApprovalDecisionLog are
    deliberately NOT purged: they are the file-access and approval audit trails.
    """
    from datetime import timedelta

    from .models import AuditLog

    days = getattr(settings, 'AUDIT_LOG_RETENTION_DAYS', 365)
    if days <= 0:
        return {'deleted': 0, 'reason': 'retention disabled'}
    cutoff = timezone.now() - timedelta(days=days)
    deleted, _ = AuditLog.objects.filter(created_at__lt=cutoff).delete()
    logger.info('purged %s audit log row(s) older than %s days', deleted, days)
    return {'deleted': deleted, 'retention_days': days}


@shared_task
def generate_department_reports(slot):
    """Write every department's scheduled report row for one daily slot.

    The 16 *AutoReport tables the dashboards read were never written on a
    schedule: ten management commands existed but were referenced by no task and
    no beat entry, seven departments had no command at all, and the
    deploy/*.cron.example files invoked a venv path that does not exist on a
    Docker host. Those dashboard panels were therefore permanently empty while a
    healthy beat container made it look scheduled.
    See SITE_AUDIT_FINDINGS.md A8 and portal/reporting.py.
    """
    from .reporting import generate_all

    written, failed = generate_all(slot)
    if failed:
        # Visible in the worker log and to any log shipper, rather than a silent
        # partial run.
        logger.error('department reports for %s: %d written, %d FAILED (%s)',
                     slot, len(written), len(failed), ', '.join(failed))
    else:
        logger.info('department reports for %s: %d written', slot, len(written))
    return {'slot': slot, 'written': written, 'failed': failed}
