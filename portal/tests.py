from decimal import Decimal
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.utils import timezone
import json
from .models import (DashboardPage,FormDefinition,StockItem,Alert,ActionItem,Employee,AttendanceEvent,ApprovalRequest,ApprovalDecisionLog)
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


# ---------------------------------------------------------------------------
# Phase 3 - correctness and performance
# ---------------------------------------------------------------------------

def _attendance_event(employee, name, day, at):
    from django.utils import timezone as tz
    from datetime import datetime
    zone = tz.get_current_timezone()
    AttendanceEvent.objects.create(
        employee=employee, event=name,
        occurred_at=tz.make_aware(datetime.combine(day, at), zone))


def _full_operator_day(employee, day):
    """08:00 start, 13:00-14:00 break, 17:00 finish - a complete 480-minute day."""
    from datetime import time as t
    _attendance_event(employee, 'CHECK_IN', day, t(8, 0))
    _attendance_event(employee, 'BREAK_IN', day, t(13, 0))
    _attendance_event(employee, 'BREAK_OUT', day, t(14, 0))
    _attendance_event(employee, 'CHECK_OUT', day, t(17, 0))


def _working_day():
    """A date that is not Friday, the Project 1 weekly closed day."""
    from django.utils import timezone as tz
    from datetime import timedelta
    day = tz.localdate()
    while day.weekday() == 4:
        day -= timedelta(days=1)
    return day


class PayrollArithmeticTests(TestCase):
    """TECHNICAL_ASSESSMENT.md 5.1 to 5.4."""

    def setUp(self):
        from decimal import Decimal as D
        self.day = _working_day()
        self.operator = Employee.objects.create(
            employee_id='OP-100', name='Cutter', role='Operator',
            category='OPERATOR', daily_cost=D('480'))

    def _gate_pass(self, pass_type, start, end, status='APPROVED'):
        from django.utils import timezone as tz
        from datetime import datetime
        from portal.models import AttendanceGatePass
        zone = tz.get_current_timezone()
        return AttendanceGatePass.objects.create(
            employee=self.operator, pass_type=pass_type, status=status, reason='test',
            out_at=tz.make_aware(datetime.combine(self.day, start), zone),
            in_at=tz.make_aware(datetime.combine(self.day, end), zone))

    def test_role_alone_still_selects_the_operator_shift(self):
        """category defaults to 'STAFF' and is always truthy, so the role must
        still be consulted or most of the workforce gets the wrong shift."""
        from decimal import Decimal as D
        from portal.services import attendance_schedule
        employee = Employee.objects.create(
            employee_id='OP-101', name='No Category', role='Operator',
            daily_cost=D('480'))
        self.assertEqual(employee.category, 'STAFF')       # the default
        schedule = attendance_schedule(employee.category, employee.role)
        self.assertEqual(schedule['scheduled_minutes'], 480)

    def test_an_approved_unpaid_gate_pass_is_deducted_from_paid_duty(self):
        from datetime import time as t
        from decimal import Decimal as D
        _full_operator_day(self.operator, self.day)
        self._gate_pass('UNPAID', t(9, 0), t(12, 0))       # three hours
        summary = calculate_attendance_day(self.operator, self.day)
        self.assertEqual(summary.gate_pass_unpaid_minutes, 180)
        # 480 scheduled less 180 unpaid absence.
        self.assertEqual(summary.worked_minutes, 300)
        self.assertEqual(summary.unpaid_minutes, 180)
        self.assertEqual(summary.worked_cost, D('300.00'))

    def test_a_paid_gate_pass_does_not_reduce_or_inflate_pay(self):
        """A paid pass sits inside check-in to check-out, so its minutes are
        already counted. It is reported, never added."""
        from datetime import time as t
        from decimal import Decimal as D
        _full_operator_day(self.operator, self.day)
        self._gate_pass('PAID', t(10, 0), t(11, 0))
        summary = calculate_attendance_day(self.operator, self.day)
        self.assertEqual(summary.worked_minutes, 480)
        self.assertEqual(summary.office_gate_pass_paid_minutes, 60)
        self.assertEqual(summary.unpaid_minutes, 0)
        # A full day costs exactly a full day.
        self.assertEqual(summary.worked_cost, D('480.00'))
        # The paid-pass figure is a subset of worked time, so it must never be
        # added to worked_cost - that would bill 540 for a 480 day.
        self.assertLessEqual(summary.gate_pass_paid_cost, summary.worked_cost)

    def test_unpaid_minutes_no_longer_understated_by_a_paid_pass(self):
        """Was `scheduled - min(worked + paid_gate, scheduled)`: a partial day
        with a paid pass under-reported unpaid time by the length of the pass."""
        from datetime import time as t
        _attendance_event(self.operator, 'CHECK_IN', self.day, t(8, 0))
        _attendance_event(self.operator, 'CHECK_OUT', self.day, t(14, 40))  # 400 min
        self._gate_pass('PAID', t(10, 0), t(11, 0))
        summary = calculate_attendance_day(self.operator, self.day)
        self.assertEqual(summary.worked_minutes, 400)
        self.assertEqual(summary.unpaid_minutes, 80)        # not 20

    def test_a_pending_gate_pass_has_no_effect(self):
        from datetime import time as t
        _full_operator_day(self.operator, self.day)
        self._gate_pass('UNPAID', t(9, 0), t(12, 0), status='PENDING')
        summary = calculate_attendance_day(self.operator, self.day)
        self.assertEqual(summary.worked_minutes, 480)
        self.assertEqual(summary.gate_pass_unpaid_minutes, 0)

    def test_gate_pass_minutes_are_clipped_to_the_shift(self):
        """A pass running past scheduled checkout must not deduct time the
        employee was never scheduled for."""
        from datetime import time as t
        _full_operator_day(self.operator, self.day)
        self._gate_pass('UNPAID', t(16, 0), t(19, 0))       # 1h inside, 2h after
        summary = calculate_attendance_day(self.operator, self.day)
        self.assertEqual(summary.gate_pass_unpaid_minutes, 60)
        self.assertEqual(summary.worked_minutes, 420)

    def test_shift_is_read_from_attendanceshift(self):
        """AttendanceShift was modelled, admin-registered and never read."""
        from datetime import time as t
        from portal.models import AttendanceShift
        from portal.services import attendance_schedule, clear_shift_cache
        AttendanceShift.objects.create(
            code='SHIFT-TEST-OP', name='Short operator shift',
            employee_category='OPERATOR', check_in=t(7, 0),
            break1_in=t(12, 0), break1_out=t(12, 30),
            check_out=t(15, 0), mandatory_minutes=450, grace_minutes=5,
            ot_break_minutes=15, active=True)
        clear_shift_cache()
        schedule = attendance_schedule('OPERATOR')
        self.assertEqual(schedule['scheduled_minutes'], 450)
        self.assertEqual(schedule['start'], t(7, 0))
        self.assertEqual(schedule['grace_minutes'], 5)
        # ot_start is derived as checkout + ot_break_minutes.
        self.assertEqual(schedule['ot_start'], t(15, 15))
        self.assertIn('AttendanceShift', schedule['source'])
        clear_shift_cache()

    def test_editing_a_shift_takes_effect_without_a_restart(self):
        from datetime import time as t
        from portal.models import AttendanceShift
        from portal.services import attendance_schedule
        shift = AttendanceShift.objects.create(
            code='SHIFT-TEST-ST', name='Staff', employee_category='STAFF',
            check_in=t(9, 0), break1_in=t(13, 0), break1_out=t(14, 0),
            check_out=t(18, 0), mandatory_minutes=480, grace_minutes=10,
            ot_break_minutes=30, active=True)
        self.assertEqual(attendance_schedule('STAFF')['scheduled_minutes'], 480)
        shift.mandatory_minutes = 500
        shift.save()                    # post_save clears the cache
        self.assertEqual(attendance_schedule('STAFF')['scheduled_minutes'], 500)
        from portal.services import clear_shift_cache
        clear_shift_cache()

    def test_friday_is_the_weekly_closed_day(self):
        from datetime import timedelta
        from django.utils import timezone as tz
        friday = tz.localdate()
        while friday.weekday() != 4:
            friday -= timedelta(days=1)
        summary = calculate_attendance_day(self.operator, friday)
        self.assertEqual(summary.scheduled_minutes, 0)
        self.assertEqual(summary.status, 'WEEKEND')


class TimezoneTests(TestCase):
    """TECHNICAL_ASSESSMENT.md 5.5."""

    def test_the_operating_clock_is_dhaka(self):
        from django.conf import settings
        self.assertEqual(settings.TIME_ZONE, 'Asia/Dhaka',
                         'TIME_ZONE in .env is overriding the Asia/Dhaka default. '
                         'The attendance engine and the 08:00/13:00/20:00 report '
                         'slots are only correct on the Bangladesh clock.')

    def test_celery_shares_the_django_clock(self):
        from django.conf import settings
        # Celery reads only CELERY_* keys; unset, beat silently used UTC and the
        # three "Bangladesh" slots fired four to six hours late.
        self.assertEqual(settings.CELERY_TIMEZONE, settings.TIME_ZONE)

    def test_attendance_anchors_resolve_in_the_configured_zone(self):
        from decimal import Decimal as D
        day = _working_day()
        employee = Employee.objects.create(
            employee_id='OP-TZ', name='Anchor', role='Operator',
            category='OPERATOR', daily_cost=D('480'))
        _full_operator_day(employee, day)
        summary = calculate_attendance_day(employee, day)
        # A local 08:00-17:00 day with a one-hour break is exactly the shift.
        self.assertEqual(summary.worked_minutes, 480)
        self.assertEqual(summary.late_minutes, 0)
        self.assertEqual(summary.early_leave_minutes, 0)


class CurrencyTests(TestCase):
    """TECHNICAL_ASSESSMENT.md 5.6."""

    def setUp(self):
        from decimal import Decimal as D
        from django.utils import timezone as tz
        from portal.models import ExchangeRate
        self.today = tz.localdate()
        ExchangeRate.objects.create(base_currency='EUR', quote_currency='BDT',
                                    rate=D('130'), rate_date=self.today, source='MANUAL')
        ExchangeRate.objects.create(base_currency='EUR', quote_currency='USD',
                                    rate=D('1.10'), rate_date=self.today, source='MANUAL')

    def test_direct_inverse_and_cross_rates(self):
        from decimal import Decimal as D
        from portal.currency import convert
        self.assertEqual(convert(D('100'), 'EUR', 'BDT'), D('13000.00'))
        self.assertEqual(convert(D('13000'), 'BDT', 'EUR'), D('100.00'))
        # BDT -> EUR -> USD
        self.assertEqual(convert(D('13000'), 'BDT', 'USD'), D('110.00'))

    def test_same_currency_is_identity(self):
        from decimal import Decimal as D
        from portal.currency import convert
        self.assertEqual(convert(D('42.50'), 'EUR', 'EUR'), D('42.50'))

    def test_a_missing_rate_raises_rather_than_guessing(self):
        from decimal import Decimal as D
        from portal.currency import RateUnavailable, convert
        with self.assertRaises(RateUnavailable):
            convert(D('100'), 'JPY', 'EUR')

    def test_a_stale_rate_is_refused(self):
        from datetime import timedelta
        from decimal import Decimal as D
        from portal.models import ExchangeRate
        from portal.currency import RateUnavailable, convert
        ExchangeRate.objects.all().delete()
        ExchangeRate.objects.create(
            base_currency='EUR', quote_currency='BDT', rate=D('130'),
            rate_date=self.today - timedelta(days=400), source='MANUAL')
        with self.assertRaises(RateUnavailable):
            convert(D('100'), 'EUR', 'BDT')

    def test_sum_converted_reports_what_it_could_not_convert(self):
        from decimal import Decimal as D
        from portal.currency import sum_converted
        total, missing = sum_converted([(D('100'), 'EUR'), (D('1300'), 'BDT'),
                                        (D('50'), 'JPY')], 'EUR')
        self.assertEqual(total, D('110.00'))       # 100 EUR + 10 EUR
        self.assertEqual(missing, ['JPY'])         # not silently dropped

    def test_ceo_summary_reports_in_the_base_currency(self):
        from decimal import Decimal as D
        from portal.views import _ceo_summary
        from portal.models import FinanceTransaction, MasterOrder
        MasterOrder.objects.create(master_order_id='MO-CUR-1', buyer='B', product='Cap',
                                   quantity=10, order_value=D('1300'), currency='BDT')
        MasterOrder.objects.create(master_order_id='MO-CUR-2', buyer='B', product='Cap',
                                   quantity=10, order_value=D('100'), currency='EUR')
        FinanceTransaction.objects.create(transaction_type='INCOME', currency='EUR',
                                          amount=D('1000'))
        payload = _ceo_summary(self.today)
        self.assertEqual(payload['base_currency'], 'EUR')
        # 1300 BDT converts to 10 EUR, plus 100 EUR - not a raw sum of 1400.
        self.assertEqual(payload['order_value'], '110.00')
        self.assertEqual(payload['fx_unconvertible'], [])

    def test_ceo_summary_flags_currencies_it_cannot_convert(self):
        from decimal import Decimal as D
        from portal.views import _ceo_summary
        from portal.models import MasterOrder
        MasterOrder.objects.create(master_order_id='MO-CUR-3', buyer='B', product='Cap',
                                   quantity=1, order_value=D('999'), currency='JPY')
        payload = _ceo_summary(self.today)
        self.assertIn('JPY', payload['fx_unconvertible'])


