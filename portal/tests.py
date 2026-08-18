from decimal import Decimal
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone
import json
from .models import (DashboardPage,FormDefinition,StockItem,Alert,Employee,AttendanceEvent,ApprovalRequest,ApprovalDecisionLog)
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


# ---------------------------------------------------------------------------
# Phase 2 - authorisation and file access
# ---------------------------------------------------------------------------

def _make_user(username, role=None, superuser=False):
    """Create a user holding a single Project 1 role."""
    from django.contrib.auth.models import Group
    from portal.models import UserProfile
    user = User.objects.create_user(username=username, password='pw-for-tests-1234')
    if superuser:
        user.is_superuser = True
        user.is_staff = True
        user.save()
    if role:
        UserProfile.objects.create(user=user, role=role)
        group, _ = Group.objects.get_or_create(name=role)
        user.groups.add(group)
    return user


def _client_for(user):
    client = Client()
    assert client.login(username=user.username, password='pw-for-tests-1234')
    return client


class ApprovalAuthorityTests(TestCase):
    """The approval endpoint is the hinge every 'senior approval' control hangs
    on. It previously had no checks at all: any authenticated user could raise a
    request and approve it themselves, or flip somebody else's rejected request
    to approved. TECHNICAL_ASSESSMENT.md 4.1.
    """

    def setUp(self):
        from portal import roles
        self.operator = _make_user('op', roles.OPERATOR)
        self.manager = _make_user('mgr', roles.OPERATION_MANAGER)
        self.other_manager = _make_user('mgr2', roles.UNIT_MANAGER)
        self.developer = _make_user('dev', roles.DEVELOPER)

    def _raise(self, client, reference='MO-1', approval_type='MANUAL_STOCK_OVERRIDE'):
        response = client.post(
            '/api/approvals/',
            data=json.dumps({'approval_type': approval_type, 'reference': reference,
                             'reason': 'test'}),
            content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['approval_id']

    def _decide(self, client, pk, decision='APPROVED'):
        return client.post(f'/api/approvals/{pk}/decision/',
                           data=json.dumps({'decision': decision}),
                           content_type='application/json')

    def test_operator_cannot_approve_at_all(self):
        approval_id = self._raise(_client_for(self.manager))
        response = self._decide(_client_for(self.operator), approval_id)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ApprovalRequest.objects.get(pk=approval_id).status, 'PENDING')

    def test_requester_cannot_approve_their_own_request(self):
        client = _client_for(self.manager)
        approval_id = self._raise(client)
        response = self._decide(client, approval_id)
        self.assertEqual(response.status_code, 403)
        self.assertIn('yourself', response.json()['error'])
        self.assertEqual(ApprovalRequest.objects.get(pk=approval_id).status, 'PENDING')

    def test_a_different_senior_role_can_approve(self):
        approval_id = self._raise(_client_for(self.manager))
        response = self._decide(_client_for(self.other_manager), approval_id)
        self.assertEqual(response.status_code, 200, response.content)
        approval = ApprovalRequest.objects.get(pk=approval_id)
        self.assertEqual(approval.status, 'APPROVED')
        self.assertNotEqual(approval.requested_by_id, approval.approved_by_id)

    def test_a_settled_request_cannot_be_decided_again(self):
        approval_id = self._raise(_client_for(self.manager))
        self.assertEqual(self._decide(_client_for(self.other_manager), approval_id).status_code, 200)
        again = self._decide(_client_for(self.other_manager), approval_id, 'REJECTED')
        self.assertEqual(again.status_code, 409)
        self.assertEqual(ApprovalRequest.objects.get(pk=approval_id).status, 'APPROVED')

    def test_developer_cannot_approve_business_transactions(self):
        approval_id = self._raise(_client_for(self.manager))
        response = self._decide(_client_for(self.developer), approval_id)
        self.assertEqual(response.status_code, 403)

    def test_narrowed_approval_type_excludes_other_managers(self):
        from portal import roles
        shipping = _make_user('ship', roles.SHIPPING_MANAGER)
        finance = _make_user('fin', roles.FINANCE_MANAGER)
        approval_id = self._raise(_client_for(self.manager), approval_type='BANK_DETAIL_CHANGE')
        self.assertEqual(self._decide(_client_for(shipping), approval_id).status_code, 403)
        self.assertEqual(self._decide(_client_for(finance), approval_id).status_code, 200)

    def test_every_decision_is_recorded(self):
        approval_id = self._raise(_client_for(self.manager))
        self._decide(_client_for(self.other_manager), approval_id)
        log = ApprovalDecisionLog.objects.get(approval_id=approval_id)
        self.assertEqual(log.decision, 'APPROVED')
        self.assertEqual(log.previous_status, 'PENDING')
        self.assertEqual(log.decided_by, self.other_manager)
        self.assertIn('Unit Manager', log.approver_roles)

    def test_malformed_body_is_rejected_not_a_500(self):
        client = _client_for(self.manager)
        response = client.post('/api/approvals/', data='not json',
                               content_type='application/json')
        self.assertEqual(response.status_code, 400)


