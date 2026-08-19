"""Currency conversion for consolidated reporting.

Before this module, any figure spanning more than one entity was a raw sum of
amounts in different currencies. MasterOrder.order_value carried no currency at
all, FinanceTransaction defaulted to EUR while production, supplier and material
models defaulted to BDT, and the CEO dashboard computed
``profit_today = income - expense - production_cost`` across all of them. At
roughly 130 BDT to the euro those headline figures were wrong by orders of
magnitude. See TECHNICAL_ASSESSMENT.md 5.6.

Design rules:

*   **Never guess.** A missing or stale rate raises ``RateUnavailable``. A
    reporting engine that silently substitutes 1.0, or drops the rows it cannot
    convert, produces a plausible number that is wrong - the worst outcome for
    figures someone will price work from.
*   **Effective-dated.** Conversion uses the most recent rate on or before the
    date being reported, so a restated prior period reproduces the same answer.
*   **Auditable.** Every rate is a row with a source and provider.
"""

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.utils import timezone

from .models import ExchangeRate

CENTS = Decimal('0.01')


class RateUnavailable(Exception):
    """No usable exchange rate exists for the requested pair and date."""


def base_currency():
    return getattr(settings, 'BASE_CURRENCY', 'EUR').upper()


def _normalise(code):
    return (code or '').strip().upper()


def get_rate(from_currency, to_currency, on_date=None):
    """Units of ``to_currency`` per one unit of ``from_currency``.

    Looks for a direct rate, then the inverse, then a cross via the base
    currency. Raises RateUnavailable rather than returning an assumed value.
    """
    source = _normalise(from_currency)
    target = _normalise(to_currency)
    if not source or not target:
        raise RateUnavailable('Both currencies must be given.')
    if source == target:
        return Decimal('1')

    on_date = on_date or timezone.localdate()
    max_age = getattr(settings, 'EXCHANGE_RATE_MAX_AGE_DAYS', 7)
    earliest = on_date - timedelta(days=max_age)

    def latest(base, quote):
        return (ExchangeRate.objects
                .filter(base_currency=base, quote_currency=quote,
                        rate_date__lte=on_date, rate_date__gte=earliest)
                .order_by('-rate_date').first())

    direct = latest(source, target)
    if direct and direct.rate > 0:
        return direct.rate

    inverse = latest(target, source)
    if inverse and inverse.rate > 0:
        return Decimal('1') / inverse.rate

    # Cross via the base currency, e.g. BDT -> EUR -> USD.
    base = base_currency()
    if source != base and target != base:
        try:
            return get_rate(source, base, on_date) * get_rate(base, target, on_date)
        except RateUnavailable:
            pass

    raise RateUnavailable(
        f'No exchange rate for {source}->{target} on or before {on_date} '
        f'(within {max_age} days). Add one in admin, or run '
        f'"manage.py fetch_exchange_rates".'
    )


def convert(amount, from_currency, to_currency=None, on_date=None):
    """Convert a Decimal amount. Raises RateUnavailable if no rate applies."""
    if amount is None:
        return Decimal('0.00')
    target = _normalise(to_currency) or base_currency()
    rate = get_rate(from_currency, target, on_date)
    return (Decimal(str(amount)) * rate).quantize(CENTS, rounding=ROUND_HALF_UP)


def convert_or_none(amount, from_currency, to_currency=None, on_date=None):
    """Convert, or return None when no rate is available.

    For callers that must render a dashboard rather than fail: the caller is
    expected to surface the gap, not treat None as zero.
    """
    try:
        return convert(amount, from_currency, to_currency, on_date)
    except RateUnavailable:
        return None


def sum_converted(rows, to_currency=None, on_date=None):
    """Total a sequence of (amount, currency) pairs in one currency.

    Returns ``(total, unconvertible)`` where ``unconvertible`` lists the currency
    codes that had no usable rate. The total covers only what converted, and the
    caller must show the shortfall rather than presenting a partial sum as
    complete.
    """
    target = _normalise(to_currency) or base_currency()
    total = Decimal('0.00')
    missing = set()
    for amount, code in rows:
        if not amount:
            continue
        try:
            total += convert(amount, code, target, on_date)
        except RateUnavailable:
            missing.add(_normalise(code))
    return total.quantize(CENTS, rounding=ROUND_HALF_UP), sorted(missing)