class ProcurementScoringTests(TestCase):
    """TECHNICAL_ASSESSMENT.md 5.7 - price_score was hardcoded to 100."""

    def test_the_cheapest_landed_cost_scores_highest(self):
        from decimal import Decimal as D
        from portal.models import (ProcurementComparison, ProcurementRequest,
                                   SupplierMaster)
        request = ProcurementRequest.objects.create(
            request_no='PR-1', description='Fabric', required_qty=D('100'))
        cheap = SupplierMaster.objects.create(supplier_id='S-CHEAP', company_name='Cheap Ltd')
        dear = SupplierMaster.objects.create(supplier_id='S-DEAR', company_name='Dear Ltd')
        low = ProcurementComparison.objects.create(
            request=request, supplier=cheap, unit_price=D('10'))
        high = ProcurementComparison.objects.create(
            request=request, supplier=dear, unit_price=D('20'))
        low.refresh_from_db(); high.refresh_from_db()
        self.assertEqual(low.price_score, D('100.00'))
        self.assertEqual(high.price_score, D('50.00'))       # twice the cost
        self.assertGreater(low.total_score, high.total_score)

    def test_adding_a_cheaper_quote_rescores_the_existing_ones(self):
        from decimal import Decimal as D
        from portal.models import (ProcurementComparison, ProcurementRequest,
                                   SupplierMaster)
        request = ProcurementRequest.objects.create(
            request_no='PR-2', description='Trims', required_qty=D('10'))
        first = ProcurementComparison.objects.create(
            request=request,
            supplier=SupplierMaster.objects.create(supplier_id='S-1', company_name='One'),
            unit_price=D('100'))
        first.refresh_from_db()
        self.assertEqual(first.price_score, D('100.00'))
        ProcurementComparison.objects.create(
            request=request,
            supplier=SupplierMaster.objects.create(supplier_id='S-2', company_name='Two'),
            unit_price=D('50'))
        first.refresh_from_db()
        self.assertEqual(first.price_score, D('50.00'))      # no longer the best

    def test_freight_and_duty_land_in_the_comparison(self):
        from decimal import Decimal as D
        from portal.models import (ProcurementComparison, ProcurementRequest,
                                   SupplierMaster)
        request = ProcurementRequest.objects.create(
            request_no='PR-3', description='Yarn', required_qty=D('100'))
        row = ProcurementComparison.objects.create(
            request=request,
            supplier=SupplierMaster.objects.create(supplier_id='S-3', company_name='Three'),
            unit_price=D('10'), freight_cost=D('500'))
        row.refresh_from_db()
        # 10 + 500/100 = 15 landed.
        self.assertEqual(row.landed_unit_cost, D('15.0000'))


class DataIntegrityTests(TestCase):
    """TECHNICAL_ASSESSMENT.md 5.8, 5.9, 6.5."""

    def test_completed_is_a_declared_order_status(self):
        """The buyer-delivery module wrote 'COMPLETED', which was not in choices,
        so an invalid status was persisted silently."""
        from portal.models import MasterOrder
        valid = {code for code, _label in MasterOrder.STATUS}
        self.assertIn('COMPLETED', valid)
        self.assertIn('DELIVERED', valid)

    def test_action_item_status_has_choices_and_one_open_definition(self):
        self.assertTrue(ActionItem._meta.get_field('status').choices)
        valid = {code for code, _label in ActionItem.STATUS}
        self.assertTrue(set(ActionItem.OPEN_STATUSES) <= valid)
        self.assertNotIn('COMPLETED', ActionItem.OPEN_STATUSES)
        # 'DONE' was excluded by the header counter but never written by anything.
        self.assertNotIn('DONE', valid)

    def test_open_action_counts_agree_everywhere(self):
        from portal.context_processors import _compute
        ActionItem.objects.create(title='open', status='OPEN')
        ActionItem.objects.create(title='doing', status='IN_PROGRESS')
        ActionItem.objects.create(title='done', status='COMPLETED')
        expected = ActionItem.objects.filter(
            status__in=ActionItem.OPEN_STATUSES).count()
        self.assertEqual(expected, 2)
        # The global control strip previously excluded 'DONE' and so counted all
        # three, disagreeing with every dashboard on the same screen.
        self.assertEqual(_compute()['global_actions'], expected)

    def test_dashboard_post_handlers_are_atomic(self):
        """A failure part-way through a multi-model write must roll back."""
        import ast
        from pathlib import Path
        source = Path('portal/views.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        unguarded = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try) or len(node.handlers) != 1:
                continue
            handler_src = ast.get_source_segment(source, node.handlers[0]) or ''
            if '_handle_post_error' not in handler_src:
                continue
            if not (len(node.body) == 1 and isinstance(node.body[0], ast.With)):
                unguarded.append(node.lineno)
        self.assertEqual(unguarded, [],
                         f'POST handlers not wrapped in transaction.atomic(): {unguarded}')

    def test_expected_errors_reach_the_user_and_bugs_do_not(self):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory
        from portal.views import _handle_post_error
        for exc, should_show in [(ValueError('Barcode is mandatory.'), True),
                                 (PermissionError('Senior approval required.'), True),
                                 (AttributeError("'NoneType' has no attribute 'x'"), False)]:
            with self.subTest(exc=type(exc).__name__):
                request = RequestFactory().post('/cutting-dashboard/')
                request.user = User.objects.create_user(
                    username=f'u{abs(hash(str(exc))) % 100000}', password='x')
                request.session = {}
                request._messages = FallbackStorage(request)
                _handle_post_error(request, exc)
                shown = [m.message for m in request._messages]
                if should_show:
                    self.assertIn(str(exc), shown)
                else:
                    # A defect must not be presented as if it were guidance.
                    self.assertNotIn(str(exc), shown)
                    self.assertTrue(any('logged for IT' in m for m in shown))


class QueryEfficiencyTests(TestCase):
    """TECHNICAL_ASSESSMENT.md 6.1 and 6.7."""

    def test_filtered_columns_are_indexed(self):
        from portal.models import (ActionItem, Alert, AttendanceDailySummary,
                                   AttendanceEvent, CuttingProductionEntry,
                                   MaterialMovement, StockScan)
        expected = [
            (Alert, 'actioned'), (Alert, 'level'), (ActionItem, 'status'),
            (AttendanceDailySummary, 'work_date'), (AttendanceEvent, 'occurred_at'),
            (CuttingProductionEntry, 'work_date'), (MaterialMovement, 'movement_type'),
            (StockScan, 'direction'),
        ]
        for model, field_name in expected:
            with self.subTest(model=model.__name__, field=field_name):
                field = model._meta.get_field(field_name)
                self.assertTrue(field.db_index or field.unique,
                                f'{model.__name__}.{field_name} is filtered but not indexed')

    def test_created_at_is_indexed_on_every_timestamped_model(self):
        from portal.models import Alert, MasterOrder, StockItem
        for model in (Alert, MasterOrder, StockItem):
            with self.subTest(model=model.__name__):
                self.assertTrue(model._meta.get_field('created_at').db_index)

    def test_control_strip_does_not_filter_on_a_date_function(self):
        """occurred_at__date=today applies a function to the column, so no index
        on occurred_at can serve it."""
        from pathlib import Path
        source = Path('portal/context_processors.py').read_text(encoding='utf-8')
        code = '\n'.join(line for line in source.split('\n')
                          if not line.strip().startswith(('#', '``', '*')))
        self.assertNotIn('occurred_at__date=', code)

    def test_control_strip_sums_in_the_database(self):
        from decimal import Decimal as D
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        from portal.context_processors import _compute
        day = _working_day()
        for index in range(12):
            employee = Employee.objects.create(
                employee_id=f'CS-{index}', name=f'W{index}', role='Operator',
                category='OPERATOR', daily_cost=D('100'))
            _attendance_event(employee, 'CHECK_IN', day, __import__('datetime').time(8, 0))
        with CaptureQueriesContext(connection) as captured:
            payload = _compute()
        # Constant query count regardless of headcount: the old version loaded
        # every present employee and summed daily_cost in Python.
        self.assertLess(len(captured), 8, f'{len(captured)} queries for the control strip')
        self.assertEqual(payload['today_present_staff'], 12)
        self.assertEqual(payload['today_staff_cost'], D('1200'))


class ExchangeRateFeedTests(TestCase):
    """The daily rate feed is opt-in and must never invent a rate."""

    def test_it_is_a_no_op_until_configured(self):
        from io import StringIO
        from django.core.management import call_command
        from django.test import override_settings
        from portal.models import ExchangeRate
        out = StringIO()
        with override_settings(EXCHANGE_RATE_API_URL=''):
            call_command('fetch_exchange_rates', stdout=out)
        self.assertIn('not set', out.getvalue())
        self.assertEqual(ExchangeRate.objects.count(), 0)

    def test_a_non_https_endpoint_is_refused(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError) as caught:
            call_command('fetch_exchange_rates', url='http://rates.example/latest')
        self.assertIn('https', str(caught.exception))

    def test_a_provider_response_is_stored_as_dated_rates(self):
        import json as _json
        from decimal import Decimal as D
        from unittest.mock import patch
        from django.core.management import call_command
        from portal.models import ExchangeRate

        class _Response:
            def read(self):
                return _json.dumps({'base': 'EUR', 'date': '2026-08-18',
                                    'rates': {'BDT': 131.25, 'USD': 1.09,
                                              'BAD': 'not-a-number'}}).encode()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with patch('portal.management.commands.fetch_exchange_rates.urlopen',
                   return_value=_Response()):
            call_command('fetch_exchange_rates', url='https://rates.example/latest',
                         verbosity=0)
        bdt = ExchangeRate.objects.get(quote_currency='BDT')
        self.assertEqual(bdt.rate, D('131.25'))
        self.assertEqual(str(bdt.rate_date), '2026-08-18')
        self.assertEqual(bdt.source, 'AUTO')
        self.assertEqual(bdt.provider, 'rates.example')
        # An unparseable value is skipped, never stored as zero or one.
        self.assertFalse(ExchangeRate.objects.filter(quote_currency='BAD').exists())

    def test_a_transport_failure_raises_a_red_alert(self):
        from unittest.mock import patch
        from urllib.error import URLError
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with patch('portal.management.commands.fetch_exchange_rates.urlopen',
                   side_effect=URLError('dns failure')):
            with self.assertRaises(CommandError):
                call_command('fetch_exchange_rates', url='https://rates.example/latest',
                             verbosity=0)
        self.assertTrue(Alert.objects.filter(level='RED',
                                             reference='EXCHANGE_RATE_FEED').exists())


class AuditRetentionTests(TestCase):
    """AuditMiddleware writes a row per request with no retention (6.7)."""

    def test_only_expired_request_logs_are_purged(self):
        from datetime import timedelta
        from django.test import override_settings
        from django.utils import timezone as tz
        from portal.models import AuditLog, FileAccessLog
        from portal.tasks import purge_expired_audit_logs

        old = AuditLog.objects.create(method='GET', path='/old/', status_code=200)
        recent = AuditLog.objects.create(method='GET', path='/recent/', status_code=200)
        AuditLog.objects.filter(pk=old.pk).update(
            created_at=tz.now() - timedelta(days=400))
        # The file-access trail is evidence and must survive the purge.
        FileAccessLog.objects.create(resource_type='document', resource_id=1,
                                     file_name='x.pdf', action='VIEW')

        with override_settings(AUDIT_LOG_RETENTION_DAYS=365):
            result = purge_expired_audit_logs()

        self.assertEqual(result['deleted'], 1)
        self.assertFalse(AuditLog.objects.filter(pk=old.pk).exists())
        self.assertTrue(AuditLog.objects.filter(pk=recent.pk).exists())
        self.assertEqual(FileAccessLog.objects.count(), 1)


# ---------------------------------------------------------------------------
# Phase 4 - organisation scoping
# ---------------------------------------------------------------------------

class OrganisationTreeTests(TestCase):
    """The materialised path is what subtree scoping matches on, so it has to be
    right after every insert and move."""

    def setUp(self):
        from portal.models import OrganizationNode
        self.country = OrganizationNode.objects.create(name='Bangladesh', node_type='Country')
        self.company = OrganizationNode.objects.create(name='Rozalia BD', node_type='Company',
                                                       parent=self.country)
        self.factory_a = OrganizationNode.objects.create(name='Dhaka Factory',
                                                         node_type='Factory', parent=self.company)
        self.factory_b = OrganizationNode.objects.create(name='Chattogram Factory',
                                                         node_type='Factory', parent=self.company)
        self.unit = OrganizationNode.objects.create(name='Unit 1',
                                                    node_type='Production Unit',
                                                    parent=self.factory_a)

    def test_paths_and_depths_are_maintained_on_save(self):
        for node in (self.country, self.company, self.factory_a, self.unit):
            node.refresh_from_db()
        self.assertEqual(self.country.path, f'/{self.country.pk}/')
        self.assertEqual(self.country.depth, 0)
        self.assertEqual(self.unit.path,
                         f'/{self.country.pk}/{self.company.pk}/{self.factory_a.pk}/{self.unit.pk}/')
        self.assertEqual(self.unit.depth, 3)

    def test_descendants_and_ancestors(self):
        self.assertIn(self.unit, list(self.factory_a.descendants()))
        self.assertNotIn(self.unit, list(self.factory_b.descendants()))
        self.assertEqual([n.pk for n in self.unit.ancestors()],
                         [self.country.pk, self.company.pk, self.factory_a.pk])

    def test_moving_a_node_repaths_its_subtree(self):
        self.factory_a.parent = self.factory_b
        self.factory_a.save()
        self.unit.refresh_from_db()
        self.assertTrue(self.unit.path.startswith(self.factory_b.path))
        self.assertIn(self.unit, list(self.factory_b.descendants()))

    def test_rebuild_command_repairs_a_corrupted_path(self):
        from django.core.management import call_command
        from portal.models import OrganizationNode
        OrganizationNode.objects.filter(pk=self.unit.pk).update(path='/wrong/', depth=99)
        call_command('rebuild_org_paths', verbosity=0)
        self.unit.refresh_from_db()
        self.assertTrue(self.unit.path.startswith(self.factory_a.path))
        self.assertEqual(self.unit.depth, 3)

    def test_timezone_is_inherited_from_the_nearest_ancestor_that_sets_one(self):
        from django.conf import settings
        self.assertEqual(self.unit.effective_timezone, settings.TIME_ZONE)
        self.country.timezone = 'Asia/Dhaka'
        self.country.save()
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.effective_timezone, 'Asia/Dhaka')
        self.factory_a.timezone = 'Asia/Kolkata'
        self.factory_a.save()
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.effective_timezone, 'Asia/Kolkata')


