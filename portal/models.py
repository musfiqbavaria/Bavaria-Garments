from datetime import timedelta
from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

from .tenancy import ScopedManager

class TimeStamped(models.Model):
    # created_at is indexed on the base class because almost every dashboard
    # orders or filters by it, and 170-odd concrete models inherit from here.
    created_at=models.DateTimeField(auto_now_add=True,db_index=True); updated_at=models.DateTimeField(auto_now=True)
    class Meta: abstract=True

class OrganizationNode(TimeStamped):
    """A node in the organisation tree: country, company, factory, unit, store.

    This modelled the hierarchy from the start but almost nothing referenced it,
    so no record could be attributed to a site. It is now the scoping backbone -
    see portal/tenancy.py and TECHNICAL_ASSESSMENT.md 6.2.
    """
    TYPES=[(x,x) for x in ['Country','Company','Factory','Production Unit','Area','Branch','Department','Warehouse','Retail Store','Franchise Store']]
    name=models.CharField(max_length=180); node_type=models.CharField(max_length=40,choices=TYPES,db_index=True); parent=models.ForeignKey('self',null=True,blank=True,on_delete=models.PROTECT,related_name='children')
    #: Materialised path, e.g. '/1/4/9/'. Subtree membership is then one indexed
    #: prefix match rather than a recursive walk. Maintained by save().
    path=models.CharField(max_length=255,blank=True,db_index=True)
    depth=models.PositiveSmallIntegerField(default=0)
    #: Local operating clock for this site. Blank means inherit from the parent,
    #: and ultimately settings.TIME_ZONE. Payroll depends on this: attendance
    #: anchors must resolve in the timezone the workforce actually works in.
    timezone=models.CharField(max_length=64,blank=True,
        help_text="IANA name, e.g. Asia/Dhaka. Blank inherits from the parent site.")
    active=models.BooleanField(default=True)

    class Meta:
        indexes=[models.Index(fields=['path'],name='idx_org_node_path')]

    def compute_path(self):
        return f'{self.parent.path}{self.pk}/' if self.parent_id and self.parent.path else f'/{self.pk}/'

    @property
    def effective_timezone(self):
        """This site's timezone, inherited from ancestors when not set."""
        node=self
        seen=0
        while node is not None and seen < 20:
            if node.timezone:
                return node.timezone
            node=node.parent
            seen += 1
        from django.conf import settings as _settings
        return _settings.TIME_ZONE

    def ancestors(self):
        ids=[int(x) for x in (self.path or '').strip('/').split('/') if x]
        return OrganizationNode.objects.filter(pk__in=ids).exclude(pk=self.pk).order_by('depth')

    def descendants(self):
        if not self.path:
            return OrganizationNode.objects.none()
        return OrganizationNode.objects.filter(path__startswith=self.path).exclude(pk=self.pk)

    @property
    def full_path_name(self):
        names=[n.name for n in self.ancestors()]+[self.name]
        return ' / '.join(names)

    def save(self,*args,**kwargs):
        creating=self.pk is None
        super().save(*args,**kwargs)
        path=self.compute_path()
        depth=max(0,len(path.strip('/').split('/'))-1)
        if path!=self.path or depth!=self.depth:
            OrganizationNode.objects.filter(pk=self.pk).update(path=path,depth=depth)
            self.path=path; self.depth=depth
            if not creating:
                # Re-path the subtree: a moved node changes every descendant.
                for child in OrganizationNode.objects.filter(parent_id=self.pk):
                    child.save()

    def __str__(self):
        return f'{self.node_type}: {self.name}'


class Department(TimeStamped):
    name=models.CharField(max_length=120,unique=True); code=models.CharField(max_length=20,unique=True); active=models.BooleanField(default=True,db_index=True)
    def __str__(self): return self.name

class UserProfile(TimeStamped):
    user=models.OneToOneField(User,on_delete=models.CASCADE,related_name='profile'); employee_id=models.CharField(max_length=50,blank=True); role=models.CharField(max_length=80,default='Staff'); department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL); scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.SET_NULL)

class DashboardPage(TimeStamped):
    page_id=models.PositiveIntegerField(unique=True); title=models.CharField(max_length=220); slug=models.SlugField(unique=True); group=models.CharField(max_length=80); enabled=models.BooleanField(default=True,db_index=True)
    #: Slug of the module that replaced this page. 20 registry entries are legacy
    #: stubs duplicating a real module, so the dashboard offered two of everything -
    #: one working, one empty. Those are disabled and redirect here rather than
    #: 404ing a bookmarked link. See TECHNICAL_ASSESSMENT.md 3.1.
    superseded_by=models.SlugField(blank=True)
    def __str__(self): return f'{self.page_id}. {self.title}'

class Employee(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    CATEGORIES=[(x,x.title()) for x in ['STAFF','OPERATOR','HELPER']]
    employee_id=models.CharField(max_length=50,unique=True)
    name=models.CharField(max_length=160)
    role=models.CharField(max_length=80)
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    category=models.CharField(max_length=20,choices=CATEGORIES,default='STAFF')
    status=models.CharField(max_length=20,default='ACTIVE',db_index=True)
    daily_cost=models.DecimalField(max_digits=12,decimal_places=2,default=0)
    monthly_gross_salary=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    factory_unit=models.CharField(max_length=160,blank=True)
    shift_code=models.CharField(max_length=40,blank=True)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class AttendanceEvent(TimeStamped):
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE); event=models.CharField(max_length=30); occurred_at=models.DateTimeField(default=timezone.now,db_index=True); source=models.CharField(max_length=50,default='Manual'); device_ref=models.CharField(max_length=100,blank=True)

class MasterOrder(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    # 'COMPLETED' was written by the buyer-delivery module but was absent from
    # this list, so an invalid status was persisted silently (nothing in the
    # project ever calls full_clean). v10 defines completion as a distinct step
    # after delivery - shipping marks DELIVERED on proof of delivery, and buyer
    # confirmation closes the order - so it is a real state, now declared.
    STATUS=[(s,s) for s in ['OPPORTUNITY','CONFIRMED','PLANNING','PRODUCTION','QC','PACKING','READY_TO_SHIP','SHIPPED','DELIVERED','COMPLETED','HOLD']]
    master_order_id=models.CharField(max_length=60,unique=True); buyer=models.CharField(max_length=180); product=models.CharField(max_length=180); quantity=models.PositiveIntegerField(default=0); order_value=models.DecimalField(max_digits=16,decimal_places=2,default=0); currency=models.CharField(max_length=10,default='USD',help_text='Currency of order_value. Consolidated reporting converts to settings.BASE_CURRENCY.'); confirmed_at=models.DateTimeField(null=True,blank=True); delivery_due=models.DateTimeField(null=True,blank=True); status=models.CharField(max_length=30,choices=STATUS,default='OPPORTUNITY',db_index=True)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class StockItem(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    sku=models.CharField(max_length=80,unique=True); name=models.CharField(max_length=180); category=models.CharField(max_length=60); unit=models.CharField(max_length=20,default='PCS'); qty=models.DecimalField(max_digits=16,decimal_places=3,default=0); reserved_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0); unit_cost=models.DecimalField(max_digits=14,decimal_places=4,default=0); currency=models.CharField(max_length=10,default='BDT',help_text='Currency of unit_cost.')

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class StockMovement(TimeStamped):
    item=models.ForeignKey(StockItem,on_delete=models.PROTECT); movement_type=models.CharField(max_length=30,db_index=True); quantity=models.DecimalField(max_digits=16,decimal_places=3); reference=models.CharField(max_length=100,blank=True,db_index=True); barcode=models.CharField(max_length=160,blank=True,db_index=True); performed_by=models.ForeignKey(User,null=True,on_delete=models.SET_NULL)

class FormDefinition(TimeStamped):
    form_id=models.PositiveIntegerField(unique=True); code=models.CharField(max_length=30,unique=True); name=models.CharField(max_length=220); department=models.CharField(max_length=120); category=models.CharField(max_length=60); version=models.CharField(max_length=20,default='1.0'); status=models.CharField(max_length=20,default='ACTIVE',db_index=True); requires_approval=models.BooleanField(default=False); red_alert_enabled=models.BooleanField(default=True)

class FormSubmission(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    definition=models.ForeignKey(FormDefinition,on_delete=models.PROTECT); reference=models.CharField(max_length=120,blank=True,db_index=True); submitted_by=models.ForeignKey(User,null=True,on_delete=models.SET_NULL); status=models.CharField(max_length=30,default='DRAFT',db_index=True); data=models.JSONField(default=dict); approved_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='approved_forms'); approved_at=models.DateTimeField(null=True,blank=True)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class Alert(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    LEVELS=[(x,x) for x in ['INFO','WARNING','RED']]
    title=models.CharField(max_length=180); message=models.TextField(blank=True); level=models.CharField(max_length=20,choices=LEVELS,default='INFO',db_index=True); department=models.CharField(max_length=120,blank=True); reference=models.CharField(max_length=120,blank=True,db_index=True); actioned=models.BooleanField(default=False,db_index=True); actioned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL); actioned_at=models.DateTimeField(null=True,blank=True)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class ActionItem(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    # status had no choices, so three vocabularies coexisted: writers used
    # 'OPEN' and 'COMPLETED', while the global control strip and the Celery
    # snapshot excluded 'DONE' - a value nothing ever wrote. The header counter
    # therefore counted completed items forever while the dashboards beside it
    # showed the correct figure. See TECHNICAL_ASSESSMENT.md 5.9.
    STATUS=[(x,x.title()) for x in ['OPEN','IN_PROGRESS','BLOCKED','COMPLETED','CANCELLED']]
    PRIORITIES=[(x,x.title()) for x in ['LOW','NORMAL','HIGH','URGENT']]
    #: The single definition of "not finished". Import this instead of writing
    #: another exclude() with a hand-typed status string.
    OPEN_STATUSES=['OPEN','IN_PROGRESS','BLOCKED']
    title=models.CharField(max_length=180); assigned_to=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL); department=models.CharField(max_length=120,blank=True); due_at=models.DateTimeField(null=True,blank=True); status=models.CharField(max_length=30,choices=STATUS,default='OPEN',db_index=True); priority=models.CharField(max_length=20,choices=PRIORITIES,default='NORMAL',db_index=True)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class Communication(TimeStamped):
    CHANNELS=[(x,x) for x in ['CHAT','WHATSAPP','EMAIL','IP_PHONE']]
    channel=models.CharField(max_length=20,choices=CHANNELS); sender=models.CharField(max_length=180,blank=True); recipient=models.CharField(max_length=180,blank=True); subject=models.CharField(max_length=220,blank=True); body=models.TextField(blank=True); reference=models.CharField(max_length=120,blank=True,db_index=True); status=models.CharField(max_length=30,default='NEW',db_index=True)

class DocumentRecord(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    document_id=models.CharField(max_length=80,unique=True); title=models.CharField(max_length=220); category=models.CharField(max_length=100); department=models.CharField(max_length=120,blank=True); reference=models.CharField(max_length=120,blank=True,db_index=True); version=models.CharField(max_length=20,default='1.0'); file=models.FileField(upload_to='documents/%Y/%m/',blank=True); confidential=models.BooleanField(default=False); expires_at=models.DateTimeField(null=True,blank=True); uploaded_by=models.ForeignKey(User,null=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class BarcodeAsset(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    TYPES=[(x,x) for x in ['BUNDLE','MATERIAL','STOCK','PRODUCT','CARTON','EMPLOYEE','ASSET','DOCUMENT']]
    code=models.CharField(max_length=180,unique=True); asset_type=models.CharField(max_length=30,choices=TYPES); reference=models.CharField(max_length=120,blank=True,db_index=True); payload=models.JSONField(default=dict); active=models.BooleanField(default=True,db_index=True)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class FinanceTransaction(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    TYPES=[(x,x) for x in ['INCOME','EXPENSE','RECEIVABLE','PAYABLE','INCENTIVE_RECEIVABLE','INCENTIVE_APPROVED','INCENTIVE_PAID']]
    transaction_type=models.CharField(max_length=40,choices=TYPES); country=models.CharField(max_length=80,default='Ireland'); currency=models.CharField(max_length=10,default='EUR'); amount=models.DecimalField(max_digits=16,decimal_places=2); reference=models.CharField(max_length=120,blank=True,db_index=True); overseas_receipt=models.BooleanField(default=False); incentive_rate=models.DecimalField(max_digits=6,decimal_places=3,default=0); incentive_amount=models.DecimalField(max_digits=16,decimal_places=2,default=0)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class ExchangeRate(TimeStamped):
    """Effective-dated FX rates for consolidated reporting.

    Cross-entity financial figures were computed by summing raw amounts across
    records in different currencies, with no rate table anywhere in the project:
    MasterOrder.order_value had no currency field at all, FinanceTransaction
    defaulted to EUR while production and supplier models defaulted to BDT, and
    the CEO dashboard added them together. At roughly 130 BDT to the euro the
    headline "profit today" was wrong by orders of magnitude.
    See TECHNICAL_ASSESSMENT.md 5.6.

    One row means: on `rate_date`, one unit of `base_currency` buys `rate` units
    of `quote_currency`.
    """
    SOURCES=[(x,x) for x in ['MANUAL','AUTO','SEED']]
    base_currency=models.CharField(max_length=10)
    quote_currency=models.CharField(max_length=10)
    rate_date=models.DateField(default=timezone.localdate,db_index=True)
    rate=models.DecimalField(max_digits=20,decimal_places=10)
    source=models.CharField(max_length=20,choices=SOURCES,default='MANUAL')
    provider=models.CharField(max_length=120,blank=True)
    recorded_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    class Meta:
        constraints=[models.UniqueConstraint(
            fields=['base_currency','quote_currency','rate_date'],
            name='unique_exchange_rate_per_day')]
        indexes=[models.Index(fields=['base_currency','quote_currency','-rate_date'],
                              name='idx_exchange_rate_lookup')]
        ordering=['-rate_date','base_currency','quote_currency']
    def __str__(self):
        return f'{self.rate_date} 1 {self.base_currency} = {self.rate} {self.quote_currency}'

class ReportSnapshot(TimeStamped):
    snapshot_type=models.CharField(max_length=60); generated_at=models.DateTimeField(default=timezone.now,db_index=True); data=models.JSONField(default=dict)

class AuditLog(models.Model):
    user=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL); method=models.CharField(max_length=10); path=models.CharField(max_length=500); status_code=models.PositiveIntegerField(default=200); ip=models.GenericIPAddressField(null=True,blank=True); created_at=models.DateTimeField(auto_now_add=True)

class ApprovalRequest(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    STATUS=[(x,x) for x in ['PENDING','APPROVED','REJECTED','CANCELLED']]
    approval_type=models.CharField(max_length=60)
    reference=models.CharField(max_length=120,db_index=True)
    requested_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='approval_requests')
    requested_at=models.DateTimeField(default=timezone.now,db_index=True)
    status=models.CharField(max_length=20,choices=STATUS,default='PENDING',db_index=True)
    approved_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='approval_decisions')
    approved_at=models.DateTimeField(null=True,blank=True)
    reason=models.TextField(blank=True)
    payload=models.JSONField(default=dict)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class ApprovalDecisionLog(models.Model):
    """Append-only record of every decision taken on an ApprovalRequest.

    ApprovalRequest keeps only the latest decision, so before this model a
    request could be flipped between APPROVED and REJECTED with no trace of who
    did what. Every "senior approval" control in the platform reads
    ApprovalRequest.status, which makes that history audit-critical.
    See TECHNICAL_ASSESSMENT.md 4.1.
    """
    approval=models.ForeignKey(ApprovalRequest,on_delete=models.CASCADE,related_name='decision_log')
    decided_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='approval_decision_log')
    previous_status=models.CharField(max_length=20)
    decision=models.CharField(max_length=20)
    reason=models.TextField(blank=True)
    approver_roles=models.CharField(max_length=255,blank=True,help_text='Roles held by the approver at the moment of the decision.')
    ip=models.GenericIPAddressField(null=True,blank=True)
    user_agent=models.CharField(max_length=500,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering=['-created_at']
        indexes=[models.Index(fields=['approval','-created_at'],name='idx_approval_decision_log')]
    def __str__(self): return f'{self.decision} on {self.approval_id} by {self.decided_by or "unknown"}'

class StockScan(TimeStamped):
    DIRECTIONS=[('IN','STOCK IN SCAN'),('OUT','STOCK OUT SCAN')]
    item=models.ForeignKey(StockItem,on_delete=models.PROTECT)
    direction=models.CharField(max_length=3,choices=DIRECTIONS,db_index=True)
    quantity=models.DecimalField(max_digits=16,decimal_places=3)
    barcode=models.CharField(max_length=160,db_index=True)
    reference=models.CharField(max_length=120,db_index=True)
    source_location=models.CharField(max_length=220,blank=True)
    destination_location=models.CharField(max_length=220,blank=True)
    scanned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    scanned_at=models.DateTimeField(default=timezone.now,db_index=True)
    manual_override=models.BooleanField(default=False)
    override_reason=models.TextField(blank=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)

class ValueVariance(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    reference=models.CharField(max_length=120,db_index=True)
    department=models.CharField(max_length=120,blank=True)
    currency=models.CharField(max_length=10,default='BDT')
    expected_value=models.DecimalField(max_digits=16,decimal_places=2)
    actual_value=models.DecimalField(max_digits=16,decimal_places=2)
    variance_amount=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    reason=models.TextField(blank=True)
    status=models.CharField(max_length=30,default='OPEN',db_index=True)
    recorded_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class DeviceIntegration(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    TYPES=[(x,x) for x in ['ATTENDANCE','CCTV','NVR','IP_PHONE','OTHER']]
    name=models.CharField(max_length=160)
    device_type=models.CharField(max_length=30,choices=TYPES)
    manufacturer=models.CharField(max_length=100,blank=True)
    model=models.CharField(max_length=100,blank=True)
    serial_number=models.CharField(max_length=120,blank=True)
    location=models.CharField(max_length=180,blank=True)
    endpoint=models.CharField(max_length=300,blank=True)
    active=models.BooleanField(default=True,db_index=True)
    config=models.JSONField(default=dict,blank=True)
    last_seen_at=models.DateTimeField(null=True,blank=True)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class AttendanceDailySummary(TimeStamped):
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE)
    work_date=models.DateField(db_index=True)
    scheduled_minutes=models.PositiveIntegerField(default=0)
    worked_minutes=models.PositiveIntegerField(default=0)
    break_minutes=models.PositiveIntegerField(default=0)
    overtime_minutes=models.PositiveIntegerField(default=0)
    unpaid_minutes=models.PositiveIntegerField(default=0)
    late_minutes=models.PositiveIntegerField(default=0)
    early_leave_minutes=models.PositiveIntegerField(default=0)
    office_gate_pass_paid_minutes=models.PositiveIntegerField(default=0)
    gate_pass_unpaid_minutes=models.PositiveIntegerField(default=0)
    npt_minutes=models.PositiveIntegerField(default=0)
    scheduled_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    worked_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    due_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    late_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    gate_pass_paid_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    gate_pass_unpaid_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    npt_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    status=models.CharField(max_length=30,default='PENDING',db_index=True)
    calculation=models.JSONField(default=dict)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['employee','work_date'],name='unique_employee_attendance_day')]

# Project 1 - Stock & Material Master
class MaterialMaster(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    CATEGORIES=[(x,x) for x in ['FABRIC','ACCESSORY','RAW_MATERIAL','PACKAGING','LABEL','THREAD','POLY','CHEMICAL','OTHER']]
    STATUSES=[(x,x) for x in ['ACTIVE','HOLD','INACTIVE']]
    material_code=models.CharField(max_length=80,unique=True)
    name=models.CharField(max_length=180)
    category=models.CharField(max_length=40,choices=CATEGORIES)
    subcategory=models.CharField(max_length=100,blank=True)
    description=models.TextField(blank=True)
    composition=models.JSONField(default=dict,blank=True,help_text='Example: {"Cotton": 95, "Spandex": 5}')
    gsm=models.DecimalField(max_digits=10,decimal_places=2,null=True,blank=True)
    width=models.DecimalField(max_digits=10,decimal_places=3,null=True,blank=True)
    width_unit=models.CharField(max_length=20,default='INCH')
    colour=models.CharField(max_length=80,blank=True)
    uom=models.CharField(max_length=20,default='PCS')
    standard_cost=models.DecimalField(max_digits=16,decimal_places=4,default=0)
    currency=models.CharField(max_length=10,default='BDT')
    min_stock=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    reorder_level=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    max_stock=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    status=models.CharField(max_length=20,choices=STATUSES,default='ACTIVE',db_index=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='materials_created')
    def __str__(self): return f'{self.material_code} - {self.name}'

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class MaterialLot(TimeStamped):
    STOCK_STATUSES=[(x,x) for x in ['RAW_MATERIAL','ACCESSORIES','WASTAGE','REJECT','FINISHED_PRODUCT','READY_FOR_SHIPMENT','RETURN']]
    QC_STATUSES=[(x,x) for x in ['PENDING','APPROVED','HOLD','REJECTED']]
    material=models.ForeignKey(MaterialMaster,on_delete=models.PROTECT,related_name='lots')
    lot_no=models.CharField(max_length=100)
    roll_barcode=models.CharField(max_length=180,unique=True)
    purchase_order_no=models.CharField(max_length=120,blank=True)
    customer_order_no=models.CharField(max_length=120,blank=True)
    supplier=models.CharField(max_length=180,blank=True)
    received_date=models.DateField(default=timezone.localdate)
    production_date=models.DateField(null=True,blank=True)
    expiry_date=models.DateField(null=True,blank=True)
    length=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    length_unit=models.CharField(max_length=20,default='METRE')
    original_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    current_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    reserved_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    unit_cost=models.DecimalField(max_digits=16,decimal_places=4,default=0)
    location=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='material_lots')
    stock_status=models.CharField(max_length=30,choices=STOCK_STATUSES,default='RAW_MATERIAL',db_index=True)
    qc_status=models.CharField(max_length=20,choices=QC_STATUSES,default='PENDING',db_index=True)
    notes=models.TextField(blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['material','lot_no'],name='unique_material_lot_no')]
    @property
    def available_qty(self): return self.current_qty-self.reserved_qty
    @property
    def total_value(self): return self.current_qty*self.unit_cost
    def __str__(self): return f'{self.roll_barcode} / {self.material.material_code}'

class MaterialReservation(TimeStamped):
    STATUS=[(x,x) for x in ['ACTIVE','RELEASED','CONSUMED','CANCELLED']]
    material=models.ForeignKey(MaterialMaster,on_delete=models.PROTECT,related_name='reservations')
    lot=models.ForeignKey(MaterialLot,null=True,blank=True,on_delete=models.PROTECT,related_name='reservations')
    order_reference=models.CharField(max_length=120)
    quantity=models.DecimalField(max_digits=16,decimal_places=3)
    status=models.CharField(max_length=20,choices=STATUS,default='ACTIVE',db_index=True)
    reserved_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    released_at=models.DateTimeField(null=True,blank=True)

class MaterialMovement(TimeStamped):
    TYPES=[(x,x) for x in ['STOCK_IN_SCAN','STOCK_OUT_SCAN','TRANSFER','RESERVE','RELEASE','WASTAGE','REJECT','RETURN','ADJUSTMENT']]
    material=models.ForeignKey(MaterialMaster,on_delete=models.PROTECT,related_name='movements')
    lot=models.ForeignKey(MaterialLot,null=True,blank=True,on_delete=models.PROTECT,related_name='movements')
    movement_type=models.CharField(max_length=30,choices=TYPES,db_index=True)
    quantity=models.DecimalField(max_digits=16,decimal_places=3)
    unit_cost=models.DecimalField(max_digits=16,decimal_places=4,default=0)
    barcode=models.CharField(max_length=180,db_index=True)
    reference=models.CharField(max_length=120,db_index=True)
    order_reference=models.CharField(max_length=120,blank=True)
    purchase_order_no=models.CharField(max_length=120,blank=True)
    source_location=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='material_movements_out')
    destination_location=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='material_movements_in')
    performed_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    performed_at=models.DateTimeField(default=timezone.now,db_index=True)
    manual_entry=models.BooleanField(default=False)
    reason=models.TextField(blank=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    balance_after=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    metadata=models.JSONField(default=dict,blank=True)


class AssetMachine(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    ASSET_TYPES=[(x,x) for x in ['MACHINE','EQUIPMENT','TOOL','VEHICLE','IT_EQUIPMENT','FURNITURE','OTHER']]
    STATUSES=[(x,x) for x in ['ACTIVE','IDLE','UNDER_MAINTENANCE','BREAKDOWN','HOLD','RETIRED','DISPOSED']]
    CONDITIONS=[(x,x) for x in ['NEW','EXCELLENT','GOOD','FAIR','POOR','UNSERVICEABLE']]
    DEPRECIATION=[(x,x) for x in ['STRAIGHT_LINE','DECLINING_BALANCE','NONE']]
    asset_code=models.CharField(max_length=80,unique=True)
    barcode=models.CharField(max_length=180,unique=True)
    name=models.CharField(max_length=180)
    asset_type=models.CharField(max_length=30,choices=ASSET_TYPES,default='MACHINE')
    category=models.CharField(max_length=100,blank=True)
    machine_type=models.CharField(max_length=120,blank=True)
    manufacturer=models.CharField(max_length=120,blank=True)
    model=models.CharField(max_length=120,blank=True)
    serial_number=models.CharField(max_length=160,blank=True)
    supplier=models.CharField(max_length=180,blank=True)
    purchase_date=models.DateField(null=True,blank=True)
    purchase_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    currency=models.CharField(max_length=10,default='BDT')
    current_value=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    depreciation_method=models.CharField(max_length=30,choices=DEPRECIATION,default='STRAIGHT_LINE')
    depreciation_rate=models.DecimalField(max_digits=7,decimal_places=3,default=0)
    warranty_expiry=models.DateField(null=True,blank=True)
    location=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='assets')
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL,related_name='assets')
    assigned_to=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL,related_name='assigned_assets')
    status=models.CharField(max_length=30,choices=STATUSES,default='ACTIVE',db_index=True)
    condition=models.CharField(max_length=30,choices=CONDITIONS,default='GOOD')
    operation_capabilities=models.JSONField(default=list,blank=True)
    standard_speed=models.DecimalField(max_digits=12,decimal_places=3,default=0)
    speed_unit=models.CharField(max_length=40,blank=True)
    available_minutes_per_day=models.PositiveIntegerField(default=480)
    efficiency_percent=models.DecimalField(max_digits=6,decimal_places=2,default=100)
    power_rating=models.DecimalField(max_digits=12,decimal_places=3,default=0)
    power_unit=models.CharField(max_length=20,default='KW')
    maintenance_interval_days=models.PositiveIntegerField(default=30)
    last_maintenance_date=models.DateField(null=True,blank=True)
    next_maintenance_date=models.DateField(null=True,blank=True)
    notes=models.TextField(blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='assets_created')
    @property
    def is_maintenance_due(self):
        return bool(self.next_maintenance_date and self.next_maintenance_date <= timezone.localdate())
    @property
    def daily_effective_minutes(self):
        return int(self.available_minutes_per_day * float(self.efficiency_percent) / 100)
    def __str__(self): return f'{self.asset_code} - {self.name}'

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class AssetMaintenance(TimeStamped):
    TYPES=[(x,x) for x in ['PREVENTIVE','CORRECTIVE','BREAKDOWN','CALIBRATION','INSPECTION','SERVICE']]
    STATUS=[(x,x) for x in ['PLANNED','IN_PROGRESS','COMPLETED','CANCELLED']]
    asset=models.ForeignKey(AssetMachine,on_delete=models.PROTECT,related_name='maintenance_records')
    maintenance_type=models.CharField(max_length=30,choices=TYPES,default='PREVENTIVE')
    reference=models.CharField(max_length=120,db_index=True)
    scheduled_date=models.DateField(null=True,blank=True)
    started_at=models.DateTimeField(null=True,blank=True,db_index=True)
    completed_at=models.DateTimeField(null=True,blank=True,db_index=True)
    technician=models.CharField(max_length=160,blank=True)
    vendor=models.CharField(max_length=180,blank=True)
    description=models.TextField(blank=True)
    parts_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    labour_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    other_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    currency=models.CharField(max_length=10,default='BDT')
    meter_reading=models.DecimalField(max_digits=16,decimal_places=3,null=True,blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='PLANNED',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    performed_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    @property
    def total_cost(self): return self.parts_cost+self.labour_cost+self.other_cost

class AssetDowntime(TimeStamped):
    REASONS=[(x,x) for x in ['BREAKDOWN','MAINTENANCE','POWER','NO_OPERATOR','NO_MATERIAL','QUALITY_HOLD','CHANGEOVER','OTHER']]
    asset=models.ForeignKey(AssetMachine,on_delete=models.PROTECT,related_name='downtime_records')
    reference=models.CharField(max_length=120,db_index=True)
    reason=models.CharField(max_length=30,choices=REASONS,default='BREAKDOWN')
    started_at=models.DateTimeField(default=timezone.now,db_index=True)
    ended_at=models.DateTimeField(null=True,blank=True)
    description=models.TextField(blank=True)
    production_impact_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    recorded_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    @property
    def duration_minutes(self):
        end=self.ended_at or timezone.now()
        return max(0,int((end-self.started_at).total_seconds()//60))

class AssetMovement(TimeStamped):
    TYPES=[(x,x) for x in ['ASSIGN','TRANSFER','RETURN','RELOCATE','RETIRE','DISPOSE']]
    asset=models.ForeignKey(AssetMachine,on_delete=models.PROTECT,related_name='movements')
    movement_type=models.CharField(max_length=30,choices=TYPES,db_index=True)
    reference=models.CharField(max_length=120,db_index=True)
    source_location=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='asset_movements_out')
    destination_location=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='asset_movements_in')
    assigned_to=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL)
    barcode=models.CharField(max_length=180,db_index=True)
    reason=models.TextField(blank=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    performed_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    performed_at=models.DateTimeField(default=timezone.now,db_index=True)


class BuyerOpportunity(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    STAGES=[(x,x.replace('_',' ').title()) for x in ['NEW_ENQUIRY','QUALIFICATION','FEASIBILITY_CHECK','COSTING','QUOTATION_SENT','SAMPLE_DEVELOPMENT','SAMPLE_SENT','BUYER_REVIEW','NEGOTIATION','AWAITING_CONFIRMATION','WON','LOST','HOLD','CANCELLED']]
    PRIORITIES=[(x,x.title()) for x in ['LOW','NORMAL','HIGH','URGENT']]
    enquiry_no=models.CharField(max_length=60,unique=True)
    opportunity_no=models.CharField(max_length=60,unique=True)
    buyer_company=models.CharField(max_length=180)
    buyer_contact=models.CharField(max_length=160,blank=True)
    buyer_email=models.EmailField(blank=True); buyer_phone=models.CharField(max_length=80,blank=True)
    buyer_country=models.CharField(max_length=100,blank=True); delivery_destination=models.CharField(max_length=180,blank=True)
    product=models.CharField(max_length=180); style_no=models.CharField(max_length=100,blank=True); item_no=models.CharField(max_length=100,blank=True)
    description=models.TextField(blank=True); target_quantity=models.PositiveIntegerField(default=0)
    target_unit_price=models.DecimalField(max_digits=14,decimal_places=4,default=0); currency=models.CharField(max_length=10,default='USD')
    expected_order_value=models.DecimalField(max_digits=16,decimal_places=2,default=0); probability_percent=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    required_delivery_date=models.DateField(null=True,blank=True); enquiry_date=models.DateField(default=timezone.localdate); follow_up_date=models.DateField(null=True,blank=True)
    incoterms=models.CharField(max_length=40,blank=True); payment_terms=models.CharField(max_length=180,blank=True)
    fabric_requirements=models.TextField(blank=True); accessory_requirements=models.TextField(blank=True); sample_requirements=models.TextField(blank=True)
    stage=models.CharField(max_length=40,choices=STAGES,default='NEW_ENQUIRY',db_index=True); priority=models.CharField(max_length=20,choices=PRIORITIES,default='NORMAL',db_index=True)
    owner=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='buyer_opportunities')
    merchandiser=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL)
    source=models.CharField(max_length=100,blank=True); competitor_info=models.TextField(blank=True); lost_reason=models.TextField(blank=True)
    feasibility_status=models.CharField(max_length=30,default='PENDING'); readiness_score=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    quotation_version=models.PositiveIntegerField(default=0); converted_order=models.OneToOneField(MasterOrder,null=True,blank=True,on_delete=models.SET_NULL,related_name='source_opportunity')
    notes=models.TextField(blank=True)
    @property
    def weighted_value(self): return (self.expected_order_value*self.probability_percent/100).quantize(Decimal('0.01'))
    @property
    def follow_up_overdue(self): return bool(self.follow_up_date and self.follow_up_date < timezone.localdate() and self.stage not in {'WON','LOST','CANCELLED'})
    def __str__(self): return f'{self.opportunity_no} - {self.buyer_company}'

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class OpportunityQuotation(TimeStamped):
    STATUS=[(x,x.title()) for x in ['DRAFT','PENDING_APPROVAL','APPROVED','SENT','ACCEPTED','REJECTED','EXPIRED']]
    opportunity=models.ForeignKey(BuyerOpportunity,on_delete=models.CASCADE,related_name='quotations')
    version=models.PositiveIntegerField(); quotation_no=models.CharField(max_length=80,unique=True)
    unit_price=models.DecimalField(max_digits=14,decimal_places=4); quantity=models.PositiveIntegerField(default=0); currency=models.CharField(max_length=10,default='USD')
    total_value=models.DecimalField(max_digits=16,decimal_places=2,default=0); valid_until=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='DRAFT',db_index=True); terms=models.TextField(blank=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL); created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    class Meta: unique_together=[('opportunity','version')]

