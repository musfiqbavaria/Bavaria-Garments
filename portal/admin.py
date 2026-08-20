"""Admin registration for Project 1.

For most of the 179 models the admin is the only editing UI that exists, so its
configuration is not a nicety. It was 33 bare ``admin.site.register(m)`` calls:
across every registered model there were **zero** ``list_display``, zero
``search_fields``, zero ``list_filter``, zero ``date_hierarchy`` and zero
inlines. Every changelist was a column of "ModelName object (1)", a table with
thousands of rows had no search box, and the page title read "Django
administration". See SITE_AUDIT_FINDINGS.md B16.

Rather than hand-write 179 ModelAdmin classes - which would be unmaintainable and
would drift from the models the first time a field was renamed - configuration is
derived from each model's own fields by ``ProjectAdmin``. A model gains a sensible
changelist by existing, and keeps it when its fields change. A model that needs
something specific still declares its own subclass.

Eleven models had no admin registration and no dedicated page, so their data
could not be reached at all (B17). The whole Iron module was among them - the one
department of eleven that was missing - along with ``FactoryProcessStandard``,
which carries the SMV, SAM and cost-per-minute standards that every efficiency
figure on the platform derives from, and ``ExchangeRate``, which .env.example
states is "maintained by Finance in admin" (B15).
"""

from django.contrib import admin
from django.db import models as django_models

from .models import *                                     # noqa: F401,F403
from .models import (
    ApprovalDecisionLog, ApprovalRequest, AuditLog, BarcodeScanEvent,
    ExchangeRate, FileAccessLog, LocalizedContent, StorefrontConfiguration,
)

# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------
admin.site.site_header = 'Emerald Rozalia — Project 1 administration'
admin.site.site_title = 'Emerald Rozalia admin'
admin.site.index_title = 'Business management system'


# ---------------------------------------------------------------------------
# Conventions
# ---------------------------------------------------------------------------

#: Field names that identify a record to a person, most identifying first.
_IDENTIFIERS = (
    'barcode', 'code', 'asset_code', 'employee_id', 'master_order_id', 'sku',
    'reference', 'application_reference', 'enquiry_no', 'opportunity_no',
    'po_number', 'invoice_no', 'carton_no', 'style_no', 'slug',
    'name', 'title',
)

#: High-cardinality FK targets. A default select widget would load every row
#: into the page; a raw id field will not.
_RAW_ID_TARGETS = {
    'Employee', 'MasterOrder', 'StockItem', 'AssetMachine', 'MaterialMaster',
    'MaterialLot', 'CuttingBundle', 'AttendanceEvent', 'FormSubmission',
    'CommunicationMessage', 'CommunicationThread', 'User',
}

#: Auto-pluralising by appending "s" gets these wrong.
_PLURALS = {
    'AttendanceDailySummary': 'attendance daily summaries',
    'AttendanceDeviceStatus': 'attendance device statuses',
    'AttendanceGatePass': 'attendance gate passes',
    'AttendanceHoliday': 'attendance holidays',
    'BuyerOpportunity': 'buyer opportunities',
    'CuttingLay': 'cutting lays',
    'CuttingProductionEntry': 'cutting production entries',
    'EmbroideryProductionEntry': 'embroidery production entries',
    'FreeCapacityOpportunity': 'free capacity opportunities',
    'HRInternalMobility': 'HR internal mobilities',
    'HRVacancy': 'HR vacancies',
    'HandIronProductionEntry': 'hand iron production entries',
    'IronProductionEntry': 'iron production entries',
    'LabelProductionEntry': 'label production entries',
    'OpportunityActivity': 'opportunity activities',
    'PolyPackingEntry': 'poly packing entries',
    'PurchaseThreeWayMatch': 'purchase three-way matches',
    'StaffDutySummary': 'staff duty summaries',
    'StaffPayrollSummary': 'staff payroll summaries',
    'StaffScheduleEntry': 'staff schedule entries',
}