class ScopedQueryTests(TestCase):
    """Cross-site isolation. TECHNICAL_ASSESSMENT.md 6.2."""

    def setUp(self):
        from decimal import Decimal as D
        from portal.models import MasterOrder, OrganizationNode
        self.company = OrganizationNode.objects.create(name='Rozalia', node_type='Company')
        self.dhaka = OrganizationNode.objects.create(name='Dhaka', node_type='Factory',
                                                     parent=self.company)
        self.dhaka_unit = OrganizationNode.objects.create(name='Dhaka Unit 1',
                                                          node_type='Production Unit',
                                                          parent=self.dhaka)
        self.ctg = OrganizationNode.objects.create(name='Chattogram', node_type='Factory',
                                                    parent=self.company)
        self.dhaka_order = MasterOrder.objects.create(
            master_order_id='MO-DHK-1', buyer='B', product='Cap', quantity=1,
            order_value=D('100'), scope=self.dhaka)
        self.unit_order = MasterOrder.objects.create(
            master_order_id='MO-DHK-UNIT', buyer='B', product='Cap', quantity=1,
            order_value=D('100'), scope=self.dhaka_unit)
        self.ctg_order = MasterOrder.objects.create(
            master_order_id='MO-CTG-1', buyer='B', product='Cap', quantity=1,
            order_value=D('100'), scope=self.ctg)
        self.unassigned = MasterOrder.objects.create(
            master_order_id='MO-NONE', buyer='B', product='Cap', quantity=1,
            order_value=D('100'))

    def _user_at(self, username, node, role=None):
        from portal import roles
        from portal.models import UserProfile
        from django.contrib.auth.models import Group
        user = User.objects.create_user(username=username, password='pw-for-tests-1234')
        UserProfile.objects.create(user=user, role=role or roles.UNIT_MANAGER, scope=node)
        group, _ = Group.objects.get_or_create(name=role or roles.UNIT_MANAGER)
        user.groups.add(group)
        return user

    def test_a_factory_scope_sees_itself_and_below_but_not_a_sibling(self):
        from portal.models import MasterOrder
        from portal.tenancy import scope_context
        with scope_context(self.dhaka):
            visible = set(MasterOrder.objects.values_list('master_order_id', flat=True))
        self.assertIn('MO-DHK-1', visible)
        self.assertIn('MO-DHK-UNIT', visible)          # descendant
        self.assertNotIn('MO-CTG-1', visible)          # sibling factory
        self.assertIn('MO-NONE', visible)              # unassigned, strict mode off

    def test_a_parent_scope_sees_both_factories(self):
        from portal.models import MasterOrder
        from portal.tenancy import scope_context
        with scope_context(self.company):
            visible = set(MasterOrder.objects.values_list('master_order_id', flat=True))
        self.assertIn('MO-DHK-1', visible)
        self.assertIn('MO-CTG-1', visible)

    def test_no_active_scope_means_no_filtering(self):
        """Management commands, Celery tasks and the shell are system contexts."""
        from portal.models import MasterOrder
        self.assertEqual(MasterOrder.objects.count(), 4)

    def test_strict_mode_hides_unassigned_records(self):
        from django.test import override_settings
        from portal.models import MasterOrder
        from portal.tenancy import scope_context
        with override_settings(TENANCY_STRICT=True):
            with scope_context(self.dhaka):
                visible = set(MasterOrder.objects.values_list('master_order_id', flat=True))
        self.assertIn('MO-DHK-1', visible)
        self.assertNotIn('MO-NONE', visible)

    def test_unscoped_block_bypasses_the_active_scope(self):
        from portal.models import MasterOrder
        from portal.tenancy import scope_context, unscoped
        with scope_context(self.dhaka):
            self.assertNotIn('MO-CTG-1',
                             set(MasterOrder.objects.values_list('master_order_id', flat=True)))
            with unscoped():
                self.assertEqual(MasterOrder.objects.count(), 4)
            # The scope is restored after the block.
            self.assertNotIn('MO-CTG-1',
                             set(MasterOrder.objects.values_list('master_order_id', flat=True)))

    def test_all_objects_is_never_scoped(self):
        from portal.models import MasterOrder
        from portal.tenancy import scope_context
        with scope_context(self.dhaka):
            self.assertEqual(MasterOrder.all_objects.count(), 4)

    def test_related_access_and_deletes_are_not_scoped(self):
        """_base_manager must stay unscoped or reverse accessors go silently
        incomplete and cascade deletes leave orphans."""
        from portal.models import CuttingPlan, MasterOrder
        CuttingPlan.objects.create(plan_no='CP-CTG-1', order=self.ctg_order,
                                   product='Cap', scope=self.ctg)
        self.assertEqual(MasterOrder._meta.base_manager_name, 'all_objects')
        from portal.tenancy import scope_context
        with scope_context(self.dhaka):
            # Reached through the parent, so the sibling factory's plan is still
            # visible to a legitimate traversal.
            self.assertEqual(self.ctg_order.cutting_plans.count(), 1)

    def test_the_view_layer_is_scoped_through_the_middleware(self):
        from portal import roles
        user = self._user_at('dhaka-mgr', self.dhaka, roles.OPERATION_MANAGER)
        client = Client()
        self.assertTrue(client.login(username=user.username, password='pw-for-tests-1234'))
        body = client.get('/cutting-dashboard/').content.decode('utf8', 'replace')
        self.assertIn('MO-DHK-1', body)
        self.assertNotIn('MO-CTG-1', body)

    def test_a_user_without_a_scope_sees_everything(self):
        from portal import roles
        user = self._user_at('hq-mgr', None, roles.OPERATION_MANAGER)
        client = Client()
        client.login(username=user.username, password='pw-for-tests-1234')
        body = client.get('/cutting-dashboard/').content.decode('utf8', 'replace')
        self.assertIn('MO-DHK-1', body)
        self.assertIn('MO-CTG-1', body)

    def test_a_superuser_is_never_scoped(self):
        from portal.tenancy import resolve_user_scope
        from portal.models import UserProfile
        root = User.objects.create_user(username='root4', password='pw-for-tests-1234',
                                        is_superuser=True, is_staff=True)
        UserProfile.objects.create(user=root, role='CEO', scope=self.dhaka)
        self.assertIsNone(resolve_user_scope(root))

    def test_scope_does_not_leak_between_requests(self):
        """gunicorn reuses threads, so a leaked scope would apply to whoever was
        served next on that worker."""
        from portal import roles
        from portal.models import MasterOrder
        from portal.tenancy import current_scope
        user = self._user_at('leak-mgr', self.dhaka, roles.OPERATION_MANAGER)
        client = Client()
        client.login(username=user.username, password='pw-for-tests-1234')
        client.get('/cutting-dashboard/')
        self.assertIsNone(current_scope())
        self.assertEqual(MasterOrder.objects.count(), 4)


