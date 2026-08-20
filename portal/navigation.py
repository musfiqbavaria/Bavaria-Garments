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

from .authorization import AUTHENTICATED, PUBLIC, ROUTE_POLICY
from .roles import has_any_role

#: (url name, label, group). Group orders the menu; None means top level.
#: Every entry must name a route in ROUTE_POLICY - NavigationTests enforces it.
NAV = [
    ('dashboard', 'Dashboard', None),
    ('global_dashboard', 'All Modules', None),

    ('report_master', 'Report Master', 'Reporting'),
    ('ceo_dashboard', 'CEO Report', 'Reporting'),
    ('account_master', 'Account Master', 'Reporting'),

    ('profit_before_spend', 'Profit Before Spend', 'Finance'),
    ('profit_feasibility_gate', 'Feasibility Gate', 'Finance'),
    ('free_capacity_opportunity', 'Free Capacity', 'Finance'),

    ('buyer_opportunity', 'Buyer Opportunity', 'Commercial'),
    ('buyer_delivery_sla', 'Delivery SLA', 'Commercial'),

    ('sewing_master', 'Sewing', 'Production'),
    ('cutting_dashboard', 'Cutting', 'Production'),
    ('embroidery_dashboard', 'Embroidery', 'Production'),
    ('label_dashboard', 'Label', 'Production'),
    ('hand_iron_dashboard', 'Hand Iron', 'Production'),
    ('iron_dashboard', 'Industrial Iron', 'Production'),
    ('poly_dashboard', 'Poly', 'Production'),
    ('finishing_dashboard', 'Finishing', 'Production'),
    ('packing_dashboard', 'Packing', 'Production'),
    ('factory_resource_core', 'Factory Resources', 'Production'),
    ('bundle_traceability_finder', 'Bundle Traceability', 'Production'),

    ('qc_dashboard', 'In-line QC', 'Quality'),
    ('final_qc_dashboard', 'Final QC', 'Quality'),

    ('shipping_dashboard', 'Shipping', 'Logistics'),

    ('supplier_dashboard', 'Supplier', 'Supply chain'),
    ('sourcing_dashboard', 'Sourcing', 'Supply chain'),
    ('procurement_dashboard', 'Procurement', 'Supply chain'),
    ('purchases_dashboard', 'Purchases', 'Supply chain'),

    ('stock_material_master', 'Stock & Material', 'Stock'),
    ('asset_machine_master', 'Asset & Machine', 'Stock'),

    ('hr_dashboard', 'HR', 'People'),
    ('attendance_dashboard', 'Attendance', 'People'),
    ('hr_recruitment_applications', 'Recruitment', 'People'),

    ('staff_self_service', 'Self-Service', 'Tools'),
    ('forms_master', 'Forms Master', 'Tools'),
    ('barcode_master', 'Barcode', 'Tools'),
    ('barcode_scan_control', 'Scan Control', 'Tools'),
    ('communication_center', 'Communication', 'Tools'),
    ('universal_file_center', 'File Center', 'Tools'),
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
            groups.setdefault(group, []).append(item)

    return {
        'nav_top': top,
        # Ordered as NAV declares them, not alphabetically, so related
        # departments stay adjacent.
        'nav_groups': [{'title': g, 'items': items} for g, items in groups.items()],
        'nav_can_admin': bool(user.is_staff or user.is_superuser),
    }