class ProjectAdmin(admin.ModelAdmin):
    """A usable changelist derived from the model's own fields.

    Everything is computed in ``__init__`` from ``_meta`` so it cannot fall out
    of step with the model.
    """

    #: Columns beyond the identifier that are worth showing when present.
    INTERESTING = (
        'status', 'stage', 'level', 'priority', 'category', 'department',
        'scope', 'quantity', 'qty', 'amount', 'currency', 'actioned', 'enabled',
        'active', 'approved',
    )

    def __init__(self, model, admin_site):
        super().__init__(model, admin_site)
        fields = {f.name: f for f in model._meta.concrete_fields}

        # A subclass that declares its own list_display, search_fields and so on
        # must win. Deriving unconditionally overwrote them, which silently threw
        # away ExchangeRateAdmin's hand-written columns.
        declared = {
            name for name in ('list_display', 'search_fields', 'list_filter',
                              'date_hierarchy', 'list_select_related',
                              'raw_id_fields')
            if getattr(type(self), name, None)
            is not getattr(admin.ModelAdmin, name, None)
        }

        def derive(name, value):
            if name not in declared:
                setattr(self, name, value)

        display, seen = [], set()

        def add(name):
            if name in fields and name not in seen:
                display.append(name)
                seen.add(name)

        # Lead with whatever identifies the record to a person.
        for name in _IDENTIFIERS:
            add(name)
            if len(display) >= 3:
                break
        for name in self.INTERESTING:
            if len(display) >= 7:
                break
            add(name)
        # Always end on a date so the newest rows are recognisable.
        for name in ('report_date', 'created_at'):
            if name in fields:
                add(name)
                break
        derive('list_display', display or ['__str__'])

        # Text fields worth searching: identifiers, plus any unique text column.
        search = []
        for name in _IDENTIFIERS:
            field = fields.get(name)
            if isinstance(field, (django_models.CharField, django_models.TextField)):
                search.append(name)
        for name, field in fields.items():
            if (isinstance(field, (django_models.CharField, django_models.TextField))
                    and getattr(field, 'unique', False) and name not in search):
                search.append(name)
        if not search:
            # No identifier and no unique column: fall back to the first few
            # free-text fields so the changelist still has a working search box.
            # A table with thousands of rows and no search is unusable.
            search = [
                name for name, field in fields.items()
                if isinstance(field, (django_models.CharField, django_models.TextField))
                and not getattr(field, 'choices', None)
            ][:4]
        if not search:
            # 21 models are pure numbers, dates and foreign keys - the per-shift
            # production entries and the auto-report rows. They are identified by
            # what they point at, so search traverses the relation.
            for field in model._meta.concrete_fields:
                if not isinstance(field, django_models.ForeignKey):
                    continue
                related = {f.name: f for f in field.related_model._meta.concrete_fields}
                picked = None
                for candidate in _IDENTIFIERS:
                    target = related.get(candidate)
                    if isinstance(target, (django_models.CharField,
                                           django_models.TextField)):
                        picked = candidate
                        break
                if picked is None:
                    # No conventional identifier on the target either: take its
                    # first free-text column. Never 'password'.
                    for name_, target in related.items():
                        if (isinstance(target, (django_models.CharField,
                                                django_models.TextField))
                                and name_ != 'password'):
                            picked = name_
                            break
                if picked:
                    search.append(f'{field.name}__{picked}')
                if len(search) >= 3:
                    break
        if not search:
            # Last resort: a coded column such as the report slot. Searching a
            # choice value is still better than no search box at all - the
            # auto-report tables are one row per department per slot per day.
            search = [
                name for name, field in fields.items()
                if isinstance(field, django_models.CharField)
            ][:2]
        derive('search_fields', search)

        # Only cheap filters: a fixed choice list or a boolean. A ForeignKey
        # filter would query the whole related table to build the sidebar.
        derive('list_filter', [
            name for name, field in fields.items()
            if (getattr(field, 'choices', None)
                or isinstance(field, django_models.BooleanField))
        ][:6])

        for name in ('report_date', 'created_at'):
            if name in fields:
                derive('date_hierarchy', name)
                break

        # One query for the changelist instead of one per row per FK.
        derive('list_select_related', [
            f.name for f in model._meta.concrete_fields
            if isinstance(f, django_models.ForeignKey)
        ])
        derive('raw_id_fields', [
            f.name for f in model._meta.concrete_fields
            if isinstance(f, django_models.ForeignKey)
            and f.related_model.__name__ in _RAW_ID_TARGETS
        ])

        plural = _PLURALS.get(model.__name__)
        if plural and not model._meta.verbose_name_plural.endswith(plural):
            model._meta.verbose_name_plural = plural

    def get_queryset(self, request):
        """Respect organisation scoping in the admin, as the application does.

        ``ModelAdmin.get_queryset`` uses ``_default_manager``, and every scoped
        model pins ``default_manager_name='all_objects'`` - deliberately, so
        related descriptors and cascade deletes see every row. The consequence
        was that the admin was completely unscoped: any user with the Django
        ``is_staff`` flag saw every organisation's, country's and factory's data,
        even though the same user is scoped everywhere else in the platform.

        Registering all 179 models widened that surface, so it is closed here:
        for a scoped model the admin reads through ``objects`` - the
        ``ScopedManager`` - which honours the scope ``TenancyMiddleware``
        activated for this request. A superuser resolves to no scope and still
        sees everything, which is what the break-glass account is for.

        See SITE_AUDIT_FINDINGS.md B16 and portal/tenancy.py.
        """
        from .tenancy import is_scoped_model

        if is_scoped_model(self.model):
            queryset = self.model.objects.get_queryset()
        else:
            queryset = self.model._default_manager.get_queryset()
        ordering = self.get_ordering(request)
        if ordering:
            queryset = queryset.order_by(*ordering)
        return queryset

    def get_readonly_fields(self, request, obj=None):
        """Timestamps are set by the database, never typed in."""
        base = list(super().get_readonly_fields(request, obj))
        for name in ('created_at', 'updated_at'):
            if name in {f.name for f in self.model._meta.concrete_fields}:
                base.append(name)
        return base