class OpportunityActivity(TimeStamped):
    TYPES=[(x,x.title()) for x in ['CALL','EMAIL','WHATSAPP','MEETING','NOTE','FOLLOW_UP','SAMPLE','STATUS_CHANGE']]
    opportunity=models.ForeignKey(BuyerOpportunity,on_delete=models.CASCADE,related_name='activities'); activity_type=models.CharField(max_length=30,choices=TYPES)
    subject=models.CharField(max_length=220); details=models.TextField(blank=True); next_follow_up=models.DateField(null=True,blank=True)
    performed_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL); occurred_at=models.DateTimeField(default=timezone.now,db_index=True)


class CommunicationThread(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    TYPES=[(x,x.replace('_',' ').title()) for x in ['INTERNAL','BUYER','ORDER','SUPPLIER','HR','PRODUCTION','ALERT','SUPPORT']]
    PRIORITIES=[(x,x.title()) for x in ['LOW','NORMAL','HIGH','URGENT']]
    thread_no=models.CharField(max_length=70,unique=True)
    subject=models.CharField(max_length=240)
    thread_type=models.CharField(max_length=30,choices=TYPES,default='INTERNAL')
    priority=models.CharField(max_length=20,choices=PRIORITIES,default='NORMAL',db_index=True)
    reference=models.CharField(max_length=140,blank=True,db_index=True)
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    buyer_opportunity=models.ForeignKey('BuyerOpportunity',null=True,blank=True,on_delete=models.SET_NULL,related_name='communication_threads')
    order=models.ForeignKey(MasterOrder,null=True,blank=True,on_delete=models.SET_NULL,related_name='communication_threads')
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='created_comm_threads')
    assigned_to=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='assigned_comm_threads')
    participants=models.ManyToManyField(User,blank=True,related_name='communication_threads')
    status=models.CharField(max_length=30,default='OPEN',db_index=True)
    last_message_at=models.DateTimeField(default=timezone.now,db_index=True)
    closed_at=models.DateTimeField(null=True,blank=True)
    def __str__(self): return f'{self.thread_no} - {self.subject}'

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class CommunicationMessage(TimeStamped):
    CHANNELS=[(x,x.replace('_',' ').title()) for x in ['CHAT_24_7','INTERNAL_CHAT','EMAIL','WHATSAPP','SMS','VOICE_CALL','VIDEO_CALL','VIDEO_CONFERENCE','SOCIAL_DM','NOTICE','BROADCAST','SYSTEM_ALERT']]
    DIRECTIONS=[('INBOUND','Inbound'),('OUTBOUND','Outbound'),('INTERNAL','Internal')]
    STATUS=[(x,x.title()) for x in ['DRAFT','QUEUED','SENT','DELIVERED','READ','FAILED','RECEIVED']]
    thread=models.ForeignKey(CommunicationThread,on_delete=models.CASCADE,related_name='messages')
    channel=models.CharField(max_length=30,choices=CHANNELS,default='CHAT_24_7')
    direction=models.CharField(max_length=20,choices=DIRECTIONS,default='INTERNAL',db_index=True)
    sender_user=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='comm_messages_sent')
    sender_address=models.CharField(max_length=220,blank=True)
    recipient_address=models.CharField(max_length=500,blank=True)
    subject=models.CharField(max_length=240,blank=True)
    body=models.TextField(blank=True)
    status=models.CharField(max_length=20,choices=STATUS,default='SENT',db_index=True)
    external_message_id=models.CharField(max_length=180,blank=True)
    sent_at=models.DateTimeField(default=timezone.now,db_index=True)
    delivered_at=models.DateTimeField(null=True,blank=True)
    read_at=models.DateTimeField(null=True,blank=True)
    is_red_alert=models.BooleanField(default=False)
    metadata=models.JSONField(default=dict,blank=True)

class CommunicationReadReceipt(TimeStamped):
    message=models.ForeignKey(CommunicationMessage,on_delete=models.CASCADE,related_name='read_receipts')
    user=models.ForeignKey(User,on_delete=models.CASCADE,related_name='comm_read_receipts')
    read_at=models.DateTimeField(default=timezone.now)
    class Meta: unique_together=[('message','user')]

class CommunicationAttachment(TimeStamped):
    message=models.ForeignKey(CommunicationMessage,on_delete=models.CASCADE,related_name='attachments')
    file=models.FileField(upload_to='communication/%Y/%m/')
    original_name=models.CharField(max_length=255,blank=True)
    mime_type=models.CharField(max_length=120,blank=True)
    file_size=models.PositiveBigIntegerField(default=0)
    uploaded_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class CommunicationNotice(TimeStamped):
    TYPES=[(x,x.replace('_',' ').title()) for x in ['NOTICE','BROADCAST','RED_ALERT']]
    title=models.CharField(max_length=240)
    body=models.TextField()
    notice_type=models.CharField(max_length=20,choices=TYPES,default='NOTICE')
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    target_role=models.CharField(max_length=120,blank=True)
    target_all=models.BooleanField(default=False)
    starts_at=models.DateTimeField(default=timezone.now)
    expires_at=models.DateTimeField(null=True,blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    active=models.BooleanField(default=True,db_index=True)

class CommunicationConnector(TimeStamped):
    TYPES=[(x,x.replace('_',' ').title()) for x in ['EMAIL_SMTP','WHATSAPP_BUSINESS','SMS_GATEWAY','VOICE_PROVIDER','VIDEO_PROVIDER','SOCIAL_MEDIA']]
    name=models.CharField(max_length=120)
    connector_type=models.CharField(max_length=40,choices=TYPES)
    enabled=models.BooleanField(default=False,db_index=True)
    config=models.JSONField(default=dict,blank=True)
    last_checked_at=models.DateTimeField(null=True,blank=True)
    last_status=models.CharField(max_length=80,blank=True)
    def __str__(self): return self.name


class ProfitFeasibilityGate(TimeStamped):
    DECISIONS=[(x,x.replace('_',' ').title()) for x in ['PENDING','ACCEPT','ACCEPT_WITH_RISK','HOLD','REJECT']]
    opportunity=models.OneToOneField('BuyerOpportunity',on_delete=models.CASCADE,related_name='profit_feasibility_gate')
    currency=models.CharField(max_length=10,default='USD')
    selling_price_per_unit=models.DecimalField(max_digits=14,decimal_places=4,default=0)
    quantity=models.PositiveIntegerField(default=0)

    material_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    accessory_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    labour_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    production_overhead=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    finishing_packing_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    logistics_freight_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    finance_bank_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    commission_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    compliance_testing_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    contingency_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    other_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)

    minimum_margin_percent=models.DecimalField(max_digits=6,decimal_places=2,default=10)
    capacity_score=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    material_readiness_score=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    workforce_score=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    machine_score=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    lead_time_score=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    quality_score=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    compliance_score=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    buyer_credit_score=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    commercial_risk_score=models.DecimalField(max_digits=5,decimal_places=2,default=0)

    production_days_required=models.PositiveIntegerField(default=0)
    available_days=models.PositiveIntegerField(default=0)
    critical_bottleneck=models.TextField(blank=True)
    material_shortage=models.TextField(blank=True)
    capacity_risk=models.TextField(blank=True)
    commercial_risk=models.TextField(blank=True)
    remarks=models.TextField(blank=True)

    system_recommendation=models.CharField(max_length=30,choices=DECISIONS,default='PENDING',db_index=True)
    final_decision=models.CharField(max_length=30,choices=DECISIONS,default='PENDING',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL,related_name='profit_feasibility_gates')
    reviewed_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='feasibility_reviews')
    reviewed_at=models.DateTimeField(null=True,blank=True)

    @property
    def revenue(self):
        return (self.selling_price_per_unit * Decimal(self.quantity)).quantize(Decimal('0.01'))

    @property
    def total_cost(self):
        return sum([
            self.material_cost,self.accessory_cost,self.labour_cost,self.production_overhead,
            self.finishing_packing_cost,self.logistics_freight_cost,self.finance_bank_cost,
            self.commission_cost,self.compliance_testing_cost,self.contingency_cost,self.other_cost
        ], Decimal('0')).quantize(Decimal('0.01'))

    @property
    def gross_profit(self):
        return (self.revenue-self.total_cost).quantize(Decimal('0.01'))

    @property
    def margin_percent(self):
        if self.revenue <= 0: return Decimal('0.00')
        return ((self.gross_profit/self.revenue)*100).quantize(Decimal('0.01'))

    @property
    def feasibility_score(self):
        scores=[
            self.capacity_score,self.material_readiness_score,self.workforce_score,self.machine_score,
            self.lead_time_score,self.quality_score,self.compliance_score,self.buyer_credit_score
        ]
        if not scores: return Decimal('0.00')
        return (sum(scores,Decimal('0'))/Decimal(len(scores))).quantize(Decimal('0.01'))

    @property
    def gate_passed(self):
        return self.final_decision in {'ACCEPT','ACCEPT_WITH_RISK'} and self.approval_id and self.approval.status=='APPROVED'

    def calculate_recommendation(self):
        if self.margin_percent < self.minimum_margin_percent:
            return 'REJECT'
        if self.feasibility_score < Decimal('60'):
            return 'HOLD'
        if self.commercial_risk_score >= Decimal('70') or self.available_days < self.production_days_required:
            return 'ACCEPT_WITH_RISK'
        if self.feasibility_score >= Decimal('75'):
            return 'ACCEPT'
        return 'ACCEPT_WITH_RISK'

    def save(self,*args,**kwargs):
        self.system_recommendation=self.calculate_recommendation()
        super().save(*args,**kwargs)

    def __str__(self):
        return f'Gate {self.opportunity.opportunity_no} - {self.final_decision}'


