"""Fetch daily exchange rates from a configured provider.

    python manage.py fetch_exchange_rates
    python manage.py fetch_exchange_rates --dry-run
    python manage.py fetch_exchange_rates --url https://api.frankfurter.app/latest

Runs daily at 06:30 via Celery beat, before the 08:00 reporting slot.

Deliberately provider-agnostic and disabled by default: no request is made
unless EXCHANGE_RATE_API_URL is set. The endpoint is an external service, so it
must be authorised before use. Only public reference rates are retrieved - no
company data is transmitted.

Accepts the common response shape used by most providers:

    {"base": "EUR", "date": "2026-08-18", "rates": {"BDT": 131.2, "USD": 1.09}}

"amount"/"result"-style single-pair responses are not supported; point the URL at
a multi-rate endpoint.
"""
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from portal.models import Alert, ExchangeRate


class Command(BaseCommand):
    help = 'Fetch and store daily exchange rates from the configured provider.'

    def add_arguments(self, parser):
        parser.add_argument('--url', default=None,
                            help='Override EXCHANGE_RATE_API_URL for this run.')
        parser.add_argument('--base', default=None,
                            help='Base currency to request (default settings.BASE_CURRENCY).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Fetch and report, but write nothing.')

    def handle(self, *args, **options):
        url = (options['url'] or getattr(settings, 'EXCHANGE_RATE_API_URL', '')).strip()
        base = (options['base'] or getattr(settings, 'BASE_CURRENCY', 'EUR')).upper()

        if not url:
            # Not an error: the feed is opt-in. Say so clearly and stop.
            self.stdout.write(self.style.WARNING(
                'EXCHANGE_RATE_API_URL is not set, so no rates were fetched. '
                'Set it in .env to enable the daily feed, or maintain rates '
                'manually in admin (Portal > Exchange rates).'))
            return

        scheme = urlparse(url).scheme
        if scheme != 'https':
            raise CommandError(
                f'EXCHANGE_RATE_API_URL must use https, got {scheme or "no scheme"!r}. '
                'Rates are used for financial reporting and must not be fetched '
                'over an unauthenticated channel.')

        payload = self._fetch(url, base)
        rates = payload.get('rates')
        if not isinstance(rates, dict) or not rates:
            raise CommandError(f'Provider response contained no "rates" object: '
                               f'{json.dumps(payload)[:300]}')

        reported_base = (payload.get('base') or base).upper()
        rate_date = self._parse_date(payload.get('date'))

        written = 0
        skipped = []
        for quote, value in sorted(rates.items()):
            quote = str(quote).strip().upper()
            if quote == reported_base:
                continue
            try:
                decimal_value = Decimal(str(value))
            except (InvalidOperation, TypeError):
                skipped.append(f'{quote}={value!r}')
                continue
            if decimal_value <= 0:
                skipped.append(f'{quote}={value!r} (not positive)')
                continue
            if options['dry_run']:
                written += 1
                continue
            ExchangeRate.objects.update_or_create(
                base_currency=reported_base, quote_currency=quote, rate_date=rate_date,
                defaults={'rate': decimal_value, 'source': 'AUTO',
                          'provider': urlparse(url).netloc},
            )
            written += 1

        verb = 'would store' if options['dry_run'] else 'stored'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {written} rate(s) for base {reported_base} dated {rate_date} '
            f'from {urlparse(url).netloc}'))
        if skipped:
            self.stderr.write(self.style.WARNING(
                f'{len(skipped)} rate(s) skipped as unusable: {", ".join(skipped[:10])}'))

    # -- helpers ------------------------------------------------------------
    def _fetch(self, url, base):
        query = {'from': base, 'base': base}
        api_key = getattr(settings, 'EXCHANGE_RATE_API_KEY', '')
        if api_key:
            query['access_key'] = api_key
        separator = '&' if '?' in url else '?'
        request = Request(f'{url}{separator}{urlencode(query)}',
                         headers={'Accept': 'application/json',
                                  'User-Agent': 'EmeraldRozaliaProject1/1.0'})
        timeout = getattr(settings, 'EXCHANGE_RATE_TIMEOUT_SECONDS', 10)
        try:
            with urlopen(request, timeout=timeout) as response:   # noqa: S310 - https enforced above
                body = response.read().decode('utf-8', 'replace')
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            # A failed fetch must be visible, not silent: consolidated figures
            # would otherwise keep using an ageing rate with nobody informed.
            # portal.currency refuses to convert once a rate exceeds
            # EXCHANGE_RATE_MAX_AGE_DAYS, so reporting fails loudly rather than
            # reporting stale numbers as current.
            Alert.objects.create(
                title='Exchange rate feed failed', level='RED', department='Finance',
                reference='EXCHANGE_RATE_FEED',
                message=f'Could not fetch rates from {urlparse(url).netloc}: {exc}. '
                        f'Consolidated reporting will refuse to convert once the '
                        f'stored rates exceed the permitted age.')
            raise CommandError(f'Exchange rate fetch failed: {exc}') from exc

        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise CommandError(f'Provider returned invalid JSON: {body[:300]}') from exc
        if not isinstance(payload, dict):
            raise CommandError('Provider response was not a JSON object.')
        return payload

    def _parse_date(self, raw):
        if raw:
            try:
                return date.fromisoformat(str(raw)[:10])
            except ValueError:
                pass
        return timezone.localdate()