class AuthorizationPolicyTests(TestCase):
    """Route authorisation. Every portal route must be classified, and roles
    must actually be enforced. TECHNICAL_ASSESSMENT.md 4.2.
    """

    def test_every_portal_route_is_classified(self):
        import re
        from pathlib import Path
        from portal.authorization import ROUTE_POLICY
        source = Path('portal/urls.py').read_text(encoding='utf-8')
        names = set(re.findall(r"name='([a-z_0-9]+)'", source))
        unclassified = sorted(names - set(ROUTE_POLICY))
        self.assertEqual(
            unclassified, [],
            'routes missing from ROUTE_POLICY (they will be denied by default): '
            + ', '.join(unclassified))

    def test_policy_has_no_entries_for_routes_that_no_longer_exist(self):
        import re
        from pathlib import Path
        from portal.authorization import ROUTE_POLICY
        source = Path('portal/urls.py').read_text(encoding='utf-8')
        names = set(re.findall(r"name='([a-z_0-9]+)'", source))
        stale = sorted(set(ROUTE_POLICY) - names)
        self.assertEqual(stale, [], 'ROUTE_POLICY entries with no matching route: '
                                    + ', '.join(stale))

    def test_policy_only_references_known_roles(self):
        from portal import roles
        from portal.authorization import ROUTE_POLICY, PUBLIC, AUTHENTICATED
        for name, policy in ROUTE_POLICY.items():
            if policy in (PUBLIC, AUTHENTICATED):
                continue
            unknown = sorted(set(policy) - set(roles.ALL_ROLES))
            self.assertEqual(unknown, [], f'{name} references unknown roles: {unknown}')

    def test_operator_is_refused_privileged_dashboards(self):
        from portal import roles
        client = _client_for(_make_user('op2', roles.OPERATOR))
        for url in ['/ceo-dashboard/', '/hr-dashboard/', '/attendance-dashboard/',
                    '/account-master/', '/supplier-dashboard/', '/qc-dashboard/',
                    '/cutting-dashboard/', '/stock-material-master/',
                    '/api/devices/', '/api/ceo-dashboard/', '/report-master/']:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 403)

    def test_each_role_reaches_its_own_area(self):
        from portal import roles
        cases = [
            (roles.HR_MANAGER, '/hr-dashboard/'),
            (roles.FINANCE_MANAGER, '/account-master/'),
            (roles.CEO, '/ceo-dashboard/'),
            (roles.OPERATION_MANAGER, '/cutting-dashboard/'),
            (roles.SHIPPING_MANAGER, '/shipping-dashboard/'),
            (roles.IT_MANAGER, '/api/devices/'),
            (roles.VENDOR_MANAGER, '/supplier-dashboard/'),
        ]
        for index, (role, url) in enumerate(cases):
            with self.subTest(role=role, url=url):
                client = _client_for(_make_user(f'r{index}', role))
                self.assertEqual(client.get(url).status_code, 200)

    def test_hr_manager_cannot_reach_finance_or_production(self):
        from portal import roles
        client = _client_for(_make_user('hrm', roles.HR_MANAGER))
        self.assertEqual(client.get('/account-master/').status_code, 403)
        self.assertEqual(client.get('/cutting-dashboard/').status_code, 403)

    def test_shared_pages_stay_open_to_every_authenticated_user(self):
        from portal import roles
        client = _client_for(_make_user('helper', roles.HELPER))
        for url in ['/dashboard/', '/forms-master/', '/files/', '/bundle-barcode/']:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200)

    def test_api_refusal_is_json_not_html(self):
        from portal import roles
        client = _client_for(_make_user('op3', roles.OPERATOR))
        response = client.get('/api/ceo-dashboard/')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()['ok'])

    def test_unknown_role_string_grants_nothing(self):
        from portal.models import UserProfile
        user = User.objects.create_user(username='typo', password='pw-for-tests-1234')
        UserProfile.objects.create(user=user, role='Cheif Executive')      # misspelled
        self.assertEqual(_client_for(user).get('/ceo-dashboard/').status_code, 403)

    def test_django_is_staff_alone_grants_no_business_access(self):
        user = User.objects.create_user(username='adminish', password='pw-for-tests-1234')
        user.is_staff = True
        user.save()
        self.assertEqual(_client_for(user).get('/ceo-dashboard/').status_code, 403)

    def test_superuser_passes_everywhere(self):
        client = _client_for(_make_user('root', superuser=True))
        for url in ['/ceo-dashboard/', '/hr-dashboard/', '/account-master/']:
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 200)

    def test_anonymous_visitor_is_redirected_to_login_not_forbidden(self):
        response = Client().get('/ceo-dashboard/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])


class FileAccessPolicyTests(TestCase):
    """Universal File Controls. TECHNICAL_ASSESSMENT.md 4.3."""

    #: Every resource type _resolve_file_resource can return. Kept explicit so
    #: adding a type without classifying it fails here rather than silently
    #: denying, or worse, silently allowing.
    ALL_RESOURCE_TYPES = [
        'document', 'communication_attachment',
        'staff_document', 'staff_application_attachment', 'staff_profile_photo',
        'delivery_pod', 'delivery_signature', 'delivery_photo',
        'attendance_cctv_thumbnail',
        'hr_recruitment_cv', 'hr_leave_attachment', 'hr_training_certificate',
        'hr_case_attachment',
        'cutting_auto_report',
        'embroidery_artwork', 'embroidery_program', 'embroidery_sample_image',
        'embroidery_auto_report',
        'label_artwork', 'label_specification', 'label_proof', 'label_sample_image',
        'label_auto_report',
        'hand_iron_instruction', 'hand_iron_qc_photo', 'hand_iron_auto_report',
        'iron_instruction', 'iron_qc_photo', 'iron_auto_report',
        'poly_specification', 'poly_artwork', 'poly_qc_photo', 'poly_auto_report',
        'packing_specification', 'packing_qc_photo', 'packing_auto_report',
        'qc_specification', 'qc_approved_sample', 'qc_inspection_photo',
        'qc_inspection_sheet', 'qc_auto_report',
        'final_qc_specification', 'final_qc_approved_sample', 'final_qc_packing_spec',
        'final_qc_inspection_sheet', 'final_qc_photo', 'final_qc_auto_report',
        'shipping_instruction', 'shipping_document', 'shipping_pod',
        'shipping_buyer_signature', 'shipping_delivery_photo', 'shipping_auto_report',
        'sourcing_specification', 'sourcing_quotation', 'sourcing_sample',
        'sourcing_auto_report',
        'supplier_document', 'supplier_quotation', 'supplier_po', 'supplier_invoice',
        'supplier_grn', 'supplier_delivery_note',
        'purchase_amendment', 'purchase_return',
    ]

    def test_every_resource_type_is_classified_deliberately(self):
        from portal import roles
        from portal.views import _file_access_decision
        ceo = _make_user('ceo-files', roles.CEO)
        unclassified = []
        for file_type in self.ALL_RESOURCE_TYPES:
            meta = {'type': file_type, 'thread': None, 'employee_id': None}
            _allowed, reason = _file_access_decision(ceo, meta)
            if 'not classified' in reason:
                unclassified.append(file_type)
        self.assertEqual(unclassified, [],
                         'resource types with no policy: ' + ', '.join(unclassified))

    def test_the_type_list_matches_the_resolver(self):
        """Guard against the list above drifting from _resolve_file_resource."""
        import re
        from pathlib import Path
        source = Path('portal/views.py').read_text(encoding='utf-8')
        block = source[source.index('def _resolve_file_resource'):
                       source.index('def _file_access_decision')]
        found = set()
        for match in re.finditer(r"key\s*==\s*'([a-z_]+)'|key in \{([^}]*)\}", block):
            if match.group(1):
                found.add(match.group(1))
            else:
                found.update(re.findall(r"'([a-z_]+)'", match.group(2)))
        self.assertEqual(sorted(found), sorted(self.ALL_RESOURCE_TYPES))

    def test_operator_cannot_read_a_corporate_document(self):
        from portal import roles
        from portal.models import DocumentRecord
        ceo = _make_user('ceo2', roles.CEO)
        operator = _make_user('op4', roles.OPERATOR)
        document = DocumentRecord.objects.create(
            document_id='D-1', title='Board Pack', category='Board',
            uploaded_by=ceo, file='documents/board.pdf')
        response = _client_for(operator).get(f'/files/document/{document.pk}/view/')
        self.assertEqual(response.status_code, 403)

    def test_a_refusal_is_written_to_the_audit_log(self):
        from portal import roles
        from portal.models import DocumentRecord, FileAccessLog
        ceo = _make_user('ceo3', roles.CEO)
        operator = _make_user('op5', roles.OPERATOR)
        document = DocumentRecord.objects.create(
            document_id='D-2', title='Board Pack', category='Board',
            uploaded_by=ceo, file='documents/board.pdf')
        _client_for(operator).get(f'/files/document/{document.pk}/view/')
        entry = FileAccessLog.objects.get(user=operator, resource_id=document.pk)
        self.assertFalse(entry.granted)
        self.assertIn('management role', entry.denial_reason)

    def test_confidential_document_is_not_limited_to_its_uploader(self):
        from portal import roles
        from portal.models import DocumentRecord
        from portal.views import _file_access_decision
        uploader = _make_user('uploader', roles.HR_MANAGER)
        DocumentRecord.objects.create(
            document_id='D-3', title='Payroll', category='Finance',
            uploaded_by=uploader, confidential=True, file='documents/pay.xlsx')
        meta = {'type': 'document', 'confidential': True, 'uploaded_by_id': uploader.id}
        # Finance authority reaches it; an operator does not.
        self.assertTrue(_file_access_decision(_make_user('fin2', roles.FINANCE_MANAGER), meta)[0])
        self.assertTrue(_file_access_decision(uploader, meta)[0])
        self.assertFalse(_file_access_decision(_make_user('op6', roles.OPERATOR), meta)[0])

    def test_delivery_proof_is_not_open_to_everyone(self):
        from portal import roles
        from portal.views import _file_access_decision
        meta = {'type': 'delivery_pod', 'uploaded_by_id': None}
        self.assertFalse(_file_access_decision(_make_user('op7', roles.OPERATOR), meta)[0])
        self.assertTrue(_file_access_decision(_make_user('shp2', roles.SHIPPING_MANAGER), meta)[0])

    def test_production_files_are_reachable_by_production_roles(self):
        """These branches were unreachable for non-staff before the rewrite."""
        from portal import roles
        from portal.views import _file_access_decision
        manager = _make_user('om2', roles.OPERATION_MANAGER)
        for file_type in ['label_artwork', 'poly_qc_photo', 'iron_instruction',
                          'final_qc_photo', 'cutting_auto_report']:
            with self.subTest(file_type=file_type):
                allowed, reason = _file_access_decision(manager, {'type': file_type})
                self.assertTrue(allowed, reason)

    def test_file_centre_does_not_leak_other_users_access_history(self):
        from portal import roles
        from portal.models import FileAccessLog
        ceo = _make_user('ceo4', roles.CEO)
        operator = _make_user('op8', roles.OPERATOR)
        FileAccessLog.objects.create(
            user=ceo, resource_type='document', resource_id=1,
            file_name='payroll-2026.xlsx', action='DOWNLOAD', reference='HR-CONF')
        body = _client_for(operator).get('/files/').content.decode('utf8', 'replace')
        self.assertNotIn('payroll-2026.xlsx', body)
        # The executive who performed it still sees the global log.
        body = _client_for(ceo).get('/files/').content.decode('utf8', 'replace')
        self.assertIn('payroll-2026.xlsx', body)


class HardeningTests(TestCase):
    """Assorted Phase 2 hardening. TECHNICAL_ASSESSMENT.md 4.4, 4.5, 4.6."""

    def test_incentive_preview_rejects_bad_input_instead_of_a_500(self):
        from portal import roles
        client = _client_for(_make_user('fin3', roles.FINANCE_MANAGER))
        response = client.get('/api/finance/overseas-preview/?amount=abc')
        self.assertEqual(response.status_code, 400)
        response = client.get('/api/finance/overseas-preview/?amount=1000')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['incentive_receivable'], '25.00')

    def test_password_validators_are_enabled(self):
        from django.core.exceptions import ValidationError
        from django.contrib.auth.password_validation import validate_password
        with self.assertRaises(ValidationError):
            validate_password('1')
        with self.assertRaises(ValidationError):
            validate_password('password')

    def test_login_is_rate_limited(self):
        from django.core.cache import cache
        from django.test import override_settings
        client = Client()
        # Force a local cache: REDIS_URL points at the compose hostname, which
        # does not resolve outside the stack, and the throttle fails open.
        locmem = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                              'LOCATION': 'throttle-test'}}
        with override_settings(LOGIN_ATTEMPT_LIMIT=3, CACHES=locmem):
            cache.clear()
            for _ in range(3):
                response = client.post('/login/', {'username': 'x', 'password': 'y'})
                self.assertEqual(response.status_code, 200)
            blocked = client.post('/login/', {'username': 'x', 'password': 'y'})
            self.assertEqual(blocked.status_code, 429)
            cache.clear()

    def test_login_does_not_honour_an_off_site_next_parameter(self):
        user = _make_user('redir', 'Staff')
        response = Client().post('/login/?next=https://evil.example/steal',
                                 {'username': user.username,
                                  'password': 'pw-for-tests-1234'})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('evil.example', response['Location'])

    def test_media_is_not_served_by_url(self):
        """Uploads must only be reachable through the audited file endpoint."""
        from django.urls import resolve
        from django.urls.exceptions import Resolver404
        with self.assertRaises(Resolver404):
            resolve('/media/documents/anything.pdf')

    def test_audit_models_are_read_only_in_admin(self):
        from django.contrib import admin
        from portal.models import AuditLog, FileAccessLog, ApprovalDecisionLog
        for model in (AuditLog, FileAccessLog, ApprovalDecisionLog):
            with self.subTest(model=model.__name__):
                site_admin = admin.site._registry[model]
                self.assertFalse(site_admin.has_add_permission(None))
                self.assertFalse(site_admin.has_change_permission(None))
                self.assertFalse(site_admin.has_delete_permission(None))