class FreeCapacityOpportunity(TimeStamped):
    DECISIONS=[(x,x.replace('_',' ').title()) for x in ['PENDING','TAKE_QUICK_ORDER','TAKE_WITH_RISK','HOLD','REJECT']]
    opportunity=models.ForeignKey('BuyerOpportunity',null=True,blank=True,on_delete=models.SET_NULL,related_name='free_capacity_checks')
    reference=models.CharField(max_length=100,unique=True)
    product=models.CharField(max_length=180,blank=True)
    quantity=models.PositiveIntegerField(default=0)
    requested_delivery_date=models.DateField(null=True,blank=True)
    required_minutes=models.PositiveIntegerField(default=0)

    available_machine_minutes=models.PositiveIntegerField(default=0)
    available_workforce_minutes=models.PositiveIntegerField(default=0)
    available_line_minutes=models.PositiveIntegerField(default=0)
    reserved_minutes=models.PositiveIntegerField(default=0)
    safety_buffer_minutes=models.PositiveIntegerField(default=0)

    material_readiness_percent=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    accessories_readiness_percent=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    qc_readiness_percent=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    finishing_readiness_percent=models.DecimalField(max_digits=5,decimal_places=2,default=0)

    selling_value=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    incremental_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    minimum_margin_percent=models.DecimalField(max_digits=6,decimal_places=2,default=10)
    current_confirmed_load_percent=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    rush_risk_percent=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    notes=models.TextField(blank=True)

    system_recommendation=models.CharField(max_length=30,choices=DECISIONS,default='PENDING',db_index=True)
    final_decision=models.CharField(max_length=30,choices=DECISIONS,default='PENDING',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL,related_name='free_capacity_opportunities')
    reviewed_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    reviewed_at=models.DateTimeField(null=True,blank=True)

    @property
    def free_minutes(self):
        raw=min(self.available_machine_minutes,self.available_workforce_minutes,self.available_line_minutes)
        return max(raw-self.reserved_minutes-self.safety_buffer_minutes,0)

    @property
    def capacity_fit_percent(self):
        if self.required_minutes <= 0: return Decimal('0.00')
        return (Decimal(self.free_minutes)/Decimal(self.required_minutes)*100).quantize(Decimal('0.01'))

    @property
    def incremental_profit(self):
        return (self.selling_value-self.incremental_cost).quantize(Decimal('0.01'))

    @property
    def margin_percent(self):
        if self.selling_value <= 0: return Decimal('0.00')
        return ((self.incremental_profit/self.selling_value)*100).quantize(Decimal('0.01'))

    @property
    def readiness_score(self):
        vals=[self.material_readiness_percent,self.accessories_readiness_percent,self.qc_readiness_percent,self.finishing_readiness_percent]
        return (sum(vals,Decimal('0'))/Decimal(len(vals))).quantize(Decimal('0.01'))

    @property
    def gate_passed(self):
        return self.final_decision in {'TAKE_QUICK_ORDER','TAKE_WITH_RISK'} and self.approval_id and self.approval.status=='APPROVED'

    def calculate_recommendation(self):
        if self.margin_percent < self.minimum_margin_percent:
            return 'REJECT'
        if self.free_minutes < self.required_minutes:
            return 'HOLD'
        if self.material_readiness_percent < 80 or self.accessories_readiness_percent < 80:
            return 'HOLD'
        if self.current_confirmed_load_percent >= 95:
            return 'REJECT'
        if self.rush_risk_percent >= 60 or self.readiness_score < 85:
            return 'TAKE_WITH_RISK'
        return 'TAKE_QUICK_ORDER'

    def save(self,*args,**kwargs):
        self.system_recommendation=self.calculate_recommendation()
        super().save(*args,**kwargs)

    def __str__(self):
        return f'{self.reference} - {self.final_decision}'


class FileAccessLog(models.Model):
    ACTIONS=[(x,x.title()) for x in ['VIEW','PREVIEW','DOWNLOAD','PRINT']]
    user=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    resource_type=models.CharField(max_length=60)
    resource_id=models.PositiveBigIntegerField()
    file_name=models.CharField(max_length=255)
    action=models.CharField(max_length=20,choices=ACTIONS)
    reference=models.CharField(max_length=180,blank=True,db_index=True)
    ip=models.GenericIPAddressField(null=True,blank=True)
    user_agent=models.CharField(max_length=500,blank=True)
    # Refused attempts are the security-relevant ones. Previously the log was
    # written only after the permission check passed, so a denial left no trace.
    granted=models.BooleanField(default=True,db_index=True)
    denial_reason=models.CharField(max_length=255,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering=['-created_at']
        indexes=[
            models.Index(fields=['user','-created_at'],name='idx_file_access_user'),
            models.Index(fields=['granted','-created_at'],name='idx_file_access_granted'),
        ]

    def __str__(self):
        verdict='' if self.granted else ' DENIED'
        return f'{self.action} {self.file_name} by {self.user or "anonymous"}{verdict}'


class BuyerDeliverySLA(TimeStamped):
    STATUS=[(x,x.replace('_',' ').title()) for x in ['ORDER_CONFIRMED','PLANNING','PRODUCTION','QC','PACKED','DISPATCHED','IN_TRANSIT','OUT_FOR_DELIVERY','DELIVERED','DELIVERY_CONFIRMED','OVERDUE','EXCEPTION_APPROVED']]
    order=models.OneToOneField(MasterOrder,on_delete=models.CASCADE,related_name='delivery_sla')
    buyer_name=models.CharField(max_length=180)
    contact_person=models.CharField(max_length=160,blank=True)
    phone=models.CharField(max_length=80,blank=True)
    email=models.EmailField(blank=True)
    street=models.CharField(max_length=255,blank=True)
    city=models.CharField(max_length=120,blank=True)
    state=models.CharField(max_length=120,blank=True)
    postal_code=models.CharField(max_length=40,blank=True)
    country=models.CharField(max_length=120,blank=True)
    location_text=models.CharField(max_length=255,blank=True)

    confirmed_at=models.DateTimeField(default=timezone.now)
    max_delivery_days=models.PositiveSmallIntegerField(default=15)
    dispatch_target_date=models.DateField(null=True,blank=True)
    delivery_deadline=models.DateField(null=True,blank=True)
    expected_delivery_date=models.DateField(null=True,blank=True)
    actual_dispatch_at=models.DateTimeField(null=True,blank=True)
    actual_delivery_at=models.DateTimeField(null=True,blank=True)

    courier=models.CharField(max_length=120,blank=True)
    tracking_number=models.CharField(max_length=180,blank=True)
    shipping_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    status=models.CharField(max_length=40,choices=STATUS,default='ORDER_CONFIRMED',db_index=True)

    exception_required=models.BooleanField(default=False)
    exception_reason=models.TextField(blank=True)
    exception_new_delivery_date=models.DateField(null=True,blank=True)
    exception_approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL,related_name='delivery_sla_exceptions')

    receiver_name=models.CharField(max_length=160,blank=True)
    proof_of_delivery=models.FileField(upload_to='delivery/pod/%Y/%m/',blank=True)
    buyer_signature=models.FileField(upload_to='delivery/signatures/%Y/%m/',blank=True)
    delivery_photo=models.FileField(upload_to='delivery/photos/%Y/%m/',blank=True)
    gps_location=models.CharField(max_length=180,blank=True)
    courier_confirmation=models.CharField(max_length=255,blank=True)
    confirmed_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='delivery_confirmations')
    confirmed_delivery_at=models.DateTimeField(null=True,blank=True)

    @property
    def effective_deadline(self):
        if self.exception_approval_id and self.exception_approval.status=='APPROVED' and self.exception_new_delivery_date:
            return self.exception_new_delivery_date
        return self.delivery_deadline

    @property
    def days_remaining(self):
        deadline=self.effective_deadline
        if not deadline: return None
        return (deadline-timezone.localdate()).days

    @property
    def overdue(self):
        if self.status in {'DELIVERED','DELIVERY_CONFIRMED'}:
            return False
        d=self.days_remaining
        return d is not None and d < 0

    @property
    def can_complete_order(self):
        return self.status=='DELIVERY_CONFIRMED' and bool(self.actual_delivery_at or self.confirmed_delivery_at)

    def save(self,*args,**kwargs):
        if not self.delivery_deadline and self.confirmed_at:
            self.delivery_deadline=(self.confirmed_at + timedelta(days=self.max_delivery_days)).date()
        if not self.dispatch_target_date and self.confirmed_at:
            self.dispatch_target_date=(self.confirmed_at + timedelta(days=min(12,self.max_delivery_days))).date()
        if self.expected_delivery_date and self.delivery_deadline and self.expected_delivery_date > self.delivery_deadline:
            self.exception_required=True
        if self.overdue:
            self.status='OVERDUE'
        super().save(*args,**kwargs)

    def __str__(self):
        return f'{self.order.master_order_id} → {self.buyer_name}'


class ProfitBeforeSpendControl(TimeStamped):
    CATEGORIES=[(x,x.replace('_',' ').title()) for x in [
        'MATERIAL','ACCESSORIES','LABOUR','PRODUCTION','MACHINE_MAINTENANCE','UTILITY',
        'FINISHING_PACKING','LOGISTICS_FREIGHT','QUALITY_COMPLIANCE','SOURCING',
        'SAMPLE','MERCHANDISING','FINANCE_BANK','COMMISSION','OTHER'
    ]]
    DECISIONS=[(x,x.replace('_',' ').title()) for x in ['PENDING','ALLOW','ALLOW_WITH_APPROVAL','HOLD','BLOCK']]
    reference=models.CharField(max_length=100,unique=True)
    order=models.ForeignKey(MasterOrder,null=True,blank=True,on_delete=models.SET_NULL,related_name='pre_spend_checks')
    opportunity=models.ForeignKey('BuyerOpportunity',null=True,blank=True,on_delete=models.SET_NULL,related_name='pre_spend_checks')
    spend_category=models.CharField(max_length=40,choices=CATEGORIES)
    description=models.CharField(max_length=255)
    vendor=models.CharField(max_length=180,blank=True)
    currency=models.CharField(max_length=10,default='USD')
    requested_amount=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    committed_amount=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    revenue_snapshot=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    base_cost_snapshot=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    prior_approved_spend=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    minimum_margin_percent=models.DecimalField(max_digits=6,decimal_places=2,default=10)
    system_decision=models.CharField(max_length=30,choices=DECISIONS,default='PENDING',db_index=True)
    final_decision=models.CharField(max_length=30,choices=DECISIONS,default='PENDING',db_index=True)
    reason=models.TextField(blank=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL,related_name='profit_before_spend_checks')
    requested_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='pre_spend_requested')
    reviewed_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='pre_spend_reviewed')
    reviewed_at=models.DateTimeField(null=True,blank=True)

    @property
    def projected_total_cost(self):
        return (self.base_cost_snapshot+self.prior_approved_spend+self.requested_amount).quantize(Decimal('0.01'))

    @property
    def projected_profit(self):
        return (self.revenue_snapshot-self.projected_total_cost).quantize(Decimal('0.01'))

    @property
    def projected_margin_percent(self):
        if self.revenue_snapshot <= 0: return Decimal('0.00')
        return ((self.projected_profit/self.revenue_snapshot)*100).quantize(Decimal('0.01'))

    @property
    def can_spend(self):
        if self.final_decision=='ALLOW':
            return True
        return self.final_decision=='ALLOW_WITH_APPROVAL' and self.approval_id and self.approval.status=='APPROVED'

    def calculate_decision(self):
        if self.revenue_snapshot <= 0:
            return 'BLOCK'
        if self.projected_profit < 0:
            return 'BLOCK'
        if self.projected_margin_percent < self.minimum_margin_percent:
            return 'HOLD'
        buffer=self.projected_margin_percent-self.minimum_margin_percent
        if buffer < Decimal('3'):
            return 'ALLOW_WITH_APPROVAL'
        return 'ALLOW'

    def save(self,*args,**kwargs):
        self.system_decision=self.calculate_decision()
        super().save(*args,**kwargs)

    def __str__(self):
        return f'{self.reference} - {self.system_decision}'


class StaffSelfServiceProfile(TimeStamped):
    user=models.OneToOneField(User,on_delete=models.CASCADE,related_name='staff_self_service_profile')
    employee=models.OneToOneField(Employee,on_delete=models.CASCADE,related_name='self_service_profile')
    company=models.CharField(max_length=180,default='Emerald Rozalia Limited')
    factory_unit=models.CharField(max_length=180,blank=True)
    designation=models.CharField(max_length=160,blank=True)
    join_date=models.DateField(null=True,blank=True)
    payout_day=models.PositiveSmallIntegerField(default=24)
    currency=models.CharField(max_length=10,default='EUR')
    language=models.CharField(max_length=40,default='English')
    country=models.CharField(max_length=80,default='Ireland')
    profile_photo=models.FileField(upload_to='staff/profile/%Y/%m/',blank=True)
    emergency_contact=models.CharField(max_length=180,blank=True)
    phone=models.CharField(max_length=80,blank=True)
    email=models.EmailField(blank=True)
    active=models.BooleanField(default=True,db_index=True)

class StaffApplication(TimeStamped):
    TYPES=[(x,x.replace('_',' ').title()) for x in ['LEAVE','OVERTIME','EXPENSE','TRAINING','ASSET_EQUIPMENT','HR_SUPPORT','GRIEVANCE','OTHER']]
    STATUS=[(x,x.title()) for x in ['DRAFT','SUBMITTED','PENDING','APPROVED','RETURNED','REJECTED','CANCELLED']]
    application_no=models.CharField(max_length=80,unique=True)
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name='self_service_applications')
    application_type=models.CharField(max_length=40,choices=TYPES)
    subject=models.CharField(max_length=220)
    details=models.TextField(blank=True)
    status=models.CharField(max_length=20,choices=STATUS,default='SUBMITTED',db_index=True)
    submitted_at=models.DateTimeField(default=timezone.now)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    attachment=models.FileField(upload_to='staff/applications/%Y/%m/',blank=True)

class StaffDocument(TimeStamped):
    TYPES=[(x,x.replace('_',' ').title()) for x in [
        'ID_CARD','APPOINTMENT_LETTER','JOINING_LETTER','COMPANY_HANDBOOK','PAYSLIP',
        'PASSPORT','WORK_PERMIT','DRIVING_LICENSE','TRAINING_CERTIFICATE','OTHER'
    ]]
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name='self_service_documents')
    document_type=models.CharField(max_length=40,choices=TYPES)
    title=models.CharField(max_length=220)
    reference=models.CharField(max_length=120,blank=True,db_index=True)
    version=models.CharField(max_length=20,default='1.0')
    issue_date=models.DateField(null=True,blank=True)
    effective_date=models.DateField(null=True,blank=True)
    expires_at=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=30,default='ACTIVE',db_index=True)
    file=models.FileField(upload_to='staff/documents/%Y/%m/',blank=True)
    confidential=models.BooleanField(default=True)
    uploaded_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    @property
    def days_to_expiry(self):
        if not self.expires_at: return None
        return (self.expires_at-timezone.localdate()).days

class StaffDutySummary(TimeStamped):
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name='duty_summaries')
    period_start=models.DateField()
    period_end=models.DateField()
    approved_minutes=models.PositiveIntegerField(default=0)
    scheduled_minutes=models.PositiveIntegerField(default=0)
    overtime_minutes=models.PositiveIntegerField(default=0)
    late_minutes=models.PositiveIntegerField(default=0)
    absent_minutes=models.PositiveIntegerField(default=0)

class StaffPayrollSummary(TimeStamped):
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name='payroll_summaries')
    payroll_month=models.DateField()
    gross_pay=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    net_pay=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    currency=models.CharField(max_length=10,default='EUR')
    payout_date=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=30,default='PENDING',db_index=True)
    payslip=models.ForeignKey(StaffDocument,null=True,blank=True,on_delete=models.SET_NULL,related_name='payroll_records')

class StaffScheduleEntry(TimeStamped):
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name='schedule_entries')
    date=models.DateField()
    start_time=models.TimeField(null=True,blank=True)
    break_start=models.TimeField(null=True,blank=True)
    break_end=models.TimeField(null=True,blank=True)
    end_time=models.TimeField(null=True,blank=True)
    title=models.CharField(max_length=180,default='Work Shift')
    location=models.CharField(max_length=180,blank=True)
    status=models.CharField(max_length=30,default='SCHEDULED',db_index=True)

class StaffNotification(TimeStamped):
    LEVELS=[(x,x) for x in ['INFO','SUCCESS','WARNING','RED']]
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name='self_service_notifications')
    title=models.CharField(max_length=220)
    body=models.TextField(blank=True)
    level=models.CharField(max_length=20,choices=LEVELS,default='INFO',db_index=True)
    reference=models.CharField(max_length=120,blank=True,db_index=True)
    read_at=models.DateTimeField(null=True,blank=True)

class StaffAnnouncement(TimeStamped):
    title=models.CharField(max_length=220)
    body=models.TextField()
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    starts_at=models.DateTimeField(default=timezone.now)
    expires_at=models.DateTimeField(null=True,blank=True)
    active=models.BooleanField(default=True,db_index=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class StaffEvent(TimeStamped):
    employee=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.CASCADE,related_name='self_service_events')
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.CASCADE)
    title=models.CharField(max_length=220)
    starts_at=models.DateTimeField()
    ends_at=models.DateTimeField(null=True,blank=True)
    location=models.CharField(max_length=180,blank=True)
    details=models.TextField(blank=True)


class HRRecruitment(TimeStamped):
    STATUS=[(x,x.replace('_',' ').title()) for x in ['OPEN','SHORTLISTED','INTERVIEW','OFFERED','HIRED','REJECTED','CLOSED']]
    candidate_name=models.CharField(max_length=180)
    email=models.EmailField(blank=True)
    phone=models.CharField(max_length=80,blank=True)
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    position=models.CharField(max_length=160)
    status=models.CharField(max_length=30,choices=STATUS,default='OPEN',db_index=True)
    joining_date=models.DateField(null=True,blank=True)
    cv=models.FileField(upload_to='hr/recruitment/%Y/%m/',blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class HRLeaveRequest(TimeStamped):
    STATUS=[(x,x.title()) for x in ['PENDING','APPROVED','REJECTED','CANCELLED','RETURNED']]
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name='hr_leave_requests')
    leave_type=models.CharField(max_length=80,default='Annual Leave')
    start_date=models.DateField()
    end_date=models.DateField()
    reason=models.TextField(blank=True)
    status=models.CharField(max_length=20,choices=STATUS,default='PENDING',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    attachment=models.FileField(upload_to='hr/leave/%Y/%m/',blank=True)

class HRTrainingRecord(TimeStamped):
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name='hr_training')
    course=models.CharField(max_length=220)
    provider=models.CharField(max_length=180,blank=True)
    due_date=models.DateField(null=True,blank=True)
    completed_date=models.DateField(null=True,blank=True)
    cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    status=models.CharField(max_length=30,default='DUE',db_index=True)
    certificate=models.FileField(upload_to='hr/training/%Y/%m/',blank=True)

class HRPerformanceReview(TimeStamped):
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name='performance_reviews')
    period=models.CharField(max_length=80)
    score=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    productivity_percent=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    notes=models.TextField(blank=True)
    reviewed_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class HRComplaintIncident(TimeStamped):
    TYPES=[(x,x.title()) for x in ['COMPLAINT','GRIEVANCE','INCIDENT','DISCIPLINARY']]
    STATUS=[(x,x.title()) for x in ['OPEN','INVESTIGATING','ACTIONED','CLOSED']]
    reference=models.CharField(max_length=80,unique=True)
    employee=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL)
    case_type=models.CharField(max_length=30,choices=TYPES)
    subject=models.CharField(max_length=220)
    details=models.TextField(blank=True)
    severity=models.CharField(max_length=20,default='MEDIUM')
    status=models.CharField(max_length=30,choices=STATUS,default='OPEN',db_index=True)
    assigned_to=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    attachment=models.FileField(upload_to='hr/cases/%Y/%m/',blank=True)

class HRRecognitionReward(TimeStamped):
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name='recognitions')
    title=models.CharField(max_length=180)
    points=models.PositiveIntegerField(default=0)
    reward_value=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    reason=models.TextField(blank=True)
    awarded_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class HRInternalMobility(TimeStamped):
    TYPES=[(x,x.title()) for x in ['TRANSFER','PROMOTION','DEPARTMENT_CHANGE','RELOCATION']]
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name='mobility_records')
    movement_type=models.CharField(max_length=30,choices=TYPES,db_index=True)
    from_department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL,related_name='+')
    to_department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL,related_name='+')
    effective_date=models.DateField()
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    notes=models.TextField(blank=True)

class HRPolicyAcknowledgement(TimeStamped):
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name='policy_acknowledgements')
    policy_title=models.CharField(max_length=220)
    version=models.CharField(max_length=30,default='1.0')
    acknowledged_at=models.DateTimeField(null=True,blank=True)
    due_date=models.DateField(null=True,blank=True)

class HRSurveyResult(TimeStamped):
    employee=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL)
    survey_name=models.CharField(max_length=180)
    score=models.DecimalField(max_digits=5,decimal_places=2,default=0)
    response=models.TextField(blank=True)
    anonymous=models.BooleanField(default=False)

class HRWorkforcePlan(TimeStamped):
    department=models.ForeignKey(Department,on_delete=models.CASCADE)
    period=models.DateField()
    required_headcount=models.PositiveIntegerField(default=0)
    current_headcount=models.PositiveIntegerField(default=0)
    planned_headcount=models.PositiveIntegerField(default=0)
    notes=models.TextField(blank=True)


