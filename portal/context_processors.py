"""Global control-strip counters, rendered on every authenticated page.

This ran five count queries plus a full load of every present employee into
Python on every single request, and filtered attendance with
``occurred_at__date=today`` - a function on the column, which no index can serve.
See TECHNICAL_ASSESSMENT.md 6.7.

Now: one aggregate per concern, a range predicate that uses the index on
``occurred_at``, summing in the database, and a short cache because header
counters do not need to be accurate to the second. The cache is global rather
than per-user because every figure here is organisation-wide.
"""
import logging
from datetime import datetime, time, timedelta

from django.core.cache import cache
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import ActionItem, Alert, AttendanceEvent, Employee

logger = logging.getLogger('portal.context')

CACHE_KEY = 'portal:global-control-strip'
CACHE_SECONDS = 30


def _compute():
    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(today, time.min), tz)
    end = start + timedelta(days=1)

    alerts = Alert.objects.filter(actioned=False).aggregate(
        total=Count('id'),
        red=Count('id', filter=Q(level='RED')),
    )

    present_ids = (AttendanceEvent.objects
                   .filter(occurred_at__gte=start, occurred_at__lt=end,
                           event__iexact='CHECK_IN')
                   .values_list('employee_id', flat=True).distinct())

    # Sum in the database rather than loading every present employee. At five
    # thousand workers the old version pulled five thousand rows per request.
    present = Employee.objects.filter(id__in=present_ids).aggregate(
        headcount=Count('id'), cost=Sum('daily_cost'))

    return {
        'global_alerts': alerts['total'] or 0,
        'global_red_alerts': alerts['red'] or 0,
        'global_actions': ActionItem.objects.filter(
            status__in=ActionItem.OPEN_STATUSES).count(),
        'today_present_staff': present['headcount'] or 0,
        'today_staff_cost': present['cost'] or 0,
        # Cosmetic placeholder: nothing in the data model records a login
        # sequence number, so there is no real figure to show here yet.
        'login_no': 1,
    }


def global_portal(request):
    if not request.user.is_authenticated:
        return {}
    try:
        cached = cache.get(CACHE_KEY)
        if cached is not None:
            return cached
    except Exception:
        # Never let a cache outage take out every page in the platform.
        logger.warning('control-strip cache unavailable on read')
        return _compute()

    payload = _compute()
    try:
        cache.set(CACHE_KEY, payload, CACHE_SECONDS)
    except Exception:
        logger.warning('control-strip cache unavailable on write')
    return payload
