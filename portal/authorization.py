"""Default-deny route authorisation for Project 1.

Before this module the platform had 99 ``@login_required`` views and no
permission checks at all, so any authenticated user - including a Helper - could
open the CEO dashboard and POST to every department dashboard
(TECHNICAL_ASSESSMENT.md 4.2).

Enforcement lives in one middleware and one table rather than 99 decorators.
That keeps the whole access policy readable in a single place, applies to routes
added later, and means a route nobody has classified is refused instead of
silently open. A missing entry is a deployment-blocking mistake, and
``AuthorizationPolicyTests`` fails if any portal route is unclassified.

The middleware only inspects views from the ``portal`` package, so the Django
admin keeps its own authentication and static/media are untouched.
"""

import logging

from django.http import HttpResponseForbidden, JsonResponse

from . import roles

logger = logging.getLogger('portal.authorization')

#: No authentication required.
PUBLIC = 'PUBLIC'
#: Any authenticated user, whatever their role.
AUTHENTICATED = 'AUTHENTICATED'


def _spread(names, policy):
    return {name: policy for name in names}


# ---------------------------------------------------------------------------
# ROUTE POLICY
#
# Keyed by URL name so the table survives path changes. Every portal route must
# appear here; anything missing is denied.
#
# Known limitation: `dashboard` and `page` are the module index and the generic
# placeholder that 62 unbuilt registry pages still share. They show only module
# names, the ten most recent orders and open alerts, so they are AUTHENTICATED
# for now. As those placeholders become real modules each one needs its own
# entry - see TECHNICAL_ASSESSMENT.md 3.1.
#
# Also note the department dashboards are monolithic: one view handles plan
# creation, material issue, production entry, QC release and report generation,
# so authority cannot yet be split per action. They are therefore gated to
# supervisory roles including In-Charge, which means an Operator cannot record
# their own production through the dashboard. Splitting per-action authority is
# a follow-up.
# ---------------------------------------------------------------------------
ROUTE_POLICY = {
    # --- public ------------------------------------------------------------
    'home': PUBLIC,
    'storefront_preferences': PUBLIC,
    'franchise_portal': PUBLIC,
    'investor_portal': PUBLIC,
    'factory_public_portal': PUBLIC,
    'corporate_portal': PUBLIC,
    'try_on_portal': PUBLIC,
    'returns_portal': PUBLIC,
    'wishlist_portal': PUBLIC,
    'login': PUBLIC,
    'logout': PUBLIC,

    # --- navigation, self-service and shared tools -------------------------
    'dashboard': AUTHENTICATED,
    'page': AUTHENTICATED,
    'forms_master': AUTHENTICATED,
    'api_summary': AUTHENTICATED,
    'barcode_master': AUTHENTICATED,
    'barcode_png': AUTHENTICATED,
    'staff_self_service': AUTHENTICATED,
    'api_staff_self_service': AUTHENTICATED,
    # Every user may raise an approval request; only senior roles may decide one.
    'api_approval_request': AUTHENTICATED,
    # Threads are filtered to the user's own conversations inside the view.
    'communication_center': AUTHENTICATED,
    'communication_center_export_csv': AUTHENTICATED,
    'api_communication_center': AUTHENTICATED,
    # File delivery is permission-checked per file by _user_can_access_file.
    'universal_file_center': AUTHENTICATED,
    'universal_file_action': AUTHENTICATED,

    # --- approvals ---------------------------------------------------------
    # Additionally checked per approval_type by roles.can_decide_approval, and
    # the requester can never be the approver.
    'api_approval_decision': roles.SENIOR_APPROVER_ROLES,

    # --- executive ---------------------------------------------------------
    **_spread(['ceo_dashboard', 'ceo_report_csv', 'api_ceo_dashboard'], roles.EXECUTIVE),
    # Every manager needs the central reporting hub.
    **_spread(['report_master', 'report_master_csv', 'api_report_master'], roles.MANAGEMENT),

    # --- finance -----------------------------------------------------------
    'account_master': roles.FINANCE,
    'finance_overseas_preview': roles.FINANCE,
    **_spread(['profit_before_spend', 'profit_before_spend_export_csv',
               'api_profit_before_spend'], roles.FINANCE | {roles.OPERATION_MANAGER}),
    **_spread(['profit_feasibility_gate', 'profit_feasibility_export_csv',
               'api_profit_feasibility_gate'], roles.GATES),
    **_spread(['free_capacity_opportunity', 'free_capacity_export_csv',
               'api_free_capacity_opportunity'], roles.GATES),
    # Value variance drives the BDT Red Alert engine: finance and operations.
    'api_variance': roles.FINANCE | {roles.OPERATION_MANAGER, roles.UNIT_MANAGER},

    # --- HR and workforce --------------------------------------------------
    **_spread(['hr_dashboard', 'api_hr_dashboard'], roles.HR),
    **_spread(['attendance_dashboard', 'attendance_export_csv',
               'api_attendance_dashboard', 'api_attendance_summary'], roles.HR),

    # --- stock, material and assets ---------------------------------------
    **_spread(['stock_material_master', 'stock_material_export_csv',
               'api_material_stock', 'api_stock_scan'], roles.STOCK),
    **_spread(['asset_machine_master', 'asset_machine_export_csv',
               'api_assets'], roles.ASSETS),

    # --- devices -----------------------------------------------------------
    # Returns CCTV/NVR/attendance endpoints, i.e. internal network addresses.
    'api_devices': roles.IT,

    # --- commercial --------------------------------------------------------
    **_spread(['buyer_opportunity', 'buyer_opportunity_export_csv',
               'api_buyer_opportunities'], roles.COMMERCIAL),
    **_spread(['buyer_delivery_sla', 'buyer_delivery_export_csv',
               'api_buyer_delivery_sla'], roles.COMMERCIAL | roles.SHIPPING),

    # --- production --------------------------------------------------------
    **_spread([
        'factory_resource_core',
        'cutting_dashboard', 'cutting_report_csv', 'api_cutting_dashboard',
        'embroidery_dashboard', 'embroidery_report_csv', 'api_embroidery_dashboard',
        'label_dashboard', 'label_report_csv', 'api_label_dashboard',
        'hand_iron_dashboard', 'hand_iron_report_csv', 'api_hand_iron_dashboard',
        'iron_dashboard', 'iron_report_csv', 'api_iron_dashboard',
        'poly_dashboard', 'poly_report_csv', 'api_poly_dashboard',
        'finishing_dashboard', 'finishing_report_csv', 'api_finishing_dashboard',
        'packing_dashboard', 'packing_report_csv', 'api_packing_dashboard',
    ], roles.PRODUCTION),

    # --- quality -----------------------------------------------------------
    **_spread(['qc_dashboard', 'qc_report_csv', 'api_qc_dashboard',
               'final_qc_dashboard', 'final_qc_report_csv',
               'api_final_qc_dashboard'], roles.QUALITY),

    # --- shipping ----------------------------------------------------------
    **_spread(['shipping_dashboard', 'shipping_report_csv',
               'api_shipping_dashboard'], roles.SHIPPING),

    # --- supply chain ------------------------------------------------------
    **_spread([
        'supplier_dashboard', 'supplier_report_csv', 'api_supplier_dashboard',
        'procurement_dashboard', 'procurement_report_csv', 'api_procurement_dashboard',
        'purchases_dashboard', 'purchase_report_csv', 'api_purchases_dashboard',
        'sourcing_dashboard', 'sourcing_report_csv', 'api_sourcing_dashboard',
    ], roles.PROCUREMENT),
}


