"""Global control-strip counters, rendered on every authenticated page.

This ran five count queries plus a full load of every present employee into
Python on every single request, and filtered attendance with
``occurred_at__date=today`` - a function on the column, which no index can serve.
See TECHNICAL_ASSESSMENT.md 6.7.

Now: one aggregate per concern, a range predicate that uses the index on
``occurred_at``, summing in the database, and a short cache because header
counters do not need to be accurate to the second.

The cache key is per organisation scope. It used to be one global key, on the
stated grounds that "every figure here is organisation-wide". That was true
before Phase 4, but ``Alert``, ``ActionItem`` and ``Employee`` now carry a
``ScopedManager``, so these figures are computed for whoever's request populates
the cache and were then served to every other user for the next 30 seconds -
one site's alert counts, headcount and staff cost in everybody's header. See
SITE_AUDIT_FINDINGS.md A9.

The staff-cost figure is also a bare ``Sum`` over ``Employee.daily_cost``, whose
rows may be denominated in different currencies. It is converted into
``BASE_CURRENCY`` here and labelled, and when a rate is missing the strip says
the figure is partial rather than showing a total that silently omits rows.
See SITE_AUDIT_FINDINGS.md A11.
"""
import logging
from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Q, Sum
from django.utils import timezone

from . import currency as currency_service
from .models import ActionItem, Alert, AttendanceEvent, Employee
from .tenancy import current_scope

logger = logging.getLogger('portal.context')

CACHE_KEY_PREFIX = 'portal:global-control-strip'
CACHE_SECONDS = 30


def _cache_key():
    """One cache entry per organisation scope.

    Two users in the same scope share a cached payload, which is the point of
    the cache; two users in different scopes never do, which is the point of
    this function. ``None`` (unscoped, e.g. a superuser) gets its own key rather
    than sharing with any real site.
    """
    scope = current_scope()
    if scope is None:
        return f'{CACHE_KEY_PREFIX}:unscoped'
    # A scope is a node id or a collection of them depending on the user's
    # assignments; normalise to a stable, order-independent string.
    if isinstance(scope, (list, tuple, set, frozenset)):
        token = ','.join(str(x) for x in sorted(scope))
    else:
        token = str(scope)
    return f'{CACHE_KEY_PREFIX}:{token}'


def _staff_cost(present_employee_ids):
    """Total daily staff cost in BASE_CURRENCY, and whether it is complete.

    Returns ``(total, unconvertible_count, currency)``. Employees whose currency
    has no usable rate are counted rather than silently added at 1.0 - the same
    rule ``portal.currency`` applies everywhere else.
    """
    base = getattr(settings, 'BASE_CURRENCY', 'EUR')
    # Employee carries no currency of its own; the cost is denominated in the
    # operating currency of the site the employee belongs to. Grouping by site
    # keeps this to one query however many employees are present.
    rows = (Employee.objects.filter(id__in=present_employee_ids)
            .values('scope__currency').annotate(subtotal=Sum('daily_cost')))
    total, unconvertible = currency_service.sum_converted(
        [(r['subtotal'] or 0, r['scope__currency'] or base) for r in rows], base)
    return total, len(unconvertible), base


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
        headcount=Count('id'))

    try:
        cost, unconvertible, base = _staff_cost(present_ids)
    except Exception:
        # A missing rate table must not take out every page in the platform.
        logger.warning('staff cost could not be converted', exc_info=True)
        cost, unconvertible, base = None, 0, getattr(settings, 'BASE_CURRENCY', 'EUR')

    if cost is None:
        cost_display = '—'
    else:
        cost_display = f'{base} {cost:,.2f}'
        if unconvertible:
            # Say the total is partial rather than presenting it as complete.
            cost_display += f' (+{unconvertible} unconverted)'

    return {
        'global_alerts': alerts['total'] or 0,
        'global_red_alerts': alerts['red'] or 0,
        'global_actions': ActionItem.objects.filter(
            status__in=ActionItem.OPEN_STATUSES).count(),
        'today_present_staff': present['headcount'] or 0,
        'today_staff_cost': cost,
        #: Pre-formatted with its currency. Templates must use this, not the raw
        #: figure, so no page shows an unlabelled cross-currency total again.
        'today_staff_cost_display': cost_display,
        'today_staff_cost_currency': base,
        'today_staff_cost_partial': bool(unconvertible),
        'base_currency': base,
        # The date these figures describe. A page left open overnight otherwise
        # keeps presenting yesterday's numbers as "today".
        'control_strip_date': today,
    }


def global_portal(request):
    if not request.user.is_authenticated:
        return {}
    key = _cache_key()
    try:
        cached = cache.get(key)
        if cached is not None:
            return cached
    except Exception:
        # Never let a cache outage take out every page in the platform.
        logger.warning('control-strip cache unavailable on read')
        return _compute()

    payload = _compute()
    try:
        cache.set(key, payload, CACHE_SECONDS)
    except Exception:
        logger.warning('control-strip cache unavailable on write')
    return payload
