"""The navigation bar, built from the same policy that enforces access.

``base.html`` used to hardcode thirteen links, nine of them as literal paths, and
show all of them to every authenticated user. Five went to pages most roles
cannot open:

    ACCOUNT MASTER    roles.FINANCE       4 of 20 roles
    REPORT MASTER     roles.MANAGEMENT   16 of 20
    CEO REPORT        roles.EXECUTIVE     2 of 20
    Stock & Material  roles.STOCK         6 of 20
    SEWING MASTER     roles.PRODUCTION    7 of 20

A Helper, Operator or Staff member holds none of those groups, so every one of
the five refused them - and the refusal was a bare-text HttpResponseForbidden
with no layout and no way back. See SITE_AUDIT_FINDINGS.md B12.

Nine department dashboards were also absent from the navbar and from every other
page a user could reach, so Production, Quality, Shipping and Procurement staff
had no path to their own department at all (A2).

Both are fixed by generating the navigation from one table and filtering it
through ``ROUTE_POLICY``. A link is rendered only if the viewer would actually be
allowed through, so the navbar can no longer offer a 403. Because the filter
reads the authorisation table rather than a copy of it, the two cannot drift.
"""

from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext_lazy as _

from .authorization import AUTHENTICATED, PUBLIC, ROUTE_POLICY
from .roles import has_any_role

#: (url name, label, group). Group orders the menu; None means top level.
#: Every entry must name a route in ROUTE_POLICY - NavigationTests enforces it.
NAV = [
    ('dashboard', _('Dashboard'), None),
    ('global_dashboard', _('All Modules'), None),

    ('report_master', _('Report Master'), _('Reporting')),
    ('ceo_dashboard', _('CEO Report'), _('Reporting')),
    ('account_master', _('Account Master'), _('Reporting')),

    ('profit_before_spend', _('Profit Before Spend'), _('Finance')),
    ('profit_feasibility_gate', _('Feasibility Gate'), _('Finance')),
    ('free_capacity_opportunity', _('Free Capacity'), _('Finance')),

    ('buyer_opportunity', _('Buyer Opportunity'), _('Commercial')),
    ('buyer_delivery_sla', _('Delivery SLA'), _('Commercial')),

    ('sewing_master', _('Sewing'), _('Production')),
    ('cutting_dashboard', _('Cutting'), _('Production')),
    ('embroidery_dashboard', _('Embroidery'), _('Production')),
    ('label_dashboard', _('Label'), _('Production')),
    ('hand_iron_dashboard', _('Hand Iron'), _('Production')),
    ('iron_dashboard', _('Industrial Iron'), _('Production')),
    ('poly_dashboard', _('Poly'), _('Production')),
    ('finishing_dashboard', _('Finishing'), _('Production')),
    ('packing_dashboard', _('Packing'), _('Production')),
    ('factory_resource_core', _('Factory Resources'), _('Production')),
    ('bundle_traceability_finder', _('Bundle Traceability'), _('Production')),

    ('qc_dashboard', _('In-line QC'), _('Quality')),
    ('final_qc_dashboard', _('Final QC'), _('Quality')),

    ('shipping_dashboard', _('Shipping'), _('Logistics')),

    ('supplier_dashboard', _('Supplier'), _('Supply chain')),
    ('sourcing_dashboard', _('Sourcing'), _('Supply chain')),
    ('procurement_dashboard', _('Procurement'), _('Supply chain')),
    ('purchases_dashboard', _('Purchases'), _('Supply chain')),

    ('stock_material_master', _('Stock & Material'), _('Stock')),
    ('asset_machine_master', _('Asset & Machine'), _('Stock')),

    ('hr_dashboard', _('HR'), _('People')),
    ('attendance_dashboard', _('Attendance'), _('People')),
    ('hr_recruitment_applications', _('Recruitment'), _('People')),

    ('staff_self_service', _('Self-Service'), _('Tools')),
    ('password_change', _('Change Password'), _('Tools')),
    ('forms_master', _('Forms Master'), _('Tools')),
    ('barcode_master', _('Barcode'), _('Tools')),
    ('barcode_scan_control', _('Scan Control'), _('Tools')),
    ('communication_center', _('Communication'), _('Tools')),
    ('universal_file_center', _('File Center'), _('Tools')),
]


def _may_view(user, url_name):
    """True if ``user`` would be allowed through ``url_name`` by ROUTE_POLICY.

    Deliberately the same decision the middleware makes, read from the same
    table, so a link is never offered that the request would then refuse.
    """
    policy = ROUTE_POLICY.get(url_name)
    if policy is None:
        # Unclassified: the middleware denies it, so do not advertise it.
        return False
    if policy in (PUBLIC, AUTHENTICATED):
        return True
    if user.is_superuser:
        return True
    return has_any_role(user, policy)


def navigation(request):
    """Context processor: the menu this user may actually use."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}

    top, groups = [], {}
    for url_name, label, group in NAV:
        if not _may_view(user, url_name):
            continue
        try:
            url = reverse(url_name)
        except NoReverseMatch:                             # pragma: no cover
            continue
        item = {'url': url, 'label': label, 'name': url_name}
        if group is None:
            top.append(item)
        else:
            # Keyed on the resolved title, not the lazy proxy: two _('Reporting')
            # calls are distinct objects, so using them as dict keys would split
            # one menu into several. LocaleMiddleware has already set the active
            # language by the time a context processor runs, so resolving here is
            # correct.
            groups.setdefault(str(group), []).append(item)

    return {
        'nav_top': top,
        # Ordered as NAV declares them, not alphabetically, so related
        # departments stay adjacent.
        'nav_groups': [{'title': g, 'items': items} for g, items in groups.items()],
        'nav_can_admin': bool(user.is_staff or user.is_superuser),
    }