class ReadOnlyAuditAdmin(ProjectAdmin):
    """Audit records are evidence: viewable, never editable.

    AuditLog and FileAccessLog were registered bare, so any user with the Django
    is_staff flag could edit or delete access history - which defeats the audit
    trail the compliance requirement depends on. ApprovalDecisionLog is the
    record of who authorised what, so it is locked the same way.
    """

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ExchangeRate)
class ExchangeRateAdmin(ProjectAdmin):
    """.env.example says rates are "maintained by Finance in admin".

    They could not be: the model was not registered, so with the default
    configuration (no EXCHANGE_RATE_API_URL) there was no way to enter or correct
    a rate at all. portal/currency.py then refuses to convert - correctly - which
    left every cross-currency figure permanently unconvertible.
    See SITE_AUDIT_FINDINGS.md B15.
    """
    list_display = ['base_currency', 'quote_currency', 'rate', 'rate_date',
                    'source', 'provider', 'recorded_by']
    list_filter = ['source', 'base_currency', 'quote_currency']
    search_fields = ['base_currency', 'quote_currency', 'provider']
    date_hierarchy = 'rate_date'
    ordering = ['-rate_date', 'base_currency', 'quote_currency']
    raw_id_fields = ['recorded_by']


@admin.register(OrganizationNode)
class OrganizationNodeAdmin(ProjectAdmin):
    """The scoping backbone, so worth showing the tree properly."""
    list_display = ['name', 'node_type', 'parent', 'timezone', 'currency',
                    'active', 'path']
    list_filter = ['node_type', 'active']
    search_fields = ['name', 'path']
    readonly_fields = ['path', 'depth', 'created_at', 'updated_at']
    ordering = ['path']


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def _register_all():
    """Register every concrete model that is not already registered.

    Enumerating the app's models rather than listing them by hand means a new
    model cannot be left without a UI - which is how eleven of them, including
    the entire Iron module, ended up unreachable. See SITE_AUDIT_FINDINGS.md B17.
    """
    from django.apps import apps

    audit_models = {AuditLog, FileAccessLog, ApprovalDecisionLog, BarcodeScanEvent}
    registered, skipped = 0, 0
    for model in apps.get_app_config('portal').get_models():
        if model in admin.site._registry:
            skipped += 1
            continue
        admin.site.register(model, ReadOnlyAuditAdmin if model in audit_models
                            else ProjectAdmin)
        registered += 1
    return registered, skipped


_register_all()