class AttendanceShift(TimeStamped):
    CATEGORIES=[(x,x.title()) for x in ['STAFF','OPERATOR','HELPER']]
    code=models.CharField(max_length=40,unique=True)
    name=models.CharField(max_length=120)
    employee_category=models.CharField(max_length=20,choices=CATEGORIES)
    check_in=models.TimeField()
    break1_in=models.TimeField()
    break1_out=models.TimeField()
    break2_in=models.TimeField(null=True,blank=True)
    break2_out=models.TimeField(null=True,blank=True)
    check_out=models.TimeField()
    mandatory_minutes=models.PositiveIntegerField()
    grace_minutes=models.PositiveIntegerField(default=10)
    ot_break_minutes=models.PositiveIntegerField(default=30)
    active=models.BooleanField(default=True,db_index=True)

class AttendanceGatePass(TimeStamped):
    TYPES=[('PAID','Office Gate Pass (Paid)'),('UNPAID','Gate Pass (Unpaid)')]
    STATUS=[(x,x.title()) for x in ['PENDING','APPROVED','REJECTED','RETURNED']]
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name='gate_passes')
    pass_type=models.CharField(max_length=20,choices=TYPES,db_index=True)
    out_at=models.DateTimeField()
    in_at=models.DateTimeField(null=True,blank=True)
    reason=models.CharField(max_length=255)
    status=models.CharField(max_length=20,choices=STATUS,default='PENDING',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    @property
    def minutes(self):
        if not self.in_at: return 0
        return max(int((self.in_at-self.out_at).total_seconds()//60),0)

class AttendanceOvertime(TimeStamped):
    STATUS=[(x,x.title()) for x in ['PENDING','APPROVED','REJECTED','PAID']]
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name='overtime_records')
    work_date=models.DateField(db_index=True)
    start_at=models.DateTimeField()
    end_at=models.DateTimeField()
    minutes=models.PositiveIntegerField(default=0)
    reason=models.CharField(max_length=255,blank=True)
    status=models.CharField(max_length=20,choices=STATUS,default='PENDING',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)

    def save(self,*args,**kwargs):
        if self.end_at and self.start_at:
            self.minutes=max(int((self.end_at-self.start_at).total_seconds()//60),0)
        super().save(*args,**kwargs)

class AttendanceNPT(TimeStamped):
    CATEGORIES=[(x,x.replace('_',' ').title()) for x in ['MACHINE_BREAKDOWN','POWER_OUTAGE','MATERIAL_SHORTAGE','QUALITY_HOLD','WAITING','OTHER']]
    employee=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL,related_name='npt_records')
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    work_date=models.DateField(db_index=True)
    category=models.CharField(max_length=40,choices=CATEGORIES)
    minutes=models.PositiveIntegerField(default=0)
    reason=models.CharField(max_length=255)
    cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    actioned=models.BooleanField(default=False,db_index=True)
    actioned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class AttendanceHoliday(TimeStamped):
    name=models.CharField(max_length=180)
    holiday_date=models.DateField(unique=True)
    paid=models.BooleanField(default=True)
    country=models.CharField(max_length=80,default='Bangladesh')
    notes=models.CharField(max_length=255,blank=True)

class AttendanceDeviceStatus(TimeStamped):
    device=models.OneToOneField(DeviceIntegration,on_delete=models.CASCADE,related_name='attendance_status')
    online=models.BooleanField(default=False)
    last_sync_at=models.DateTimeField(null=True,blank=True)
    last_record_at=models.DateTimeField(null=True,blank=True)
    records_today=models.PositiveIntegerField(default=0)
    error_message=models.CharField(max_length=255,blank=True)

class AttendanceCCTVFeed(TimeStamped):
    name=models.CharField(max_length=140)
    location=models.CharField(max_length=180)
    camera_ref=models.CharField(max_length=120,blank=True)
    stream_url=models.CharField(max_length=500,blank=True)
    active=models.BooleanField(default=True,db_index=True)
    thumbnail=models.FileField(upload_to='attendance/cctv/%Y/%m/',blank=True)
    last_seen_at=models.DateTimeField(null=True,blank=True)

class AttendanceManualAdjustment(TimeStamped):
    employee=models.ForeignKey(Employee,on_delete=models.CASCADE,related_name='attendance_adjustments')
    work_date=models.DateField(db_index=True)
    field_name=models.CharField(max_length=80)
    old_value=models.CharField(max_length=180,blank=True)
    new_value=models.CharField(max_length=180)
    reason=models.TextField()
    requested_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='attendance_adjustment_requests')
    approval=models.ForeignKey(ApprovalRequest,on_delete=models.PROTECT,related_name='attendance_adjustments')
    applied_at=models.DateTimeField(null=True,blank=True)


class CuttingPlan(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    STATUS=[(x,x.replace('_',' ').title()) for x in ['DRAFT','PENDING_APPROVAL','APPROVED','IN_PROGRESS','QC','COMPLETED','HOLD']]
    plan_no=models.CharField(max_length=80,unique=True)
    order=models.ForeignKey(MasterOrder,on_delete=models.CASCADE,related_name='cutting_plans')
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    product=models.CharField(max_length=180)
    colour=models.CharField(max_length=100,blank=True)
    size_range=models.CharField(max_length=120,blank=True)
    planned_qty=models.PositiveIntegerField(default=0)
    target_date=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='DRAFT',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class CuttingFabricIssue(TimeStamped):
    plan=models.ForeignKey(CuttingPlan,on_delete=models.CASCADE,related_name='fabric_issues')
    stock_item=models.ForeignKey(StockItem,null=True,blank=True,on_delete=models.SET_NULL)
    roll_no=models.CharField(max_length=100)
    lot_no=models.CharField(max_length=100,blank=True)
    shade=models.CharField(max_length=80,blank=True)
    unit=models.CharField(max_length=20,default='METRE')
    issued_qty=models.DecimalField(max_digits=14,decimal_places=3,default=0)
    consumed_qty=models.DecimalField(max_digits=14,decimal_places=3,default=0)
    returned_qty=models.DecimalField(max_digits=14,decimal_places=3,default=0)
    stock_out_scan=models.CharField(max_length=180,blank=True)
    issued_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class CuttingLay(TimeStamped):
    plan=models.ForeignKey(CuttingPlan,on_delete=models.CASCADE,related_name='lays')
    lay_no=models.CharField(max_length=80)
    marker_no=models.CharField(max_length=80,blank=True)
    marker_length=models.DecimalField(max_digits=12,decimal_places=3,default=0)
    marker_efficiency=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    ply_count=models.PositiveIntegerField(default=0)
    fabric_qty=models.DecimalField(max_digits=14,decimal_places=3,default=0)
    target_pieces=models.PositiveIntegerField(default=0)
    actual_pieces=models.PositiveIntegerField(default=0)
    started_at=models.DateTimeField(null=True,blank=True,db_index=True)
    completed_at=models.DateTimeField(null=True,blank=True,db_index=True)

class CuttingBundle(TimeStamped):
    STATUS=[(x,x.title()) for x in ['CREATED','QC','PASSED','REJECTED','TRANSFERRED']]
    plan=models.ForeignKey(CuttingPlan,on_delete=models.CASCADE,related_name='bundles')
    lay=models.ForeignKey(CuttingLay,null=True,blank=True,on_delete=models.SET_NULL,related_name='bundles')
    bundle_no=models.CharField(max_length=100,unique=True)
    barcode=models.CharField(max_length=180,unique=True)
    size=models.CharField(max_length=50,blank=True)
    colour=models.CharField(max_length=80,blank=True)
    quantity=models.PositiveIntegerField(default=0)
    qc_pass_qty=models.PositiveIntegerField(default=0)
    reject_qty=models.PositiveIntegerField(default=0)
    recut_qty=models.PositiveIntegerField(default=0)
    status=models.CharField(max_length=30,choices=STATUS,default='CREATED',db_index=True)
    stock_in_scan=models.CharField(max_length=180,blank=True)
    stock_out_scan=models.CharField(max_length=180,blank=True)
    next_department=models.CharField(max_length=100,default='Sewing')

class CuttingProductionEntry(TimeStamped):
    plan=models.ForeignKey(CuttingPlan,on_delete=models.CASCADE,related_name='production_entries')
    work_date=models.DateField(db_index=True)
    employee=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL)
    machine=models.ForeignKey(AssetMachine,null=True,blank=True,on_delete=models.SET_NULL)
    process=models.CharField(max_length=100,default='Cutting')
    target_qty=models.PositiveIntegerField(default=0)
    actual_qty=models.PositiveIntegerField(default=0)
    process_minutes=models.PositiveIntegerField(default=0)
    npt_minutes=models.PositiveIntegerField(default=0)
    cost_per_minute=models.DecimalField(max_digits=12,decimal_places=4,default=0)
    process_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    manual_entry=models.BooleanField(default=False)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)

    def save(self,*args,**kwargs):
        self.process_cost=(Decimal(self.process_minutes)*self.cost_per_minute).quantize(Decimal('0.01'))
        super().save(*args,**kwargs)

class CuttingVariance(TimeStamped):
    TYPES=[(x,x.title()) for x in ['FABRIC','QUANTITY','VALUE','WASTAGE','REJECT','BUNDLE']]
    plan=models.ForeignKey(CuttingPlan,on_delete=models.CASCADE,related_name='variances')
    variance_type=models.CharField(max_length=30,choices=TYPES)
    expected=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    actual=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    variance=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    value_variance_bdt=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    reason=models.CharField(max_length=255,blank=True)
    actioned=models.BooleanField(default=False,db_index=True)
    actioned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    actioned_at=models.DateTimeField(null=True,blank=True)

    def save(self,*args,**kwargs):
        self.variance=self.actual-self.expected
        super().save(*args,**kwargs)

class CuttingAutoReport(TimeStamped):
    SLOTS=[('08:00','08:00'),('13:00','13:00'),('20:00','20:00')]
    report_date=models.DateField(db_index=True)
    slot=models.CharField(max_length=10,choices=SLOTS,db_index=True)
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    summary=models.JSONField(default=dict)
    outstanding_alerts=models.PositiveIntegerField(default=0)
    pending_actions=models.PositiveIntegerField(default=0)
    escalated_items=models.PositiveIntegerField(default=0)
    generated_at=models.DateTimeField(auto_now_add=True,db_index=True)
    generated_file=models.FileField(upload_to='cutting/auto-reports/%Y/%m/',blank=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['report_date','slot','department'],name='unique_cutting_auto_report_slot')]


class EmbroideryPlan(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    STATUS=[(x,x.replace('_',' ').title()) for x in ['DRAFT','PENDING_APPROVAL','APPROVED','SAMPLE','IN_PROGRESS','QC','COMPLETED','HOLD']]
    plan_no=models.CharField(max_length=80,unique=True)
    order=models.ForeignKey(MasterOrder,on_delete=models.CASCADE,related_name='embroidery_plans')
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    product=models.CharField(max_length=180)
    style_no=models.CharField(max_length=100,blank=True)
    design_no=models.CharField(max_length=100,blank=True)
    colour=models.CharField(max_length=100,blank=True)
    size_range=models.CharField(max_length=120,blank=True)
    stitch_count=models.PositiveIntegerField(default=0)
    planned_qty=models.PositiveIntegerField(default=0)
    target_date=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='DRAFT',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    artwork=models.FileField(upload_to='embroidery/artwork/%Y/%m/',blank=True)
    program_file=models.FileField(upload_to='embroidery/programs/%Y/%m/',blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class EmbroideryBundleScan(TimeStamped):
    DIRECTIONS=[('IN','BUNDLE IN SCAN'),('OUT','BUNDLE OUT SCAN')]
    STATUS=[(x,x.replace('_',' ').title()) for x in ['VALID','BLOCKED','DUPLICATE','MISMATCH','WRONG_DESTINATION']]
    plan=models.ForeignKey(EmbroideryPlan,on_delete=models.CASCADE,related_name='bundle_scans')
    bundle=models.ForeignKey(CuttingBundle,on_delete=models.PROTECT,related_name='embroidery_scans')
    direction=models.CharField(max_length=3,choices=DIRECTIONS,db_index=True)
    barcode=models.CharField(max_length=180,db_index=True)
    expected_qty=models.PositiveIntegerField(default=0)
    actual_qty=models.PositiveIntegerField(default=0)
    source_department=models.CharField(max_length=100,blank=True)
    destination_department=models.CharField(max_length=100,blank=True)
    employee=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL)
    machine=models.ForeignKey(AssetMachine,null=True,blank=True,on_delete=models.SET_NULL)
    scan_status=models.CharField(max_length=30,choices=STATUS,default='VALID')
    scanned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    scanned_at=models.DateTimeField(default=timezone.now,db_index=True)

class EmbroideryMaterialIssue(TimeStamped):
    plan=models.ForeignKey(EmbroideryPlan,on_delete=models.CASCADE,related_name='material_issues')
    stock_item=models.ForeignKey(StockItem,null=True,blank=True,on_delete=models.SET_NULL)
    material_type=models.CharField(max_length=80,default='THREAD')
    colour=models.CharField(max_length=80,blank=True)
    lot_no=models.CharField(max_length=100,blank=True)
    unit=models.CharField(max_length=20,default='PCS')
    issued_qty=models.DecimalField(max_digits=14,decimal_places=3,default=0)
    consumed_qty=models.DecimalField(max_digits=14,decimal_places=3,default=0)
    returned_qty=models.DecimalField(max_digits=14,decimal_places=3,default=0)
    stock_out_scan=models.CharField(max_length=180,blank=True)
    issued_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class EmbroiderySample(TimeStamped):
    STATUS=[(x,x.title()) for x in ['PENDING','APPROVED','REJECTED','REVISE']]
    plan=models.ForeignKey(EmbroideryPlan,on_delete=models.CASCADE,related_name='samples')
    sample_no=models.CharField(max_length=80)
    stitch_count=models.PositiveIntegerField(default=0)
    sample_qty=models.PositiveIntegerField(default=1)
    status=models.CharField(max_length=20,choices=STATUS,default='PENDING',db_index=True)
    remarks=models.TextField(blank=True)
    approved_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    approved_at=models.DateTimeField(null=True,blank=True)
    sample_image=models.FileField(upload_to='embroidery/samples/%Y/%m/',blank=True)

class EmbroideryProductionEntry(TimeStamped):
    plan=models.ForeignKey(EmbroideryPlan,on_delete=models.CASCADE,related_name='production_entries')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='embroidery_production')
    work_date=models.DateField(db_index=True)
    employee=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL)
    machine=models.ForeignKey(AssetMachine,null=True,blank=True,on_delete=models.SET_NULL)
    machine_heads=models.PositiveIntegerField(default=1)
    target_qty=models.PositiveIntegerField(default=0)
    actual_qty=models.PositiveIntegerField(default=0)
    stitch_count=models.PositiveIntegerField(default=0)
    process_minutes=models.PositiveIntegerField(default=0)
    npt_minutes=models.PositiveIntegerField(default=0)
    cost_per_minute=models.DecimalField(max_digits=12,decimal_places=4,default=0)
    thread_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    other_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    process_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    total_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    manual_entry=models.BooleanField(default=False)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)

    def save(self,*args,**kwargs):
        self.process_cost=(Decimal(self.process_minutes)*self.cost_per_minute).quantize(Decimal('0.01'))
        self.total_cost=(self.process_cost+self.thread_cost+self.other_cost).quantize(Decimal('0.01'))
        super().save(*args,**kwargs)

class EmbroideryQC(TimeStamped):
    STATUS=[(x,x.title()) for x in ['PASS','REJECT','REPAIR','REWORK']]
    plan=models.ForeignKey(EmbroideryPlan,on_delete=models.CASCADE,related_name='qc_records')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='embroidery_qc')
    inspected_qty=models.PositiveIntegerField(default=0)
    pass_qty=models.PositiveIntegerField(default=0)
    reject_qty=models.PositiveIntegerField(default=0)
    repair_qty=models.PositiveIntegerField(default=0)
    rework_qty=models.PositiveIntegerField(default=0)
    status=models.CharField(max_length=20,choices=STATUS,default='PASS',db_index=True)
    defect_reason=models.CharField(max_length=255,blank=True)
    inspected_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class EmbroideryVariance(TimeStamped):
    TYPES=[(x,x.title()) for x in ['BUNDLE','QUANTITY','THREAD','VALUE','REJECT','REWORK']]
    plan=models.ForeignKey(EmbroideryPlan,on_delete=models.CASCADE,related_name='variances')
    variance_type=models.CharField(max_length=30,choices=TYPES)
    expected=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    actual=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    variance=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    value_variance_bdt=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    reason=models.CharField(max_length=255,blank=True)
    actioned=models.BooleanField(default=False,db_index=True)
    actioned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    actioned_at=models.DateTimeField(null=True,blank=True)

    def save(self,*args,**kwargs):
        self.variance=self.actual-self.expected
        super().save(*args,**kwargs)

class EmbroideryAutoReport(TimeStamped):
    SLOTS=[('08:00','08:00'),('13:00','13:00'),('20:00','20:00')]
    report_date=models.DateField(db_index=True)
    slot=models.CharField(max_length=10,choices=SLOTS,db_index=True)
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    summary=models.JSONField(default=dict)
    outstanding_alerts=models.PositiveIntegerField(default=0)
    pending_actions=models.PositiveIntegerField(default=0)
    escalated_items=models.PositiveIntegerField(default=0)
    generated_at=models.DateTimeField(auto_now_add=True,db_index=True)
    generated_file=models.FileField(upload_to='embroidery/auto-reports/%Y/%m/',blank=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['report_date','slot','department'],name='unique_embroidery_auto_report_slot')]


class LabelPlan(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    STATUS=[(x,x.replace('_',' ').title()) for x in ['DRAFT','PENDING_APPROVAL','APPROVED','SAMPLE','IN_PROGRESS','QC','READY','COMPLETED','HOLD']]
    LABEL_TYPES=[(x,x.replace('_',' ').title()) for x in [
        'MAIN_BRAND','SIZE','CARE_WASH','COMPOSITION','COUNTRY_OF_ORIGIN','WOVEN','PRINTED',
        'HEAT_TRANSFER','BARCODE','QR','PRICE_HANG_TAG','CUSTOM'
    ]]
    plan_no=models.CharField(max_length=80,unique=True)
    order=models.ForeignKey(MasterOrder,on_delete=models.CASCADE,related_name='label_plans')
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    buyer=models.CharField(max_length=180,blank=True)
    brand=models.CharField(max_length=180,blank=True)
    style_no=models.CharField(max_length=100,blank=True)
    product=models.CharField(max_length=180)
    colour=models.CharField(max_length=100,blank=True)
    size_range=models.CharField(max_length=120,blank=True)
    label_type=models.CharField(max_length=40,choices=LABEL_TYPES)
    label_code=models.CharField(max_length=100)
    version=models.CharField(max_length=30,default='1.0')
    planned_qty=models.PositiveIntegerField(default=0)
    target_date=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='DRAFT',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    artwork=models.FileField(upload_to='label/artwork/%Y/%m/',blank=True)
    specification=models.FileField(upload_to='label/specification/%Y/%m/',blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class LabelProof(TimeStamped):
    STATUS=[(x,x.title()) for x in ['PENDING','APPROVED','REJECTED','REVISE']]
    plan=models.ForeignKey(LabelPlan,on_delete=models.CASCADE,related_name='proofs')
    proof_no=models.CharField(max_length=100)
    version=models.CharField(max_length=30,default='1.0')
    proof_file=models.FileField(upload_to='label/proofs/%Y/%m/',blank=True)
    sample_image=models.FileField(upload_to='label/samples/%Y/%m/',blank=True)
    status=models.CharField(max_length=20,choices=STATUS,default='PENDING',db_index=True)
    remarks=models.TextField(blank=True)
    approved_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    approved_at=models.DateTimeField(null=True,blank=True)

class LabelMaterialIssue(TimeStamped):
    plan=models.ForeignKey(LabelPlan,on_delete=models.CASCADE,related_name='material_issues')
    stock_item=models.ForeignKey(StockItem,null=True,blank=True,on_delete=models.SET_NULL)
    supplier=models.CharField(max_length=180,blank=True)
    batch_no=models.CharField(max_length=100,blank=True)
    lot_no=models.CharField(max_length=100,blank=True)
    unit=models.CharField(max_length=20,default='PCS')
    issued_qty=models.DecimalField(max_digits=14,decimal_places=3,default=0)
    consumed_qty=models.DecimalField(max_digits=14,decimal_places=3,default=0)
    returned_qty=models.DecimalField(max_digits=14,decimal_places=3,default=0)
    stock_out_scan=models.CharField(max_length=180,blank=True)
    issued_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class LabelProductionEntry(TimeStamped):
    plan=models.ForeignKey(LabelPlan,on_delete=models.CASCADE,related_name='production_entries')
    work_date=models.DateField(db_index=True)
    employee=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL)
    machine=models.ForeignKey(AssetMachine,null=True,blank=True,on_delete=models.SET_NULL)
    target_qty=models.PositiveIntegerField(default=0)
    actual_qty=models.PositiveIntegerField(default=0)
    process_minutes=models.PositiveIntegerField(default=0)
    npt_minutes=models.PositiveIntegerField(default=0)
    cost_per_minute=models.DecimalField(max_digits=12,decimal_places=4,default=0)
    material_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    other_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    process_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    total_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    manual_entry=models.BooleanField(default=False)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)

    def save(self,*args,**kwargs):
        self.process_cost=(Decimal(self.process_minutes)*self.cost_per_minute).quantize(Decimal('0.01'))
        self.total_cost=(self.process_cost+self.material_cost+self.other_cost).quantize(Decimal('0.01'))
        super().save(*args,**kwargs)