class ScopeCoverageTests(TestCase):
    """Guards on the shape of the scoping layer itself."""

    def test_the_aggregate_roots_are_scoped(self):
        from portal import models as m
        expected = [
            m.Employee, m.MasterOrder, m.StockItem, m.Alert, m.ActionItem,
            m.DocumentRecord, m.FinanceTransaction, m.ApprovalRequest,
            m.MaterialMaster, m.AssetMachine, m.SupplierMaster, m.BuyerOpportunity,
            m.CommunicationThread, m.CuttingPlan, m.EmbroideryPlan, m.LabelPlan,
            m.QCInspectionPlan, m.HandIronPlan, m.PolyPlan, m.IronPlan,
            m.FinalQCPlan, m.FinishingPlan, m.PackingPlan, m.ShippingPlan,
            m.ProcurementRequest, m.SourcingRequest, m.PurchaseTransaction,
        ]
        from portal.tenancy import is_scoped_model
        for model in expected:
            with self.subTest(model=model.__name__):
                self.assertTrue(is_scoped_model(model),
                                f'{model.__name__} holds business data but has no scope')

    def test_every_scoped_model_pins_its_base_manager(self):
        """A scoped _base_manager breaks related descriptors and cascade deletes."""
        from portal.tenancy import scoped_models
        offenders = [model.__name__ for model in scoped_models()
                     if model._meta.base_manager_name != 'all_objects']
        self.assertEqual(offenders, [],
                         f'scoped models with an unpinned base manager: {offenders}')

    def test_report_unscoped_counts_what_is_left_to_assign(self):
        from io import StringIO
        from decimal import Decimal as D
        from django.core.management import call_command
        from portal.models import MasterOrder
        MasterOrder.objects.create(master_order_id='MO-U1', buyer='B', product='Cap',
                                   quantity=1, order_value=D('1'))
        out = StringIO()
        call_command('report_unscoped', stdout=out)
        report = out.getvalue()
        self.assertIn('MasterOrder', report)
        self.assertIn('no site', report)


class SiteTimezonePayrollTests(TestCase):
    """Per-site clocks are the reason the workforce needed a scope."""

    def test_attendance_uses_the_site_clock_not_the_server_clock(self):
        from datetime import datetime, time as t
        from decimal import Decimal as D
        from django.utils import timezone as tz
        import zoneinfo
        from portal.models import AttendanceEvent, OrganizationNode
        from portal.services import employee_timezone

        site = OrganizationNode.objects.create(name='Kolkata Unit', node_type='Factory',
                                               timezone='Asia/Kolkata')
        employee = Employee.objects.create(employee_id='OP-TZ4', name='Cross Border',
                                           role='Operator', category='OPERATOR',
                                           daily_cost=D('480'), scope=site)
        self.assertEqual(employee_timezone(employee), zoneinfo.ZoneInfo('Asia/Kolkata'))

        # A local 08:00-17:00 day in the site's own clock is a full shift.
        day = _working_day()
        zone = zoneinfo.ZoneInfo('Asia/Kolkata')
        for name, at in [('CHECK_IN', t(8, 0)), ('BREAK_IN', t(13, 0)),
                         ('BREAK_OUT', t(14, 0)), ('CHECK_OUT', t(17, 0))]:
            AttendanceEvent.objects.create(
                employee=employee, event=name,
                occurred_at=datetime.combine(day, at).replace(tzinfo=zone))
        summary = calculate_attendance_day(employee, day)
        self.assertEqual(summary.worked_minutes, 480)
        self.assertEqual(summary.late_minutes, 0)

    def test_an_unscoped_employee_falls_back_to_the_project_clock(self):
        from decimal import Decimal as D
        from django.utils import timezone as tz
        from portal.services import employee_timezone
        employee = Employee.objects.create(employee_id='OP-TZ5', name='No Site',
                                           role='Operator', category='OPERATOR',
                                           daily_cost=D('480'))
        self.assertEqual(employee_timezone(employee), tz.get_current_timezone())

    def test_an_invalid_timezone_does_not_stop_payroll(self):
        from decimal import Decimal as D
        from django.utils import timezone as tz
        from portal.models import OrganizationNode
        from portal.services import employee_timezone
        site = OrganizationNode.objects.create(name='Typo Site', node_type='Factory',
                                               timezone='Not/AZone')
        employee = Employee.objects.create(employee_id='OP-TZ6', name='Typo',
                                           role='Operator', category='OPERATOR',
                                           daily_cost=D('480'), scope=site)
        self.assertEqual(employee_timezone(employee), tz.get_current_timezone())


class EnvironmentConfigTests(TestCase):
    """Configuration wiring. Every value in .env must actually reach a setting.

    The proposed .env used a second vocabulary - SECRET_KEY, DEBUG, DB_ENGINE,
    DEFAULT_CURRENCY - and asked for email, WhatsApp and OpenAI settings that
    nothing in the project read. Writing those names into the file alone would
    have left all of them inert, so settings.py accepts both spellings and these
    tests hold that contract.
    """

    def test_env_prefers_the_canonical_name_then_falls_back(self):
        import os
        from unittest.mock import patch
        from core.settings import env
        with patch.dict(os.environ, {'DJANGO_SECRET_KEY': 'canonical',
                                     'SECRET_KEY': 'short'}, clear=False):
            self.assertEqual(env('DJANGO_SECRET_KEY', 'SECRET_KEY'), 'canonical')
        with patch.dict(os.environ, {'SECRET_KEY': 'short'}, clear=False):
            os.environ.pop('DJANGO_SECRET_KEY', None)
            self.assertEqual(env('DJANGO_SECRET_KEY', 'SECRET_KEY'), 'short')

    def test_env_treats_an_empty_value_as_unset(self):
        import os
        from unittest.mock import patch
        from core.settings import env
        with patch.dict(os.environ, {'A_TEST_VAR': '   '}, clear=False):
            self.assertEqual(env('A_TEST_VAR', default='fallback'), 'fallback')

    def test_boolean_flags_accept_the_spellings_people_write(self):
        import os
        from unittest.mock import patch
        from core.settings import _flag
        for value in ('1', 'true', 'True', 'TRUE', 'yes', 'on'):
            with self.subTest(value=value):
                with patch.dict(os.environ, {'A_TEST_FLAG': value}, clear=False):
                    self.assertTrue(_flag('A_TEST_FLAG'))
        for value in ('0', 'false', 'False', 'no', 'off', ''):
            with self.subTest(value=value):
                with patch.dict(os.environ, {'A_TEST_FLAG': value}, clear=False):
                    self.assertFalse(_flag('A_TEST_FLAG'))

    def test_int_falls_back_rather_than_crashing_on_junk(self):
        import os
        from unittest.mock import patch
        from core.settings import _int
        with patch.dict(os.environ, {'A_TEST_INT': 'not-a-number'}, clear=False):
            self.assertEqual(_int('A_TEST_INT', default=42), 42)

    def test_every_setting_the_example_declares_is_read_by_settings(self):
        """A variable documented in .env.example but never read is a trap: it
        looks configured and does nothing."""
        import re
        from pathlib import Path
        from django.conf import settings

        example = Path('.env.example').read_text(encoding='utf-8')
        declared = {m.group(1) for m in
                    re.finditer(r'^([A-Z][A-Z0-9_]*)=', example, re.M)}
        source = Path('core/settings.py').read_text(encoding='utf-8')
        seeder = Path('portal/management/commands/seed_project1.py').read_text(encoding='utf-8')
        backup = Path('scripts/backup.sh').read_text(encoding='utf-8')
        consumers = source + seeder + backup

        unread = sorted(name for name in declared if name not in consumers)
        self.assertEqual(unread, [],
                         'documented in .env.example but read by nothing: '
                         + ', '.join(unread))

    def test_email_is_configured(self):
        from django.conf import settings
        # There was no email configuration at all, so the Communication Center's
        # EMAIL channel had no transport.
        self.assertTrue(settings.EMAIL_BACKEND)
        self.assertTrue(settings.DEFAULT_FROM_EMAIL)
        self.assertIsInstance(settings.EMAIL_PORT, int)
        self.assertIsInstance(settings.EMAIL_USE_TLS, bool)

    def test_the_integration_placeholders_exist_but_are_empty_by_default(self):
        from django.conf import settings
        # Declared so credentials live in the environment rather than in source,
        # but nothing sends through them yet and none should be set by default.
        for name in ('WHATSAPP_ACCESS_TOKEN', 'WHATSAPP_PHONE_NUMBER_ID',
                     'WHATSAPP_BUSINESS_ACCOUNT_ID', 'OPENAI_API_KEY'):
            with self.subTest(name=name):
                self.assertTrue(hasattr(settings, name))

    def test_placeholder_secrets_are_blocked_out_of_debug(self):
        from core.settings import _PUBLISHED_DEV_SECRETS
        for placeholder in ('unsafe-dev', 'local-dev-change-before-production',
                            'CHANGE_THIS_TO_A_LONG_RANDOM_SECRET'):
            with self.subTest(placeholder=placeholder):
                self.assertIn(placeholder, _PUBLISHED_DEV_SECRETS)

    def test_sqlite_is_selectable_without_postgres(self):
        """The project previously could not run at all without Postgres and
        psycopg, which made it awkward to try anything without Docker."""
        from pathlib import Path
        source = Path('core/settings.py').read_text(encoding='utf-8')
        self.assertIn("django.db.backends.sqlite3", source)
        self.assertIn("DB_ENGINE", source)


