from decimal import Decimal
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone
from .models import DashboardPage,FormDefinition,StockItem,Alert,Employee,AttendanceEvent
from .services import apply_stock_scan,record_variance,calculate_attendance_day

class RegistryTests(TestCase):
    def test_registry_models_exist(self):
        self.assertEqual(DashboardPage.objects.count(),0)
        self.assertEqual(FormDefinition.objects.count(),0)

class OperationalRulesTests(TestCase):
    def setUp(self):
        self.user=User.objects.create_user(username='tester',password='x')
        self.item=StockItem.objects.create(sku='FAB-1',name='Fabric',category='Raw Material',qty=Decimal('100'),reserved_qty=Decimal('10'))
    def test_stock_out_requires_scan_and_respects_available(self):
        with self.assertRaises(ValueError):
            apply_stock_scan(item=self.item,direction='OUT',quantity=1,barcode='',reference='MO-1',user=self.user)
        with self.assertRaises(ValueError):
            apply_stock_scan(item=self.item,direction='OUT',quantity=91,barcode='B1',reference='MO-1',user=self.user)
        apply_stock_scan(item=self.item,direction='OUT',quantity=20,barcode='B1',reference='MO-1',user=self.user)
        self.item.refresh_from_db(); self.assertEqual(self.item.qty,Decimal('80'))
    def test_bdt_variance_over_ten_creates_red_alert(self):
        record_variance(reference='PO-1',expected=100,actual=111,department='Finance',currency='BDT',user=self.user)
        self.assertTrue(Alert.objects.filter(level='RED',reference='PO-1').exists())
    def test_operator_schedule(self):
        emp=Employee.objects.create(employee_id='OP-1',name='Operator',role='Operator')
        day=timezone.localdate()
        tz=timezone.get_current_timezone()
        from datetime import datetime,time
        AttendanceEvent.objects.create(employee=emp,event='CHECK_IN',occurred_at=timezone.make_aware(datetime.combine(day,time(8,0)),tz))
        AttendanceEvent.objects.create(employee=emp,event='BREAK_IN',occurred_at=timezone.make_aware(datetime.combine(day,time(13,0)),tz))
        AttendanceEvent.objects.create(employee=emp,event='BREAK_OUT',occurred_at=timezone.make_aware(datetime.combine(day,time(14,0)),tz))
        AttendanceEvent.objects.create(employee=emp,event='CHECK_OUT',occurred_at=timezone.make_aware(datetime.combine(day,time(17,0)),tz))
        summary=calculate_attendance_day(emp,day)
        self.assertEqual(summary.scheduled_minutes,480); self.assertEqual(summary.worked_minutes,480)


class TemplateIntegrityTests(TestCase):
    """Regression gate for the formatter damage described in
    TECHNICAL_ASSESSMENT.md 2.2.

    An HTML formatter once rewrote the interior of Django tags across the
    template tree, which took 30 of 39 templates out of service. Nothing in the
    project detected it. These two tests do.
    """

    def _template_files(self):
        from pathlib import Path
        from django.conf import settings
        files = []
        for d in settings.TEMPLATES[0]['DIRS']:
            files.extend(sorted(Path(d).rglob('*.html')))
        self.assertTrue(files, 'no templates found - check TEMPLATES DIRS')
        return files

    def test_every_template_compiles(self):
        from django.template.loader import get_template
        from django.template import TemplateSyntaxError
        broken = []
        for path in self._template_files():
            try:
                get_template(path.name)
            except TemplateSyntaxError as exc:
                broken.append(f'{path.name}: {exc}')
        self.assertEqual(broken, [], 'templates failed to compile:\n' + '\n'.join(broken))

    def test_no_template_tag_contains_a_newline(self):
        """Django's lexer matches {%.*?%} without re.DOTALL, so a tag broken
        across lines silently stops being a tag. Some such breaks raise
        TemplateSyntaxError, but others render the tag as visible text instead,
        which no compile check would catch. Fail on the cause, not the symptom.
        """
        import re
        pattern = re.compile(r'\{%(?:[^%]|%(?!\}))*?\n|\{\{(?:[^}]|\}(?!\}))*?\n')
        offenders = []
        for path in self._template_files():
            text = path.read_text(encoding='utf-8')
            for match in pattern.finditer(text):
                line = text[:match.start()].count('\n') + 1
                offenders.append(f'{path.name}:{line}: {match.group(0)[:60]!r}')
        self.assertEqual(
            offenders, [],
            'Django template tags split across lines - a formatter has been run '
            'over the templates. See .prettierignore:\n' + '\n'.join(offenders))


class RouteSmokeTests(TestCase):
    """Assert no route and no registered page raises a server error."""

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command
        call_command('seed_project1', verbosity=0)

    def _client(self):
        user = User.objects.filter(is_superuser=True).first()
        self.assertIsNotNone(user, 'seed_project1 did not create an admin user')
        user.set_password('smoke-test-pw')
        user.save()
        client = Client()
        self.assertTrue(client.login(username=user.username, password='smoke-test-pw'))
        return client

    def test_no_route_returns_a_server_error(self):
        import re
        from pathlib import Path
        source = Path('portal/urls.py').read_text(encoding='utf-8')
        # Skip parameterised routes, and login/logout: requesting logout mid-run
        # would end the session and make every later request look like a 302.
        routes = [r for r in re.findall(r"path\('([^']*)'", source)
                  if '<' not in r and 'login' not in r and 'logout' not in r]
        self.assertGreater(len(routes), 50, 'route discovery found too few routes')
        client = self._client()
        failures = []
        for route in routes:
            url = '/' + route
            try:
                status = client.get(url).status_code
            except Exception as exc:                       # noqa: BLE001 - reporting
                failures.append(f'{url}: {type(exc).__name__}: {exc}')
                continue
            if status >= 500:
                failures.append(f'{url}: HTTP {status}')
        self.assertEqual(failures, [], 'routes returning a server error:\n' + '\n'.join(failures))

    def test_every_registered_page_renders(self):
        import json
        from pathlib import Path
        pages = json.loads(Path('data/page_registry.json').read_text(encoding='utf-8'))
        client = self._client()
        failures = []
        for page in pages:
            url = f"/p/{page['slug']}/"
            try:
                response = client.get(url, follow=True)
            except Exception as exc:                       # noqa: BLE001 - reporting
                failures.append(f'{url}: {type(exc).__name__}: {exc}')
                continue
            if response.status_code != 200:
                failures.append(f'{url}: HTTP {response.status_code}')
        self.assertEqual(failures, [],
                         f'{len(failures)} of {len(pages)} registered pages failed:\n'
                         + '\n'.join(failures))