class LabelQC(TimeStamped):
    STATUS=[(x,x.title()) for x in ['PASS','REJECT','REWORK','HOLD']]
    plan=models.ForeignKey(LabelPlan,on_delete=models.CASCADE,related_name='qc_records')
    inspected_qty=models.PositiveIntegerField(default=0)
    pass_qty=models.PositiveIntegerField(default=0)
    reject_qty=models.PositiveIntegerField(default=0)
    rework_qty=models.PositiveIntegerField(default=0)
    status=models.CharField(max_length=20,choices=STATUS,default='PASS',db_index=True)
    defect_reason=models.CharField(max_length=255,blank=True)
    checked_version=models.CharField(max_length=30,blank=True)
    checked_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class LabelAllocation(TimeStamped):
    STATUS=[(x,x.replace('_',' ').title()) for x in ['ALLOCATED','ISSUED','PART_USED','RETURNED','CLOSED','BLOCKED']]
    plan=models.ForeignKey(LabelPlan,on_delete=models.CASCADE,related_name='allocations')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='label_allocations')
    order=models.ForeignKey(MasterOrder,on_delete=models.CASCADE,related_name='label_allocations')
    allocated_qty=models.PositiveIntegerField(default=0)
    issued_qty=models.PositiveIntegerField(default=0)
    used_qty=models.PositiveIntegerField(default=0)
    returned_qty=models.PositiveIntegerField(default=0)
    rejected_qty=models.PositiveIntegerField(default=0)
    label_in_scan=models.CharField(max_length=180,blank=True)
    label_out_scan=models.CharField(max_length=180,blank=True)
    destination_department=models.CharField(max_length=100,blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='ALLOCATED',db_index=True)
    issued_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    @property
    def balance_qty(self):
        return max(self.allocated_qty-self.used_qty-self.returned_qty-self.rejected_qty,0)

class LabelVariance(TimeStamped):
    TYPES=[(x,x.replace('_',' ').title()) for x in ['QUANTITY','VALUE','VERSION','WRONG_LABEL','DUPLICATE_ISSUE','WASTAGE','REJECT']]
    plan=models.ForeignKey(LabelPlan,on_delete=models.CASCADE,related_name='variances')
    variance_type=models.CharField(max_length=40,choices=TYPES)
    expected=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    actual=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    variance=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    value_variance_bdt=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    reason=models.CharField(max_length=255,blank=True)
    actioned=models.BooleanField(default=False,db_index=True)
    actioned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    actioned_at=models.DateTimeField(null=True,blank=True)

    def save(self,*args,**kwargs):
        self.variance=self.actual-self.expected
        super().save(*args,**kwargs)

class LabelAutoReport(TimeStamped):
    SLOTS=[('08:00','08:00'),('13:00','13:00'),('20:00','20:00')]
    report_date=models.DateField(db_index=True)
    slot=models.CharField(max_length=10,choices=SLOTS,db_index=True)
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    summary=models.JSONField(default=dict)
    outstanding_alerts=models.PositiveIntegerField(default=0)
    pending_actions=models.PositiveIntegerField(default=0)
    escalated_items=models.PositiveIntegerField(default=0)
    generated_at=models.DateTimeField(auto_now_add=True,db_index=True)
    generated_file=models.FileField(upload_to='label/auto-reports/%Y/%m/',blank=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['report_date','slot','department'],name='unique_label_auto_report_slot')]


class QCInspectionPlan(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    STAGES=[(x,x.replace('_',' ').title()) for x in [
        'INCOMING_MATERIAL','CUTTING','EMBROIDERY','SEWING_INLINE','LABEL_ACCESSORY',
        'FINISHING','FINAL_INSPECTION','PACKING','PRE_SHIPMENT'
    ]]
    STATUS=[(x,x.replace('_',' ').title()) for x in ['DRAFT','PENDING_APPROVAL','APPROVED','IN_PROGRESS','HOLD','COMPLETED','CLOSED']]
    plan_no=models.CharField(max_length=90,unique=True)
    order=models.ForeignKey(MasterOrder,on_delete=models.CASCADE,related_name='qc_inspection_plans')
    stage=models.CharField(max_length=40,choices=STAGES,db_index=True)
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    buyer=models.CharField(max_length=180,blank=True)
    style_no=models.CharField(max_length=100,blank=True)
    product=models.CharField(max_length=180,blank=True)
    colour=models.CharField(max_length=100,blank=True)
    size_range=models.CharField(max_length=120,blank=True)
    specification_version=models.CharField(max_length=40,default='1.0')
    aql_level=models.CharField(max_length=40,default='2.5')
    lot_size=models.PositiveIntegerField(default=0)
    sample_size=models.PositiveIntegerField(default=0)
    planned_inspection_date=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='DRAFT',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    specification_file=models.FileField(upload_to='qc/specifications/%Y/%m/',blank=True)
    approved_sample_file=models.FileField(upload_to='qc/approved-samples/%Y/%m/',blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class QCBundleScan(TimeStamped):
    DIRECTIONS=[('IN','QC BUNDLE IN SCAN'),('OUT','QC BUNDLE OUT SCAN')]
    STATUS=[(x,x.replace('_',' ').title()) for x in ['VALID','BLOCKED','DUPLICATE','MISMATCH','HOLD']]
    plan=models.ForeignKey(QCInspectionPlan,on_delete=models.CASCADE,related_name='bundle_scans')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='qc_scans')
    barcode=models.CharField(max_length=180,blank=True,db_index=True)
    direction=models.CharField(max_length=3,choices=DIRECTIONS,db_index=True)
    expected_qty=models.PositiveIntegerField(default=0)
    actual_qty=models.PositiveIntegerField(default=0)
    source_department=models.CharField(max_length=100,blank=True)
    destination_department=models.CharField(max_length=100,blank=True)
    scan_status=models.CharField(max_length=30,choices=STATUS,default='VALID')
    scanned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    scanned_at=models.DateTimeField(default=timezone.now,db_index=True)

class QCInspection(TimeStamped):
    RESULT=[(x,x.replace('_',' ').title()) for x in ['PASS','HOLD','REWORK','REJECT','CONDITIONAL_PASS']]
    plan=models.ForeignKey(QCInspectionPlan,on_delete=models.CASCADE,related_name='inspections')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='qc_inspections')
    inspector=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    inspected_qty=models.PositiveIntegerField(default=0)
    pass_qty=models.PositiveIntegerField(default=0)
    critical_defects=models.PositiveIntegerField(default=0)
    major_defects=models.PositiveIntegerField(default=0)
    minor_defects=models.PositiveIntegerField(default=0)
    rework_qty=models.PositiveIntegerField(default=0)
    reject_qty=models.PositiveIntegerField(default=0)
    measurement_fail_qty=models.PositiveIntegerField(default=0)
    shade_fail_qty=models.PositiveIntegerField(default=0)
    label_fail_qty=models.PositiveIntegerField(default=0)
    workmanship_fail_qty=models.PositiveIntegerField(default=0)
    packing_fail_qty=models.PositiveIntegerField(default=0)
    result=models.CharField(max_length=30,choices=RESULT,default='HOLD',db_index=True)
    comments=models.TextField(blank=True)
    photo=models.FileField(upload_to='qc/inspection-photos/%Y/%m/',blank=True)
    inspection_sheet=models.FileField(upload_to='qc/inspection-sheets/%Y/%m/',blank=True)
    completed_at=models.DateTimeField(default=timezone.now,db_index=True)

    @property
    def total_defects(self):
        return self.critical_defects+self.major_defects+self.minor_defects

    @property
    def defect_rate_percent(self):
        if not self.inspected_qty:
            return Decimal('0.00')
        return (Decimal(self.total_defects)/Decimal(self.inspected_qty)*100).quantize(Decimal('0.01'))

    @property
    def dhu(self):
        if not self.inspected_qty:
            return Decimal('0.00')
        return (Decimal(self.total_defects)/Decimal(self.inspected_qty)*100).quantize(Decimal('0.01'))

class QCDefect(TimeStamped):
    SEVERITY=[(x,x.title()) for x in ['CRITICAL','MAJOR','MINOR']]
    CATEGORIES=[(x,x.replace('_',' ').title()) for x in [
        'MEASUREMENT','WORKMANSHIP','SHADE_COLOUR','STITCHING','EMBROIDERY','LABEL',
        'ACCESSORY','FINISHING','APPEARANCE','PACKING','QUANTITY','MATERIAL','OTHER'
    ]]
    inspection=models.ForeignKey(QCInspection,on_delete=models.CASCADE,related_name='defects')
    defect_code=models.CharField(max_length=80,blank=True)
    category=models.CharField(max_length=40,choices=CATEGORIES)
    severity=models.CharField(max_length=20,choices=SEVERITY)
    description=models.CharField(max_length=255)
    quantity=models.PositiveIntegerField(default=1)
    root_cause=models.TextField(blank=True)
    corrective_action=models.TextField(blank=True)
    responsible_user=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    due_at=models.DateTimeField(null=True,blank=True)
    actioned=models.BooleanField(default=False,db_index=True)
    actioned_at=models.DateTimeField(null=True,blank=True)

class QCCAPA(TimeStamped):
    STATUS=[(x,x.replace('_',' ').title()) for x in ['OPEN','IN_PROGRESS','VERIFICATION','CLOSED','ESCALATED']]
    reference=models.CharField(max_length=100,unique=True)
    inspection=models.ForeignKey(QCInspection,on_delete=models.CASCADE,related_name='capas')
    root_cause=models.TextField()
    corrective_action=models.TextField()
    preventive_action=models.TextField(blank=True)
    responsible_user=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    due_at=models.DateTimeField(null=True,blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='OPEN',db_index=True)
    verified_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='verified_qc_capas')
    verified_at=models.DateTimeField(null=True,blank=True)

class QCReleaseGate(TimeStamped):
    DECISIONS=[(x,x.replace('_',' ').title()) for x in ['PENDING','RELEASE','RELEASE_WITH_APPROVAL','HOLD','REWORK','REJECT']]
    plan=models.OneToOneField(QCInspectionPlan,on_delete=models.CASCADE,related_name='release_gate')
    latest_inspection=models.ForeignKey(QCInspection,null=True,blank=True,on_delete=models.SET_NULL,related_name='release_gates')
    system_decision=models.CharField(max_length=30,choices=DECISIONS,default='PENDING',db_index=True)
    final_decision=models.CharField(max_length=30,choices=DECISIONS,default='PENDING',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL,related_name='qc_release_gates')
    decision_reason=models.TextField(blank=True)
    reviewed_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    reviewed_at=models.DateTimeField(null=True,blank=True)

    @property
    def gate_passed(self):
        if self.final_decision=='RELEASE':
            return True
        return self.final_decision=='RELEASE_WITH_APPROVAL' and self.approval_id and self.approval.status=='APPROVED'

    def calculate_decision(self):
        ins=self.latest_inspection
        if not ins:
            return 'PENDING'
        if ins.critical_defects > 0:
            return 'HOLD'
        if ins.result=='REJECT':
            return 'REJECT'
        if ins.result=='REWORK' or ins.rework_qty > 0:
            return 'REWORK'
        if ins.result=='PASS':
            return 'RELEASE'
        if ins.result=='CONDITIONAL_PASS':
            return 'RELEASE_WITH_APPROVAL'
        return 'HOLD'

    def save(self,*args,**kwargs):
        self.system_decision=self.calculate_decision()
        super().save(*args,**kwargs)

class QCAutoReport(TimeStamped):
    SLOTS=[('08:00','08:00'),('13:00','13:00'),('20:00','20:00')]
    report_date=models.DateField(db_index=True)
    slot=models.CharField(max_length=10,choices=SLOTS,db_index=True)
    stage=models.CharField(max_length=40,blank=True,db_index=True)
    summary=models.JSONField(default=dict)
    outstanding_alerts=models.PositiveIntegerField(default=0)
    pending_actions=models.PositiveIntegerField(default=0)
    escalated_items=models.PositiveIntegerField(default=0)
    generated_at=models.DateTimeField(auto_now_add=True,db_index=True)
    generated_file=models.FileField(upload_to='qc/auto-reports/%Y/%m/',blank=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['report_date','slot','stage'],name='unique_qc_auto_report_slot')]


class HandIronPlan(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    STATUS=[(x,x.replace('_',' ').title()) for x in ['DRAFT','PENDING_APPROVAL','APPROVED','IN_PROGRESS','QC','COMPLETED','HOLD']]
    plan_no=models.CharField(max_length=80,unique=True)
    order=models.ForeignKey(MasterOrder,on_delete=models.CASCADE,related_name='hand_iron_plans')
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    product=models.CharField(max_length=180)
    style_no=models.CharField(max_length=100,blank=True)
    colour=models.CharField(max_length=100,blank=True)
    size_range=models.CharField(max_length=120,blank=True)
    fabric_type=models.CharField(max_length=120,blank=True)
    min_temperature_c=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    max_temperature_c=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    planned_qty=models.PositiveIntegerField(default=0)
    target_date=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='DRAFT',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    instruction_file=models.FileField(upload_to='hand-iron/instructions/%Y/%m/',blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class HandIronBundleScan(TimeStamped):
    DIRECTIONS=[('IN','BUNDLE IN SCAN'),('OUT','BUNDLE OUT SCAN')]
    STATUS=[(x,x.replace('_',' ').title()) for x in ['VALID','BLOCKED','DUPLICATE','MISMATCH','QC_HOLD']]
    plan=models.ForeignKey(HandIronPlan,on_delete=models.CASCADE,related_name='bundle_scans')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='hand_iron_scans')
    direction=models.CharField(max_length=3,choices=DIRECTIONS,db_index=True)
    barcode=models.CharField(max_length=180,blank=True,db_index=True)
    expected_qty=models.PositiveIntegerField(default=0)
    actual_qty=models.PositiveIntegerField(default=0)
    source_department=models.CharField(max_length=100,blank=True)
    destination_department=models.CharField(max_length=100,blank=True)
    operator=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL)
    workstation=models.ForeignKey(AssetMachine,null=True,blank=True,on_delete=models.SET_NULL)
    scan_status=models.CharField(max_length=30,choices=STATUS,default='VALID')
    scanned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    scanned_at=models.DateTimeField(default=timezone.now,db_index=True)

class HandIronProductionEntry(TimeStamped):
    plan=models.ForeignKey(HandIronPlan,on_delete=models.CASCADE,related_name='production_entries')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='hand_iron_production')
    work_date=models.DateField(db_index=True)
    operator=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL)
    workstation=models.ForeignKey(AssetMachine,null=True,blank=True,on_delete=models.SET_NULL)
    target_qty=models.PositiveIntegerField(default=0)
    actual_qty=models.PositiveIntegerField(default=0)
    start_at=models.DateTimeField(null=True,blank=True)
    end_at=models.DateTimeField(null=True,blank=True)
    process_minutes=models.PositiveIntegerField(default=0)
    npt_minutes=models.PositiveIntegerField(default=0)
    actual_temperature_c=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    cost_per_minute=models.DecimalField(max_digits=12,decimal_places=4,default=0)
    labour_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    utility_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    process_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    total_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    manual_entry=models.BooleanField(default=False)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)

    def save(self,*args,**kwargs):
        if self.start_at and self.end_at:
            self.process_minutes=max(int((self.end_at-self.start_at).total_seconds()//60),0)
        self.process_cost=(Decimal(self.process_minutes)*self.cost_per_minute).quantize(Decimal('0.01'))
        self.total_cost=(self.process_cost+self.labour_cost+self.utility_cost).quantize(Decimal('0.01'))
        super().save(*args,**kwargs)

class HandIronQC(TimeStamped):
    RESULT=[(x,x.replace('_',' ').title()) for x in ['PASS','HOLD','REIRON','REJECT']]
    DEFECTS=[(x,x.replace('_',' ').title()) for x in ['SCORCH_BURN','SHINE_GLAZING','COLOUR_CHANGE','WATER_STEAM_MARK','CREASE','SHAPE_DISTORTION','OTHER']]
    plan=models.ForeignKey(HandIronPlan,on_delete=models.CASCADE,related_name='qc_records')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='hand_iron_qc')
    inspected_qty=models.PositiveIntegerField(default=0)
    pass_qty=models.PositiveIntegerField(default=0)
    reject_qty=models.PositiveIntegerField(default=0)
    reiron_qty=models.PositiveIntegerField(default=0)
    defect_type=models.CharField(max_length=40,choices=DEFECTS,blank=True)
    defect_reason=models.CharField(max_length=255,blank=True)
    result=models.CharField(max_length=20,choices=RESULT,default='PASS',db_index=True)
    checked_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    checked_at=models.DateTimeField(default=timezone.now,db_index=True)
    qc_photo=models.FileField(upload_to='hand-iron/qc/%Y/%m/',blank=True)

class HandIronVariance(TimeStamped):
    TYPES=[(x,x.replace('_',' ').title()) for x in ['QUANTITY','TEMPERATURE','VALUE','REJECT','REIRON','DAMAGE']]
    plan=models.ForeignKey(HandIronPlan,on_delete=models.CASCADE,related_name='variances')
    variance_type=models.CharField(max_length=30,choices=TYPES)
    expected=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    actual=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    variance=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    value_variance_bdt=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    reason=models.CharField(max_length=255,blank=True)
    actioned=models.BooleanField(default=False,db_index=True)
    actioned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    actioned_at=models.DateTimeField(null=True,blank=True)

    def save(self,*args,**kwargs):
        self.variance=self.actual-self.expected
        super().save(*args,**kwargs)

class HandIronAutoReport(TimeStamped):
    SLOTS=[('08:00','08:00'),('13:00','13:00'),('20:00','20:00')]
    report_date=models.DateField(db_index=True)
    slot=models.CharField(max_length=10,choices=SLOTS,db_index=True)
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    summary=models.JSONField(default=dict)
    outstanding_alerts=models.PositiveIntegerField(default=0)
    pending_actions=models.PositiveIntegerField(default=0)
    escalated_items=models.PositiveIntegerField(default=0)
    generated_at=models.DateTimeField(auto_now_add=True,db_index=True)
    generated_file=models.FileField(upload_to='hand-iron/auto-reports/%Y/%m/',blank=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['report_date','slot','department'],name='unique_hand_iron_auto_report_slot')]


class PolyPlan(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    STATUS=[(x,x.replace('_',' ').title()) for x in ['DRAFT','PENDING_APPROVAL','APPROVED','IN_PROGRESS','QC','READY','COMPLETED','HOLD']]
    POLY_TYPES=[(x,x.replace('_',' ').title()) for x in [
        'INDIVIDUAL_POLY','PRINTED_POLY','PLAIN_POLY','RECYCLED_POLY','RECYCLABLE_POLY',
        'BIODEGRADABLE_POLY','COMPOSTABLE_POLY','SELF_SEAL_POLY','ZIP_POLY','CUSTOM'
    ]]
    plan_no=models.CharField(max_length=80,unique=True)
    order=models.ForeignKey(MasterOrder,on_delete=models.CASCADE,related_name='poly_plans')
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    buyer=models.CharField(max_length=180,blank=True)
    brand=models.CharField(max_length=180,blank=True)
    style_no=models.CharField(max_length=100,blank=True)
    product=models.CharField(max_length=180)
    colour=models.CharField(max_length=100,blank=True)
    size_range=models.CharField(max_length=120,blank=True)
    poly_type=models.CharField(max_length=40,choices=POLY_TYPES,default='INDIVIDUAL_POLY')
    poly_code=models.CharField(max_length=100)
    poly_size=models.CharField(max_length=100,blank=True)
    thickness_micron=models.DecimalField(max_digits=8,decimal_places=2,default=0)
    material=models.CharField(max_length=120,blank=True)
    warning_text=models.CharField(max_length=255,blank=True)
    barcode_required=models.BooleanField(default=True)
    planned_qty=models.PositiveIntegerField(default=0)
    target_date=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='DRAFT',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    packing_specification=models.FileField(upload_to='poly/specifications/%Y/%m/',blank=True)
    artwork=models.FileField(upload_to='poly/artwork/%Y/%m/',blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class PolyStockIssue(TimeStamped):
    plan=models.ForeignKey(PolyPlan,on_delete=models.CASCADE,related_name='stock_issues')
    stock_item=models.ForeignKey(StockItem,null=True,blank=True,on_delete=models.SET_NULL)
    supplier=models.CharField(max_length=180,blank=True)
    batch_no=models.CharField(max_length=100,blank=True)
    lot_no=models.CharField(max_length=100,blank=True)
    issued_qty=models.PositiveIntegerField(default=0)
    returned_qty=models.PositiveIntegerField(default=0)
    damaged_qty=models.PositiveIntegerField(default=0)
    stock_out_scan=models.CharField(max_length=180)
    stock_in_return_scan=models.CharField(max_length=180,blank=True)
    issued_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class PolyBundleScan(TimeStamped):
    DIRECTIONS=[('IN','GARMENT/BUNDLE IN SCAN'),('OUT','GARMENT/BUNDLE OUT SCAN')]
    STATUS=[(x,x.replace('_',' ').title()) for x in ['VALID','BLOCKED','DUPLICATE','MISMATCH','QC_HOLD']]
    plan=models.ForeignKey(PolyPlan,on_delete=models.CASCADE,related_name='bundle_scans')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='poly_scans')
    direction=models.CharField(max_length=3,choices=DIRECTIONS,db_index=True)
    barcode=models.CharField(max_length=180,blank=True,db_index=True)
    expected_qty=models.PositiveIntegerField(default=0)
    actual_qty=models.PositiveIntegerField(default=0)
    source_department=models.CharField(max_length=100,blank=True)
    destination_department=models.CharField(max_length=100,blank=True)
    scan_status=models.CharField(max_length=30,choices=STATUS,default='VALID')
    scanned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    scanned_at=models.DateTimeField(default=timezone.now,db_index=True)

class PolyPackingEntry(TimeStamped):
    plan=models.ForeignKey(PolyPlan,on_delete=models.CASCADE,related_name='packing_entries')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='poly_packing')
    work_date=models.DateField(db_index=True)
    employee=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL)
    target_qty=models.PositiveIntegerField(default=0)
    actual_qty=models.PositiveIntegerField(default=0)
    process_minutes=models.PositiveIntegerField(default=0)
    npt_minutes=models.PositiveIntegerField(default=0)
    poly_used_qty=models.PositiveIntegerField(default=0)
    damaged_qty=models.PositiveIntegerField(default=0)
    rejected_qty=models.PositiveIntegerField(default=0)
    returned_qty=models.PositiveIntegerField(default=0)
    poly_cost_per_piece=models.DecimalField(max_digits=12,decimal_places=4,default=0)
    sticker_barcode_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    labour_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    cost_per_minute=models.DecimalField(max_digits=12,decimal_places=4,default=0)
    process_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    wastage_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    total_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    manual_entry=models.BooleanField(default=False)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)

    def save(self,*args,**kwargs):
        self.process_cost=(Decimal(self.process_minutes)*self.cost_per_minute).quantize(Decimal('0.01'))
        poly_cost=(Decimal(self.poly_used_qty)*self.poly_cost_per_piece).quantize(Decimal('0.01'))
        self.total_cost=(poly_cost+self.sticker_barcode_cost+self.labour_cost+self.process_cost+self.wastage_cost).quantize(Decimal('0.01'))
        super().save(*args,**kwargs)