class RoleSyncTests(TestCase):
    """data/roles.json was never loaded by any code before Phase 2."""

    def test_sync_roles_creates_a_group_per_declared_role(self):
        import json as _json
        from pathlib import Path
        from django.contrib.auth.models import Group
        from django.core.management import call_command
        call_command('sync_roles', verbosity=0)
        declared = {str(r).strip() for r in
                    _json.loads(Path('data/roles.json').read_text(encoding='utf-8'))}
        existing = set(Group.objects.values_list('name', flat=True))
        self.assertTrue(declared <= existing, f'missing groups: {sorted(declared - existing)}')

    def test_roles_json_and_roles_module_agree(self):
        import json as _json
        from pathlib import Path
        from portal import roles
        declared = {str(r).strip() for r in
                    _json.loads(Path('data/roles.json').read_text(encoding='utf-8'))}
        self.assertEqual(declared, set(roles.ALL_ROLES))

    def test_sync_roles_aligns_profile_role_to_group_membership(self):
        from django.core.management import call_command
        from portal import roles
        from portal.models import UserProfile
        user = User.objects.create_user(username='needs-group', password='pw-for-tests-1234')
        UserProfile.objects.create(user=user, role=roles.FINANCE_MANAGER)
        call_command('sync_roles', verbosity=0)
        self.assertTrue(user.groups.filter(name=roles.FINANCE_MANAGER).exists())
