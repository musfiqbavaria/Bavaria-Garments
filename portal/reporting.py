"""Scheduled department reports: one registry, one generator.

The platform has 16 ``*AutoReport`` models and every department dashboard reads
its own table three to seven times. None of them was ever written on a schedule:

  * ``CELERY_BEAT_SCHEDULE`` scheduled three tasks -
    ``scheduled_report_snapshot`` (which counts alerts, red alerts, open actions
    and open orders, and nothing else), the exchange-rate refresh, and the audit
    purge.
  * Ten ``manage.py run_*_auto_report`` commands existed and were referenced by
    no task and no beat entry.
  * Ten ``deploy/*.cron.example`` files invoked them via
    ``/path/to/project && /path/to/venv/bin/python``, a layout that does not
    exist on a Docker host, and nothing installed a crontab anyway.
  * Seven of the 16 models - cutting, embroidery, hand iron, iron, label, poly
    and QC - had no command at all, although the payload function each one needs
    was already written in ``portal/views.py``.

So those dashboard panels were permanently empty in the deployed system, while a
healthy ``beat`` container made it look scheduled. See SITE_AUDIT_FINDINGS.md A8.

One registry replaces the ten commands and covers all sixteen departments. A
department is scheduled by adding a line here, and ``AutoReportRegistryTests``
fails if a model or a payload function named here does not exist, or if an
``*AutoReport`` model is left out.
"""

import logging

from django.utils import timezone

logger = logging.getLogger('portal.reporting')

#: The three daily slots. These are read in TIME_ZONE (Asia/Dhaka by default),
#: which Celery beat inherits from CELERY_TIMEZONE.
SLOTS = ('08:00', '13:00', '20:00')

#: (model name, payload function name) for every department report.
#: Both are resolved lazily so importing this module does not pull in views.
REPORTS = (
    ('CuttingAutoReport', '_cutting_auto_report_payload'),
    ('EmbroideryAutoReport', '_embroidery_auto_report_payload'),
    ('LabelAutoReport', '_label_auto_report_payload'),
    ('QCAutoReport', '_qc_auto_report_payload'),
    ('HandIronAutoReport', '_hand_iron_auto_report_payload'),
    ('IronAutoReport', '_iron_auto_report_payload'),
    ('PolyAutoReport', '_poly_auto_report_payload'),
    ('FinishingAutoReport', '_finishing_payload'),
    ('FinalQCAutoReport', '_final_qc_auto_report_payload'),
    ('PackingAutoReport', '_packing_payload'),
    ('ShippingAutoReport', '_shipping_payload'),
    ('SupplierAutoReport', '_supplier_payload'),
    ('SourcingAutoReport', '_sourcing_payload'),
    ('ProcurementAutoReport', '_procurement_payload'),
    ('PurchaseAutoReport', '_purchase_payload'),
    ('CEOAutoReport', '_ceo_summary'),
)


def _counters():
    """Alert and action counts shared by every department's report row."""
    from .models import ActionItem, Alert
    open_alerts = Alert.objects.filter(actioned=False)
    return {
        'outstanding_alerts': open_alerts.count(),
        'pending_actions': ActionItem.objects.filter(
            status__in=ActionItem.OPEN_STATUSES).count(),
        'escalated_items': open_alerts.filter(level='RED').count(),
    }


def generate_one(model_name, payload_name, slot, on_date=None):
    """Write one department's report row for ``slot``.

    Returns True if the row was written. A failure is logged and returned as
    False rather than raised, so one broken department does not stop the other
    fifteen - the previous arrangement had no error handling at all because it
    never ran.
    """
    from django.apps import apps

    from . import views

    on_date = on_date or timezone.localdate()
    try:
        model = apps.get_model('portal', model_name)
        payload_fn = getattr(views, payload_name)
    except (LookupError, AttributeError):
        logger.exception('auto report %s/%s is not wired up correctly',
                         model_name, payload_name)
        return False

    try:
        # Not all the payload builders take a date: four of the supply-chain ones
        # read "today" themselves. Adapt rather than requiring 16 signatures to
        # match.
        import inspect
        takes_date = bool(inspect.signature(payload_fn).parameters)
        defaults = {'summary': payload_fn(on_date) if takes_date else payload_fn()}
        # Not every AutoReport model carries the shared counters.
        fields = {f.name for f in model._meta.concrete_fields}
        for key, value in _counters().items():
            if key in fields:
                defaults[key] = value
        model.objects.update_or_create(
            report_date=on_date, slot=slot, defaults=defaults)
    except Exception:
        logger.exception('auto report %s failed for %s %s',
                         model_name, on_date, slot)
        return False
    return True


def generate_all(slot, on_date=None):
    """Write every department's report row for ``slot``.

    Returns ``(written, failed)`` model-name lists so the caller - a Celery task
    or the management command - can report what happened instead of finishing
    silently.
    """
    written, failed = [], []
    for model_name, payload_name in REPORTS:
        if generate_one(model_name, payload_name, slot, on_date):
            written.append(model_name)
        else:
            failed.append(model_name)
    return written, failed