class PolyQC(TimeStamped):
    RESULT=[(x,x.replace('_',' ').title()) for x in ['PASS','HOLD','REWORK','REJECT']]
    DEFECTS=[(x,x.replace('_',' ').title()) for x in [
        'WRONG_POLY','WRONG_ORDER_STYLE','WRONG_SIZE','WRONG_COLOUR','DIMENSION_FAIL',
        'THICKNESS_FAIL','PRINT_FAIL','BARCODE_FAIL','WARNING_TEXT_FAIL','SEAL_FAIL',
        'DIRTY_DAMAGED','QUANTITY_FAIL','OTHER'
    ]]
    plan=models.ForeignKey(PolyPlan,on_delete=models.CASCADE,related_name='qc_records')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='poly_qc')
    inspected_qty=models.PositiveIntegerField(default=0)
    pass_qty=models.PositiveIntegerField(default=0)
    reject_qty=models.PositiveIntegerField(default=0)
    rework_qty=models.PositiveIntegerField(default=0)
    defect_type=models.CharField(max_length=40,choices=DEFECTS,blank=True)
    defect_reason=models.CharField(max_length=255,blank=True)
    result=models.CharField(max_length=20,choices=RESULT,default='PASS',db_index=True)
    checked_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    checked_at=models.DateTimeField(default=timezone.now,db_index=True)
    qc_photo=models.FileField(upload_to='poly/qc/%Y/%m/',blank=True)

class PolyVariance(TimeStamped):
    TYPES=[(x,x.replace('_',' ').title()) for x in ['QUANTITY','VALUE','WRONG_POLY','BARCODE','WASTAGE','REJECT','RETURN','DAMAGE']]
    plan=models.ForeignKey(PolyPlan,on_delete=models.CASCADE,related_name='variances')
    variance_type=models.CharField(max_length=40,choices=TYPES)
    expected=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    actual=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    variance=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    value_variance_bdt=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    reason=models.CharField(max_length=255,blank=True)
    actioned=models.BooleanField(default=False,db_index=True)
    actioned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    actioned_at=models.DateTimeField(null=True,blank=True)

    def save(self,*args,**kwargs):
        self.variance=self.actual-self.expected
        super().save(*args,**kwargs)

class PolyAutoReport(TimeStamped):
    SLOTS=[('08:00','08:00'),('13:00','13:00'),('20:00','20:00')]
    report_date=models.DateField(db_index=True)
    slot=models.CharField(max_length=10,choices=SLOTS,db_index=True)
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    summary=models.JSONField(default=dict)
    outstanding_alerts=models.PositiveIntegerField(default=0)
    pending_actions=models.PositiveIntegerField(default=0)
    escalated_items=models.PositiveIntegerField(default=0)
    generated_at=models.DateTimeField(auto_now_add=True,db_index=True)
    generated_file=models.FileField(upload_to='poly/auto-reports/%Y/%m/',blank=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['report_date','slot','department'],name='unique_poly_auto_report_slot')]


class IronPlan(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    STATUS=[(x,x.replace('_',' ').title()) for x in ['DRAFT','PENDING_APPROVAL','APPROVED','IN_PROGRESS','QC','COMPLETED','HOLD']]
    plan_no=models.CharField(max_length=80,unique=True)
    order=models.ForeignKey(MasterOrder,on_delete=models.CASCADE,related_name='iron_plans')
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    product=models.CharField(max_length=180)
    style_no=models.CharField(max_length=100,blank=True)
    colour=models.CharField(max_length=100,blank=True)
    size_range=models.CharField(max_length=120,blank=True)
    fabric_type=models.CharField(max_length=120,blank=True)
    min_temperature_c=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    max_temperature_c=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    min_steam_pressure_bar=models.DecimalField(max_digits=8,decimal_places=2,default=0)
    max_steam_pressure_bar=models.DecimalField(max_digits=8,decimal_places=2,default=0)
    planned_qty=models.PositiveIntegerField(default=0)
    target_date=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='DRAFT',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    instruction_file=models.FileField(upload_to='iron/instructions/%Y/%m/',blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class IronBundleScan(TimeStamped):
    DIRECTIONS=[('IN','BUNDLE IN SCAN'),('OUT','BUNDLE OUT SCAN')]
    STATUS=[(x,x.replace('_',' ').title()) for x in ['VALID','BLOCKED','DUPLICATE','MISMATCH','QC_HOLD','MACHINE_HOLD']]
    plan=models.ForeignKey(IronPlan,on_delete=models.CASCADE,related_name='bundle_scans')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='iron_scans')
    direction=models.CharField(max_length=3,choices=DIRECTIONS,db_index=True)
    barcode=models.CharField(max_length=180,blank=True,db_index=True)
    expected_qty=models.PositiveIntegerField(default=0)
    actual_qty=models.PositiveIntegerField(default=0)
    source_department=models.CharField(max_length=100,blank=True)
    destination_department=models.CharField(max_length=100,blank=True)
    operator=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL)
    machine=models.ForeignKey(AssetMachine,null=True,blank=True,on_delete=models.SET_NULL)
    scan_status=models.CharField(max_length=30,choices=STATUS,default='VALID')
    scanned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    scanned_at=models.DateTimeField(default=timezone.now,db_index=True)

class IronProductionEntry(TimeStamped):
    plan=models.ForeignKey(IronPlan,on_delete=models.CASCADE,related_name='production_entries')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='iron_production')
    work_date=models.DateField(db_index=True)
    operator=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL)
    helper=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL,related_name='iron_helper_entries')
    machine=models.ForeignKey(AssetMachine,null=True,blank=True,on_delete=models.SET_NULL)
    target_qty=models.PositiveIntegerField(default=0)
    actual_qty=models.PositiveIntegerField(default=0)
    start_at=models.DateTimeField(null=True,blank=True)
    end_at=models.DateTimeField(null=True,blank=True)
    process_minutes=models.PositiveIntegerField(default=0)
    npt_minutes=models.PositiveIntegerField(default=0)
    downtime_minutes=models.PositiveIntegerField(default=0)
    actual_temperature_c=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    steam_pressure_bar=models.DecimalField(max_digits=8,decimal_places=2,default=0)
    electricity_kwh=models.DecimalField(max_digits=12,decimal_places=3,default=0)
    steam_kg=models.DecimalField(max_digits=12,decimal_places=3,default=0)
    cost_per_minute=models.DecimalField(max_digits=12,decimal_places=4,default=0)
    labour_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    helper_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    machine_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    utility_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    process_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    downtime_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    total_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    manual_entry=models.BooleanField(default=False)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)

    def save(self,*args,**kwargs):
        if self.start_at and self.end_at:
            self.process_minutes=max(int((self.end_at-self.start_at).total_seconds()//60),0)
        self.process_cost=(Decimal(self.process_minutes)*self.cost_per_minute).quantize(Decimal('0.01'))
        self.total_cost=(
            self.process_cost+self.labour_cost+self.helper_cost+self.machine_cost+
            self.utility_cost+self.downtime_cost
        ).quantize(Decimal('0.01'))
        super().save(*args,**kwargs)

class IronQC(TimeStamped):
    RESULT=[(x,x.replace('_',' ').title()) for x in ['PASS','HOLD','REIRON','REWORK','REJECT']]
    DEFECTS=[(x,x.replace('_',' ').title()) for x in [
        'SCORCH_BURN','SHINE_GLAZING','STEAM_WATER_MARK','CREASE','WRINKLE',
        'COLOUR_CHANGE','SHAPE_DISTORTION','MEASUREMENT_CHANGE','FABRIC_DAMAGE','OTHER'
    ]]
    plan=models.ForeignKey(IronPlan,on_delete=models.CASCADE,related_name='qc_records')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='iron_qc')
    inspected_qty=models.PositiveIntegerField(default=0)
    pass_qty=models.PositiveIntegerField(default=0)
    reject_qty=models.PositiveIntegerField(default=0)
    reiron_qty=models.PositiveIntegerField(default=0)
    rework_qty=models.PositiveIntegerField(default=0)
    defect_type=models.CharField(max_length=40,choices=DEFECTS,blank=True)
    defect_reason=models.CharField(max_length=255,blank=True)
    result=models.CharField(max_length=20,choices=RESULT,default='PASS',db_index=True)
    checked_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    checked_at=models.DateTimeField(default=timezone.now,db_index=True)
    qc_photo=models.FileField(upload_to='iron/qc/%Y/%m/',blank=True)

class IronVariance(TimeStamped):
    TYPES=[(x,x.replace('_',' ').title()) for x in [
        'QUANTITY','TEMPERATURE','STEAM_PRESSURE','VALUE','REJECT','REIRON','REWORK','DAMAGE','DOWNTIME'
    ]]
    plan=models.ForeignKey(IronPlan,on_delete=models.CASCADE,related_name='variances')
    variance_type=models.CharField(max_length=40,choices=TYPES)
    expected=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    actual=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    variance=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    value_variance_bdt=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    reason=models.CharField(max_length=255,blank=True)
    actioned=models.BooleanField(default=False,db_index=True)
    actioned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    actioned_at=models.DateTimeField(null=True,blank=True)

    def save(self,*args,**kwargs):
        self.variance=self.actual-self.expected
        super().save(*args,**kwargs)

class IronAutoReport(TimeStamped):
    SLOTS=[('08:00','08:00'),('13:00','13:00'),('20:00','20:00')]
    report_date=models.DateField(db_index=True)
    slot=models.CharField(max_length=10,choices=SLOTS,db_index=True)
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    summary=models.JSONField(default=dict)
    outstanding_alerts=models.PositiveIntegerField(default=0)
    pending_actions=models.PositiveIntegerField(default=0)
    escalated_items=models.PositiveIntegerField(default=0)
    generated_at=models.DateTimeField(auto_now_add=True,db_index=True)
    generated_file=models.FileField(upload_to='iron/auto-reports/%Y/%m/',blank=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['report_date','slot','department'],name='unique_iron_auto_report_slot')]


class FinalQCPlan(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    STATUS=[(x,x.replace('_',' ').title()) for x in ['DRAFT','PENDING_APPROVAL','APPROVED','IN_PROGRESS','HOLD','REWORK','PASSED','REJECTED','CLOSED']]
    plan_no=models.CharField(max_length=90,unique=True)
    order=models.ForeignKey(MasterOrder,on_delete=models.CASCADE,related_name='final_qc_plans')
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    buyer=models.CharField(max_length=180,blank=True)
    style_no=models.CharField(max_length=100,blank=True)
    product=models.CharField(max_length=180,blank=True)
    colour=models.CharField(max_length=100,blank=True)
    size_range=models.CharField(max_length=120,blank=True)
    specification_version=models.CharField(max_length=40,default='1.0')
    aql_level=models.CharField(max_length=40,default='2.5')
    lot_size=models.PositiveIntegerField(default=0)
    sample_size=models.PositiveIntegerField(default=0)
    shipment_qty=models.PositiveIntegerField(default=0)
    inspection_date=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='DRAFT',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    specification_file=models.FileField(upload_to='final-qc/specifications/%Y/%m/',blank=True)
    approved_sample_file=models.FileField(upload_to='final-qc/approved-samples/%Y/%m/',blank=True)
    packing_spec_file=models.FileField(upload_to='final-qc/packing-specs/%Y/%m/',blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class FinalQCUnitScan(TimeStamped):
    DIRECTIONS=[('IN','FINAL QC IN SCAN'),('OUT','FINAL QC OUT SCAN')]
    STATUS=[(x,x.replace('_',' ').title()) for x in ['VALID','BLOCKED','DUPLICATE','MISMATCH','QC_HOLD']]
    plan=models.ForeignKey(FinalQCPlan,on_delete=models.CASCADE,related_name='unit_scans')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='final_qc_scans')
    direction=models.CharField(max_length=3,choices=DIRECTIONS,db_index=True)
    barcode=models.CharField(max_length=180,blank=True,db_index=True)
    expected_qty=models.PositiveIntegerField(default=0)
    actual_qty=models.PositiveIntegerField(default=0)
    source_department=models.CharField(max_length=100,blank=True)
    destination_department=models.CharField(max_length=100,blank=True)
    scan_status=models.CharField(max_length=30,choices=STATUS,default='VALID')
    scanned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    scanned_at=models.DateTimeField(default=timezone.now,db_index=True)

class FinalQCInspection(TimeStamped):
    RESULT=[(x,x.replace('_',' ').title()) for x in ['PASS','HOLD','REWORK','REJECT','CONDITIONAL_PASS']]
    plan=models.ForeignKey(FinalQCPlan,on_delete=models.CASCADE,related_name='inspections')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='final_qc_inspections')
    inspector=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    inspected_qty=models.PositiveIntegerField(default=0)
    pass_qty=models.PositiveIntegerField(default=0)
    critical_defects=models.PositiveIntegerField(default=0)
    major_defects=models.PositiveIntegerField(default=0)
    minor_defects=models.PositiveIntegerField(default=0)
    measurement_fail_qty=models.PositiveIntegerField(default=0)
    appearance_fail_qty=models.PositiveIntegerField(default=0)
    workmanship_fail_qty=models.PositiveIntegerField(default=0)
    label_fail_qty=models.PositiveIntegerField(default=0)
    barcode_fail_qty=models.PositiveIntegerField(default=0)
    poly_fail_qty=models.PositiveIntegerField(default=0)
    packing_fail_qty=models.PositiveIntegerField(default=0)
    carton_marking_fail_qty=models.PositiveIntegerField(default=0)
    quantity_fail_qty=models.PositiveIntegerField(default=0)
    rework_qty=models.PositiveIntegerField(default=0)
    reject_qty=models.PositiveIntegerField(default=0)
    result=models.CharField(max_length=30,choices=RESULT,default='HOLD',db_index=True)
    buyer_inspection_result=models.CharField(max_length=80,blank=True)
    comments=models.TextField(blank=True)
    inspection_sheet=models.FileField(upload_to='final-qc/inspection-sheets/%Y/%m/',blank=True)
    photo=models.FileField(upload_to='final-qc/photos/%Y/%m/',blank=True)
    completed_at=models.DateTimeField(default=timezone.now,db_index=True)

    @property
    def total_defects(self):
        return self.critical_defects+self.major_defects+self.minor_defects

    @property
    def dhu(self):
        if not self.inspected_qty:
            return Decimal('0.00')
        return (Decimal(self.total_defects)/Decimal(self.inspected_qty)*100).quantize(Decimal('0.01'))

class FinalQCDefect(TimeStamped):
    SEVERITY=[(x,x.title()) for x in ['CRITICAL','MAJOR','MINOR']]
    CATEGORIES=[(x,x.replace('_',' ').title()) for x in [
        'MEASUREMENT','APPEARANCE','WORKMANSHIP','SHADE_COLOUR','SEWING','EMBROIDERY',
        'LABEL','CARE_COMPOSITION_ORIGIN','IRONING','CLEANLINESS','ACCESSORY','BARCODE_QR',
        'POLY','FOLDING','ASSORTMENT','PACKING_RATIO','CARTON_MARKING','QUANTITY','OTHER'
    ]]
    inspection=models.ForeignKey(FinalQCInspection,on_delete=models.CASCADE,related_name='defects')
    defect_code=models.CharField(max_length=80,blank=True)
    category=models.CharField(max_length=50,choices=CATEGORIES)
    severity=models.CharField(max_length=20,choices=SEVERITY)
    description=models.CharField(max_length=255)
    quantity=models.PositiveIntegerField(default=1)
    root_cause=models.TextField(blank=True)
    corrective_action=models.TextField(blank=True)
    responsible_user=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    due_at=models.DateTimeField(null=True,blank=True)
    actioned=models.BooleanField(default=False,db_index=True)
    actioned_at=models.DateTimeField(null=True,blank=True)

class FinalQCCAPA(TimeStamped):
    STATUS=[(x,x.replace('_',' ').title()) for x in ['OPEN','IN_PROGRESS','VERIFICATION','CLOSED','ESCALATED']]
    reference=models.CharField(max_length=100,unique=True)
    inspection=models.ForeignKey(FinalQCInspection,on_delete=models.CASCADE,related_name='capas')
    root_cause=models.TextField()
    corrective_action=models.TextField()
    preventive_action=models.TextField(blank=True)
    responsible_user=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    due_at=models.DateTimeField(null=True,blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='OPEN',db_index=True)
    verified_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='verified_final_qc_capas')
    verified_at=models.DateTimeField(null=True,blank=True)

class FinalQCRelease(TimeStamped):
    DECISIONS=[(x,x.replace('_',' ').title()) for x in ['PENDING','READY_TO_SHIP','CONDITIONAL_RELEASE','HOLD','REWORK','REJECT']]
    plan=models.OneToOneField(FinalQCPlan,on_delete=models.CASCADE,related_name='release')
    latest_inspection=models.ForeignKey(FinalQCInspection,null=True,blank=True,on_delete=models.SET_NULL,related_name='release_records')
    system_decision=models.CharField(max_length=30,choices=DECISIONS,default='PENDING',db_index=True)
    final_decision=models.CharField(max_length=30,choices=DECISIONS,default='PENDING',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL,related_name='final_qc_releases')
    pre_shipment_signoff_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='final_qc_signoffs')
    pre_shipment_signoff_at=models.DateTimeField(null=True,blank=True)
    decision_reason=models.TextField(blank=True)
    shipment_readiness_percent=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    released_at=models.DateTimeField(null=True,blank=True)

    @property
    def released(self):
        if self.final_decision=='READY_TO_SHIP':
            return True
        return self.final_decision=='CONDITIONAL_RELEASE' and self.approval_id and self.approval.status=='APPROVED'

    def calculate_decision(self):
        ins=self.latest_inspection
        if not ins:
            return 'PENDING'
        if ins.critical_defects > 0:
            return 'HOLD'
        if ins.result=='REJECT':
            return 'REJECT'
        if ins.result=='REWORK' or ins.rework_qty > 0:
            return 'REWORK'
        if ins.result=='PASS':
            return 'READY_TO_SHIP'
        if ins.result=='CONDITIONAL_PASS':
            return 'CONDITIONAL_RELEASE'
        return 'HOLD'

    def save(self,*args,**kwargs):
        self.system_decision=self.calculate_decision()
        super().save(*args,**kwargs)

class FinalQCAutoReport(TimeStamped):
    SLOTS=[('08:00','08:00'),('13:00','13:00'),('20:00','20:00')]
    report_date=models.DateField(db_index=True)
    slot=models.CharField(max_length=10,choices=SLOTS,db_index=True)
    summary=models.JSONField(default=dict)
    outstanding_alerts=models.PositiveIntegerField(default=0)
    pending_actions=models.PositiveIntegerField(default=0)
    escalated_items=models.PositiveIntegerField(default=0)
    generated_at=models.DateTimeField(auto_now_add=True,db_index=True)
    generated_file=models.FileField(upload_to='final-qc/auto-reports/%Y/%m/',blank=True)

    class Meta:
        constraints=[models.UniqueConstraint(fields=['report_date','slot'],name='unique_final_qc_auto_report_slot')]

class FinishingPlan(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    plan_no=models.CharField(max_length=90,unique=True)
    order=models.ForeignKey(MasterOrder,on_delete=models.CASCADE,related_name="finishing_plans")
    planned_qty=models.PositiveIntegerField(default=0); target_date=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=30,default="DRAFT",db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    instruction_file=models.FileField(upload_to="finishing/instructions/%Y/%m/",blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class FinishingScan(TimeStamped):
    plan=models.ForeignKey(FinishingPlan,on_delete=models.CASCADE,related_name="scans")
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name="finishing_scans")
    direction=models.CharField(max_length=3,db_index=True); barcode=models.CharField(max_length=180,blank=True,db_index=True)
    expected_qty=models.PositiveIntegerField(default=0); actual_qty=models.PositiveIntegerField(default=0)
    scan_status=models.CharField(max_length=30,default="VALID")
    scanned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL); scanned_at=models.DateTimeField(default=timezone.now,db_index=True)

