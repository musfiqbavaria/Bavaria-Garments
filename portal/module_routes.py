"""One table mapping a page-registry slug to the route that serves it.

Before this module the same mapping was maintained by hand in three places that
had drifted apart:

  * ``page_view`` carried a chain of 19 ``if slug=='...': return redirect(...)``
    lines. The chain stopped at ``poly-dashboard``, so every module routed after
    it - iron, final QC, finishing, packing, shipping, supplier, procurement,
    purchases, sourcing, account master, CEO - fell through to the generic
    placeholder even though each has a complete dashboard. It also held one rule
    that could never fire, ``sewing-dashboard``, because the registry slug for
    Sewing is ``sewing``.
  * ``dashboard.html`` special-cased four slugs inline in a nested ``{% if %}``.
  * ``report_master.html`` built its own links with a hardcoded ``/page/``
    prefix, which is not a route at all - the placeholder is ``/p/``. All 27
    drill-down links on the central reporting hub returned 404.

See SITE_AUDIT_FINDINGS.md A1, A2, A3 and B26.

Keeping the mapping in one place means a module gains a real page by adding one
line here, and ``ModuleRouteTests`` fails if a key is not a registry slug or a
value is not a registered route - so the table cannot drift from reality again.

The registry deliberately carries more than one slug for some modules, because
the numbered page list is the specification and its numbering is fixed. Where
two slugs name the same module both map to the same route rather than one of
them silently landing on the placeholder.
"""

from django.urls import NoReverseMatch, reverse

#: registry slug -> url name. A slug absent from this table has no dedicated
#: page yet and is served by the generic placeholder, which says so.
MODULE_ROUTES = {
    # --- public storefront and portals ------------------------------------
    'home': 'home',
    'wishlist': 'wishlist_portal',
    'returns-refunds': 'returns_portal',
    'investor-relations': 'investor_portal',
    'virtual-try-on': 'try_on_portal',
    'corporate-bulk-order': 'corporate_portal',
    'factory-interactive': 'factory_public_portal',
    'franchise-application': 'franchise_portal',
    'franchise-partner': 'franchise_portal',

    # --- executive and reporting ------------------------------------------
    'ceo-dashboard': 'ceo_dashboard',
    'report-master': 'report_master',
    'account-master': 'account_master',

    # --- finance gates -----------------------------------------------------
    'profit-before-spend': 'profit_before_spend',
    # Registry #64 spells it "send"; it is the same control.
    'profit-before-send': 'profit_before_spend',
    'profit-feasibility-gate': 'profit_feasibility_gate',
    'profit-feasibility': 'profit_feasibility_gate',
    'free-capacity-opportunity': 'free_capacity_opportunity',
    'free-capacity': 'free_capacity_opportunity',

    # --- commercial --------------------------------------------------------
    'buyer-opportunity': 'buyer_opportunity',
    'buyer-delivery-sla': 'buyer_delivery_sla',

    # --- HR and workforce --------------------------------------------------
    'hr-dashboard': 'hr_dashboard',
    'hr-master': 'hr_dashboard',
    'attendance-dashboard': 'attendance_dashboard',
    'attendance-master': 'attendance_dashboard',
    'self-service': 'staff_self_service',
    'staff-self-service-portal': 'staff_self_service',
    'recruitment': 'hr_recruitment_applications',

    # --- stock, material and assets ---------------------------------------
    'stock-material-master': 'stock_material_master',
    'asset-machine-master': 'asset_machine_master',

    # --- production --------------------------------------------------------
    'cutting': 'cutting_dashboard',
    'cutting-dashboard': 'cutting_dashboard',
    'embroidery': 'embroidery_dashboard',
    'embroidery-dashboard': 'embroidery_dashboard',
    'sewing': 'sewing_master',
    'label': 'label_dashboard',
    'label-dashboard': 'label_dashboard',
    'hand-iron-dashboard': 'hand_iron_dashboard',
    'hand-steam-iron': 'hand_iron_dashboard',
    'iron-dashboard': 'iron_dashboard',
    'poly': 'poly_dashboard',
    'poly-dashboard': 'poly_dashboard',
    'finishing': 'finishing_dashboard',
    'finishing-dashboard': 'finishing_dashboard',
    'packing': 'packing_dashboard',
    'packing-dashboard': 'packing_dashboard',

    # --- quality -----------------------------------------------------------
    'qc': 'qc_dashboard',
    'qc-dashboard': 'qc_dashboard',
    'final-qc': 'final_qc_dashboard',
    'final-qc-dashboard': 'final_qc_dashboard',

    # --- shipping ----------------------------------------------------------
    'shipping-dashboard': 'shipping_dashboard',

    # --- supply chain ------------------------------------------------------
    'supplier-dashboard': 'supplier_dashboard',
    'supplier-master': 'supplier_dashboard',
    'sourcing': 'sourcing_dashboard',
    'sourcing-dashboard': 'sourcing_dashboard',
    'procurement-dashboard': 'procurement_dashboard',
    'purchase-procurement': 'procurement_dashboard',
    'purchases-dashboard': 'purchases_dashboard',

    # --- shared tools ------------------------------------------------------
    'forms-master': 'forms_master',
    'company-forms': 'forms_master',
    'bundle-barcode': 'barcode_master',
    'universal-file-center': 'universal_file_center',
    'document-storage': 'universal_file_center',
    'staff-documents': 'universal_file_center',
    'communication-center-master': 'communication_center',
}


def module_url(slug):
    """Return the URL of the real page for ``slug``, or None if there is none.

    Returning None is the signal to fall back to the placeholder, so a registry
    entry never becomes a dead link.
    """
    name = MODULE_ROUTES.get(slug)
    if not name:
        return None
    try:
        return reverse(name)
    except NoReverseMatch:                                 # pragma: no cover
        # Guarded rather than raised so a renamed route degrades to the
        # placeholder instead of 500ing every page that lists modules.
        return None


def module_route_name(slug):
    """Return the url name serving ``slug``, or None."""
    return MODULE_ROUTES.get(slug)


def has_module_page(slug):
    """True if ``slug`` is served by a purpose-built page."""
    return module_url(slug) is not None