class PublicRecruitmentHardeningTests(TestCase):
    """Controls on the public /careers/ endpoint and the HR decision path.

    /careers/ is the only unauthenticated route that writes rows and stores
    uploaded files, and the HR screen that decides those applications is the
    only place outside api_approval_decision that settles an ApprovalRequest.
    Both arrived without the protections the rest of the platform relies on: no
    rate limit, no server-side validation, and a decision path that wrote
    ApprovalRequest.status directly - skipping the role check, the
    already-decided check and the append-only log that
    TECHNICAL_ASSESSMENT.md 4.1 exists to guarantee.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import tempfile
        from django.test import override_settings
        # Uploads must not land in the working tree when the suite runs.
        cls._media = tempfile.mkdtemp(prefix='er-test-media-')
        cls._override = override_settings(MEDIA_ROOT=cls._media)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        import shutil
        cls._override.disable()
        shutil.rmtree(cls._media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        from django.core.cache import cache
        from portal.models import HRVacancy
        # LocMemCache is shared across tests in a process; a counter left by
        # another test would make the throttle assertions meaningless.
        cache.clear()
        self.vacancy = HRVacancy.objects.create(
            reference='VAC-TEST-1', title='Sewing Operator',
            description='Machine operator, Limerick.', status='PUBLISHED')

    def _apply(self, client=None, **overrides):
        from django.core.files.uploadedfile import SimpleUploadedFile
        data = {'vacancy_id': self.vacancy.pk, 'candidate_name': 'Jane Doe',
                'email': 'jane@example.com', 'phone': '+353 1 234',
                'country': 'Ireland', 'consent': 'yes'}
        data.update(overrides)
        data.setdefault('cv', SimpleUploadedFile('cv.pdf', b'%PDF-1.4 test'))
        return (client or Client()).post('/careers/', data)

    # --- rate limiting -----------------------------------------------------

    def test_public_applications_are_rate_limited_per_ip(self):
        from django.test import override_settings
        from portal.models import HRRecruitment
        with override_settings(CAREERS_SUBMISSION_LIMIT=3,
                               CAREERS_SUBMISSION_WINDOW_SECONDS=60):
            client = Client()
            codes = [self._apply(client, email='c{}@example.com'.format(i)).status_code
                     for i in range(9)]
        self.assertEqual(codes.count(429), 6, codes)
        self.assertEqual(HRRecruitment.objects.count(), 3)
        # The approval queue is bounded with it, not just the application table.
        self.assertEqual(ApprovalRequest.objects.count(), 3)

    def test_rejected_submissions_also_count_towards_the_limit(self):
        """Otherwise a loop of deliberately invalid posts would never trip it."""
        from django.test import override_settings
        from portal.models import HRRecruitment
        with override_settings(CAREERS_SUBMISSION_LIMIT=2,
                               CAREERS_SUBMISSION_WINDOW_SECONDS=60):
            client = Client()
            codes = [client.post('/careers/', {'vacancy_id': self.vacancy.pk,
                                               'consent': 'no'}).status_code
                     for _ in range(5)]
        self.assertEqual(codes.count(429), 3, codes)
        self.assertEqual(HRRecruitment.objects.count(), 0)

    def test_throttle_fails_open_when_the_cache_is_unavailable(self):
        """A security control must not become an outage - same rule as /login/."""
        from unittest import mock
        with mock.patch('portal.views.cache.get', side_effect=RuntimeError('cache down')), \
             mock.patch('portal.views.cache.add', side_effect=RuntimeError('cache down')):
            response = self._apply()
        self.assertEqual(response.status_code, 200)

    # --- server-side validation --------------------------------------------

    def test_over_length_input_is_rejected_and_nothing_is_stored(self):
        """candidate_name is max_length=180. PostgreSQL raises DataError on
        overflow, which is not an IntegrityError and so was not in
        EXPECTED_POST_ERRORS: one long field from an anonymous visitor was an
        unhandled 500."""
        from portal.models import HRRecruitment
        response = self._apply(candidate_name='N' * 5000)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(HRRecruitment.objects.count(), 0)
        self.assertContains(response, 'at most 180 characters')

    def test_invalid_email_is_rejected(self):
        from portal.models import HRRecruitment
        response = self._apply(email='definitely not an email')
        self.assertEqual(HRRecruitment.objects.count(), 0)
        self.assertContains(response, 'valid email address')

    def test_a_valid_application_is_still_accepted(self):
        from portal.models import HRRecruitment
        response = self._apply()
        self.assertEqual(response.status_code, 200)
        application = HRRecruitment.objects.get()
        self.assertEqual(application.status, 'PENDING_APPROVAL')
        self.assertTrue(application.application_reference.startswith('ER-APP-'))
        self.assertIsNotNone(application.approval)
        self.assertEqual(application.approval.approval_type, 'RECRUITMENT_APPLICATION')

    def test_a_failed_submission_does_not_report_success(self):
        response = self._apply(candidate_name='N' * 5000)
        self.assertNotContains(response, 'APPLICATION RECEIVED')

    # --- the HR decision path ----------------------------------------------

    def _application(self):
        from portal.models import HRRecruitment
        self._apply()
        return HRRecruitment.objects.get()

    def _decide(self, user, application, decision):
        return _client_for(user).post('/hr-dashboard/', {
            'action': 'recruitment_decision', 'application_id': application.pk,
            'decision': decision})

    def test_hr_decision_is_written_to_the_append_only_log(self):
        from portal import roles
        hr = _make_user('hr-decider', roles.HR_MANAGER)
        application = self._application()
        self._decide(hr, application, 'REJECTED')
        application.refresh_from_db()
        application.approval.refresh_from_db()
        self.assertEqual(application.status, 'REJECTED')
        self.assertEqual(application.approval.status, 'REJECTED')
        log = ApprovalDecisionLog.objects.get(approval_id=application.approval_id)
        self.assertEqual((log.previous_status, log.decision), ('PENDING', 'REJECTED'))
        self.assertEqual(log.decided_by, hr)
        self.assertIn('HR Manager', log.approver_roles)

    def test_a_decided_recruitment_approval_cannot_be_flipped(self):
        """The regression this class exists for: rejecting an application and
        then posting ACTIVE used to flip the settled approval to APPROVED and
        record nothing anywhere."""
        from portal import roles
        hr = _make_user('hr-flipper', roles.HR_MANAGER)
        application = self._application()
        self._decide(hr, application, 'REJECTED')
        self._decide(hr, application, 'ACTIVE')
        application.refresh_from_db()
        application.approval.refresh_from_db()
        self.assertEqual(application.approval.status, 'REJECTED')
        self.assertEqual(application.status, 'REJECTED')
        self.assertEqual(ApprovalDecisionLog.objects.filter(
            approval_id=application.approval_id).count(), 1)

    def test_on_hold_leaves_the_approval_pending_and_still_decidable(self):
        from portal import roles
        hr = _make_user('hr-holder', roles.HR_MANAGER)
        application = self._application()
        self._decide(hr, application, 'ON_HOLD')
        application.refresh_from_db()
        application.approval.refresh_from_db()
        self.assertEqual(application.status, 'ON_HOLD')
        self.assertEqual(application.approval.status, 'PENDING')
        self.assertEqual(ApprovalDecisionLog.objects.count(), 0)
        # A held application can still be taken forward later.
        self._decide(hr, application, 'ACTIVE')
        application.refresh_from_db()
        application.approval.refresh_from_db()
        self.assertEqual(application.status, 'ACTIVE')
        self.assertEqual(application.approval.status, 'APPROVED')

    def test_a_manager_outside_hr_cannot_decide_a_recruitment_approval(self):
        """RECRUITMENT_* is narrowed to HR rather than left on the sixteen-role
        management default, because the decision ends in a real Employee record
        and a portal account."""
        from portal import roles
        shipping = _make_user('ship-decider', roles.SHIPPING_MANAGER)
        application = self._application()
        self._decide(shipping, application, 'ACTIVE')
        application.refresh_from_db()
        application.approval.refresh_from_db()
        self.assertEqual(application.approval.status, 'PENDING')
        self.assertEqual(application.status, 'PENDING_APPROVAL')
        self.assertEqual(ApprovalDecisionLog.objects.count(), 0)

    def test_no_view_settles_an_approval_outside_the_shared_control(self):
        """Guard against a future caller assigning ApprovalRequest.status inline
        again and quietly skipping the four rules."""
        from pathlib import Path
        import re
        source = Path('portal/views.py').read_text(encoding='utf-8')
        # Matches a dotted path too, so app.approval.status = 'APPROVED' is
        # caught and not just approval.status = 'APPROVED'.
        pattern = re.compile(r'^\s*[\w.]*approval[\w.]*\.status\s*=\s*[\'"](APPROVED|REJECTED)[\'"]',
                             re.IGNORECASE)
        offenders = [line.strip() for line in source.splitlines() if pattern.match(line)]
        self.assertEqual(offenders, [],
                         'ApprovalRequest.status assigned outside '
                         '_apply_approval_decision:\n' + '\n'.join(offenders))


# ---------------------------------------------------------------------------
# Site audit - regression gates
#
# Every test here fails on a defect listed in SITE_AUDIT_FINDINGS.md. The suite
# already had RouteSmokeTests, which requests every non-parameterised route and
# every registered page - but it asserts only `status < 500`, so a 404 passed,
# and it runs solely as a superuser, so no role-gated refusal was ever
# exercised. That is exactly why 27 dead links and six unstyled pages shipped.
# ---------------------------------------------------------------------------

def _all_templates():
    from pathlib import Path
    from django.conf import settings
    files = []
    for d in settings.TEMPLATES[0]['DIRS']:
        files.extend(sorted(Path(d).rglob('*.html')))
    return files


class LinkIntegrityTests(TestCase):
    """Every link a template emits must resolve.

    report_master.html linked 27 times to /page/<slug>/ while the placeholder
    route is /p/. Because they were literal href strings rather than {% url %}
    tags, nothing detected it: the template compiled, the route smoke test
    passed (the page itself is fine), and only a human clicking a link would
    find out. See SITE_AUDIT_FINDINGS.md A1.
    """

    IGNORE_PREFIXES = ('http://', 'https://', 'mailto:', 'tel:', '#', '{{', '{%',
                       'javascript:', 'data:')

    def _literal_hrefs(self):
        """(template, href) for every literal internal href in the tree."""
        import re
        found = []
        pattern = re.compile(r'(?:href|action)="(/[^"]*)"')
        for path in _all_templates():
            text = path.read_text(encoding='utf-8')
            for match in pattern.finditer(text):
                href = match.group(1)
                if href.startswith(self.IGNORE_PREFIXES):
                    continue
                line = text[:match.start()].count('\n') + 1
                found.append((f'{path.name}:{line}', href))
        return found

    def test_every_literal_internal_link_resolves(self):
        from django.urls import Resolver404, resolve
        broken = []
        for where, href in self._literal_hrefs():
            path = href.split('?', 1)[0].split('#', 1)[0]
            if '{{' in path or '{%' in path:
                # A template expression; covered by the rendering tests instead.
                continue
            try:
                resolve(path)
            except Resolver404:
                broken.append(f'{where}: {href} matches no route')
        self.assertEqual(
            broken, [],
            'templates link to paths that no route serves:\n' + '\n'.join(broken))

    def test_no_template_builds_a_placeholder_path_by_hand(self):
        """The /p/<slug>/ prefix must come from {% url %} or the view.

        Hand-building it is what produced the /page/ typo. Catch the shape, not
        just the one instance.
        """
        import re
        offenders = []
        for path in _all_templates():
            text = path.read_text(encoding='utf-8')
            for match in re.finditer(r'(?:href|action)="(/p(?:age)?/\{\{[^"]*)"', text):
                line = text[:match.start()].count('\n') + 1
                offenders.append(f'{path.name}:{line}: {match.group(1)}')
        self.assertEqual(
            offenders, [],
            'placeholder paths built by string interpolation - use the url the '
            'view resolves from portal/module_routes.py:\n' + '\n'.join(offenders))


class ModuleRouteTests(TestCase):
    """portal/module_routes.py must stay in step with the registry and the URLs.

    The mapping used to live in three hand-maintained places that had drifted:
    a 19-line if-chain in page_view that stopped at poly-dashboard, an inline
    {% if %} in dashboard.html, and a hardcoded prefix in report_master.html.
    See SITE_AUDIT_FINDINGS.md A2, A3, B26.
    """

    def _registry_slugs(self):
        import json
        from pathlib import Path
        from django.conf import settings
        data = json.loads(
            (settings.BASE_DIR / 'data/page_registry.json').read_text(encoding='utf-8'))
        return {entry['slug'] for entry in data}

    def test_every_mapped_slug_is_a_registry_slug(self):
        from portal.module_routes import MODULE_ROUTES
        unknown = sorted(set(MODULE_ROUTES) - self._registry_slugs())
        self.assertEqual(
            unknown, [],
            'MODULE_ROUTES maps slugs that are not in data/page_registry.json, so '
            'the rule can never fire (this is how the dead sewing-dashboard rule '
            f'survived): {unknown}')

    def test_every_mapped_route_reverses(self):
        from django.urls import NoReverseMatch, reverse
        from portal.module_routes import MODULE_ROUTES
        broken = []
        for slug, name in sorted(MODULE_ROUTES.items()):
            try:
                reverse(name)
            except NoReverseMatch:
                broken.append(f'{slug} -> {name}')
        self.assertEqual(broken, [], 'MODULE_ROUTES names routes that do not exist:\n'
                                     + '\n'.join(broken))

    def test_a_module_with_a_real_page_is_not_sent_to_the_placeholder(self):
        """A slug with a dedicated dashboard must redirect, not render page.html.

        Eleven modules with complete dashboards - iron, final QC, finishing,
        packing, shipping, supplier, procurement, purchases, sourcing, account
        master and CEO - used to land on the stub.
        """
        from django.core.management import call_command
        call_command('seed_project1', verbosity=0)
        user = User.objects.filter(is_superuser=True).first()
        user.set_password('link-test-pw')
        user.save()
        client = Client()
        self.assertTrue(client.login(username=user.username, password='link-test-pw'))

        for slug in ['ceo-dashboard', 'account-master', 'iron-dashboard',
                     'final-qc-dashboard', 'finishing-dashboard', 'packing-dashboard',
                     'shipping-dashboard', 'supplier-dashboard', 'procurement-dashboard',
                     'purchases-dashboard', 'sourcing-dashboard', 'sewing']:
            with self.subTest(slug=slug):
                response = client.get(f'/p/{slug}/')
                self.assertEqual(
                    response.status_code, 302,
                    f'/p/{slug}/ should redirect to its real dashboard, not render '
                    'the placeholder')


class NavigationTests(TestCase):
    """The navbar must never offer a link the request would then refuse.

    base.html hardcoded thirteen links and showed all of them to everyone. Five
    were role-gated: a Helper, Operator or Staff member saw ACCOUNT MASTER,
    REPORT MASTER, CEO REPORT, Stock & Material and SEWING MASTER and was
    refused by all five, landing on a bare-text 403 with no layout and no way
    back. See SITE_AUDIT_FINDINGS.md B12.
    """

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command
        call_command('seed_project1', verbosity=0)

    def test_every_nav_entry_is_a_classified_route(self):
        from django.urls import NoReverseMatch, reverse
        from portal.authorization import ROUTE_POLICY
        from portal.navigation import NAV
        problems = []
        for url_name, label, _group in NAV:
            if url_name not in ROUTE_POLICY:
                problems.append(f'{url_name} ({label}) is not in ROUTE_POLICY')
                continue
            try:
                reverse(url_name)
            except NoReverseMatch:
                problems.append(f'{url_name} ({label}) does not reverse')
        self.assertEqual(problems, [], 'navigation is out of step with the routes:\n'
                                       + '\n'.join(problems))

    def test_no_role_is_shown_a_link_it_cannot_open(self):
        """The decisive test: for all 20 roles, follow every link the navbar
        renders and assert none of them is refused."""
        from portal.roles import ALL_ROLES
        failures = []
        for index, role in enumerate(sorted(ALL_ROLES)):
            user = _make_user(f'navuser{index}', role=role)
            client = _client_for(user)
            response = client.get('/dashboard/', follow=True)
            self.assertEqual(response.status_code, 200, f'{role} cannot open /dashboard/')
            links = {item['url'] for item in response.context['nav_top']}
            for group in response.context['nav_groups']:
                links.update(item['url'] for item in group['items'])
            for url in sorted(links):
                status = client.get(url).status_code
                if status in (403, 404) or status >= 500:
                    failures.append(f'{role}: navbar offers {url} -> HTTP {status}')
        self.assertEqual(
            failures, [],
            'the navbar offered links that were then refused:\n' + '\n'.join(failures))

    def test_a_limited_role_sees_fewer_links_than_an_executive(self):
        """Guards against the filter being wired up but inert."""
        helper = _client_for(_make_user('navhelper', role='Helper'))
        ceo = _client_for(_make_user('navceo', role='CEO'))

        def link_count(client):
            ctx = client.get('/dashboard/', follow=True).context
            n = len(ctx['nav_top'])
            for group in ctx['nav_groups']:
                n += len(group['items'])
            return n

        helper_links, ceo_links = link_count(helper), link_count(ceo)
        self.assertLess(
            helper_links, ceo_links,
            f'Helper sees {helper_links} links and CEO sees {ceo_links}: the '
            'role filter is not discriminating')

    def test_every_navigable_route_is_reachable_from_the_navbar(self):
        """Nine complete department dashboards had no inbound link anywhere.

        Final QC, finishing, iron, packing, procurement, purchases, shipping,
        sourcing and supplier could only be reached by typing the URL, so the
        staff who work in those departments had no path to their own page.
        See SITE_AUDIT_FINDINGS.md A2.
        """
        from portal.navigation import NAV
        navigable = {name for name, _l, _g in NAV}
        must_be_linked = [
            'final_qc_dashboard', 'finishing_dashboard', 'iron_dashboard',
            'packing_dashboard', 'procurement_dashboard', 'purchases_dashboard',
            'shipping_dashboard', 'sourcing_dashboard', 'supplier_dashboard',
        ]
        missing = [n for n in must_be_linked if n not in navigable]
        self.assertEqual(missing, [], f'department dashboards with no nav entry: {missing}')


class ContentSecurityPolicyTests(TestCase):
    """No template may rely on an inline event handler or inline script.

    nginx sets `script-src 'self'` with no 'unsafe-inline', so 41 inline onclick
    attributes across 34 templates - every Print button, every modal open and
    close, the sewing production dialog, the public Apply Now button and the
    command-centre auto-refresh - were dead in production while working under
    runserver, where there is no CSP. See SITE_AUDIT_FINDINGS.md A5.
    """

    def test_no_template_uses_an_inline_event_handler(self):
        import re
        pattern = re.compile(r'\son(?:click|change|submit|input|load|focus|blur|'
                             r'keydown|keyup|mouseover|mouseout)\s*=')
        offenders = []
        for path in _all_templates():
            text = path.read_text(encoding='utf-8')
            for match in pattern.finditer(text):
                line = text[:match.start()].count('\n') + 1
                offenders.append(f'{path.name}:{line}: {text[match.start():match.start()+50].strip()}')
        self.assertEqual(
            offenders, [],
            "inline event handlers are blocked by the production CSP "
            "(script-src 'self'). Use data-action and static/js/app.js:\n"
            + '\n'.join(offenders))

    def test_no_template_contains_an_inline_script_block(self):
        import re
        offenders = []
        for path in _all_templates():
            text = path.read_text(encoding='utf-8')
            for match in re.finditer(r'<script(?![^>]*\bsrc=)[^>]*>\s*\S', text):
                line = text[:match.start()].count('\n') + 1
                offenders.append(f'{path.name}:{line}')
        self.assertEqual(
            offenders, [],
            'inline <script> blocks are blocked by the production CSP. Move the '
            'code into static/js/ and load it with src:\n' + '\n'.join(offenders))

    def test_the_csp_has_not_been_loosened_to_paper_over_this(self):
        """If someone adds 'unsafe-inline' to script-src, say so loudly."""
        from pathlib import Path
        from django.conf import settings
        conf = (settings.BASE_DIR / 'nginx/default.conf').read_text(encoding='utf-8')
        script_src = ''
        for part in conf.split(';'):
            if 'script-src' in part:
                script_src = part
        self.assertIn('script-src', script_src, 'no script-src in the CSP')
        self.assertNotIn(
            'unsafe-inline', script_src,
            "script-src has been given 'unsafe-inline'. That re-permits inline "
            'handlers instead of fixing them, and re-opens the injection path on '
            'the public homepage.')


class StylesheetIntegrityTests(TestCase):
    """Every class a page uses must be defined somewhere that page loads.

    Six role-gated pages - both profit gates, both buyer pages, the free-capacity
    page and the Communication Center - used page-head, kpis, kpi, table-wrap,
    primary, form-grid and grid-2, none of which existed in any stylesheet or
    inline <style>. They rendered as unstyled stacked text: no KPI cards, no page
    header, no table wrapper. See SITE_AUDIT_FINDINGS.md A6.
    """

    def _defined_classes(self, css_text):
        import re
        return set(re.findall(r'\.([A-Za-z][\w-]*)', css_text))

    def test_no_template_uses_an_undefined_class(self):
        import re
        from pathlib import Path
        from django.conf import settings
        static_root = settings.BASE_DIR / 'static'
        app_css = self._defined_classes(
            (static_root / 'css/app.css').read_text(encoding='utf-8'))

        offenders = []
        for path in _all_templates():
            text = path.read_text(encoding='utf-8')
            available = set()
            # Stylesheets this page loads by name.
            for sheet in re.findall(r"static '(css/[^']+)'", text):
                target = static_root / sheet
                if target.is_file():
                    available |= self._defined_classes(
                        target.read_text(encoding='utf-8'))
            # app.css comes with the base layout.
            if '{% extends' in text:
                available |= app_css
            # Anything the page styles itself.
            for block in re.findall(r'<style[^>]*>(.*?)</style>', text, re.S):
                available |= self._defined_classes(block)

            used = set()
            for match in re.finditer(r'class="([^"{}]*)"', text):
                used |= set(match.group(1).split())
            for missing in sorted(used - available):
                offenders.append(f'{path.name}: .{missing}')

        self.assertEqual(
            offenders, [],
            f'{len(offenders)} class(es) are used by a page that defines them '
            'nowhere it loads, so that markup renders unstyled:\n'
            + '\n'.join(offenders))


class MessageDisplayTests(TestCase):
    """A view that reports something must be able to show it.

    151 messages.* calls are made across 33 views and base.html had no block to
    render them, so 139 of them produced nothing at all - an operator saving a
    production entry, recording QC or issuing material got no confirmation and,
    on the failure paths, no error either. See SITE_AUDIT_FINDINGS.md A4.
    """

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command
        call_command('seed_project1', verbosity=0)

    def test_the_base_layout_renders_messages(self):
        from pathlib import Path
        from django.conf import settings
        base = (settings.BASE_DIR / 'templates/base.html').read_text(encoding='utf-8')
        self.assertIn('{% if messages %}', base,
                      'base.html does not render the messages framework, so every '
                      'messages.success/error call on a page that extends it is '
                      'invisible')
        self.assertIn('{% for message in messages %}', base)

    def test_a_message_renders_with_its_severity(self):
        """The layout must show the text AND distinguish an error from a success.

        Before this, the 26 dashboards that did render messages did so as a bare
        <div class="card">, so a failed save looked exactly like a successful one.
        """
        from django.template.loader import render_to_string

        class FakeMessage:
            def __init__(self, tags, text):
                self.tags = tags
                self.level_tag = tags
                self._text = text

            def __str__(self):
                return self._text

        html = render_to_string('403.html', {
            'messages': [
                FakeMessage('success', 'Production entry saved for bundle 12345.'),
                FakeMessage('error', 'Quantity exceeds the bundle size.'),
            ],
        })
        self.assertIn('Production entry saved for bundle 12345.', html,
                      'the layout does not render message text')
        self.assertIn('Quantity exceeds the bundle size.', html)
        self.assertIn('message-success', html,
                      'a success is not distinguishable from an error')
        self.assertIn('message-error', html)

    def test_every_template_that_renders_messages_is_consistent(self):
        """A page must not render messages twice, once itself and once via base."""
        import re
        offenders = []
        for path in _all_templates():
            if path.name == 'base.html':
                continue
            text = path.read_text(encoding='utf-8')
            if '{% extends' in text and 'in messages' in text:
                offenders.append(path.name)
        self.assertEqual(
            offenders, [],
            'these pages extend base.html and also render messages themselves, so '
            f'every message appears twice: {offenders}')


class AccessibilityBaselineTests(TestCase):
    """Counted baselines that must not regress.

    The audit found 0 label-for bindings across 1,404 form controls, 0 th scope
    across 641 headers, and no lang attribute on the layout used by 36 pages.
    These tests hold the ground that has been recovered.
    """

    def test_the_base_layout_declares_a_language(self):
        from django.conf import settings
        base = (settings.BASE_DIR / 'templates/base.html').read_text(encoding='utf-8')
        self.assertRegex(base, r'<html[^>]*\slang=',
                         'base.html has no lang attribute, so every page that '
                         'extends it declares no language to a screen reader')

    def test_the_base_layout_offers_a_skip_link(self):
        from django.conf import settings
        base = (settings.BASE_DIR / 'templates/base.html').read_text(encoding='utf-8')
        self.assertIn('skip-link', base)
        self.assertIn('id="main"', base)

    def test_every_image_has_an_alt_attribute(self):
        import re
        offenders = []
        for path in _all_templates():
            text = path.read_text(encoding='utf-8')
            for match in re.finditer(r'<img\b[^>]*>', text):
                if not re.search(r'\salt=', match.group(0)):
                    line = text[:match.start()].count('\n') + 1
                    offenders.append(f'{path.name}:{line}')
        self.assertEqual(offenders, [], 'images with no alt attribute:\n'
                                        + '\n'.join(offenders))

    def test_focus_styling_exists_in_the_global_stylesheet(self):
        from django.conf import settings
        css = (settings.BASE_DIR / 'static/css/app.css').read_text(encoding='utf-8')
        self.assertIn(':focus-visible', css,
                      'app.css defines no focus styling, so a keyboard user has '
                      'no visible cursor on the 36 pages it serves')

    def test_no_stylesheet_removes_the_focus_ring(self):
        import re
        from django.conf import settings
        offenders = []
        for sheet in sorted((settings.BASE_DIR / 'static/css').glob('*.css')):
            text = sheet.read_text(encoding='utf-8')
            for match in re.finditer(r'outline\s*:\s*(none|0)\b', text):
                offenders.append(f'{sheet.name}: {match.group(0)}')
        self.assertEqual(offenders, [],
                         'a stylesheet removes the focus outline:\n' + '\n'.join(offenders))


class RolePageRenderTests(TestCase):
    """Render pages as a real role, not as a superuser.

    RouteSmokeTests logs in as the seeded superuser, which passes every
    authorisation check, so no role-gated path was ever exercised and a refusal
    page was never seen.
    """

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command
        call_command('seed_project1', verbosity=0)

    def test_a_refused_page_is_a_styled_page_not_bare_text(self):
        """Every role hitting a link it lacks used to land on a bare-text 403."""
        client = _client_for(_make_user('refuseduser', role='Helper'))
        response = client.get('/ceo-dashboard/')
        self.assertEqual(response.status_code, 403)
        body = response.content.decode()
        self.assertIn('<html', body.lower(),
                      'the 403 is bare text with no layout, no branding and no way '
                      'back to the application')
        self.assertIn('href', body,
                      'the 403 page offers no link back')

    def test_each_role_can_render_its_own_landing_page(self):
        from portal.roles import ALL_ROLES
        failures = []
        for index, role in enumerate(sorted(ALL_ROLES)):
            client = _client_for(_make_user(f'landing{index}', role=role))
            response = client.get('/dashboard/', follow=True)
            if response.status_code != 200:
                failures.append(f'{role}: HTTP {response.status_code}')
        self.assertEqual(failures, [], 'roles that cannot open the landing page:\n'
                                       + '\n'.join(failures))

    def test_the_placeholder_does_not_claim_to_be_active(self):
        """It badged every unbuilt module with a green ACTIVE pill and six
        buttons that issued no request. See SITE_AUDIT_FINDINGS.md A3."""
        user = User.objects.filter(is_superuser=True).first()
        user.set_password('placeholder-pw')
        user.save()
        client = Client()
        self.assertTrue(client.login(username=user.username, password='placeholder-pw'))
        from portal.models import DashboardPage
        from portal.module_routes import MODULE_ROUTES
        unbuilt = (DashboardPage.objects.filter(enabled=True)
                   .exclude(slug__in=MODULE_ROUTES).first())
        self.assertIsNotNone(unbuilt, 'no unbuilt module to check')
        body = client.get(f'/p/{unbuilt.slug}/').content.decode()
        self.assertNotIn('>ACTIVE<', body,
                         'an unbuilt module still presents itself as ACTIVE')
        self.assertIn('NOT BUILT YET', body)


class AdminConfigurationTests(TestCase):
    """For most of the 179 models the admin is the only editing UI there is.

    It was 33 bare register() calls with zero list_display, zero search_fields,
    zero list_filter and zero date_hierarchy across every model, and eleven
    models - the whole Iron module among them - were not registered at all, so
    their data could not be reached anywhere.
    See SITE_AUDIT_FINDINGS.md B15, B16, B17.
    """

    def _models(self):
        from django.apps import apps
        return list(apps.get_app_config('portal').get_models())

    def test_every_model_is_reachable_in_the_admin(self):
        from django.contrib import admin
        unregistered = sorted(m.__name__ for m in self._models()
                              if m not in admin.site._registry)
        self.assertEqual(
            unregistered, [],
            'these models have no admin and no dedicated page, so their data '
            f'cannot be created or corrected anywhere: {unregistered}')

    def test_the_iron_module_is_registered(self):
        """Every other department was; Iron was the one that was not."""
        from django.contrib import admin
        from portal import models as m
        for model in [m.IronPlan, m.IronBundleScan, m.IronProductionEntry,
                      m.IronQC, m.IronVariance, m.IronAutoReport]:
            with self.subTest(model=model.__name__):
                self.assertIn(model, admin.site._registry)

    def test_the_standards_that_drive_every_efficiency_figure_are_editable(self):
        """FactoryProcessStandard carries sam, smv and cost_per_minute, and
        SewingBundleAssignment carries the per-operator per-machine figures. Both
        were read by views and editable nowhere."""
        from django.contrib import admin
        from portal.models import FactoryProcessStandard, SewingBundleAssignment
        self.assertIn(FactoryProcessStandard, admin.site._registry)
        self.assertIn(SewingBundleAssignment, admin.site._registry)

    def test_exchange_rates_can_be_maintained_by_finance(self):
        """.env.example states rates are "maintained by Finance in admin", and
        with no API URL configured that is the only way in. The model was not
        registered, so there was none. See SITE_AUDIT_FINDINGS.md B15."""
        from django.contrib import admin
        from portal.models import ExchangeRate
        self.assertIn(ExchangeRate, admin.site._registry)
        options = admin.site._registry[ExchangeRate]
        self.assertTrue(options.list_display)
        self.assertIn('rate', options.list_display)

    def test_every_changelist_is_usable(self):
        from django.contrib import admin
        problems = []
        for model in self._models():
            options = admin.site._registry[model]
            if not options.list_display or list(options.list_display) == ['__str__']:
                problems.append(f'{model.__name__}: no list_display')
            if not options.search_fields:
                problems.append(f'{model.__name__}: no search_fields')
        self.assertEqual(
            problems, [],
            'a changelist with no columns is a list of "object (1)", and one with '
            'no search box is unusable once the table has thousands of rows:\n'
            + '\n'.join(problems))

    def test_no_model_renders_as_object_repr(self):
        """161 of 179 models had no __str__, so they appeared as
        "ModelName object (1)" in every changelist and every FK dropdown."""
        problems = []
        for model in self._models():
            instance = model()
            instance.pk = 7
            try:
                label = str(instance)
            except Exception as exc:                       # noqa: BLE001
                # A model-specific __str__ that dereferences an unset FK. Real
                # rows have it set; not a labelling defect.
                if 'RelatedObjectDoesNotExist' in type(exc).__name__:
                    continue
                problems.append(f'{model.__name__}: {type(exc).__name__}')
                continue
            if 'object (' in label:
                problems.append(f'{model.__name__}: {label}')
        self.assertEqual(problems, [], 'models with no readable label:\n'
                                       + '\n'.join(problems))

    def test_the_audit_trail_stays_read_only(self):
        """Registering every model must not have opened the audit tables."""
        from django.contrib import admin
        from portal.models import (ApprovalDecisionLog, AuditLog,
                                   BarcodeScanEvent, FileAccessLog)
        request = None
        for model in [AuditLog, FileAccessLog, ApprovalDecisionLog, BarcodeScanEvent]:
            with self.subTest(model=model.__name__):
                options = admin.site._registry[model]
                self.assertFalse(options.has_add_permission(request))
                self.assertFalse(options.has_change_permission(request))
                self.assertFalse(options.has_delete_permission(request))

    def test_the_admin_is_branded(self):
        from django.contrib import admin
        self.assertIn('Emerald Rozalia', admin.site.site_header)
        self.assertNotIn('Django', admin.site.site_header)

    def test_no_wrong_plural_reaches_the_admin(self):
        """Django pluralises by appending "s", which gives "BuyerOpportunitys".

        Checked against the correction table rather than a suffix rule: "cutting
        lays" and "attendance holidays" are correct English and a naive
        endswith('ys') test flags them.
        """
        from portal.admin import _PLURALS
        offenders = []
        for model in self._models():
            expected = _PLURALS.get(model.__name__)
            if not expected:
                continue
            actual = str(model._meta.verbose_name_plural)
            if actual != expected:
                offenders.append(f'{model.__name__}: "{actual}" (want "{expected}")')
        self.assertEqual(offenders, [], 'wrong plurals shown in the admin:\n'
                                        + '\n'.join(offenders))


class AdminTenancyTests(TestCase):
    """The admin must not be a way around organisation scoping.

    ModelAdmin.get_queryset uses _default_manager, and every scoped model pins
    default_manager_name='all_objects' - deliberately, so related descriptors and
    cascade deletes see every row. The consequence was that the admin was
    entirely unscoped: any is_staff user saw every organisation's data, though
    the same user is scoped everywhere else. See SITE_AUDIT_FINDINGS.md B16.
    """

    def setUp(self):
        from portal.models import Alert, OrganizationNode, UserProfile
        self.global_node = OrganizationNode.objects.create(
            name='Global', node_type='Global')
        self.bd = OrganizationNode.objects.create(
            name='Bangladesh', node_type='Country', parent=self.global_node)
        self.ie = OrganizationNode.objects.create(
            name='Ireland', node_type='Country', parent=self.global_node)
        Alert.all_objects.create(title='Dhaka line stopped', department='Sewing',
                                 level='RED', scope=self.bd)
        Alert.all_objects.create(title='Limerick order late', department='Sales',
                                 level='RED', scope=self.ie)

        self.bd_user = _make_user('bdadmin')
        self.bd_user.is_staff = True
        self.bd_user.save()
        UserProfile.objects.update_or_create(
            user=self.bd_user, defaults={'role': 'Unit Manager', 'scope': self.bd})

    def test_a_scoped_admin_user_sees_only_their_own_site(self):
        from django.contrib import admin
        from django.test import RequestFactory
        from portal.models import Alert
        from portal.tenancy import scope_context

        options = admin.site._registry[Alert]
        request = RequestFactory().get('/admin/portal/alert/')
        request.user = self.bd_user

        with scope_context(self.bd):
            titles = sorted(str(a) for a in options.get_queryset(request))
        self.assertEqual(
            titles, ['Dhaka line stopped'],
            'the admin changelist showed another organisation\'s records to a '
            f'scoped is_staff user: {titles}')

    def test_a_superuser_still_sees_everything(self):
        from django.contrib import admin
        from django.test import RequestFactory
        from portal.models import Alert
        from portal.tenancy import scope_context

        root = _make_user('rootadmin', superuser=True)
        options = admin.site._registry[Alert]
        request = RequestFactory().get('/admin/portal/alert/')
        request.user = root
        with scope_context(None):
            self.assertEqual(options.get_queryset(request).count(), 2,
                             'the break-glass account must not be scoped')


class AutoReportScheduleTests(TestCase):
    """The 16 department report tables must actually be written.

    Nothing wrote them: ten management commands existed but were referenced by no
    task and no beat entry, seven departments had no command at all, and the
    deploy/*.cron.example files invoked a venv path that does not exist on a
    Docker host. Those dashboard panels were permanently empty while a healthy
    beat container made it look scheduled. See SITE_AUDIT_FINDINGS.md A8.
    """

    def test_the_registry_covers_every_auto_report_model(self):
        from django.apps import apps
        from portal.reporting import REPORTS
        declared = {name for name, _payload in REPORTS}
        actual = {m.__name__ for m in apps.get_app_config('portal').get_models()
                  if m.__name__.endswith('AutoReport')}
        missing = sorted(actual - declared)
        self.assertEqual(
            missing, [],
            'these report tables are read by a dashboard but nothing generates '
            f'them: {missing}')

    def test_every_registry_entry_resolves(self):
        from django.apps import apps
        from portal import views
        from portal.reporting import REPORTS
        problems = []
        for model_name, payload_name in REPORTS:
            try:
                apps.get_model('portal', model_name)
            except LookupError:
                problems.append(f'{model_name}: no such model')
            if not hasattr(views, payload_name):
                problems.append(f'{payload_name}: no such payload function')
        self.assertEqual(problems, [], 'the report registry is out of step:\n'
                                       + '\n'.join(problems))

    def test_every_slot_is_scheduled_in_beat(self):
        from django.conf import settings
        from portal.reporting import SLOTS
        scheduled = {
            tuple(entry.get('args', ()))[0]
            for entry in settings.CELERY_BEAT_SCHEDULE.values()
            if entry['task'] == 'portal.tasks.generate_department_reports'
            and entry.get('args')
        }
        self.assertEqual(
            set(SLOTS), scheduled,
            'a report slot exists that Celery beat never fires: '
            f'{sorted(set(SLOTS) - scheduled)}')

    def test_generating_a_slot_writes_every_department(self):
        from django.core.management import call_command
        from django.apps import apps
        from portal.reporting import REPORTS, generate_all
        call_command('seed_project1', verbosity=0)

        written, failed = generate_all('08:00')
        self.assertEqual(failed, [], f'report generation failed for: {failed}')
        self.assertEqual(len(written), len(REPORTS))

        # And the rows are really there.
        for model_name, _payload in REPORTS:
            with self.subTest(model=model_name):
                model = apps.get_model('portal', model_name)
                self.assertTrue(
                    model.objects.filter(slot='08:00').exists(),
                    f'{model_name} has no row after generation')

    def test_regenerating_the_same_slot_does_not_duplicate(self):
        from django.core.management import call_command
        from portal.models import PackingAutoReport
        from portal.reporting import generate_all
        call_command('seed_project1', verbosity=0)
        generate_all('13:00')
        generate_all('13:00')
        self.assertEqual(PackingAutoReport.objects.filter(slot='13:00').count(), 1,
                         'a catch-up run duplicated the row instead of updating it')

    def test_no_stale_cron_examples_remain(self):
        """Following them alongside the beat service would run every report twice."""
        from django.conf import settings
        stale = sorted(p.name for p in (settings.BASE_DIR / 'deploy').glob('*.cron.example'))
        self.assertEqual(
            stale, [],
            'these cron examples invoke a venv path that does not exist on a '
            f'Docker host and duplicate the beat schedule: {stale}')


class PasswordChangeTests(TestCase):
    """Every user must be able to rotate their own password.

    There were zero password routes. The only path was /admin/password_change/,
    which requires is_staff - and roles.py documents that is_staff must not
    confer business access. An Operator or Helper could not change their own
    password and there was no reset flow. See SITE_AUDIT_FINDINGS.md A10.
    """

    def test_a_helper_can_open_the_password_page(self):
        client = _client_for(_make_user('pwhelper', role='Helper'))
        response = client.get('/account/password/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Change your password')

    def test_a_helper_can_change_their_own_password(self):
        user = _make_user('pwchanger', role='Helper')
        client = _client_for(user)
        response = client.post('/account/password/', {
            'old_password': 'pw-for-tests-1234',
            'new_password1': 'Kh4ki-Bundle-Ledger-77',
            'new_password2': 'Kh4ki-Bundle-Ledger-77',
        })
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.check_password('Kh4ki-Bundle-Ledger-77'))

    def test_changing_the_password_does_not_sign_the_user_out(self):
        """Without update_session_auth_hash the session dies on success."""
        user = _make_user('pwstay', role='Operator')
        client = _client_for(user)
        client.post('/account/password/', {
            'old_password': 'pw-for-tests-1234',
            'new_password1': 'Kh4ki-Bundle-Ledger-77',
            'new_password2': 'Kh4ki-Bundle-Ledger-77',
        }, follow=True)
        self.assertEqual(client.get('/dashboard/', follow=True).status_code, 200)
        self.assertTrue(client.session.get('_auth_user_id'),
                        'the user was signed out by changing their own password')

    def test_a_weak_password_is_refused_with_the_reason_on_the_page(self):
        client = _client_for(_make_user('pwweak', role='Staff'))
        response = client.post('/account/password/', {
            'old_password': 'pw-for-tests-1234',
            'new_password1': 'password',
            'new_password2': 'password',
        })
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('field-error', body,
                      'the reason is not shown beside the field that caused it')

    def test_the_wrong_current_password_is_refused(self):
        user = _make_user('pwwrong', role='Staff')
        client = _client_for(user)
        client.post('/account/password/', {
            'old_password': 'not-the-current-password',
            'new_password1': 'Kh4ki-Bundle-Ledger-77',
            'new_password2': 'Kh4ki-Bundle-Ledger-77',
        })
        user.refresh_from_db()
        self.assertTrue(user.check_password('pw-for-tests-1234'),
                        'the password changed without the current one')


class ParameterValidationTests(TestCase):
    """A bad path parameter must be refused, not silently reinterpreted."""

    def test_an_unknown_barcode_instruction_mode_is_a_404(self):
        """It silently rewrote any unknown mode to 'generation' and returned 200,
        so an operator following a mistyped or stale workstation link was shown
        the generation sheet instead of the scanning one - on a page whose whole
        purpose is to be the printed procedure at a workstation.
        See SITE_AUDIT_FINDINGS.md B28."""
        client = _client_for(_make_user('barcodeop', role='Operator'))
        self.assertEqual(client.get('/barcode-instructions/scan/').status_code, 200)
        self.assertEqual(client.get('/barcode-instructions/generation/').status_code, 200)
        self.assertEqual(
            client.get('/barcode-instructions/scanning/').status_code, 404,
            'an unknown mode still returns 200 with the wrong instructions')

    def test_the_landing_page_does_not_hardcode_a_country(self):
        """It filtered the organisation tree by name__icontains='Bangladesh' and
        fell back to EVERY node when there was no such country, so a deployment
        for another country showed the whole estate unfiltered.
        See SITE_AUDIT_FINDINGS.md B19."""
        from django.conf import settings
        source = (settings.BASE_DIR / 'portal/views.py').read_text(encoding='utf-8')
        start = source.index('def dashboard(request)')
        end = source.index('\ndef ', start + 10)
        # Comments explaining the old behaviour are not the old behaviour.
        code = '\n'.join(line for line in source[start:end].splitlines()
                         if not line.lstrip().startswith('#'))
        self.assertNotIn(
            "name__icontains='Bangladesh'", code,
            'the landing page still hardcodes a country')
        self.assertNotIn(
            'OrganizationNode.objects.all()', code,
            'the landing page still falls back to every node in the estate')


class LocalisationTests(TestCase):
    """USE_I18N was True and nothing else was in place.

    No LocaleMiddleware, no LANGUAGES, no LOCALE_PATHS, no locale directory and
    not one {% trans %} in 49 templates - so the setting was inert and every
    string was hardcoded English, in a system the organisation requires to serve
    multiple languages. See SITE_AUDIT_FINDINGS.md B18.
    """

    def test_the_translation_machinery_is_wired_up(self):
        from django.conf import settings
        self.assertTrue(settings.USE_I18N)
        self.assertIn('django.middleware.locale.LocaleMiddleware', settings.MIDDLEWARE)
        self.assertTrue(getattr(settings, 'LANGUAGES', None),
                        'no LANGUAGES, so there is nothing to switch between')
        self.assertTrue(getattr(settings, 'LOCALE_PATHS', None),
                        'no LOCALE_PATHS, so a catalogue would never be found')

    def test_locale_middleware_runs_before_anything_renders(self):
        """After SessionMiddleware, and before the tenancy and audit middleware
        that touch the database, or the active language is not yet set."""
        from django.conf import settings
        order = settings.MIDDLEWARE
        self.assertLess(
            order.index('django.contrib.sessions.middleware.SessionMiddleware'),
            order.index('django.middleware.locale.LocaleMiddleware'),
            'LocaleMiddleware cannot read a language from the session if it runs '
            'before SessionMiddleware')

    def test_the_language_can_actually_be_switched(self):
        """End to end through Django's set_language view, not just settings."""
        user = _make_user('lang1', role='CEO')
        client = _client_for(user)
        response = client.post('/i18n/setlang/', {'language': 'bn', 'next': '/dashboard/'})
        self.assertIn(response.status_code, (302, 200))
        from django.conf import settings
        self.assertEqual(client.session.get(settings.LANGUAGE_COOKIE_NAME)
                         or client.cookies.get(settings.LANGUAGE_COOKIE_NAME).value,
                         'bn')

    def test_the_layout_declares_the_active_language(self):
        client = _client_for(_make_user('lang2', role='CEO'))
        body = client.get('/dashboard/', follow=True).content.decode()
        self.assertRegex(body, r'<html[^>]*\slang="[a-z]{2}',
                         'the page declares no language to a screen reader')

    def test_the_shared_chrome_is_marked_for_translation(self):
        from django.conf import settings
        base = (settings.BASE_DIR / 'templates/base.html').read_text(encoding='utf-8')
        self.assertIn('{% load static i18n %}', base)
        for phrase in ['Skip to content', 'Log out', 'Alerts', 'Staff Cost']:
            with self.subTest(phrase=phrase):
                self.assertIn(f'{{% trans "{phrase}" %}}', base,
                              f'"{phrase}" in the shared chrome is not translatable')

    def test_numbers_are_grouped(self):
        """A five-figure piece count read as 12500, and a raw Decimal as
        1234.5000."""
        from django.conf import settings
        self.assertTrue(getattr(settings, 'USE_THOUSAND_SEPARATOR', False))
        self.assertIn('django.contrib.humanize', settings.INSTALLED_APPS,
                      'humanize is not installed, so |intcomma is unavailable to '
                      'the templates')

    def test_the_locale_directory_states_what_is_outstanding(self):
        """The body copy of the 49 templates is still English. That has to be
        recorded, not left for someone to discover."""
        from django.conf import settings
        readme = settings.BASE_DIR / 'locale' / 'README.md'
        self.assertTrue(readme.is_file(), 'locale/ has no README')
        text = readme.read_text(encoding='utf-8')
        self.assertIn('hardcoded English', text)


class ApiContractTests(TestCase):
    """A JSON endpoint must answer like one.

    authorization.py deferred an unauthenticated request to @login_required,
    which redirects - so every /api/ endpoint answered a fetch client with a 302
    to an HTML login page instead of a 401. And _wants_json keyed off the /api/
    path prefix, so finance_overseas_preview, which returns JSON from
    /api/finance/... but was reachable under its own name, could hand back an
    HTML body with a JSON status. See SITE_AUDIT_FINDINGS.md C40.
    """

    def test_an_unauthenticated_api_request_gets_401_not_a_redirect(self):
        response = Client().get('/api/summary/')
        self.assertEqual(
            response.status_code, 401,
            'a fetch client got a redirect to an HTML login page instead of 401')
        self.assertEqual(response['Content-Type'].split(';')[0], 'application/json')
        self.assertFalse(response.json()['ok'])

    def test_an_unauthenticated_page_request_still_redirects_to_login(self):
        """A person must get a sign-in prompt, not a 401."""
        response = Client().get('/ceo-dashboard/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

    def test_a_refused_api_request_is_json_not_html(self):
        from portal import roles
        client = _client_for(_make_user('apihelper', role=roles.HELPER))
        response = client.get('/api/ceo-dashboard/')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response['Content-Type'].split(';')[0], 'application/json')

    def test_an_accept_json_header_is_honoured_off_the_api_prefix(self):
        response = Client().get('/ceo-dashboard/', HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 401)


class DeadWeightTests(TestCase):
    """Files that nothing references should not be shipped or stored."""

    def test_no_unreferenced_reference_screenshots_remain_in_static(self):
        from django.conf import settings
        stale = settings.BASE_DIR / 'static' / 'reference'
        self.assertFalse(
            stale.exists(),
            'static/reference/ held three PNGs referenced by no template, view '
            'or stylesheet, and excluded from the Docker image - so they were '
            'stored but unusable')

    def test_every_static_image_is_referenced_somewhere(self):
        import re
        from django.conf import settings
        static_root = settings.BASE_DIR / 'static'
        haystack = []
        for path in list((settings.BASE_DIR / 'templates').rglob('*.html')):
            haystack.append(path.read_text(encoding='utf-8'))
        for path in list((static_root / 'css').glob('*.css')):
            haystack.append(path.read_text(encoding='utf-8'))
        for path in list((static_root / 'js').glob('*.js')):
            haystack.append(path.read_text(encoding='utf-8'))
        for path in (settings.BASE_DIR / 'portal').glob('*.py'):
            haystack.append(path.read_text(encoding='utf-8'))
        blob = '\n'.join(haystack)

        orphans = []
        for image in sorted((static_root / 'img').rglob('*.png')):
            if image.name not in blob:
                orphans.append(str(image.relative_to(static_root)))
        self.assertEqual(
            orphans, [],
            'these images are shipped in the Docker image and referenced by '
            f'nothing: {orphans}')