class FinishingProduction(TimeStamped):
    plan=models.ForeignKey(FinishingPlan,on_delete=models.CASCADE,related_name="production")
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name="finishing_production")
    work_date=models.DateField(db_index=True); operator=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL)
    helper=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL,related_name="finishing_helper_entries")
    machine=models.ForeignKey(AssetMachine,null=True,blank=True,on_delete=models.SET_NULL)
    target_qty=models.PositiveIntegerField(default=0); actual_qty=models.PositiveIntegerField(default=0); wip_qty=models.PositiveIntegerField(default=0)
    process_minutes=models.PositiveIntegerField(default=0); npt_minutes=models.PositiveIntegerField(default=0); downtime_minutes=models.PositiveIntegerField(default=0)
    trimmed_qty=models.PositiveIntegerField(default=0); stain_checked_qty=models.PositiveIntegerField(default=0); cleaned_qty=models.PositiveIntegerField(default=0)
    measurement_checked_qty=models.PositiveIntegerField(default=0); accessory_checked_qty=models.PositiveIntegerField(default=0)
    appearance_checked_qty=models.PositiveIntegerField(default=0); label_checked_qty=models.PositiveIntegerField(default=0); folding_ready_qty=models.PositiveIntegerField(default=0)
    labour_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0); process_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    utility_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0); rework_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0); total_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    manual_entry=models.BooleanField(default=False); approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    def save(self,*args,**kwargs):
        self.total_cost=self.labour_cost+self.process_cost+self.utility_cost+self.rework_cost; super().save(*args,**kwargs)

class FinishingQC(TimeStamped):
    DEFECTS=[(x,x.replace("_"," ").title()) for x in ["UNCUT_THREAD","STAIN_SPOT","DIRT","MEASUREMENT","BUTTON_SNAP_ZIP","ACCESSORY","SHAPE_APPEARANCE","IRON_PRESS","LABEL","FOLDING","DAMAGE","OTHER"]]
    plan=models.ForeignKey(FinishingPlan,on_delete=models.CASCADE,related_name="qc_records")
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name="finishing_qc")
    inspected_qty=models.PositiveIntegerField(default=0); pass_qty=models.PositiveIntegerField(default=0); rework_qty=models.PositiveIntegerField(default=0); reject_qty=models.PositiveIntegerField(default=0)
    defect_type=models.CharField(max_length=40,choices=DEFECTS,blank=True); defect_qty=models.PositiveIntegerField(default=0); comments=models.TextField(blank=True)
    result=models.CharField(max_length=20,default="HOLD",db_index=True); checked_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL); checked_at=models.DateTimeField(default=timezone.now,db_index=True)
    qc_photo=models.FileField(upload_to="finishing/qc/%Y/%m/",blank=True)

class FinishingAutoReport(TimeStamped):
    report_date=models.DateField(db_index=True); slot=models.CharField(max_length=10,db_index=True); summary=models.JSONField(default=dict)
    outstanding_alerts=models.PositiveIntegerField(default=0); pending_actions=models.PositiveIntegerField(default=0); escalated_items=models.PositiveIntegerField(default=0)
    generated_file=models.FileField(upload_to="finishing/auto-reports/%Y/%m/",blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=["report_date","slot"],name="unique_finishing_report_slot")]


class PackingPlan(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    STATUS=[(x,x.replace('_',' ').title()) for x in ['DRAFT','PENDING_APPROVAL','APPROVED','IN_PROGRESS','QC','READY','HOLD','COMPLETED']]
    plan_no=models.CharField(max_length=90,unique=True)
    order=models.ForeignKey(MasterOrder,on_delete=models.CASCADE,related_name='packing_plans')
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    buyer=models.CharField(max_length=180,blank=True)
    style_no=models.CharField(max_length=100,blank=True)
    product=models.CharField(max_length=180,blank=True)
    colour=models.CharField(max_length=100,blank=True)
    size_range=models.CharField(max_length=120,blank=True)
    packing_ratio=models.CharField(max_length=120,blank=True)
    carton_marking=models.CharField(max_length=255,blank=True)
    planned_qty=models.PositiveIntegerField(default=0)
    target_date=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='DRAFT',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    packing_spec_file=models.FileField(upload_to='packing/specifications/%Y/%m/',blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class PackingScan(TimeStamped):
    DIRECTIONS=[('IN','PACKING IN SCAN'),('OUT','PACKING OUT SCAN')]
    STATUS=[(x,x.replace('_',' ').title()) for x in ['VALID','BLOCKED','DUPLICATE','MISMATCH','FINAL_QC_HOLD','PACKING_QC_HOLD']]
    plan=models.ForeignKey(PackingPlan,on_delete=models.CASCADE,related_name='scans')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='packing_scans')
    direction=models.CharField(max_length=3,choices=DIRECTIONS,db_index=True)
    barcode=models.CharField(max_length=180,blank=True,db_index=True)
    expected_qty=models.PositiveIntegerField(default=0)
    actual_qty=models.PositiveIntegerField(default=0)
    scan_status=models.CharField(max_length=30,choices=STATUS,default='VALID')
    scanned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    scanned_at=models.DateTimeField(default=timezone.now,db_index=True)

class PackingCarton(TimeStamped):
    plan=models.ForeignKey(PackingPlan,on_delete=models.CASCADE,related_name='cartons')
    carton_no=models.CharField(max_length=100,unique=True)
    barcode=models.CharField(max_length=180,unique=True)
    size_colour_matrix=models.JSONField(default=dict)
    packed_qty=models.PositiveIntegerField(default=0)
    gross_weight_kg=models.DecimalField(max_digits=10,decimal_places=3,default=0)
    net_weight_kg=models.DecimalField(max_digits=10,decimal_places=3,default=0)
    length_cm=models.DecimalField(max_digits=10,decimal_places=2,default=0)
    width_cm=models.DecimalField(max_digits=10,decimal_places=2,default=0)
    height_cm=models.DecimalField(max_digits=10,decimal_places=2,default=0)
    cbm=models.DecimalField(max_digits=14,decimal_places=6,default=0)
    carton_marking=models.CharField(max_length=255,blank=True)
    sealed=models.BooleanField(default=False)
    seal_no=models.CharField(max_length=100,blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    def save(self,*args,**kwargs):
        self.cbm=((self.length_cm*self.width_cm*self.height_cm)/Decimal('1000000')).quantize(Decimal('0.000001'))
        super().save(*args,**kwargs)

class PackingProduction(TimeStamped):
    plan=models.ForeignKey(PackingPlan,on_delete=models.CASCADE,related_name='production')
    bundle=models.ForeignKey(CuttingBundle,null=True,blank=True,on_delete=models.SET_NULL,related_name='packing_production')
    carton=models.ForeignKey(PackingCarton,null=True,blank=True,on_delete=models.SET_NULL,related_name='production_entries')
    work_date=models.DateField(db_index=True)
    employee=models.ForeignKey(Employee,null=True,blank=True,on_delete=models.SET_NULL)
    target_qty=models.PositiveIntegerField(default=0)
    actual_qty=models.PositiveIntegerField(default=0)
    wip_qty=models.PositiveIntegerField(default=0)
    process_minutes=models.PositiveIntegerField(default=0)
    npt_minutes=models.PositiveIntegerField(default=0)
    carton_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    poly_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    label_sticker_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    hanger_tissue_accessory_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    labour_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    process_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    utility_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    rework_wastage_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    total_cost=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    manual_entry=models.BooleanField(default=False)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)

    def save(self,*args,**kwargs):
        self.total_cost=(self.carton_cost+self.poly_cost+self.label_sticker_cost+self.hanger_tissue_accessory_cost+
                         self.labour_cost+self.process_cost+self.utility_cost+self.rework_wastage_cost)
        super().save(*args,**kwargs)

class PackingQC(TimeStamped):
    RESULT=[(x,x.replace('_',' ').title()) for x in ['PASS','HOLD','REWORK','REJECT']]
    DEFECTS=[(x,x.replace('_',' ').title()) for x in [
        'WRONG_ORDER_STYLE','WRONG_SIZE_COLOUR','WRONG_CARTON_MARKING','WRONG_ASSORTMENT',
        'BARCODE_QR','POLY','FOLDING','PACKING_RATIO','WEIGHT_TOLERANCE','DIMENSION',
        'QUANTITY_MISMATCH','SEAL','DIRTY_DAMAGED','OTHER'
    ]]
    plan=models.ForeignKey(PackingPlan,on_delete=models.CASCADE,related_name='qc_records')
    carton=models.ForeignKey(PackingCarton,null=True,blank=True,on_delete=models.SET_NULL,related_name='qc_records')
    inspected_qty=models.PositiveIntegerField(default=0)
    pass_qty=models.PositiveIntegerField(default=0)
    rework_qty=models.PositiveIntegerField(default=0)
    reject_qty=models.PositiveIntegerField(default=0)
    defect_type=models.CharField(max_length=50,choices=DEFECTS,blank=True)
    defect_qty=models.PositiveIntegerField(default=0)
    comments=models.TextField(blank=True)
    result=models.CharField(max_length=20,choices=RESULT,default='HOLD',db_index=True)
    checked_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    checked_at=models.DateTimeField(default=timezone.now,db_index=True)
    qc_photo=models.FileField(upload_to='packing/qc/%Y/%m/',blank=True)

class PackingAutoReport(TimeStamped):
    SLOTS=[('08:00','08:00'),('13:00','13:00'),('20:00','20:00')]
    report_date=models.DateField(db_index=True)
    slot=models.CharField(max_length=10,choices=SLOTS,db_index=True)
    summary=models.JSONField(default=dict)
    outstanding_alerts=models.PositiveIntegerField(default=0)
    pending_actions=models.PositiveIntegerField(default=0)
    escalated_items=models.PositiveIntegerField(default=0)
    generated_file=models.FileField(upload_to='packing/auto-reports/%Y/%m/',blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['report_date','slot'],name='unique_packing_auto_report_slot')]


class ShippingPlan(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    STATUS=[(x,x.replace('_',' ').title()) for x in [
        'DRAFT','PENDING_APPROVAL','APPROVED','READY','BOOKED','LOADING',
        'DISPATCHED','IN_TRANSIT','OUT_FOR_DELIVERY','DELIVERED','CLOSED','HOLD'
    ]]
    MODES=[(x,x.title()) for x in ['AIR','SEA','ROAD','COURIER']]
    plan_no=models.CharField(max_length=90,unique=True)
    order=models.ForeignKey(MasterOrder,on_delete=models.CASCADE,related_name='shipping_plans')
    department=models.ForeignKey(Department,null=True,blank=True,on_delete=models.SET_NULL)
    buyer=models.CharField(max_length=180,blank=True)
    consignee=models.CharField(max_length=180,blank=True)
    delivery_address=models.TextField(blank=True)
    country=models.CharField(max_length=120,blank=True)
    incoterm=models.CharField(max_length=40,blank=True)
    shipment_mode=models.CharField(max_length=20,choices=MODES,default='ROAD')
    forwarder=models.CharField(max_length=180,blank=True)
    carrier=models.CharField(max_length=180,blank=True)
    booking_no=models.CharField(max_length=120,blank=True)
    awb_bl_cmr_tracking_no=models.CharField(max_length=180,blank=True)
    container_no=models.CharField(max_length=120,blank=True)
    seal_no=models.CharField(max_length=120,blank=True)
    vehicle_no=models.CharField(max_length=120,blank=True)
    driver_name=models.CharField(max_length=180,blank=True)
    driver_phone=models.CharField(max_length=80,blank=True)
    planned_cartons=models.PositiveIntegerField(default=0)
    planned_pieces=models.PositiveIntegerField(default=0)
    gross_weight_kg=models.DecimalField(max_digits=14,decimal_places=3,default=0)
    net_weight_kg=models.DecimalField(max_digits=14,decimal_places=3,default=0)
    total_cbm=models.DecimalField(max_digits=16,decimal_places=6,default=0)
    etd=models.DateTimeField(null=True,blank=True)
    eta=models.DateTimeField(null=True,blank=True)
    actual_dispatch_at=models.DateTimeField(null=True,blank=True)
    actual_delivery_at=models.DateTimeField(null=True,blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='DRAFT',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    shipping_instruction=models.FileField(upload_to='shipping/instructions/%Y/%m/',blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class ShippingCartonScan(TimeStamped):
    SCAN_TYPES=[(x,x.replace('_',' ').title()) for x in ['SHIPPING_IN','CARTON_VERIFY','LOADING','GATE_OUT','DELIVERY']]
    STATUS=[(x,x.replace('_',' ').title()) for x in ['VALID','BLOCKED','DUPLICATE','MISMATCH','PACKING_QC_HOLD','APPROVAL_HOLD']]
    plan=models.ForeignKey(ShippingPlan,on_delete=models.CASCADE,related_name='carton_scans')
    carton=models.ForeignKey(PackingCarton,null=True,blank=True,on_delete=models.SET_NULL,related_name='shipping_scans')
    scan_type=models.CharField(max_length=30,choices=SCAN_TYPES)
    barcode=models.CharField(max_length=180,db_index=True)
    expected_qty=models.PositiveIntegerField(default=0)
    actual_qty=models.PositiveIntegerField(default=0)
    expected_seal_no=models.CharField(max_length=120,blank=True)
    actual_seal_no=models.CharField(max_length=120,blank=True)
    scan_status=models.CharField(max_length=30,choices=STATUS,default='VALID')
    scanned_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    scanned_at=models.DateTimeField(default=timezone.now,db_index=True)

class ShippingDocument(TimeStamped):
    DOC_TYPES=[(x,x.replace('_',' ').title()) for x in [
        'COMMERCIAL_INVOICE','PACKING_LIST','PURCHASE_ORDER','DELIVERY_NOTE',
        'SHIPPING_INSTRUCTION','BOOKING_CONFIRMATION','AIR_WAYBILL','BILL_OF_LADING',
        'CMR','CERTIFICATE_OF_ORIGIN','CUSTOMS_EXPORT','INSURANCE',
        'INSPECTION_CERTIFICATE','BUYER_CERTIFICATE','PROOF_OF_DELIVERY','OTHER'
    ]]
    plan=models.ForeignKey(ShippingPlan,on_delete=models.CASCADE,related_name='documents')
    document_type=models.CharField(max_length=40,choices=DOC_TYPES)
    document_no=models.CharField(max_length=120,blank=True)
    file=models.FileField(upload_to='shipping/documents/%Y/%m/')
    verified=models.BooleanField(default=False)
    verified_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='verified_shipping_documents')
    verified_at=models.DateTimeField(null=True,blank=True)
    uploaded_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='uploaded_shipping_documents')

class ShippingCost(TimeStamped):
    plan=models.OneToOneField(ShippingPlan,on_delete=models.CASCADE,related_name='cost')
    freight=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    forwarder=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    customs=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    port_airport=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    truck_vehicle=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    loading=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    documentation=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    insurance=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    duty_tax=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    handling=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    demurrage_detention=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    courier=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    other_approved=models.DecimalField(max_digits=14,decimal_places=2,default=0)
    total_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    updated_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    def save(self,*args,**kwargs):
        self.total_cost=(self.freight+self.forwarder+self.customs+self.port_airport+self.truck_vehicle+
                         self.loading+self.documentation+self.insurance+self.duty_tax+self.handling+
                         self.demurrage_detention+self.courier+self.other_approved)
        super().save(*args,**kwargs)

class ShippingPOD(TimeStamped):
    plan=models.OneToOneField(ShippingPlan,on_delete=models.CASCADE,related_name='pod')
    receiver_name=models.CharField(max_length=180)
    delivered_at=models.DateTimeField(default=timezone.now)
    proof_of_delivery=models.FileField(upload_to='shipping/pod/%Y/%m/',blank=True)
    buyer_signature=models.FileField(upload_to='shipping/signatures/%Y/%m/',blank=True)
    delivery_photo=models.FileField(upload_to='shipping/delivery-photos/%Y/%m/',blank=True)
    courier_confirmation=models.CharField(max_length=255,blank=True)
    gps_location=models.CharField(max_length=180,blank=True)
    confirmed_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class ShippingAutoReport(TimeStamped):
    SLOTS=[('08:00','08:00'),('13:00','13:00'),('20:00','20:00')]
    report_date=models.DateField(db_index=True)
    slot=models.CharField(max_length=10,choices=SLOTS,db_index=True)
    summary=models.JSONField(default=dict)
    outstanding_alerts=models.PositiveIntegerField(default=0)
    pending_actions=models.PositiveIntegerField(default=0)
    escalated_items=models.PositiveIntegerField(default=0)
    generated_file=models.FileField(upload_to='shipping/auto-reports/%Y/%m/',blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['report_date','slot'],name='unique_shipping_auto_report_slot')]


class SupplierMaster(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    STATUS=[(x,x.replace('_',' ').title()) for x in ['PENDING_KYC','PENDING_APPROVAL','APPROVED','ON_HOLD','BLOCKED','BLACKLISTED','INACTIVE']]
    supplier_id=models.CharField(max_length=60,unique=True)
    company_name=models.CharField(max_length=220)
    contact_person=models.CharField(max_length=180,blank=True); email=models.EmailField(blank=True); phone=models.CharField(max_length=80,blank=True)
    country=models.CharField(max_length=120,blank=True); address=models.TextField(blank=True)
    vat_tax_tin=models.CharField(max_length=120,blank=True); registration_no=models.CharField(max_length=120,blank=True)
    categories=models.TextField(blank=True); materials=models.TextField(blank=True)
    moq=models.CharField(max_length=100,blank=True); lead_time_days=models.PositiveIntegerField(default=0)
    currency=models.CharField(max_length=10,default='BDT'); payment_terms=models.CharField(max_length=180,blank=True)
    bank_name=models.CharField(max_length=180,blank=True); bank_account_name=models.CharField(max_length=180,blank=True)
    bank_account_no=models.CharField(max_length=120,blank=True); bank_swift=models.CharField(max_length=80,blank=True)
    capacity_notes=models.TextField(blank=True); certifications=models.TextField(blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='PENDING_KYC',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class SupplierDocument(TimeStamped):
    TYPES=[(x,x.replace('_',' ').title()) for x in ['KYC','TRADE_LICENSE','VAT_TAX_TIN','BANK','CERTIFICATE','COMPLIANCE','CONTRACT','INSURANCE','AUDIT','OTHER']]
    supplier=models.ForeignKey(SupplierMaster,on_delete=models.CASCADE,related_name='documents')
    document_type=models.CharField(max_length=40,choices=TYPES); document_no=models.CharField(max_length=120,blank=True)
    file=models.FileField(upload_to='suppliers/documents/%Y/%m/'); expiry_date=models.DateField(null=True,blank=True)
    verified=models.BooleanField(default=False); verified_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='supplier_docs_verified')
    verified_at=models.DateTimeField(null=True,blank=True); uploaded_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL,related_name='supplier_docs_uploaded')

class SupplierRFQ(TimeStamped):
    STATUS=[(x,x.replace('_',' ').title()) for x in ['DRAFT','SENT','QUOTED','SELECTED','REJECTED','EXPIRED']]
    rfq_no=models.CharField(max_length=80,unique=True); supplier=models.ForeignKey(SupplierMaster,on_delete=models.CASCADE,related_name='rfqs')
    order=models.ForeignKey(MasterOrder,null=True,blank=True,on_delete=models.SET_NULL,related_name='supplier_rfqs')
    material=models.ForeignKey(MaterialMaster,null=True,blank=True,on_delete=models.SET_NULL,related_name='supplier_rfqs')
    item_description=models.CharField(max_length=255); quantity=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    unit=models.CharField(max_length=30,blank=True); quoted_unit_price=models.DecimalField(max_digits=16,decimal_places=4,default=0)
    currency=models.CharField(max_length=10,default='BDT'); quoted_lead_days=models.PositiveIntegerField(default=0)
    valid_until=models.DateField(null=True,blank=True); sample_approved=models.BooleanField(default=False); quality_approved=models.BooleanField(default=False)
    status=models.CharField(max_length=20,choices=STATUS,default='DRAFT',db_index=True); quotation_file=models.FileField(upload_to='suppliers/quotations/%Y/%m/',blank=True)

class SupplierPurchaseOrder(TimeStamped):
    STATUS=[(x,x.replace('_',' ').title()) for x in ['DRAFT','PENDING_APPROVAL','APPROVED','SENT','PART_RECEIVED','RECEIVED','CLOSED','CANCELLED','HOLD']]
    po_no=models.CharField(max_length=80,unique=True); supplier=models.ForeignKey(SupplierMaster,on_delete=models.PROTECT,related_name='purchase_orders')
    order=models.ForeignKey(MasterOrder,null=True,blank=True,on_delete=models.SET_NULL,related_name='supplier_purchase_orders')
    rfq=models.ForeignKey(SupplierRFQ,null=True,blank=True,on_delete=models.SET_NULL,related_name='purchase_orders')
    material=models.ForeignKey(MaterialMaster,null=True,blank=True,on_delete=models.SET_NULL,related_name='supplier_purchase_orders')
    description=models.CharField(max_length=255); quantity=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    unit=models.CharField(max_length=30,blank=True); unit_price=models.DecimalField(max_digits=16,decimal_places=4,default=0)
    currency=models.CharField(max_length=10,default='BDT'); total_value=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    expected_delivery=models.DateField(null=True,blank=True); status=models.CharField(max_length=30,choices=STATUS,default='DRAFT',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    po_file=models.FileField(upload_to='suppliers/po/%Y/%m/',blank=True); created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    def save(self,*args,**kwargs):
        self.total_value=self.quantity*self.unit_price; super().save(*args,**kwargs)

class SupplierReceipt(TimeStamped):
    po=models.ForeignKey(SupplierPurchaseOrder,on_delete=models.CASCADE,related_name='receipts')
    grn_no=models.CharField(max_length=80,unique=True); delivery_note_no=models.CharField(max_length=100,blank=True)
    received_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0); accepted_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    rejected_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0); stock_in_barcode=models.CharField(max_length=180,blank=True)
    stock_in_scanned=models.BooleanField(default=False); inspection_pass=models.BooleanField(default=False)
    received_at=models.DateTimeField(default=timezone.now,db_index=True); received_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)
    delivery_note=models.FileField(upload_to='suppliers/delivery-notes/%Y/%m/',blank=True); grn_file=models.FileField(upload_to='suppliers/grn/%Y/%m/',blank=True)