def _wants_json(request):
    path = request.path or ''
    return path.startswith('/api/') or 'application/json' in request.META.get('HTTP_ACCEPT', '')


def _deny(request, reason):
    logger.warning(
        'authorization denied: user=%s path=%s reason=%s',
        getattr(request.user, 'username', 'anonymous'), request.path, reason,
    )
    if _wants_json(request):
        return JsonResponse({'ok': False, 'error': 'You are not authorised to use this endpoint.'},
                            status=403)
    return HttpResponseForbidden('You are not authorised to view this page.')


class AuthorizationMiddleware:
    """Enforce ROUTE_POLICY on every view in the portal package."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        module = getattr(view_func, '__module__', '') or ''
        if not module.startswith('portal.'):
            # Not ours: the Django admin authenticates itself, and static and
            # media are served without a view.
            return None

        url_name = getattr(request.resolver_match, 'url_name', None)
        policy = ROUTE_POLICY.get(url_name)

        if policy == PUBLIC:
            return None

        if not request.user.is_authenticated:
            # Let the view's own @login_required redirect to the login page, so
            # an anonymous visitor gets a sign-in prompt rather than a 403.
            return None

        if policy is None:
            # Default deny. Reaching here means a route was added without being
            # classified in ROUTE_POLICY.
            return _deny(request, f'route {url_name!r} is not classified in ROUTE_POLICY')

        if policy == AUTHENTICATED or request.user.is_superuser:
            return None

        if roles.has_any_role(request.user, policy):
            return None

        held = sorted(roles.user_roles(request.user)) or ['none']
        return _deny(request, f'route {url_name!r} requires one of '
                              f'{sorted(policy)}; user holds {held}')