class SupplierInvoice(TimeStamped):
    STATUS=[(x,x.replace('_',' ').title()) for x in ['PENDING','VERIFIED','APPROVED','PAID','HOLD','REJECTED']]
    supplier=models.ForeignKey(SupplierMaster,on_delete=models.PROTECT,related_name='invoices'); po=models.ForeignKey(SupplierPurchaseOrder,on_delete=models.PROTECT,related_name='invoices')
    invoice_no=models.CharField(max_length=120); invoice_date=models.DateField(); amount=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    currency=models.CharField(max_length=10,default='BDT'); status=models.CharField(max_length=20,choices=STATUS,default='PENDING',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    invoice_file=models.FileField(upload_to='suppliers/invoices/%Y/%m/',blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['supplier','invoice_no'],name='unique_supplier_invoice_no')]

class SupplierPerformance(TimeStamped):
    supplier=models.OneToOneField(SupplierMaster,on_delete=models.CASCADE,related_name='performance')
    on_time_delivery_pct=models.DecimalField(max_digits=6,decimal_places=2,default=0); quality_pass_pct=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    reject_pct=models.DecimalField(max_digits=6,decimal_places=2,default=0); short_delivery_pct=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    price_variance_pct=models.DecimalField(max_digits=8,decimal_places=2,default=0); lead_time_variance_days=models.DecimalField(max_digits=10,decimal_places=2,default=0)
    total_purchase_value=models.DecimalField(max_digits=18,decimal_places=2,default=0); outstanding_payable=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    claims_returns=models.PositiveIntegerField(default=0); supplier_score=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    risk_level=models.CharField(max_length=20,default='NORMAL'); calculated_at=models.DateTimeField(default=timezone.now)

class SupplierAutoReport(TimeStamped):
    report_date=models.DateField(db_index=True); slot=models.CharField(max_length=10,db_index=True); summary=models.JSONField(default=dict)
    outstanding_alerts=models.PositiveIntegerField(default=0); pending_actions=models.PositiveIntegerField(default=0); escalated_items=models.PositiveIntegerField(default=0)
    generated_file=models.FileField(upload_to='suppliers/auto-reports/%Y/%m/',blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['report_date','slot'],name='unique_supplier_report_slot')]


class ProcurementRequest(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    STATUS=[(x,x.replace('_',' ').title()) for x in ['DRAFT','STOCK_CHECK','RFQ','EVALUATION','PENDING_APPROVAL','APPROVED','ORDERED','PART_RECEIVED','RECEIVED','CLOSED','HOLD']]
    request_no=models.CharField(max_length=90,unique=True)
    order=models.ForeignKey(MasterOrder,null=True,blank=True,on_delete=models.SET_NULL,related_name='procurement_requests')
    material=models.ForeignKey(MaterialMaster,null=True,blank=True,on_delete=models.SET_NULL,related_name='procurement_requests')
    description=models.CharField(max_length=255)
    required_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    uom=models.CharField(max_length=30,blank=True)
    required_date=models.DateField(null=True,blank=True)
    stock_available=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    reserved_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    shortage_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    budget_value=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    status=models.CharField(max_length=30,choices=STATUS,default='DRAFT',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    requested_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class ProcurementComparison(TimeStamped):
    request=models.ForeignKey(ProcurementRequest,on_delete=models.CASCADE,related_name='comparisons')
    supplier=models.ForeignKey(SupplierMaster,on_delete=models.PROTECT,related_name='procurement_comparisons')
    rfq=models.ForeignKey(SupplierRFQ,null=True,blank=True,on_delete=models.SET_NULL,related_name='comparisons')
    unit_price=models.DecimalField(max_digits=16,decimal_places=4,default=0)
    freight_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    tax_duty_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    other_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    landed_unit_cost=models.DecimalField(max_digits=16,decimal_places=4,default=0)
    lead_time_days=models.PositiveIntegerField(default=0)
    payment_terms=models.CharField(max_length=180,blank=True)
    price_score=models.DecimalField(max_digits=6,decimal_places=2,default=0,
        help_text='100 for the lowest landed cost quoted for this requirement; '
                  'proportionally lower for dearer quotes. Recalculated across '
                  'the whole comparison set whenever any row changes.')
    quality_score=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    delivery_score=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    total_score=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    selected=models.BooleanField(default=False)
    reason=models.TextField(blank=True)

    PRICE_WEIGHT=Decimal('0.40')
    QUALITY_WEIGHT=Decimal('0.35')
    DELIVERY_WEIGHT=Decimal('0.25')

    def _weighted_total(self,price_score):
        return (price_score*self.PRICE_WEIGHT
                +self.quality_score*self.QUALITY_WEIGHT
                +self.delivery_score*self.DELIVERY_WEIGHT).quantize(Decimal('0.01'))

    @classmethod
    def rescore_request(cls,request_id):
        """Recompute price and total scores across one requirement's quotes.

        Price is inherently relative, so a row cannot be scored in isolation:
        the cheapest landed cost for the requirement takes 100 and the rest score
        in proportion. Uses bulk_update so rescoring siblings does not re-enter
        save().
        """
        rows=list(cls.objects.filter(request_id=request_id))
        costs=[r.landed_unit_cost for r in rows if r.landed_unit_cost and r.landed_unit_cost>0]
        if not costs:
            return
        best=min(costs)
        for row in rows:
            if row.landed_unit_cost and row.landed_unit_cost>0:
                row.price_score=(best/row.landed_unit_cost*100).quantize(Decimal('0.01'))
            else:
                row.price_score=Decimal('0.00')
            row.total_score=row._weighted_total(row.price_score)
        cls.objects.bulk_update(rows,['price_score','total_score'])

    def save(self,*args,**kwargs):
        qty=self.request.required_qty or Decimal('1')
        self.landed_unit_cost=(self.unit_price+(self.freight_cost+self.tax_duty_cost+self.other_cost)/qty)
        perf=getattr(self.supplier,'performance',None)
        if perf:
            self.quality_score=perf.quality_pass_pct
            self.delivery_score=perf.on_time_delivery_pct
        # price_score was hardcoded to Decimal('100'), so every supplier took the
        # full 40 price points regardless of landed cost. The landed cost was
        # computed and then discarded, and ranking was driven only by quality and
        # delivery - so procurement would systematically fail to select the
        # cheapest qualified supplier. See TECHNICAL_ASSESSMENT.md 5.7.
        #
        # Score provisionally against the quotes already recorded, then rescore
        # the whole set below now that this row's landed cost is known.
        existing=[c for c in type(self).objects.filter(request_id=self.request_id)
                  .exclude(pk=self.pk).values_list('landed_unit_cost',flat=True) if c and c>0]
        best=min(existing+[self.landed_unit_cost]) if self.landed_unit_cost>0 else (min(existing) if existing else None)
        if best and self.landed_unit_cost>0:
            self.price_score=(best/self.landed_unit_cost*100).quantize(Decimal('0.01'))
        else:
            self.price_score=Decimal('0.00')
        self.total_score=self._weighted_total(self.price_score)
        super().save(*args,**kwargs)
        type(self).rescore_request(self.request_id)

class ProcurementCommitment(TimeStamped):
    request=models.OneToOneField(ProcurementRequest,on_delete=models.CASCADE,related_name='commitment')
    selected_comparison=models.ForeignKey(ProcurementComparison,null=True,blank=True,on_delete=models.SET_NULL)
    supplier=models.ForeignKey(SupplierMaster,on_delete=models.PROTECT,related_name='procurement_commitments')
    po=models.ForeignKey(SupplierPurchaseOrder,null=True,blank=True,on_delete=models.SET_NULL,related_name='procurement_commitments')
    approved_budget=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    committed_value=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    variance=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    profit_before_spend_pass=models.BooleanField(default=False)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    committed_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    def save(self,*args,**kwargs):
        self.variance=self.committed_value-self.approved_budget
        super().save(*args,**kwargs)

class ProcurementAutoReport(TimeStamped):
    report_date=models.DateField(db_index=True)
    slot=models.CharField(max_length=10,db_index=True)
    summary=models.JSONField(default=dict)
    outstanding_alerts=models.PositiveIntegerField(default=0)
    pending_actions=models.PositiveIntegerField(default=0)
    escalated_items=models.PositiveIntegerField(default=0)
    generated_file=models.FileField(upload_to='procurement/auto-reports/%Y/%m/',blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['report_date','slot'],name='unique_procurement_auto_report_slot')]


class PurchaseTransaction(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    STATUS=[(x,x.replace('_',' ').title()) for x in ['OPEN','ACKNOWLEDGED','PART_DELIVERED','DELIVERED','INSPECTION','MATCH_PENDING','PAYMENT_PENDING','PAID','CLOSED','HOLD','CANCELLED']]
    po=models.OneToOneField(SupplierPurchaseOrder,on_delete=models.PROTECT,related_name='purchase_transaction')
    procurement_request=models.ForeignKey(ProcurementRequest,null=True,blank=True,on_delete=models.SET_NULL,related_name='purchase_transactions')
    supplier_acknowledged=models.BooleanField(default=False)
    acknowledged_at=models.DateTimeField(null=True,blank=True)
    promised_delivery=models.DateField(null=True,blank=True)
    actual_received_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    accepted_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    rejected_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    short_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    status=models.CharField(max_length=30,choices=STATUS,default='OPEN',db_index=True)
    notes=models.TextField(blank=True)
    managed_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class PurchaseAmendment(TimeStamped):
    TYPES=[(x,x.replace('_',' ').title()) for x in ['QUANTITY','PRICE','DELIVERY_DATE','TERMS','CANCEL','OTHER']]
    purchase=models.ForeignKey(PurchaseTransaction,on_delete=models.CASCADE,related_name='amendments')
    amendment_no=models.CharField(max_length=80,unique=True)
    amendment_type=models.CharField(max_length=30,choices=TYPES)
    old_value=models.TextField(blank=True); new_value=models.TextField(blank=True); reason=models.TextField()
    approval=models.ForeignKey(ApprovalRequest,on_delete=models.PROTECT)
    document=models.FileField(upload_to='purchases/amendments/%Y/%m/',blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class PurchaseThreeWayMatch(TimeStamped):
    STATUS=[(x,x.replace('_',' ').title()) for x in ['PENDING','MATCHED','VARIANCE','APPROVED_EXCEPTION','BLOCKED']]
    purchase=models.ForeignKey(PurchaseTransaction,on_delete=models.CASCADE,related_name='three_way_matches')
    invoice=models.ForeignKey(SupplierInvoice,on_delete=models.PROTECT,related_name='three_way_matches')
    po_value=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    grn_value=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    invoice_value=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    quantity_variance=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    value_variance=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    status=models.CharField(max_length=30,choices=STATUS,default='PENDING',db_index=True)
    exception_approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    checked_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class PurchaseReturn(TimeStamped):
    STATUS=[(x,x.title()) for x in ['DRAFT','APPROVED','DISPATCHED','CREDIT_PENDING','CLOSED']]
    return_no=models.CharField(max_length=80,unique=True)
    purchase=models.ForeignKey(PurchaseTransaction,on_delete=models.CASCADE,related_name='returns')
    quantity=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    reason=models.TextField(); debit_note_no=models.CharField(max_length=100,blank=True)
    value=models.DecimalField(max_digits=18,decimal_places=2,default=0)
    approval=models.ForeignKey(ApprovalRequest,on_delete=models.PROTECT)
    return_document=models.FileField(upload_to='purchases/returns/%Y/%m/',blank=True)
    status=models.CharField(max_length=20,choices=STATUS,default='DRAFT',db_index=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class PurchaseAutoReport(TimeStamped):
    report_date=models.DateField(db_index=True); slot=models.CharField(max_length=10,db_index=True); summary=models.JSONField(default=dict)
    outstanding_alerts=models.PositiveIntegerField(default=0); pending_actions=models.PositiveIntegerField(default=0); escalated_items=models.PositiveIntegerField(default=0)
    generated_file=models.FileField(upload_to='purchases/auto-reports/%Y/%m/',blank=True)
    class Meta: constraints=[models.UniqueConstraint(fields=['report_date','slot'],name='unique_purchase_auto_report_slot')]


class SourcingRequest(TimeStamped):
    #: Organisation site this record belongs to. Null means unassigned and,
    #: until TENANCY_STRICT is enabled, visible to every scope.
    scope=models.ForeignKey(OrganizationNode,null=True,blank=True,on_delete=models.PROTECT,related_name='%(class)s_records',db_index=True)
    all_objects=models.Manager()
    objects=ScopedManager()
    STATUS=[(x,x.replace('_',' ').title()) for x in [
        'DRAFT','STOCK_CHECK','SUPPLIER_SEARCH','RFQ','SAMPLE','EVALUATION',
        'NEGOTIATION','PENDING_APPROVAL','NOMINATED','HANDED_TO_PROCUREMENT','CLOSED','HOLD'
    ]]
    CATEGORIES=[(x,x.replace('_',' ').title()) for x in [
        'FABRIC','YARN','ACCESSORY','LABEL','POLY_PACKAGING','TRIM','PRINTING',
        'EMBROIDERY','MACHINERY_EQUIPMENT','SERVICE_OUTSOURCING','OTHER'
    ]]
    request_no=models.CharField(max_length=90,unique=True)
    order=models.ForeignKey(MasterOrder,null=True,blank=True,on_delete=models.SET_NULL,related_name='sourcing_requests')
    material=models.ForeignKey(MaterialMaster,null=True,blank=True,on_delete=models.SET_NULL,related_name='sourcing_requests')
    category=models.CharField(max_length=40,choices=CATEGORIES,default='FABRIC')
    description=models.CharField(max_length=255)
    specification=models.TextField(blank=True)
    required_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    uom=models.CharField(max_length=30,blank=True)
    required_date=models.DateField(null=True,blank=True)
    stock_available=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    stock_reserved=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    shortage_qty=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    target_price=models.DecimalField(max_digits=16,decimal_places=4,default=0)
    target_currency=models.CharField(max_length=10,default='BDT')
    status=models.CharField(max_length=30,choices=STATUS,default='DRAFT',db_index=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    specification_file=models.FileField(upload_to='sourcing/specifications/%Y/%m/',blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    class Meta:
        base_manager_name='all_objects'
        default_manager_name='all_objects'

class SourcingCandidate(TimeStamped):
    SOURCE_TYPES=[('EXISTING','Existing Approved Supplier'),('NEW','New Supplier')]
    request=models.ForeignKey(SourcingRequest,on_delete=models.CASCADE,related_name='candidates')
    supplier=models.ForeignKey(SupplierMaster,null=True,blank=True,on_delete=models.SET_NULL,related_name='sourcing_candidates')
    supplier_name=models.CharField(max_length=220,blank=True)
    supplier_country=models.CharField(max_length=120,blank=True)
    source_type=models.CharField(max_length=20,choices=SOURCE_TYPES,default='EXISTING')
    contact_details=models.CharField(max_length=255,blank=True)
    compliance_status=models.CharField(max_length=40,default='PENDING')
    capacity_per_month=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    moq=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    lead_time_days=models.PositiveIntegerField(default=0)
    payment_terms=models.CharField(max_length=180,blank=True)
    notes=models.TextField(blank=True)
    created_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class SourcingQuotation(TimeStamped):
    STATUS=[(x,x.replace('_',' ').title()) for x in ['RECEIVED','SAMPLE_PENDING','SAMPLE_APPROVED','NEGOTIATION','FINAL','REJECTED','EXPIRED']]
    request=models.ForeignKey(SourcingRequest,on_delete=models.CASCADE,related_name='quotations')
    candidate=models.ForeignKey(SourcingCandidate,on_delete=models.CASCADE,related_name='quotations')
    rfq=models.ForeignKey(SupplierRFQ,null=True,blank=True,on_delete=models.SET_NULL,related_name='sourcing_quotations')
    quote_no=models.CharField(max_length=100)
    unit_price=models.DecimalField(max_digits=16,decimal_places=4,default=0)
    currency=models.CharField(max_length=10,default='BDT')
    freight=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    duty_tax=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    other_cost=models.DecimalField(max_digits=16,decimal_places=2,default=0)
    landed_unit_cost=models.DecimalField(max_digits=16,decimal_places=4,default=0)
    quoted_lead_days=models.PositiveIntegerField(default=0)
    moq=models.DecimalField(max_digits=16,decimal_places=3,default=0)
    payment_terms=models.CharField(max_length=180,blank=True)
    valid_until=models.DateField(null=True,blank=True)
    status=models.CharField(max_length=30,choices=STATUS,default='RECEIVED',db_index=True)
    quotation_file=models.FileField(upload_to='sourcing/quotations/%Y/%m/',blank=True)

    def save(self,*args,**kwargs):
        qty=self.request.required_qty or Decimal('1')
        self.landed_unit_cost=(self.unit_price+(self.freight+self.duty_tax+self.other_cost)/qty)
        super().save(*args,**kwargs)

class SourcingSample(TimeStamped):
    RESULT=[(x,x.replace('_',' ').title()) for x in ['PENDING','PASS','HOLD','REJECT']]
    quotation=models.ForeignKey(SourcingQuotation,on_delete=models.CASCADE,related_name='samples')
    sample_no=models.CharField(max_length=100,unique=True)
    received_at=models.DateTimeField(null=True,blank=True,db_index=True)
    quality_result=models.CharField(max_length=20,choices=RESULT,default='PENDING')
    compliance_result=models.CharField(max_length=20,choices=RESULT,default='PENDING')
    lab_test_result=models.CharField(max_length=20,choices=RESULT,default='PENDING')
    comments=models.TextField(blank=True)
    sample_file=models.FileField(upload_to='sourcing/samples/%Y/%m/',blank=True)
    checked_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class SourcingEvaluation(TimeStamped):
    request=models.ForeignKey(SourcingRequest,on_delete=models.CASCADE,related_name='evaluations')
    quotation=models.ForeignKey(SourcingQuotation,on_delete=models.CASCADE,related_name='evaluations')
    price_score=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    quality_score=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    delivery_score=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    compliance_score=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    capacity_score=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    total_score=models.DecimalField(max_digits=6,decimal_places=2,default=0)
    last_purchase_price=models.DecimalField(max_digits=16,decimal_places=4,default=0)
    current_quote_price=models.DecimalField(max_digits=16,decimal_places=4,default=0)
    price_variance_pct=models.DecimalField(max_digits=8,decimal_places=2,default=0)
    nominated=models.BooleanField(default=False)
    nomination_reason=models.TextField(blank=True)
    approval=models.ForeignKey(ApprovalRequest,null=True,blank=True,on_delete=models.SET_NULL)
    evaluated_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

    def save(self,*args,**kwargs):
        if self.last_purchase_price:
            self.price_variance_pct=((self.current_quote_price-self.last_purchase_price)/self.last_purchase_price*100).quantize(Decimal('0.01'))
        self.total_score=(
            self.price_score*Decimal('.30')+
            self.quality_score*Decimal('.30')+
            self.delivery_score*Decimal('.15')+
            self.compliance_score*Decimal('.15')+
            self.capacity_score*Decimal('.10')
        ).quantize(Decimal('0.01'))
        super().save(*args,**kwargs)

class SourcingHandoff(TimeStamped):
    request=models.OneToOneField(SourcingRequest,on_delete=models.CASCADE,related_name='handoff')
    evaluation=models.ForeignKey(SourcingEvaluation,on_delete=models.PROTECT,related_name='handoffs')
    supplier=models.ForeignKey(SupplierMaster,on_delete=models.PROTECT,related_name='sourcing_handoffs')
    procurement_request=models.ForeignKey(ProcurementRequest,null=True,blank=True,on_delete=models.SET_NULL,related_name='sourcing_handoffs')
    approved_price=models.DecimalField(max_digits=16,decimal_places=4,default=0)
    currency=models.CharField(max_length=10,default='BDT')
    approved_lead_days=models.PositiveIntegerField(default=0)
    approval=models.ForeignKey(ApprovalRequest,on_delete=models.PROTECT)
    handed_off_by=models.ForeignKey(User,null=True,blank=True,on_delete=models.SET_NULL)

class SourcingAutoReport(TimeStamped):
    report_date=models.DateField(db_index=True)
    slot=models.CharField(max_length=10,db_index=True)
    summary=models.JSONField(default=dict)
    outstanding_alerts=models.PositiveIntegerField(default=0)
    pending_actions=models.PositiveIntegerField(default=0)
    escalated_items=models.PositiveIntegerField(default=0)
    generated_file=models.FileField(upload_to='sourcing/auto-reports/%Y/%m/',blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['report_date','slot'],name='unique_sourcing_auto_report_slot')]

class CEOAutoReport(TimeStamped):
    report_date=models.DateField(db_index=True)
    slot=models.CharField(max_length=10,db_index=True)
    summary=models.JSONField(default=dict)
    outstanding_alerts=models.PositiveIntegerField(default=0)
    pending_actions=models.PositiveIntegerField(default=0)
    escalated_items=models.PositiveIntegerField(default=0)
    generated_file=models.FileField(upload_to="ceo/auto-reports/%Y/%m/",blank=True)
    class Meta:
        constraints=[models.UniqueConstraint(fields=["report_date","slot"],name="unique_ceo_auto_report_slot")]
