import io, json, logging, os
from decimal import Decimal, InvalidOperation
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.http import FileResponse, HttpResponse, JsonResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.db.models import Q
from django.core.cache import cache
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
import qrcode
from .models import *
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from . import roles as roles_mod
from .currency import RateUnavailable, base_currency as currency_base, convert, convert_or_none
from .roles import can_decide_approval, has_any_role, user_roles
from .services import apply_stock_scan, record_variance, calculate_attendance_day, attendance_schedule

logger = logging.getLogger('portal.views')


#: Errors that mean "the user or the data is wrong". These carry a message worth
#: showing. Anything else is a defect and must be logged, not presented as
#: guidance.
EXPECTED_POST_ERRORS = (ValueError, PermissionError, ValidationError,
                        IntegrityError, Http404)


def _handle_post_error(request, exc):
    """Report a failed dashboard action.

    The dashboards previously caught bare Exception and passed str(exc) straight
    to the operator, so a programming error arrived as if it were a validation
    message and nothing was logged anywhere. LOGGING was also unconfigured, so
    there was no server-side record of any failure at all.
    """
    from django.contrib import messages
    if isinstance(exc, EXPECTED_POST_ERRORS):
        messages.error(request, str(exc))
        logger.info('rejected action: user=%s path=%s error=%s',
                    getattr(request.user, 'username', 'anonymous'), request.path, exc)
        return
    # Unexpected: log the traceback and give the operator something actionable
    # without leaking internals.
    logger.exception('unhandled error in %s for user=%s',
                     request.path, getattr(request.user, 'username', 'anonymous'))
    messages.error(request, 'Something went wrong and the change was not saved. '
                            'The error has been logged for IT.')

# --- form field name lists rendered by the department dashboards ------------
# Previously these lived in the templates as {% for n in "a b c".split %},
# which is not valid Django template syntax and raised TemplateSyntaxError.
FINISHING_PRODUCTION_FIELDS=[
 'target_qty','actual_qty','wip_qty','process_minutes','npt_minutes','downtime_minutes',
 'trimmed_qty','stain_checked_qty','cleaned_qty','measurement_checked_qty',
 'accessory_checked_qty','appearance_checked_qty','label_checked_qty','folding_ready_qty',
 'labour_cost','process_cost','utility_cost','rework_cost',
]
PACKING_COST_FIELDS=[
 'carton_cost','poly_cost','label_sticker_cost','hanger_tissue_accessory_cost',
 'labour_cost','process_cost','utility_cost','rework_wastage_cost',
]
SHIPPING_COST_FIELDS=[
 'freight','forwarder','customs','port_airport','truck_vehicle','loading','documentation',
 'insurance','duty_tax','handling','demurrage_detention','courier','other_approved',
]


def _login_throttle_key(request):
    ip=_file_access_ip(request) or 'unknown'
    return f'login-attempts:{ip}'


def _login_attempts(request):
    """Failed attempts from this IP inside the window. Fails open."""
    try:
        return cache.get(_login_throttle_key(request), 0)
    except Exception:
        # A security control must not become an outage. If the cache backend is
        # unreachable the throttle stops throttling, loudly.
        logger.warning('login throttle unavailable: cache backend error')
        return 0


def _record_failed_login(request):
    key=_login_throttle_key(request)
    window=getattr(settings,'LOGIN_ATTEMPT_WINDOW_SECONDS',900)
    try:
        # add() only sets the key if absent, so the window starts at the first
        # failure and is not extended by later ones.
        cache.add(key,0,window)
        cache.incr(key)
    except Exception:
        logger.warning('login throttle unavailable: could not record failed attempt')


def _clear_failed_logins(request):
    try:
        cache.delete(_login_throttle_key(request))
    except Exception:
        pass


def _safe_next_url(request):
    """Validate ?next= before redirecting to it.

    Redirecting to an unvalidated parameter is an open redirect: a link to
    /login/?next=https://evil.example/ would bounce a freshly authenticated
    user off-site, which is a credible phishing aid.
    """
    candidate=request.GET.get('next') or request.POST.get('next') or ''
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return None


def login_view(request):
    if request.user.is_authenticated: return redirect('dashboard')
    error=''
    limit=getattr(settings,'LOGIN_ATTEMPT_LIMIT',10)
    if request.method=='POST':
        if _login_attempts(request) >= limit:
            # No rate limiting existed on /login/, so credentials could be
            # brute-forced without limit.
            logger.warning('login throttled: ip=%s', _file_access_ip(request))
            return render(request,'login.html',{
                'error':'Too many failed sign-in attempts. Please try again later.',
            },status=429)
        u=authenticate(request,username=request.POST.get('username',''),password=request.POST.get('password',''))
        if u:
            _clear_failed_logins(request)
            login(request,u)
            return redirect(_safe_next_url(request) or 'dashboard')
        _record_failed_login(request)
        error='Invalid username or password.'
    return render(request,'login.html',{'error':error})

def logout_view(request): logout(request); return redirect('login')

def home(request): return render(request,'public_home.html')

@login_required
def dashboard(request):
    pages=DashboardPage.objects.filter(enabled=True).order_by('page_id')
    return render(request,'dashboard.html',{'pages':pages,'orders':MasterOrder.objects.order_by('-updated_at')[:8]})

@login_required
def page_view(request,slug):
    if slug=='stock-material-master': return redirect('stock_material_master')
    if slug=='asset-machine-master': return redirect('asset_machine_master')
    if slug=='buyer-opportunity': return redirect('buyer_opportunity')
    if slug=='communication-center-master': return redirect('communication_center')
    if slug=='profit-feasibility-gate': return redirect('profit_feasibility_gate')
    if slug=='free-capacity-opportunity': return redirect('free_capacity_opportunity')
    if slug=='universal-file-center': return redirect('universal_file_center')
    if slug=='buyer-delivery-sla': return redirect('buyer_delivery_sla')
    if slug=='profit-before-spend': return redirect('profit_before_spend')
    if slug=='staff-self-service-portal': return redirect('staff_self_service')
    if slug=='hr-dashboard': return redirect('hr_dashboard')
    if slug=='attendance-dashboard': return redirect('attendance_dashboard')
    if slug=='cutting-dashboard': return redirect('cutting_dashboard')
    if slug=='embroidery-dashboard': return redirect('embroidery_dashboard')
    if slug=='label-dashboard': return redirect('label_dashboard')
    if slug=='qc-dashboard': return redirect('qc_dashboard')
    if slug=='hand-iron-dashboard': return redirect('hand_iron_dashboard')
    if slug=='poly-dashboard': return redirect('poly_dashboard')
    # A superseded legacy page is disabled rather than deleted, so an existing
    # link keeps working: send it to the module that replaced it instead of 404.
    replacement=DashboardPage.objects.filter(slug=slug,enabled=False).exclude(superseded_by='').first()
    if replacement:
        return redirect('page',slug=replacement.superseded_by)
    page=get_object_or_404(DashboardPage,slug=slug,enabled=True)
    ctx={'page':page,'orders':MasterOrder.objects.order_by('-updated_at')[:10],'alerts':Alert.objects.filter(actioned=False).order_by('-created_at')[:10],'actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).order_by('due_at')[:10]}
    return render(request,'page.html',ctx)

@login_required
def forms_master(request):
    qs=FormDefinition.objects.filter(status='ACTIVE').order_by('form_id')
    q=request.GET.get('q','').strip(); dept=request.GET.get('department','').strip()
    if q: qs=qs.filter(name__icontains=q)
    if dept: qs=qs.filter(department=dept)
    return render(request,'forms_master.html',{'forms':qs[:650],'departments':FormDefinition.objects.values_list('department',flat=True).distinct().order_by('department'),'count':qs.count()})

def _report_master_department_catalog():
    return [
        ('Executive / CEO','ceo-dashboard','CEO Executive Command & Report Center'),
        ('Order Master','order-master','Order Master'),
        ('Stock & Material','stock-material-master','Stock & Material Master'),
        ('Asset & Machine','asset-machine-master','Asset & Machine Master'),
        ('Buyer Enquiry / Opportunity','buyer-enquiry-order-opportunity','Buyer Enquiry / Order Opportunity'),
        ('Production','production-dashboard','Production Department'),
        ('Cutting','cutting-dashboard','Cutting'),
        ('Print','print-dashboard','Print'),
        ('Embroidery','embroidery-dashboard','Embroidery'),
        ('Sewing','sewing-dashboard','Sewing'),
        ('Label','label-dashboard','Label'),
        ('QC','qc-dashboard','Quality Control'),
        ('Hand Iron','hand-iron-dashboard','Hand Iron'),
        ('Industrial Iron','iron-dashboard','Industrial Ironing'),
        ('Poly / Polybag','poly-dashboard','Poly / Polybag'),
        ('Finishing','finishing-dashboard','Finishing'),
        ('Final QC','final-qc-dashboard','Final QC / Final Inspection'),
        ('Packing','packing-dashboard','Packing'),
        ('Shipping','shipping-dashboard','Shipping'),
        ('Supplier','supplier-dashboard','Supplier Master'),
        ('Sourcing','sourcing-dashboard','Sourcing Master'),
        ('Procurement','procurement-dashboard','Purchase / Procurement'),
        ('Purchases','purchases-dashboard','Purchases Master'),
        ('HR / Staff','staff-self-service','Staff Self-Service'),
        ('Forms','forms-master','Forms Master'),
        ('Communication','communication-center','Communication Center'),
    ]

def _report_master_auto_reports(today):
    sources=[
        ('Sourcing','SourcingAutoReport'),('Procurement','ProcurementAutoReport'),
        ('Purchases','PurchaseAutoReport'),('Supplier','SupplierAutoReport'),
        ('Finishing','FinishingAutoReport'),('Final QC','FinalQCAutoReport'),
        ('Packing','PackingAutoReport'),('Shipping','ShippingAutoReport'),
        ('Poly','PolyAutoReport'),('Industrial Iron','IronAutoReport')
    ]
    rows=[]
    for label,model_name in sources:
        model=globals().get(model_name)
        if not model:
            continue
        try:
            for obj in model.objects.filter(report_date=today).order_by('slot'):
                rows.append({
                    'department':label,'slot':obj.slot,'generated_at':getattr(obj,'generated_at',getattr(obj,'created_at',None)),
                    'alerts':getattr(obj,'outstanding_alerts',0),'pending':getattr(obj,'pending_actions',0),
                    'escalated':getattr(obj,'escalated_items',0),'summary':getattr(obj,'summary',{}),
                })
        except Exception:
            continue
    return rows

def _report_master_summary(today):
    from django.db.models import Sum
    auto_rows=_report_master_auto_reports(today)
    orders=MasterOrder.objects.all()
    return {
        'registered_pages':DashboardPage.objects.filter(enabled=True).count(),
        'orders':orders.count(),
        'order_value':str(orders.aggregate(v=Sum('order_value'))['v'] or 0),
        'production_orders':orders.filter(status='PRODUCTION').count(),
        'ready_to_ship':orders.filter(status='READY_TO_SHIP').count(),
        'shipped':orders.filter(status='SHIPPED').count(),
        'delivered':orders.filter(status='DELIVERED').count(),
        'alerts':Alert.objects.filter(actioned=False).count(),
        'red_alerts':Alert.objects.filter(actioned=False,level='RED').count(),
        'pending_actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),
        'pending_approvals':ApprovalRequest.objects.filter(status='PENDING').count(),
        'documents':DocumentRecord.objects.count(),
        'auto_reports_today':len(auto_rows),
        'communications_today':Communication.objects.filter(created_at__date=today).count(),
    }

@login_required
@require_http_methods(['GET','POST'])
def report_master(request):
    from django.contrib import messages
    from django.utils import timezone
    today=timezone.localdate()

    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='generate_snapshot':
                    slot=request.POST.get('slot','MANUAL')
                    payload=_report_master_summary(today)
                    payload['slot']=slot
                    payload['department_reports']=_report_master_auto_reports(today)
                    ReportSnapshot.objects.create(
                        snapshot_type=f'REPORT_MASTER_{slot}',
                        generated_at=timezone.now(),
                        data=payload
                    )
                    messages.success(request,f'Report Master snapshot generated: {slot}.')
                elif action=='action_alert':
                    alert=get_object_or_404(Alert,pk=request.POST.get('alert_id'))
                    alert.actioned=True;alert.actioned_by=request.user;alert.actioned_at=timezone.now();alert.save()
                    messages.success(request,'Alert marked Actioned.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('report_master')

    catalog=[]
    pages={p.slug:p for p in DashboardPage.objects.filter(enabled=True)}
    registry_by_slug={}
    try:
        registry=json.loads((settings.BASE_DIR/'data/page_registry.json').read_text())
        registry_by_slug={x.get('slug'):x for x in registry}
    except Exception:
        registry_by_slug={}

    for group,slug,title in _report_master_department_catalog():
        page=pages.get(slug)
        reg=registry_by_slug.get(slug,{})
        catalog.append({
            'group':group,'slug':slug,'title':page.title if page else title,
            'page_id':page.page_id if page else reg.get('id',''),
            'enabled':bool(page) or bool(reg),
            'actions':reg.get('actions',[])[:8]
        })

    context={
        'today':today,
        'summary':_report_master_summary(today),
        'catalog':catalog,
        'auto_reports':_report_master_auto_reports(today),
        'snapshots':ReportSnapshot.objects.order_by('-generated_at')[:40],
        'orders':MasterOrder.objects.order_by('-created_at')[:100],
        'alerts':Alert.objects.filter(actioned=False).order_by('-created_at')[:30],
        'actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).order_by('-created_at')[:30],
        'approvals':ApprovalRequest.objects.filter(status='PENDING').order_by('-created_at')[:30],
        'documents':DocumentRecord.objects.order_by('-created_at')[:30],
        'communications':Communication.objects.order_by('-created_at')[:30],
    }
    return render(request,'report_master.html',context)

@login_required
def report_master_csv(request):
    import csv
    from django.utils import timezone
    today=timezone.localdate()
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']=f'attachment; filename="report-master-{today}.csv"'
    w=csv.writer(response)
    w.writerow(['REPORT MASTER',today])
    summary=_report_master_summary(today)
    for key,value in summary.items():
        w.writerow([key,value])
    w.writerow([])
    w.writerow(['Department','Slot','Alerts','Pending Actions','Escalated','Generated'])
    for row in _report_master_auto_reports(today):
        w.writerow([row['department'],row['slot'],row['alerts'],row['pending'],row['escalated'],row['generated_at']])
    w.writerow([])
    w.writerow(['Order','Buyer','Product','Quantity','Value','Status'])
    for o in MasterOrder.objects.order_by('-created_at'):
        w.writerow([o.master_order_id,o.buyer,o.product,o.quantity,o.order_value,o.status])
    return response

@login_required
def api_report_master(request):
    from django.utils import timezone
    payload=_report_master_summary(timezone.localdate())
    payload['department_reports']=_report_master_auto_reports(timezone.localdate())
    return JsonResponse(payload)

@login_required
@require_http_methods(['GET','POST'])
def barcode_master(request):
    created=None
    if request.method=='POST':
        code=request.POST.get('code','').strip() or f'BUNDLE-{timezone.now():%Y%m%d%H%M%S}'
        typ=request.POST.get('asset_type','BUNDLE')
        ref=request.POST.get('reference','').strip()
        asset,_=BarcodeAsset.objects.get_or_create(code=code,defaults={'asset_type':typ,'reference':ref,'payload':{'created_by':request.user.username}})
        created=asset
    return render(request,'barcode_master.html',{'created':created,'assets':BarcodeAsset.objects.order_by('-created_at')[:50]})

@login_required
def barcode_png(request,code):
    asset=get_object_or_404(BarcodeAsset,code=code)
    img=qrcode.make(json.dumps({'code':asset.code,'type':asset.asset_type,'reference':asset.reference},separators=(',',':')))
    b=io.BytesIO(); img.save(b,format='PNG')
    return HttpResponse(b.getvalue(),content_type='image/png')

@login_required
def finance_overseas_preview(request):
    # Decimal() on unvalidated query input raised decimal.InvalidOperation and
    # returned an unhandled 500 for anything non-numeric, e.g. ?amount=abc.
    # With DEBUG on that response carried a full traceback.
    raw_amount=request.GET.get('amount','0') or '0'
    try:
        amount=Decimal(raw_amount)
    except InvalidOperation:
        return JsonResponse({'ok':False,'error':'amount must be a decimal number.'},status=400)
    if amount < 0:
        return JsonResponse({'ok':False,'error':'amount cannot be negative.'},status=400)
    country=request.GET.get('country','Bangladesh')
    try:
        rate=Decimal(os.getenv('BANGLADESH_OVERSEAS_INCENTIVE_RATE','2.5')) if country.lower()=='bangladesh' else Decimal('0')
    except InvalidOperation:
        return JsonResponse({'ok':False,'error':'BANGLADESH_OVERSEAS_INCENTIVE_RATE is not a valid decimal.'},status=500)
    incentive=(amount*rate/Decimal('100')).quantize(Decimal('0.01'))
    return JsonResponse({'country':country,'amount':str(amount),'rate_percent':str(rate),'incentive_receivable':str(incentive),'display_total':str(amount+incentive),'note':'Incentive shown as receivable/pending until eligibility and approval are confirmed.'})

@login_required
def api_summary(request):
    return JsonResponse({'pages':DashboardPage.objects.filter(enabled=True).count(),'forms':FormDefinition.objects.filter(status='ACTIVE').count(),'alerts':Alert.objects.filter(actioned=False).count(),'red_alerts':Alert.objects.filter(actioned=False,level='RED').count(),'orders':MasterOrder.objects.count(),'barcode_assets':BarcodeAsset.objects.count()})


@login_required
@require_http_methods(['POST'])
def api_stock_scan(request):
    try:
        payload=json.loads(request.body or '{}')
        item=get_object_or_404(StockItem,sku=payload.get('sku',''))
        approval=None
        approval_id=payload.get('approval_id')
        if approval_id: approval=get_object_or_404(ApprovalRequest,id=approval_id)
        scan=apply_stock_scan(item=item,direction=str(payload.get('direction','')).upper(),quantity=payload.get('quantity',0),barcode=str(payload.get('barcode','')).strip(),reference=str(payload.get('reference','')).strip(),user=request.user,source_location=str(payload.get('source_location','')),destination_location=str(payload.get('destination_location','')),manual_override=bool(payload.get('manual_override',False)),override_reason=str(payload.get('override_reason','')),approval=approval)
        item.refresh_from_db()
        return JsonResponse({'ok':True,'scan_id':scan.id,'sku':item.sku,'qty':str(item.qty),'reserved_qty':str(item.reserved_qty),'available_qty':str(item.qty-item.reserved_qty)})
    except (ValueError,PermissionError) as exc:
        return JsonResponse({'ok':False,'error':str(exc)},status=400)

@login_required
@require_http_methods(['POST'])
def api_approval_request(request):
    try:
        payload=json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'ok':False,'error':'Request body must be valid JSON.'},status=400)
    if not isinstance(payload,dict):
        return JsonResponse({'ok':False,'error':'Request body must be a JSON object.'},status=400)
    reference=str(payload.get('reference','')).strip()
    if not reference:
        return JsonResponse({'ok':False,'error':'reference is required.'},status=400)
    obj=ApprovalRequest.objects.create(approval_type=str(payload.get('approval_type','GENERAL')).strip().upper(),reference=reference,requested_by=request.user,reason=str(payload.get('reason','')),payload=payload.get('payload') or {})
    return JsonResponse({'ok':True,'approval_id':obj.id,'status':obj.status})

@login_required
@require_http_methods(['POST'])
@transaction.atomic
def api_approval_decision(request,pk):
    """Decide an ApprovalRequest.

    This endpoint is the hinge the whole platform's "senior approval" model
    hangs on: manual stock override, manual material adjustment, asset
    retirement and disposal, manual production entry in every production
    module, conditional QC and Final QC release, Profit + Feasibility
    acceptance, quick-order acceptance, profit-before-spend authorisation,
    overtime approval and delivery-SLA exceptions all gate on
    ``approval.status == 'APPROVED'``.

    It previously had no checks whatsoever, so any authenticated user could
    raise a request and approve it themselves, or flip somebody else's rejected
    request to approved - defeating every one of those controls with two POSTs
    (TECHNICAL_ASSESSMENT.md 4.1). Four rules now apply:

      1. the approver must hold a role permitted for this approval_type;
      2. the requester may never approve their own request;
      3. a request that has already been decided cannot be re-decided;
      4. every decision is written to an append-only ApprovalDecisionLog.

    Rule 1 is also enforced at the route level by AuthorizationMiddleware; it is
    repeated here because this check is per approval_type, and because a
    security control this important should not depend on middleware ordering.
    """
    # select_for_update so two approvers racing the same request cannot both
    # pass the already-decided check.
    obj=get_object_or_404(ApprovalRequest.objects.select_for_update(),pk=pk)
    try:
        payload=json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'ok':False,'error':'Request body must be valid JSON.'},status=400)
    if not isinstance(payload,dict):
        return JsonResponse({'ok':False,'error':'Request body must be a JSON object.'},status=400)

    decision=str(payload.get('decision','')).strip().upper()
    if decision not in {'APPROVED','REJECTED'}:
        return JsonResponse({'ok':False,'error':'decision must be APPROVED or REJECTED'},status=400)

    if not can_decide_approval(request.user,obj.approval_type):
        _log_authorization_refusal(request,f'approval_type={obj.approval_type!r}')
        return JsonResponse({'ok':False,'error':'You do not hold a role permitted to decide this approval type.'},status=403)

    if obj.requested_by_id and obj.requested_by_id==request.user.id:
        _log_authorization_refusal(request,f'self-approval of approval {obj.pk}')
        return JsonResponse({'ok':False,'error':'You cannot decide an approval request you raised yourself.'},status=403)

    if obj.status!='PENDING':
        return JsonResponse({'ok':False,'error':f'This request was already {obj.status.lower()} and cannot be decided again.','status':obj.status},status=409)

    previous_status=obj.status
    obj.status=decision
    obj.approved_by=request.user
    obj.approved_at=timezone.now()
    obj.reason=str(payload.get('reason',obj.reason))
    obj.save(update_fields=['status','approved_by','approved_at','reason','updated_at'])

    ApprovalDecisionLog.objects.create(
        approval=obj,decided_by=request.user,previous_status=previous_status,
        decision=decision,reason=str(payload.get('reason','')),
        approver_roles=','.join(sorted(user_roles(request.user)))[:255],
        ip=_file_access_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT','')[:500],
    )
    return JsonResponse({'ok':True,'approval_id':obj.id,'status':obj.status})

@login_required
@require_http_methods(['POST'])
def api_variance(request):
    try:
        payload=json.loads(request.body or '{}')
        obj=record_variance(reference=str(payload.get('reference','')),expected=payload.get('expected_value',0),actual=payload.get('actual_value',0),department=str(payload.get('department','')),currency=str(payload.get('currency','BDT')),reason=str(payload.get('reason','')),user=request.user)
        return JsonResponse({'ok':True,'variance_id':obj.id,'variance_amount':str(obj.variance_amount),'red_alert':obj.currency.upper() in {'BDT','TK','৳'} and abs(obj.variance_amount)>Decimal('10')})
    except Exception as exc:
        return JsonResponse({'ok':False,'error':str(exc)},status=400)

@login_required
def api_attendance_summary(request,employee_id):
    employee=get_object_or_404(Employee,employee_id=employee_id)
    raw=request.GET.get('date') or timezone.localdate().isoformat()
    try: work_date=__import__('datetime').date.fromisoformat(raw)
    except ValueError: return JsonResponse({'ok':False,'error':'date must be YYYY-MM-DD'},status=400)
    obj=calculate_attendance_day(employee,work_date)
    return JsonResponse({'ok':True,'employee_id':employee.employee_id,'date':str(obj.work_date),'status':obj.status,'scheduled_minutes':obj.scheduled_minutes,'worked_minutes':obj.worked_minutes,'break_minutes':obj.break_minutes,'overtime_minutes':obj.overtime_minutes,'unpaid_minutes':obj.unpaid_minutes})

@login_required
def api_devices(request):
    devices=list(DeviceIntegration.objects.filter(active=True).values('id','name','device_type','manufacturer','model','serial_number','location','endpoint','last_seen_at'))
    return JsonResponse({'count':len(devices),'devices':devices})

@login_required
@require_http_methods(['GET','POST'])
def stock_material_master(request):
    from django.contrib import messages
    from django.db.models import Sum
    from .services import apply_material_movement
    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='create_material':
                    composition={}
                    raw=request.POST.get('composition','').strip()
                    if raw:
                        for part in raw.split(','):
                            if ':' in part:
                                k,v=part.split(':',1); composition[k.strip()]=Decimal(v.strip())
                    MaterialMaster.objects.create(
                        material_code=request.POST.get('material_code','').strip(),
                        name=request.POST.get('name','').strip(), category=request.POST.get('category','RAW_MATERIAL'),
                        subcategory=request.POST.get('subcategory','').strip(), composition={k:float(v) for k,v in composition.items()},
                        gsm=request.POST.get('gsm') or None, width=request.POST.get('width') or None,
                        width_unit=request.POST.get('width_unit','INCH'), colour=request.POST.get('colour','').strip(),
                        uom=request.POST.get('uom','PCS'), standard_cost=request.POST.get('standard_cost') or 0,
                        currency=request.POST.get('currency','BDT'), min_stock=request.POST.get('min_stock') or 0,
                        reorder_level=request.POST.get('reorder_level') or 0, max_stock=request.POST.get('max_stock') or 0,
                        created_by=request.user)
                    messages.success(request,'Material master created.')
                elif action=='create_lot':
                    material=get_object_or_404(MaterialMaster,pk=request.POST.get('material_id'))
                    location=OrganizationNode.objects.filter(pk=request.POST.get('location_id')).first()
                    qty=Decimal(request.POST.get('original_qty') or '0')
                    lot=MaterialLot.objects.create(material=material,lot_no=request.POST.get('lot_no','').strip(),roll_barcode=request.POST.get('roll_barcode','').strip(),purchase_order_no=request.POST.get('purchase_order_no','').strip(),customer_order_no=request.POST.get('customer_order_no','').strip(),supplier=request.POST.get('supplier','').strip(),length=request.POST.get('length') or 0,length_unit=request.POST.get('length_unit','METRE'),original_qty=qty,current_qty=0,unit_cost=request.POST.get('unit_cost') or material.standard_cost,location=location,stock_status=request.POST.get('stock_status','RAW_MATERIAL'),qc_status=request.POST.get('qc_status','PENDING'))
                    if qty>0:
                        apply_material_movement(lot=lot,movement_type='STOCK_IN_SCAN',quantity=qty,barcode=lot.roll_barcode,reference=request.POST.get('purchase_order_no','').strip() or lot.lot_no,user=request.user,destination_location=location,purchase_order_no=lot.purchase_order_no)
                    messages.success(request,'Material lot/roll created and stock-in recorded.')
                elif action=='movement':
                    lot=get_object_or_404(MaterialLot,pk=request.POST.get('lot_id'))
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first() if request.POST.get('approval_id') else None
                    source=OrganizationNode.objects.filter(pk=request.POST.get('source_location_id')).first()
                    dest=OrganizationNode.objects.filter(pk=request.POST.get('destination_location_id')).first()
                    apply_material_movement(lot=lot,movement_type=request.POST.get('movement_type',''),quantity=request.POST.get('quantity') or 0,barcode=request.POST.get('barcode','').strip() or lot.roll_barcode,reference=request.POST.get('reference','').strip(),user=request.user,source_location=source,destination_location=dest,order_reference=request.POST.get('order_reference','').strip(),purchase_order_no=request.POST.get('purchase_order_no','').strip(),manual_entry=request.POST.get('manual_entry')=='on',reason=request.POST.get('reason','').strip(),approval=approval)
                    messages.success(request,'Material movement posted successfully.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('stock_material_master')
    q=request.GET.get('q','').strip(); category=request.GET.get('category','').strip(); status=request.GET.get('status','').strip()
    materials=MaterialMaster.objects.all().order_by('material_code')
    if q: materials=materials.filter(models.Q(material_code__icontains=q)|models.Q(name__icontains=q)|models.Q(colour__icontains=q))
    if category: materials=materials.filter(category=category)
    lots=MaterialLot.objects.select_related('material','location').order_by('-updated_at')
    if status: lots=lots.filter(stock_status=status)
    movements=MaterialMovement.objects.select_related('material','lot','performed_by').order_by('-performed_at')[:100]
    all_lots=list(MaterialLot.objects.select_related('material'))
    total_qty=sum((x.current_qty for x in all_lots),Decimal('0'))
    reserved_qty=sum((x.reserved_qty for x in all_lots),Decimal('0'))
    total_value=sum((x.total_value for x in all_lots),Decimal('0'))
    low_materials=[]
    for m in MaterialMaster.objects.filter(status='ACTIVE'):
        bal=sum((l.current_qty for l in all_lots if l.material_id==m.id),Decimal('0'))
        if m.reorder_level and bal<=m.reorder_level: low_materials.append((m,bal))
    ctx={'page':DashboardPage.objects.filter(slug='stock-material-master').first(),'materials':materials[:500],'lots':lots[:500],'movements':movements,'locations':OrganizationNode.objects.filter(node_type__in=['Country','Company','Factory','Production Unit','Warehouse','Retail Store','Franchise Store']).order_by('node_type','name'),'total_qty':total_qty,'reserved_qty':reserved_qty,'available_qty':total_qty-reserved_qty,'total_value':total_value,'low_materials':low_materials[:20],'categories':[x[0] for x in MaterialMaster.CATEGORIES],'stock_statuses':[x[0] for x in MaterialLot.STOCK_STATUSES]}
    return render(request,'stock_material_master.html',ctx)

@login_required
def stock_material_export_csv(request):
    import csv
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']='attachment; filename="stock-material-master.csv"'
    w=csv.writer(response); w.writerow(['Material Code','Material','Category','Lot/Roll','Barcode','PO','Customer Order','Supplier','Colour','GSM','Width','Qty','Reserved','Available','UOM','Unit Cost','Total Value','Stock Status','QC Status','Location','Updated'])
    for lot in MaterialLot.objects.select_related('material','location').order_by('material__material_code','lot_no'):
        m=lot.material
        w.writerow([m.material_code,m.name,m.category,lot.lot_no,lot.roll_barcode,lot.purchase_order_no,lot.customer_order_no,lot.supplier,m.colour,m.gsm,m.width,lot.current_qty,lot.reserved_qty,lot.available_qty,m.uom,lot.unit_cost,lot.total_value,lot.stock_status,lot.qc_status,lot.location or '',lot.updated_at.isoformat()])
    return response

@login_required
def api_material_stock(request):
    data=[]
    for lot in MaterialLot.objects.select_related('material','location').order_by('material__material_code','lot_no'):
        data.append({'material_code':lot.material.material_code,'material':lot.material.name,'category':lot.material.category,'lot_no':lot.lot_no,'barcode':lot.roll_barcode,'qty':str(lot.current_qty),'reserved_qty':str(lot.reserved_qty),'available_qty':str(lot.available_qty),'unit':lot.material.uom,'unit_cost':str(lot.unit_cost),'total_value':str(lot.total_value),'stock_status':lot.stock_status,'qc_status':lot.qc_status,'location':str(lot.location or '')})
    return JsonResponse({'count':len(data),'results':data})


@login_required
@require_http_methods(['GET','POST'])
def asset_machine_master(request):
    from django.contrib import messages
    from datetime import datetime, timedelta
    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='create_asset':
                    ops=[x.strip() for x in request.POST.get('operation_capabilities','').split(',') if x.strip()]
                    purchase_date=request.POST.get('purchase_date') or None
                    warranty_expiry=request.POST.get('warranty_expiry') or None
                    next_date=request.POST.get('next_maintenance_date') or None
                    location=OrganizationNode.objects.filter(pk=request.POST.get('location_id')).first()
                    dept=Department.objects.filter(pk=request.POST.get('department_id')).first()
                    employee=Employee.objects.filter(pk=request.POST.get('assigned_to_id')).first()
                    cost=Decimal(request.POST.get('purchase_cost') or '0')
                    obj=AssetMachine.objects.create(asset_code=request.POST.get('asset_code','').strip(),barcode=request.POST.get('barcode','').strip(),name=request.POST.get('name','').strip(),asset_type=request.POST.get('asset_type','MACHINE'),category=request.POST.get('category','').strip(),machine_type=request.POST.get('machine_type','').strip(),manufacturer=request.POST.get('manufacturer','').strip(),model=request.POST.get('model','').strip(),serial_number=request.POST.get('serial_number','').strip(),supplier=request.POST.get('supplier','').strip(),purchase_date=purchase_date,purchase_cost=cost,current_value=request.POST.get('current_value') or cost,currency=request.POST.get('currency','BDT'),depreciation_method=request.POST.get('depreciation_method','STRAIGHT_LINE'),depreciation_rate=request.POST.get('depreciation_rate') or 0,warranty_expiry=warranty_expiry,location=location,department=dept,assigned_to=employee,status=request.POST.get('status','ACTIVE'),condition=request.POST.get('condition','GOOD'),operation_capabilities=ops,standard_speed=request.POST.get('standard_speed') or 0,speed_unit=request.POST.get('speed_unit','').strip(),available_minutes_per_day=request.POST.get('available_minutes_per_day') or 480,efficiency_percent=request.POST.get('efficiency_percent') or 100,power_rating=request.POST.get('power_rating') or 0,power_unit=request.POST.get('power_unit','KW'),maintenance_interval_days=request.POST.get('maintenance_interval_days') or 30,next_maintenance_date=next_date,created_by=request.user)
                    BarcodeAsset.objects.get_or_create(code=obj.barcode,defaults={'asset_type':'ASSET','reference':obj.asset_code,'payload':{'name':obj.name,'serial_number':obj.serial_number}})
                    messages.success(request,'Asset / machine registered successfully.')
                elif action=='maintenance':
                    asset=get_object_or_404(AssetMachine,pk=request.POST.get('asset_id'))
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first() if request.POST.get('approval_id') else None
                    status=request.POST.get('maintenance_status','PLANNED')
                    scheduled=request.POST.get('scheduled_date') or None
                    started=timezone.now() if status in {'IN_PROGRESS','COMPLETED'} else None
                    completed=timezone.now() if status=='COMPLETED' else None
                    rec=AssetMaintenance.objects.create(asset=asset,maintenance_type=request.POST.get('maintenance_type','PREVENTIVE'),reference=request.POST.get('reference','').strip(),scheduled_date=scheduled,started_at=started,completed_at=completed,technician=request.POST.get('technician','').strip(),vendor=request.POST.get('vendor','').strip(),description=request.POST.get('description','').strip(),parts_cost=request.POST.get('parts_cost') or 0,labour_cost=request.POST.get('labour_cost') or 0,other_cost=request.POST.get('other_cost') or 0,currency=request.POST.get('currency','BDT'),status=status,approval=approval,performed_by=request.user)
                    if status=='COMPLETED':
                        today=timezone.localdate(); asset.last_maintenance_date=today; asset.next_maintenance_date=today+timedelta(days=asset.maintenance_interval_days); asset.status='ACTIVE'; asset.save()
                    elif status=='IN_PROGRESS': asset.status='UNDER_MAINTENANCE'; asset.save(update_fields=['status','updated_at'])
                    messages.success(request,'Maintenance record saved.')
                elif action=='downtime':
                    asset=get_object_or_404(AssetMachine,pk=request.POST.get('asset_id'))
                    AssetDowntime.objects.create(asset=asset,reference=request.POST.get('reference','').strip(),reason=request.POST.get('reason','BREAKDOWN'),description=request.POST.get('description','').strip(),production_impact_qty=request.POST.get('production_impact_qty') or 0,recorded_by=request.user)
                    asset.status='BREAKDOWN' if request.POST.get('reason')=='BREAKDOWN' else 'IDLE'; asset.save(update_fields=['status','updated_at'])
                    messages.success(request,'Downtime started.')
                elif action=='close_downtime':
                    rec=get_object_or_404(AssetDowntime,pk=request.POST.get('downtime_id')); rec.ended_at=timezone.now(); rec.save(update_fields=['ended_at','updated_at']); rec.asset.status='ACTIVE'; rec.asset.save(update_fields=['status','updated_at']); messages.success(request,'Downtime closed and machine returned to ACTIVE.')
                elif action=='movement':
                    asset=get_object_or_404(AssetMachine,pk=request.POST.get('asset_id'))
                    dest=OrganizationNode.objects.filter(pk=request.POST.get('destination_location_id')).first(); emp=Employee.objects.filter(pk=request.POST.get('assigned_to_id')).first()
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first() if request.POST.get('approval_id') else None
                    mtype=request.POST.get('movement_type','TRANSFER')
                    if mtype in {'RETIRE','DISPOSE'} and (not approval or approval.status!='APPROVED'): raise PermissionError('Approved senior approval is required to retire or dispose an asset.')
                    AssetMovement.objects.create(asset=asset,movement_type=mtype,reference=request.POST.get('reference','').strip(),source_location=asset.location,destination_location=dest,assigned_to=emp,barcode=request.POST.get('barcode','').strip() or asset.barcode,reason=request.POST.get('reason','').strip(),approval=approval,performed_by=request.user)
                    if dest: asset.location=dest
                    if emp: asset.assigned_to=emp
                    if mtype=='RETIRE': asset.status='RETIRED'
                    if mtype=='DISPOSE': asset.status='DISPOSED'
                    asset.save(); messages.success(request,'Asset movement recorded.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('asset_machine_master')
    qs=AssetMachine.objects.select_related('location','department','assigned_to').order_by('asset_code')
    q=request.GET.get('q','').strip(); status=request.GET.get('status','').strip(); typ=request.GET.get('asset_type','').strip()
    if q: qs=qs.filter(models.Q(asset_code__icontains=q)|models.Q(name__icontains=q)|models.Q(serial_number__icontains=q)|models.Q(model__icontains=q))
    if status: qs=qs.filter(status=status)
    if typ: qs=qs.filter(asset_type=typ)
    all_assets=list(qs[:1000]); today=timezone.localdate()
    due=[a for a in all_assets if a.next_maintenance_date and a.next_maintenance_date<=today]
    active=sum(1 for a in all_assets if a.status=='ACTIVE'); breakdown=sum(1 for a in all_assets if a.status=='BREAKDOWN')
    total_value=sum((a.current_value for a in all_assets),Decimal('0')); effective_minutes=sum(a.daily_effective_minutes for a in all_assets if a.status=='ACTIVE')
    maintenance=AssetMaintenance.objects.select_related('asset').order_by('-created_at')[:100]
    downtime=AssetDowntime.objects.select_related('asset').order_by('-started_at')[:100]
    open_downtime=[x for x in downtime if x.ended_at is None]
    locations=OrganizationNode.objects.filter(node_type__in=['Country','Company','Factory','Production Unit','Warehouse','Retail Store','Franchise Store']).order_by('node_type','name')
    ctx={'page':DashboardPage.objects.filter(slug='asset-machine-master').first(),'assets':all_assets,'maintenance':maintenance,'downtime':downtime,'open_downtime':open_downtime,'locations':locations,'departments':Department.objects.filter(active=True).order_by('name'),'employees':Employee.objects.filter(status='ACTIVE').order_by('employee_id'),'asset_types':[x[0] for x in AssetMachine.ASSET_TYPES],'statuses':[x[0] for x in AssetMachine.STATUSES],'conditions':[x[0] for x in AssetMachine.CONDITIONS],'maintenance_types':[x[0] for x in AssetMaintenance.TYPES],'downtime_reasons':[x[0] for x in AssetDowntime.REASONS],'active_count':active,'breakdown_count':breakdown,'due_count':len(due),'due_assets':due[:20],'total_value':total_value,'effective_minutes':effective_minutes}
    return render(request,'asset_machine_master.html',ctx)

@login_required
def asset_machine_export_csv(request):
    import csv
    response=HttpResponse(content_type='text/csv'); response['Content-Disposition']='attachment; filename="asset-machine-master.csv"'
    w=csv.writer(response); w.writerow(['Asset Code','Barcode','Name','Type','Category','Machine Type','Manufacturer','Model','Serial','Status','Condition','Department','Location','Assigned To','Purchase Cost','Current Value','Currency','Efficiency %','Available Minutes','Effective Minutes','Last Maintenance','Next Maintenance','Warranty Expiry'])
    for a in AssetMachine.objects.select_related('department','location','assigned_to').order_by('asset_code'):
        w.writerow([a.asset_code,a.barcode,a.name,a.asset_type,a.category,a.machine_type,a.manufacturer,a.model,a.serial_number,a.status,a.condition,a.department or '',a.location or '',a.assigned_to.name if a.assigned_to else '',a.purchase_cost,a.current_value,a.currency,a.efficiency_percent,a.available_minutes_per_day,a.daily_effective_minutes,a.last_maintenance_date or '',a.next_maintenance_date or '',a.warranty_expiry or ''])
    return response

@login_required
def api_assets(request):
    rows=[]
    for a in AssetMachine.objects.select_related('location','department','assigned_to').order_by('asset_code'):
        rows.append({'asset_code':a.asset_code,'barcode':a.barcode,'name':a.name,'asset_type':a.asset_type,'machine_type':a.machine_type,'manufacturer':a.manufacturer,'model':a.model,'serial_number':a.serial_number,'status':a.status,'condition':a.condition,'location':str(a.location or ''),'department':str(a.department or ''),'assigned_to':a.assigned_to.employee_id if a.assigned_to else None,'efficiency_percent':str(a.efficiency_percent),'available_minutes_per_day':a.available_minutes_per_day,'effective_minutes_per_day':a.daily_effective_minutes,'next_maintenance_date':str(a.next_maintenance_date or ''),'maintenance_due':a.is_maintenance_due,'current_value':str(a.current_value),'currency':a.currency})
    return JsonResponse({'count':len(rows),'results':rows})


@login_required
@require_http_methods(['GET','POST'])
def buyer_opportunity(request):
    from django.contrib import messages
    from django.db.models import Q
    from datetime import datetime
    if request.method=='POST':
        action=request.POST.get('action','create')
        try:
            with transaction.atomic():
                if action=='create':
                    now=timezone.now(); seq=BuyerOpportunity.objects.filter(created_at__date=timezone.localdate()).count()+1
                    enquiry=request.POST.get('enquiry_no','').strip() or f'ENQ-{now:%Y%m%d}-{seq:04d}'
                    oppno=request.POST.get('opportunity_no','').strip() or f'OPP-{now:%Y%m%d}-{seq:04d}'
                    qty=int(request.POST.get('target_quantity') or 0); price=Decimal(request.POST.get('target_unit_price') or '0')
                    expected=Decimal(request.POST.get('expected_order_value') or '0') or (Decimal(qty)*price)
                    obj=BuyerOpportunity.objects.create(enquiry_no=enquiry,opportunity_no=oppno,buyer_company=request.POST.get('buyer_company','').strip(),buyer_contact=request.POST.get('buyer_contact','').strip(),buyer_email=request.POST.get('buyer_email','').strip(),buyer_phone=request.POST.get('buyer_phone','').strip(),buyer_country=request.POST.get('buyer_country','').strip(),delivery_destination=request.POST.get('delivery_destination','').strip(),product=request.POST.get('product','').strip(),style_no=request.POST.get('style_no','').strip(),item_no=request.POST.get('item_no','').strip(),description=request.POST.get('description','').strip(),target_quantity=qty,target_unit_price=price,currency=request.POST.get('currency','USD'),expected_order_value=expected,probability_percent=request.POST.get('probability_percent') or 10,required_delivery_date=request.POST.get('required_delivery_date') or None,follow_up_date=request.POST.get('follow_up_date') or None,incoterms=request.POST.get('incoterms','').strip(),payment_terms=request.POST.get('payment_terms','').strip(),fabric_requirements=request.POST.get('fabric_requirements','').strip(),accessory_requirements=request.POST.get('accessory_requirements','').strip(),sample_requirements=request.POST.get('sample_requirements','').strip(),stage=request.POST.get('stage','NEW_ENQUIRY'),priority=request.POST.get('priority','NORMAL'),owner=request.user,merchandiser=Employee.objects.filter(pk=request.POST.get('merchandiser_id')).first(),source=request.POST.get('source','').strip(),competitor_info=request.POST.get('competitor_info','').strip(),notes=request.POST.get('notes','').strip())
                    OpportunityActivity.objects.create(opportunity=obj,activity_type='STATUS_CHANGE',subject='Buyer enquiry created',details=f'Stage: {obj.stage}',performed_by=request.user)
                    messages.success(request,f'{obj.opportunity_no} created.')
                elif action=='stage':
                    obj=get_object_or_404(BuyerOpportunity,pk=request.POST.get('opportunity_id')); old=obj.stage; new=request.POST.get('stage',old)
                    if new=='WON' and not obj.converted_order:
                        gate=ProfitFeasibilityGate.objects.filter(opportunity=obj).select_related('approval').first()
                        if not gate or not gate.gate_passed:
                            raise PermissionError('Profit + Feasibility Gate must be ACCEPT or ACCEPT WITH RISK and have an APPROVED senior approval before this opportunity can be accepted as WON.')
                        approved=obj.quotations.filter(status__in=['APPROVED','ACCEPTED']).order_by('-version').first()
                        if not approved: raise PermissionError('An approved/accepted quotation is required before converting a Won opportunity to Order Master.')
                        oid=f'MO-{timezone.now():%Y%m%d}-{obj.pk:05d}'
                        order=MasterOrder.objects.create(master_order_id=oid,buyer=obj.buyer_company,product=obj.product,quantity=obj.target_quantity,order_value=approved.total_value,confirmed_at=timezone.now(),delivery_due=timezone.make_aware(datetime.combine(obj.required_delivery_date,datetime.min.time())) if obj.required_delivery_date else None,status='CONFIRMED')
                        obj.converted_order=order
                    obj.stage=new; obj.probability_percent=request.POST.get('probability_percent') or obj.probability_percent
                    obj.lost_reason=request.POST.get('lost_reason','').strip() if new=='LOST' else obj.lost_reason; obj.save()
                    OpportunityActivity.objects.create(opportunity=obj,activity_type='STATUS_CHANGE',subject=f'{old} → {new}',details=request.POST.get('details','').strip(),performed_by=request.user)
                    messages.success(request,'Opportunity stage updated.')
                elif action=='quotation':
                    obj=get_object_or_404(BuyerOpportunity,pk=request.POST.get('opportunity_id')); ver=obj.quotation_version+1
                    qty=int(request.POST.get('quantity') or obj.target_quantity); price=Decimal(request.POST.get('unit_price') or obj.target_unit_price); total=Decimal(qty)*price
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first() if request.POST.get('approval_id') else None
                    status=request.POST.get('quotation_status','DRAFT')
                    if status in {'APPROVED','SENT','ACCEPTED'} and (not approval or approval.status!='APPROVED'): raise PermissionError('Approved quotation approval request is required before approval/send/acceptance.')
                    OpportunityQuotation.objects.create(opportunity=obj,version=ver,quotation_no=f'QUO-{obj.opportunity_no}-{ver:02d}',unit_price=price,quantity=qty,currency=request.POST.get('currency',obj.currency),total_value=total,valid_until=request.POST.get('valid_until') or None,status=status,terms=request.POST.get('terms','').strip(),approval=approval,created_by=request.user)
                    obj.quotation_version=ver; obj.stage='QUOTATION_SENT' if status=='SENT' else obj.stage; obj.save(); messages.success(request,'Quotation version created.')
                elif action=='activity':
                    obj=get_object_or_404(BuyerOpportunity,pk=request.POST.get('opportunity_id')); nxt=request.POST.get('next_follow_up') or None
                    OpportunityActivity.objects.create(opportunity=obj,activity_type=request.POST.get('activity_type','FOLLOW_UP'),subject=request.POST.get('subject','').strip(),details=request.POST.get('details','').strip(),next_follow_up=nxt,performed_by=request.user)
                    if nxt: obj.follow_up_date=nxt; obj.save(update_fields=['follow_up_date','updated_at'])
                    messages.success(request,'Buyer activity recorded.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('buyer_opportunity')
    qs=BuyerOpportunity.objects.select_related('owner','merchandiser','converted_order').order_by('-updated_at'); q=request.GET.get('q','').strip(); stage=request.GET.get('stage','').strip()
    if q: qs=qs.filter(Q(opportunity_no__icontains=q)|Q(enquiry_no__icontains=q)|Q(buyer_company__icontains=q)|Q(product__icontains=q)|Q(style_no__icontains=q))
    if stage: qs=qs.filter(stage=stage)
    rows=list(qs[:1000]); pipeline=sum((x.expected_order_value for x in rows if x.stage not in {'LOST','CANCELLED'}),Decimal('0')); weighted=sum((x.weighted_value for x in rows if x.stage not in {'LOST','CANCELLED'}),Decimal('0'))
    overdue=[x for x in rows if x.follow_up_overdue]; won=sum(1 for x in rows if x.stage=='WON')
    ctx={'page':DashboardPage.objects.filter(slug='buyer-opportunity').first(),'opportunities':rows,'stages':[x[0] for x in BuyerOpportunity.STAGES],'priorities':[x[0] for x in BuyerOpportunity.PRIORITIES],'employees':Employee.objects.filter(status='ACTIVE').order_by('employee_id'),'activities':OpportunityActivity.objects.select_related('opportunity','performed_by').order_by('-occurred_at')[:100],'quotations':OpportunityQuotation.objects.select_related('opportunity').order_by('-created_at')[:100],'pipeline_value':pipeline,'weighted_value':weighted,'overdue':overdue,'won_count':won,'active_count':sum(1 for x in rows if x.stage not in {'WON','LOST','CANCELLED'})}
    return render(request,'buyer_opportunity.html',ctx)

@login_required
def buyer_opportunity_export_csv(request):
    import csv
    response=HttpResponse(content_type='text/csv'); response['Content-Disposition']='attachment; filename="buyer-enquiry-order-opportunity.csv"'
    w=csv.writer(response); w.writerow(['Enquiry No','Opportunity No','Buyer','Country','Contact','Product','Style','Qty','Target Price','Currency','Expected Value','Probability %','Weighted Value','Delivery Date','Follow-up','Stage','Priority','Owner','Merchandiser','Converted Order'])
    for x in BuyerOpportunity.objects.select_related('owner','merchandiser','converted_order').order_by('-updated_at'): w.writerow([x.enquiry_no,x.opportunity_no,x.buyer_company,x.buyer_country,x.buyer_contact,x.product,x.style_no,x.target_quantity,x.target_unit_price,x.currency,x.expected_order_value,x.probability_percent,x.weighted_value,x.required_delivery_date or '',x.follow_up_date or '',x.stage,x.priority,x.owner.username if x.owner else '',x.merchandiser.employee_id if x.merchandiser else '',x.converted_order.master_order_id if x.converted_order else ''])
    return response

@login_required
def api_buyer_opportunities(request):
    rows=[{'enquiry_no':x.enquiry_no,'opportunity_no':x.opportunity_no,'buyer':x.buyer_company,'product':x.product,'quantity':x.target_quantity,'currency':x.currency,'expected_value':str(x.expected_order_value),'probability_percent':str(x.probability_percent),'weighted_value':str(x.weighted_value),'stage':x.stage,'priority':x.priority,'delivery_date':str(x.required_delivery_date or ''),'follow_up_date':str(x.follow_up_date or ''),'follow_up_overdue':x.follow_up_overdue,'quotation_version':x.quotation_version,'converted_order':x.converted_order.master_order_id if x.converted_order else None} for x in BuyerOpportunity.objects.select_related('converted_order').order_by('-updated_at')]
    return JsonResponse({'count':len(rows),'results':rows})


@login_required
@require_http_methods(['GET','POST'])
def communication_center(request):
    from django.contrib import messages
    from django.db.models import Q
    from django.core.files.uploadedfile import UploadedFile
    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='new_thread':
                    seq=CommunicationThread.objects.filter(created_at__date=timezone.localdate()).count()+1
                    thread=CommunicationThread.objects.create(
                        thread_no=f'COM-{timezone.now():%Y%m%d}-{seq:05d}',
                        subject=request.POST.get('subject','').strip(),
                        thread_type=request.POST.get('thread_type','INTERNAL'),
                        priority=request.POST.get('priority','NORMAL'),
                        reference=request.POST.get('reference','').strip(),
                        department=Department.objects.filter(pk=request.POST.get('department_id')).first(),
                        buyer_opportunity=BuyerOpportunity.objects.filter(pk=request.POST.get('buyer_opportunity_id')).first(),
                        order=MasterOrder.objects.filter(pk=request.POST.get('order_id')).first(),
                        created_by=request.user,
                        assigned_to=User.objects.filter(pk=request.POST.get('assigned_to')).first()
                    )
                    thread.participants.add(request.user)
                    if thread.assigned_to: thread.participants.add(thread.assigned_to)
                    body=request.POST.get('body','').strip()
                    if body:
                        msg=CommunicationMessage.objects.create(thread=thread,channel=request.POST.get('channel','CHAT_24_7'),direction='INTERNAL',sender_user=request.user,body=body,status='SENT')
                        for f in request.FILES.getlist('attachments'):
                            CommunicationAttachment.objects.create(message=msg,file=f,original_name=f.name,mime_type=getattr(f,'content_type',''),file_size=getattr(f,'size',0),uploaded_by=request.user)
                    messages.success(request,f'{thread.thread_no} created.')
                elif action=='send_message':
                    thread=get_object_or_404(CommunicationThread,pk=request.POST.get('thread_id'))
                    channel=request.POST.get('channel','CHAT_24_7')
                    msg=CommunicationMessage.objects.create(
                        thread=thread,channel=channel,
                        direction='INTERNAL' if channel in {'CHAT_24_7','INTERNAL_CHAT'} else 'OUTBOUND',
                        sender_user=request.user,sender_address=request.POST.get('sender_address','').strip(),
                        recipient_address=request.POST.get('recipient_address','').strip(),
                        subject=request.POST.get('message_subject','').strip(),
                        body=request.POST.get('body','').strip(),
                        status='SENT' if channel in {'CHAT_24_7','INTERNAL_CHAT','NOTICE','SYSTEM_ALERT'} else 'QUEUED',
                        is_red_alert=request.POST.get('is_red_alert')=='on'
                    )
                    for f in request.FILES.getlist('attachments'):
                        CommunicationAttachment.objects.create(message=msg,file=f,original_name=f.name,mime_type=getattr(f,'content_type',''),file_size=getattr(f,'size',0),uploaded_by=request.user)
                    thread.last_message_at=timezone.now(); thread.save(update_fields=['last_message_at','updated_at'])
                    if msg.is_red_alert:
                        Alert.objects.create(level='RED',module='Communication Center',reference=thread.thread_no,message=msg.body or msg.subject,actioned=False)
                    messages.success(request,'Message recorded/queued.')
                elif action=='mark_read':
                    msg=get_object_or_404(CommunicationMessage,pk=request.POST.get('message_id'))
                    CommunicationReadReceipt.objects.get_or_create(message=msg,user=request.user,defaults={'read_at':timezone.now()})
                    msg.status='READ'; msg.read_at=timezone.now(); msg.save(update_fields=['status','read_at','updated_at'])
                    messages.success(request,'Message marked read.')
                elif action=='alert':
                    alert=Alert.objects.create(
                        title=request.POST.get('title','').strip(),
                        message=request.POST.get('body','').strip(),
                        level=request.POST.get('level','INFO'),
                        department=request.POST.get('department_name','').strip(),
                        reference=request.POST.get('reference','').strip(),
                        actioned=False
                    )
                    if request.POST.get('create_action')=='on':
                        ActionItem.objects.create(
                            title=request.POST.get('action_title','').strip() or f'Action for {alert.title}',
                            assigned_to=User.objects.filter(pk=request.POST.get('assigned_to')).first(),
                            department=request.POST.get('department_name','').strip(),
                            due_at=request.POST.get('due_at') or None,
                            status='OPEN',
                            priority='URGENT' if alert.level=='RED' else 'NORMAL'
                        )
                    messages.success(request,'Alert created.')
                elif action=='action_item':
                    ActionItem.objects.create(
                        title=request.POST.get('title','').strip(),
                        assigned_to=User.objects.filter(pk=request.POST.get('assigned_to')).first(),
                        department=request.POST.get('department_name','').strip(),
                        due_at=request.POST.get('due_at') or None,
                        status=request.POST.get('status','OPEN'),
                        priority=request.POST.get('priority','NORMAL')
                    )
                    messages.success(request,'Action item created.')
                elif action=='action_complete':
                    item=get_object_or_404(ActionItem,pk=request.POST.get('action_id'))
                    item.status='COMPLETED'; item.save(update_fields=['status','updated_at'])
                    messages.success(request,'Action completed.')
                elif action=='notice':
                    CommunicationNotice.objects.create(
                        title=request.POST.get('title','').strip(),body=request.POST.get('body','').strip(),
                        notice_type=request.POST.get('notice_type','NOTICE'),
                        department=Department.objects.filter(pk=request.POST.get('department_id')).first(),
                        target_role=request.POST.get('target_role','').strip(),
                        target_all=request.POST.get('target_all')=='on',
                        expires_at=request.POST.get('expires_at') or None,created_by=request.user,active=True
                    )
                    messages.success(request,'Notice/Broadcast created.')
                elif action=='close_thread':
                    thread=get_object_or_404(CommunicationThread,pk=request.POST.get('thread_id'))
                    thread.status='CLOSED'; thread.closed_at=timezone.now(); thread.save(update_fields=['status','closed_at','updated_at'])
                    messages.success(request,'Thread closed.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('communication_center')

    q=request.GET.get('q','').strip()
    channel=request.GET.get('channel','').strip()
    threads=CommunicationThread.objects.select_related('department','buyer_opportunity','order','created_by','assigned_to').order_by('-last_message_at')
    if q:
        threads=threads.filter(Q(thread_no__icontains=q)|Q(subject__icontains=q)|Q(reference__icontains=q))
    messages_qs=CommunicationMessage.objects.select_related('thread','sender_user').prefetch_related('attachments').order_by('-sent_at')
    if channel: messages_qs=messages_qs.filter(channel=channel)
    unread=CommunicationMessage.objects.exclude(read_receipts__user=request.user).count()
    ctx={
        'page':DashboardPage.objects.filter(slug='communication-center-master').first(),
        'threads':threads[:200],'comm_messages':messages_qs[:250],
        'notices':CommunicationNotice.objects.filter(active=True).select_related('department').order_by('-created_at')[:100],
        'connectors':CommunicationConnector.objects.order_by('connector_type','name'),
        'departments':Department.objects.all().order_by('name'),'users':User.objects.filter(is_active=True).order_by('username'),
        'buyers':BuyerOpportunity.objects.order_by('-updated_at')[:300],'orders':MasterOrder.objects.order_by('-updated_at')[:300],
        'unread_count':unread,'open_threads':CommunicationThread.objects.filter(status='OPEN').count(),
        'red_alerts':CommunicationMessage.objects.filter(is_red_alert=True).count(),
        'chat_count':CommunicationMessage.objects.filter(channel='CHAT_24_7').count(),
        'email_count':CommunicationMessage.objects.filter(channel='EMAIL').count(),
        'whatsapp_count':CommunicationMessage.objects.filter(channel='WHATSAPP').count(),
        'alerts':Alert.objects.order_by('-created_at')[:100],
        'action_items':ActionItem.objects.select_related('assigned_to').order_by('-created_at')[:100],
        'audit_logs':AuditLog.objects.select_related('user').order_by('-created_at')[:150],
        'channels':[x[0] for x in CommunicationMessage.CHANNELS],
        'thread_types':[x[0] for x in CommunicationThread.TYPES],
        'priorities':[x[0] for x in CommunicationThread.PRIORITIES]
    }
    return render(request,'communication_center.html',ctx)

@login_required
def communication_center_export_csv(request):
    import csv
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']='attachment; filename="communication-center-history.csv"'
    w=csv.writer(response); w.writerow(['Date','Thread','Subject','Channel','Direction','Sender','Recipients','Status','Red Alert','Body'])
    for m in CommunicationMessage.objects.select_related('thread','sender_user').order_by('-sent_at'):
        w.writerow([m.sent_at,m.thread.thread_no,m.thread.subject,m.channel,m.direction,m.sender_user.username if m.sender_user else m.sender_address,m.recipient_address,m.status,m.is_red_alert,m.body])
    return response

@login_required
def api_communication_center(request):
    rows=[{
        'thread_no':t.thread_no,'subject':t.subject,'type':t.thread_type,'priority':t.priority,'reference':t.reference,
        'status':t.status,'department':t.department.name if t.department else None,
        'buyer_opportunity':t.buyer_opportunity.opportunity_no if t.buyer_opportunity else None,
        'order':t.order.master_order_id if t.order else None,'last_message_at':t.last_message_at.isoformat()
    } for t in CommunicationThread.objects.select_related('department','buyer_opportunity','order').order_by('-last_message_at')[:500]]
    return JsonResponse({'count':len(rows),'unread':CommunicationMessage.objects.exclude(read_receipts__user=request.user).count(),'results':rows})


@login_required
@require_http_methods(['GET','POST'])
def profit_feasibility_gate(request):
    from django.contrib import messages
    from django.db.models import Q
    if request.method=='POST':
        action=request.POST.get('action','save_gate')
        try:
            with transaction.atomic():
                if action=='save_gate':
                    opportunity=get_object_or_404(BuyerOpportunity,pk=request.POST.get('opportunity_id'))
                    gate,_=ProfitFeasibilityGate.objects.get_or_create(opportunity=opportunity)
                    decimal_fields=[
                        'selling_price_per_unit','material_cost','accessory_cost','labour_cost','production_overhead',
                        'finishing_packing_cost','logistics_freight_cost','finance_bank_cost','commission_cost',
                        'compliance_testing_cost','contingency_cost','other_cost','minimum_margin_percent',
                        'capacity_score','material_readiness_score','workforce_score','machine_score','lead_time_score',
                        'quality_score','compliance_score','buyer_credit_score','commercial_risk_score'
                    ]
                    for fld in decimal_fields:
                        val=request.POST.get(fld)
                        if val not in [None,'']:
                            setattr(gate,fld,Decimal(val))
                    gate.currency=request.POST.get('currency',opportunity.currency or 'USD')
                    gate.quantity=int(request.POST.get('quantity') or opportunity.target_quantity or 0)
                    if not request.POST.get('selling_price_per_unit'):
                        gate.selling_price_per_unit=opportunity.target_unit_price
                    gate.production_days_required=int(request.POST.get('production_days_required') or 0)
                    gate.available_days=int(request.POST.get('available_days') or 0)
                    gate.critical_bottleneck=request.POST.get('critical_bottleneck','').strip()
                    gate.material_shortage=request.POST.get('material_shortage','').strip()
                    gate.capacity_risk=request.POST.get('capacity_risk','').strip()
                    gate.commercial_risk=request.POST.get('commercial_risk','').strip()
                    gate.remarks=request.POST.get('remarks','').strip()
                    gate.reviewed_by=request.user
                    gate.reviewed_at=timezone.now()
                    gate.save()
                    messages.success(request,f'Gate calculated for {opportunity.opportunity_no}: {gate.system_recommendation}.')
                elif action=='decision':
                    gate=get_object_or_404(ProfitFeasibilityGate,pk=request.POST.get('gate_id'))
                    decision=request.POST.get('final_decision','PENDING')
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if decision in {'ACCEPT','ACCEPT_WITH_RISK'}:
                        if not approval or approval.status!='APPROVED':
                            raise PermissionError('ACCEPT / ACCEPT WITH RISK requires an APPROVED senior Approval Request.')
                        if decision=='ACCEPT' and gate.margin_percent < gate.minimum_margin_percent:
                            raise PermissionError('Cannot ACCEPT below the minimum profit margin. Use HOLD/REJECT or revise costing.')
                    gate.final_decision=decision
                    gate.approval=approval
                    gate.reviewed_by=request.user
                    gate.reviewed_at=timezone.now()
                    gate.remarks=request.POST.get('decision_remarks','').strip() or gate.remarks
                    gate.save()
                    OpportunityActivity.objects.create(
                        opportunity=gate.opportunity,activity_type='STATUS_CHANGE',
                        subject=f'Profit + Feasibility Gate: {decision}',
                        details=f'Recommendation={gate.system_recommendation}; Margin={gate.margin_percent}%; Feasibility={gate.feasibility_score}%',
                        performed_by=request.user
                    )
                    messages.success(request,f'Final gate decision saved: {decision}.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('profit_feasibility_gate')

    q=request.GET.get('q','').strip()
    opportunities=BuyerOpportunity.objects.order_by('-updated_at')
    if q:
        opportunities=opportunities.filter(Q(opportunity_no__icontains=q)|Q(enquiry_no__icontains=q)|Q(buyer_company__icontains=q)|Q(product__icontains=q))
    gates=ProfitFeasibilityGate.objects.select_related('opportunity','approval','reviewed_by').order_by('-updated_at')
    ctx={
        'page':DashboardPage.objects.filter(slug='profit-feasibility-gate').first(),
        'opportunities':opportunities[:500],
        'gates':gates[:500],
        'pending_count':gates.filter(final_decision='PENDING').count(),
        'accept_count':gates.filter(final_decision='ACCEPT').count(),
        'risk_count':gates.filter(final_decision='ACCEPT_WITH_RISK').count(),
        'hold_count':gates.filter(final_decision='HOLD').count(),
        'reject_count':gates.filter(final_decision='REJECT').count(),
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-approved_at')[:300],
    }
    return render(request,'profit_feasibility_gate.html',ctx)

@login_required
def profit_feasibility_export_csv(request):
    import csv
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']='attachment; filename="profit-feasibility-gate.csv"'
    w=csv.writer(response)
    w.writerow(['Opportunity','Buyer','Product','Revenue','Total Cost','Gross Profit','Margin %','Minimum Margin %','Feasibility Score','System Recommendation','Final Decision','Approval','Gate Passed'])
    for g in ProfitFeasibilityGate.objects.select_related('opportunity','approval').order_by('-updated_at'):
        w.writerow([g.opportunity.opportunity_no,g.opportunity.buyer_company,g.opportunity.product,g.revenue,g.total_cost,g.gross_profit,g.margin_percent,g.minimum_margin_percent,g.feasibility_score,g.system_recommendation,g.final_decision,g.approval.reference if g.approval else '',g.gate_passed])
    return response

@login_required
def api_profit_feasibility_gate(request):
    rows=[{
        'opportunity':g.opportunity.opportunity_no,'buyer':g.opportunity.buyer_company,'product':g.opportunity.product,
        'revenue':str(g.revenue),'total_cost':str(g.total_cost),'gross_profit':str(g.gross_profit),
        'margin_percent':str(g.margin_percent),'minimum_margin_percent':str(g.minimum_margin_percent),
        'feasibility_score':str(g.feasibility_score),'system_recommendation':g.system_recommendation,
        'final_decision':g.final_decision,'gate_passed':g.gate_passed
    } for g in ProfitFeasibilityGate.objects.select_related('opportunity','approval').order_by('-updated_at')[:500]]
    return JsonResponse({'count':len(rows),'results':rows})


@login_required
@require_http_methods(['GET','POST'])
def free_capacity_opportunity(request):
    from django.contrib import messages
    from django.db.models import Q
    if request.method=='POST':
        action=request.POST.get('action','save_check')
        try:
            with transaction.atomic():
                if action=='save_check':
                    opp=BuyerOpportunity.objects.filter(pk=request.POST.get('opportunity_id')).first()
                    seq=FreeCapacityOpportunity.objects.filter(created_at__date=timezone.localdate()).count()+1
                    ref=request.POST.get('reference','').strip() or f'FCO-{timezone.now():%Y%m%d}-{seq:04d}'
                    obj=FreeCapacityOpportunity.objects.filter(reference=ref).first() or FreeCapacityOpportunity(reference=ref)
                    obj.opportunity=opp
                    obj.product=request.POST.get('product','').strip() or (opp.product if opp else '')
                    obj.quantity=int(request.POST.get('quantity') or (opp.target_quantity if opp else 0) or 0)
                    obj.requested_delivery_date=request.POST.get('requested_delivery_date') or (opp.required_delivery_date if opp else None)
                    obj.required_minutes=int(request.POST.get('required_minutes') or 0)
                    obj.available_machine_minutes=int(request.POST.get('available_machine_minutes') or 0)
                    obj.available_workforce_minutes=int(request.POST.get('available_workforce_minutes') or 0)
                    obj.available_line_minutes=int(request.POST.get('available_line_minutes') or 0)
                    obj.reserved_minutes=int(request.POST.get('reserved_minutes') or 0)
                    obj.safety_buffer_minutes=int(request.POST.get('safety_buffer_minutes') or 0)
                    for fld in [
                        'material_readiness_percent','accessories_readiness_percent','qc_readiness_percent',
                        'finishing_readiness_percent','selling_value','incremental_cost',
                        'minimum_margin_percent','current_confirmed_load_percent','rush_risk_percent'
                    ]:
                        val=request.POST.get(fld)
                        if val not in [None,'']:
                            setattr(obj,fld,Decimal(val))
                    obj.notes=request.POST.get('notes','').strip()
                    obj.reviewed_by=request.user
                    obj.reviewed_at=timezone.now()
                    obj.save()
                    messages.success(request,f'{obj.reference}: {obj.system_recommendation}. Free capacity {obj.free_minutes} minutes.')
                elif action=='decision':
                    obj=get_object_or_404(FreeCapacityOpportunity,pk=request.POST.get('capacity_id'))
                    decision=request.POST.get('final_decision','PENDING')
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if decision in {'TAKE_QUICK_ORDER','TAKE_WITH_RISK'}:
                        if not approval or approval.status!='APPROVED':
                            raise PermissionError('Taking a quick order requires an APPROVED senior Approval Request.')
                        if obj.free_minutes < obj.required_minutes:
                            raise PermissionError('Quick order cannot be accepted because protected free capacity is below required minutes.')
                        if obj.margin_percent < obj.minimum_margin_percent:
                            raise PermissionError('Quick order cannot be accepted below the minimum incremental profit margin.')
                    obj.final_decision=decision
                    obj.approval=approval
                    obj.reviewed_by=request.user
                    obj.reviewed_at=timezone.now()
                    obj.notes=request.POST.get('decision_notes','').strip() or obj.notes
                    obj.save()
                    if obj.opportunity:
                        OpportunityActivity.objects.create(
                            opportunity=obj.opportunity,
                            activity_type='STATUS_CHANGE',
                            subject=f'Free Capacity Quick Order: {decision}',
                            details=f'Free={obj.free_minutes} min; Required={obj.required_minutes} min; Margin={obj.margin_percent}%; Readiness={obj.readiness_score}%',
                            performed_by=request.user
                        )
                    messages.success(request,f'Capacity decision saved: {decision}.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('free_capacity_opportunity')

    q=request.GET.get('q','').strip()
    rows=FreeCapacityOpportunity.objects.select_related('opportunity','approval','reviewed_by').order_by('-updated_at')
    if q:
        rows=rows.filter(Q(reference__icontains=q)|Q(product__icontains=q)|Q(opportunity__opportunity_no__icontains=q)|Q(opportunity__buyer_company__icontains=q))
    ctx={
        'page':DashboardPage.objects.filter(slug='free-capacity-opportunity').first(),
        'rows':rows[:500],
        'opportunities':BuyerOpportunity.objects.order_by('-updated_at')[:500],
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-approved_at')[:300],
        'take_count':rows.filter(final_decision='TAKE_QUICK_ORDER').count(),
        'risk_count':rows.filter(final_decision='TAKE_WITH_RISK').count(),
        'hold_count':rows.filter(final_decision='HOLD').count(),
        'reject_count':rows.filter(final_decision='REJECT').count(),
        'pending_count':rows.filter(final_decision='PENDING').count(),
    }
    return render(request,'free_capacity_opportunity.html',ctx)

@login_required
def free_capacity_export_csv(request):
    import csv
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']='attachment; filename="free-capacity-quick-order-opportunity.csv"'
    w=csv.writer(response)
    w.writerow(['Reference','Opportunity','Buyer','Product','Qty','Required Minutes','Free Minutes','Capacity Fit %','Confirmed Load %','Material %','Accessories %','Readiness %','Selling Value','Incremental Cost','Profit','Margin %','Rush Risk %','Recommendation','Decision','Gate Passed'])
    for x in FreeCapacityOpportunity.objects.select_related('opportunity','approval').order_by('-updated_at'):
        w.writerow([
            x.reference,
            x.opportunity.opportunity_no if x.opportunity else '',
            x.opportunity.buyer_company if x.opportunity else '',
            x.product,x.quantity,x.required_minutes,x.free_minutes,x.capacity_fit_percent,
            x.current_confirmed_load_percent,x.material_readiness_percent,x.accessories_readiness_percent,
            x.readiness_score,x.selling_value,x.incremental_cost,x.incremental_profit,x.margin_percent,
            x.rush_risk_percent,x.system_recommendation,x.final_decision,x.gate_passed
        ])
    return response

@login_required
def api_free_capacity_opportunity(request):
    rows=[{
        'reference':x.reference,
        'opportunity':x.opportunity.opportunity_no if x.opportunity else None,
        'product':x.product,'quantity':x.quantity,
        'required_minutes':x.required_minutes,'free_minutes':x.free_minutes,
        'capacity_fit_percent':str(x.capacity_fit_percent),
        'confirmed_load_percent':str(x.current_confirmed_load_percent),
        'readiness_score':str(x.readiness_score),
        'incremental_profit':str(x.incremental_profit),
        'margin_percent':str(x.margin_percent),
        'rush_risk_percent':str(x.rush_risk_percent),
        'system_recommendation':x.system_recommendation,
        'final_decision':x.final_decision,'gate_passed':x.gate_passed
    } for x in FreeCapacityOpportunity.objects.select_related('opportunity','approval').order_by('-updated_at')[:500]]
    return JsonResponse({'count':len(rows),'results':rows})


def _file_access_ip(request):
    forwarded=request.META.get('HTTP_X_FORWARDED_FOR','')
    return (forwarded.split(',')[0].strip() if forwarded else request.META.get('REMOTE_ADDR')) or None

def _resolve_file_resource(resource_type, resource_id):
    key=(resource_type or '').lower()
    if key=='document':
        obj=get_object_or_404(DocumentRecord,pk=resource_id)
        return obj, obj.file, {
            'type':'document','name':obj.title or (obj.file.name.rsplit('/',1)[-1] if obj.file else ''),
            'reference':obj.reference or obj.document_id,
            'confidential':obj.confidential,
            'uploaded_by_id':obj.uploaded_by_id
        }
    if key=='sourcing_specification':
        obj=get_object_or_404(SourcingRequest,pk=resource_id)
        return obj,obj.specification_file,{'type':'sourcing_specification','name':obj.specification_file.name.rsplit('/',1)[-1] if obj.specification_file else '','reference':obj.request_no}
    if key=='sourcing_quotation':
        obj=get_object_or_404(SourcingQuotation,pk=resource_id)
        return obj,obj.quotation_file,{'type':'sourcing_quotation','name':obj.quotation_file.name.rsplit('/',1)[-1] if obj.quotation_file else '','reference':obj.quote_no}
    if key=='sourcing_sample':
        obj=get_object_or_404(SourcingSample,pk=resource_id)
        return obj,obj.sample_file,{'type':'sourcing_sample','name':obj.sample_file.name.rsplit('/',1)[-1] if obj.sample_file else '','reference':obj.sample_no}
    if key=='sourcing_auto_report':
        obj=get_object_or_404(SourcingAutoReport,pk=resource_id)
        return obj,obj.generated_file,{'type':'sourcing_auto_report','name':obj.generated_file.name.rsplit('/',1)[-1] if obj.generated_file else '','reference':f'SOURCE-{obj.report_date}-{obj.slot}'}
    if key=='purchase_amendment':
        obj=get_object_or_404(PurchaseAmendment,pk=resource_id); return obj,obj.document,{'type':'purchase_amendment','name':obj.document.name.rsplit('/',1)[-1] if obj.document else '','reference':obj.amendment_no}
    if key=='purchase_return':
        obj=get_object_or_404(PurchaseReturn,pk=resource_id); return obj,obj.return_document,{'type':'purchase_return','name':obj.return_document.name.rsplit('/',1)[-1] if obj.return_document else '','reference':obj.return_no}
    if key=='supplier_document':
        obj=get_object_or_404(SupplierDocument,pk=resource_id); return obj,obj.file,{'type':'supplier_document','name':obj.file.name.rsplit('/',1)[-1] if obj.file else '','reference':obj.supplier.supplier_id}
    if key=='supplier_quotation':
        obj=get_object_or_404(SupplierRFQ,pk=resource_id); return obj,obj.quotation_file,{'type':'supplier_quotation','name':obj.quotation_file.name.rsplit('/',1)[-1] if obj.quotation_file else '','reference':obj.rfq_no}
    if key=='supplier_po':
        obj=get_object_or_404(SupplierPurchaseOrder,pk=resource_id); return obj,obj.po_file,{'type':'supplier_po','name':obj.po_file.name.rsplit('/',1)[-1] if obj.po_file else '','reference':obj.po_no}
    if key=='supplier_invoice':
        obj=get_object_or_404(SupplierInvoice,pk=resource_id); return obj,obj.invoice_file,{'type':'supplier_invoice','name':obj.invoice_file.name.rsplit('/',1)[-1] if obj.invoice_file else '','reference':obj.invoice_no}
    if key=='supplier_grn':
        obj=get_object_or_404(SupplierReceipt,pk=resource_id); return obj,obj.grn_file,{'type':'supplier_grn','name':obj.grn_file.name.rsplit('/',1)[-1] if obj.grn_file else '','reference':obj.grn_no}
    if key=='supplier_delivery_note':
        obj=get_object_or_404(SupplierReceipt,pk=resource_id); return obj,obj.delivery_note,{'type':'supplier_delivery_note','name':obj.delivery_note.name.rsplit('/',1)[-1] if obj.delivery_note else '','reference':obj.grn_no}
    if key=='shipping_instruction':
        obj=get_object_or_404(ShippingPlan,pk=resource_id)
        return obj,obj.shipping_instruction,{'type':'shipping_instruction','name':obj.shipping_instruction.name.rsplit('/',1)[-1] if obj.shipping_instruction else '','reference':obj.plan_no}
    if key=='shipping_document':
        obj=get_object_or_404(ShippingDocument,pk=resource_id)
        return obj,obj.file,{'type':'shipping_document','name':obj.file.name.rsplit('/',1)[-1] if obj.file else '','reference':obj.document_no or obj.plan.plan_no}
    if key=='shipping_pod':
        obj=get_object_or_404(ShippingPOD,pk=resource_id)
        return obj,obj.proof_of_delivery,{'type':'shipping_pod','name':obj.proof_of_delivery.name.rsplit('/',1)[-1] if obj.proof_of_delivery else '','reference':obj.plan.plan_no}
    if key=='shipping_buyer_signature':
        obj=get_object_or_404(ShippingPOD,pk=resource_id)
        return obj,obj.buyer_signature,{'type':'shipping_buyer_signature','name':obj.buyer_signature.name.rsplit('/',1)[-1] if obj.buyer_signature else '','reference':obj.plan.plan_no}
    if key=='shipping_delivery_photo':
        obj=get_object_or_404(ShippingPOD,pk=resource_id)
        return obj,obj.delivery_photo,{'type':'shipping_delivery_photo','name':obj.delivery_photo.name.rsplit('/',1)[-1] if obj.delivery_photo else '','reference':obj.plan.plan_no}
    if key=='shipping_auto_report':
        obj=get_object_or_404(ShippingAutoReport,pk=resource_id)
        return obj,obj.generated_file,{'type':'shipping_auto_report','name':obj.generated_file.name.rsplit('/',1)[-1] if obj.generated_file else '','reference':f'SHIP-{obj.report_date}-{obj.slot}'}
    if key=='packing_specification':
        obj=get_object_or_404(PackingPlan,pk=resource_id)
        return obj,obj.packing_spec_file,{'type':'packing_specification','name':obj.packing_spec_file.name.rsplit('/',1)[-1] if obj.packing_spec_file else '','reference':obj.plan_no}
    if key=='packing_qc_photo':
        obj=get_object_or_404(PackingQC,pk=resource_id)
        return obj,obj.qc_photo,{'type':'packing_qc_photo','name':obj.qc_photo.name.rsplit('/',1)[-1] if obj.qc_photo else '','reference':obj.plan.plan_no}
    if key=='packing_auto_report':
        obj=get_object_or_404(PackingAutoReport,pk=resource_id)
        return obj,obj.generated_file,{'type':'packing_auto_report','name':obj.generated_file.name.rsplit('/',1)[-1] if obj.generated_file else '','reference':f'PACK-{obj.report_date}-{obj.slot}'}
    if key=='final_qc_specification':
        obj=get_object_or_404(FinalQCPlan,pk=resource_id)
        return obj,obj.specification_file,{'type':'final_qc_specification','name':obj.specification_file.name.rsplit('/',1)[-1] if obj.specification_file else '','reference':obj.plan_no}
    if key=='final_qc_approved_sample':
        obj=get_object_or_404(FinalQCPlan,pk=resource_id)
        return obj,obj.approved_sample_file,{'type':'final_qc_approved_sample','name':obj.approved_sample_file.name.rsplit('/',1)[-1] if obj.approved_sample_file else '','reference':obj.plan_no}
    if key=='final_qc_packing_spec':
        obj=get_object_or_404(FinalQCPlan,pk=resource_id)
        return obj,obj.packing_spec_file,{'type':'final_qc_packing_spec','name':obj.packing_spec_file.name.rsplit('/',1)[-1] if obj.packing_spec_file else '','reference':obj.plan_no}
    if key=='final_qc_inspection_sheet':
        obj=get_object_or_404(FinalQCInspection,pk=resource_id)
        return obj,obj.inspection_sheet,{'type':'final_qc_inspection_sheet','name':obj.inspection_sheet.name.rsplit('/',1)[-1] if obj.inspection_sheet else '','reference':obj.plan.plan_no}
    if key=='final_qc_photo':
        obj=get_object_or_404(FinalQCInspection,pk=resource_id)
        return obj,obj.photo,{'type':'final_qc_photo','name':obj.photo.name.rsplit('/',1)[-1] if obj.photo else '','reference':obj.plan.plan_no}
    if key=='final_qc_auto_report':
        obj=get_object_or_404(FinalQCAutoReport,pk=resource_id)
        return obj,obj.generated_file,{'type':'final_qc_auto_report','name':obj.generated_file.name.rsplit('/',1)[-1] if obj.generated_file else '','reference':f'FINAL-QC-{obj.report_date}-{obj.slot}'}
    if key=='iron_instruction':
        obj=get_object_or_404(IronPlan,pk=resource_id)
        return obj,obj.instruction_file,{
            'type':'iron_instruction',
            'name':obj.instruction_file.name.rsplit('/',1)[-1] if obj.instruction_file else '',
            'reference':obj.plan_no
        }
    if key=='iron_qc_photo':
        obj=get_object_or_404(IronQC,pk=resource_id)
        return obj,obj.qc_photo,{
            'type':'iron_qc_photo',
            'name':obj.qc_photo.name.rsplit('/',1)[-1] if obj.qc_photo else '',
            'reference':obj.plan.plan_no
        }
    if key=='iron_auto_report':
        obj=get_object_or_404(IronAutoReport,pk=resource_id)
        return obj,obj.generated_file,{
            'type':'iron_auto_report',
            'name':obj.generated_file.name.rsplit('/',1)[-1] if obj.generated_file else '',
            'reference':f'IRON-{obj.report_date}-{obj.slot}'
        }
    if key=='poly_specification':
        obj=get_object_or_404(PolyPlan,pk=resource_id)
        return obj,obj.packing_specification,{
            'type':'poly_specification',
            'name':obj.packing_specification.name.rsplit('/',1)[-1] if obj.packing_specification else '',
            'reference':obj.plan_no
        }
    if key=='poly_artwork':
        obj=get_object_or_404(PolyPlan,pk=resource_id)
        return obj,obj.artwork,{
            'type':'poly_artwork',
            'name':obj.artwork.name.rsplit('/',1)[-1] if obj.artwork else '',
            'reference':obj.plan_no
        }
    if key=='poly_qc_photo':
        obj=get_object_or_404(PolyQC,pk=resource_id)
        return obj,obj.qc_photo,{
            'type':'poly_qc_photo',
            'name':obj.qc_photo.name.rsplit('/',1)[-1] if obj.qc_photo else '',
            'reference':obj.plan.plan_no
        }
    if key=='poly_auto_report':
        obj=get_object_or_404(PolyAutoReport,pk=resource_id)
        return obj,obj.generated_file,{
            'type':'poly_auto_report',
            'name':obj.generated_file.name.rsplit('/',1)[-1] if obj.generated_file else '',
            'reference':f'POLY-{obj.report_date}-{obj.slot}'
        }
    if key=='hand_iron_instruction':
        obj=get_object_or_404(HandIronPlan,pk=resource_id)
        return obj,obj.instruction_file,{
            'type':'hand_iron_instruction',
            'name':obj.instruction_file.name.rsplit('/',1)[-1] if obj.instruction_file else '',
            'reference':obj.plan_no
        }
    if key=='hand_iron_qc_photo':
        obj=get_object_or_404(HandIronQC,pk=resource_id)
        return obj,obj.qc_photo,{
            'type':'hand_iron_qc_photo',
            'name':obj.qc_photo.name.rsplit('/',1)[-1] if obj.qc_photo else '',
            'reference':obj.plan.plan_no
        }
    if key=='hand_iron_auto_report':
        obj=get_object_or_404(HandIronAutoReport,pk=resource_id)
        return obj,obj.generated_file,{
            'type':'hand_iron_auto_report',
            'name':obj.generated_file.name.rsplit('/',1)[-1] if obj.generated_file else '',
            'reference':f'HIRON-{obj.report_date}-{obj.slot}'
        }
    if key=='qc_specification':
        obj=get_object_or_404(QCInspectionPlan,pk=resource_id)
        return obj,obj.specification_file,{'type':'qc_specification','name':obj.specification_file.name.rsplit('/',1)[-1] if obj.specification_file else '','reference':obj.plan_no}
    if key=='qc_approved_sample':
        obj=get_object_or_404(QCInspectionPlan,pk=resource_id)
        return obj,obj.approved_sample_file,{'type':'qc_approved_sample','name':obj.approved_sample_file.name.rsplit('/',1)[-1] if obj.approved_sample_file else '','reference':obj.plan_no}
    if key=='qc_inspection_photo':
        obj=get_object_or_404(QCInspection,pk=resource_id)
        return obj,obj.photo,{'type':'qc_inspection_photo','name':obj.photo.name.rsplit('/',1)[-1] if obj.photo else '','reference':obj.plan.plan_no}
    if key=='qc_inspection_sheet':
        obj=get_object_or_404(QCInspection,pk=resource_id)
        return obj,obj.inspection_sheet,{'type':'qc_inspection_sheet','name':obj.inspection_sheet.name.rsplit('/',1)[-1] if obj.inspection_sheet else '','reference':obj.plan.plan_no}
    if key=='qc_auto_report':
        obj=get_object_or_404(QCAutoReport,pk=resource_id)
        return obj,obj.generated_file,{'type':'qc_auto_report','name':obj.generated_file.name.rsplit('/',1)[-1] if obj.generated_file else '','reference':f'QC-{obj.report_date}-{obj.slot}'}
    if key=='label_artwork':
        obj=get_object_or_404(LabelPlan,pk=resource_id)
        return obj,obj.artwork,{'type':'label_artwork','name':obj.artwork.name.rsplit('/',1)[-1] if obj.artwork else '','reference':obj.plan_no}
    if key=='label_specification':
        obj=get_object_or_404(LabelPlan,pk=resource_id)
        return obj,obj.specification,{'type':'label_specification','name':obj.specification.name.rsplit('/',1)[-1] if obj.specification else '','reference':obj.plan_no}
    if key=='label_proof':
        obj=get_object_or_404(LabelProof,pk=resource_id)
        return obj,obj.proof_file,{'type':'label_proof','name':obj.proof_file.name.rsplit('/',1)[-1] if obj.proof_file else '','reference':obj.proof_no}
    if key=='label_sample_image':
        obj=get_object_or_404(LabelProof,pk=resource_id)
        return obj,obj.sample_image,{'type':'label_sample_image','name':obj.sample_image.name.rsplit('/',1)[-1] if obj.sample_image else '','reference':obj.proof_no}
    if key=='label_auto_report':
        obj=get_object_or_404(LabelAutoReport,pk=resource_id)
        return obj,obj.generated_file,{'type':'label_auto_report','name':obj.generated_file.name.rsplit('/',1)[-1] if obj.generated_file else '','reference':f'LBL-{obj.report_date}-{obj.slot}'}
    if key=='embroidery_artwork':
        obj=get_object_or_404(EmbroideryPlan,pk=resource_id)
        return obj,obj.artwork,{'type':'embroidery_artwork','name':obj.artwork.name.rsplit('/',1)[-1] if obj.artwork else '','reference':obj.plan_no}
    if key=='embroidery_program':
        obj=get_object_or_404(EmbroideryPlan,pk=resource_id)
        return obj,obj.program_file,{'type':'embroidery_program','name':obj.program_file.name.rsplit('/',1)[-1] if obj.program_file else '','reference':obj.plan_no}
    if key=='embroidery_sample_image':
        obj=get_object_or_404(EmbroiderySample,pk=resource_id)
        return obj,obj.sample_image,{'type':'embroidery_sample_image','name':obj.sample_image.name.rsplit('/',1)[-1] if obj.sample_image else '','reference':obj.sample_no}
    if key=='embroidery_auto_report':
        obj=get_object_or_404(EmbroideryAutoReport,pk=resource_id)
        return obj,obj.generated_file,{'type':'embroidery_auto_report','name':obj.generated_file.name.rsplit('/',1)[-1] if obj.generated_file else '','reference':f'EMB-{obj.report_date}-{obj.slot}'}
    if key=='cutting_auto_report':
        obj=get_object_or_404(CuttingAutoReport,pk=resource_id)
        return obj,obj.generated_file,{
            'type':'cutting_auto_report',
            'name':obj.generated_file.name.rsplit('/',1)[-1] if obj.generated_file else '',
            'reference':f'CUT-{obj.report_date}-{obj.slot}'
        }
    if key=='attendance_cctv_thumbnail':
        obj=get_object_or_404(AttendanceCCTVFeed,pk=resource_id)
        return obj,obj.thumbnail,{
            'type':'attendance_cctv_thumbnail',
            'name':obj.thumbnail.name.rsplit('/',1)[-1] if obj.thumbnail else '',
            'reference':obj.camera_ref or obj.name
        }
    if key=='hr_recruitment_cv':
        obj=get_object_or_404(HRRecruitment.objects.select_related('created_by'),pk=resource_id)
        return obj,obj.cv,{'type':'hr_recruitment_cv','name':obj.cv.name.rsplit('/',1)[-1] if obj.cv else '','reference':f'RECRUIT-{obj.id}','uploaded_by_id':obj.created_by_id}
    if key=='hr_leave_attachment':
        obj=get_object_or_404(HRLeaveRequest.objects.select_related('employee'),pk=resource_id)
        return obj,obj.attachment,{'type':'hr_leave_attachment','name':obj.attachment.name.rsplit('/',1)[-1] if obj.attachment else '','reference':f'LEAVE-{obj.id}','employee_id':obj.employee_id}
    if key=='hr_training_certificate':
        obj=get_object_or_404(HRTrainingRecord.objects.select_related('employee'),pk=resource_id)
        return obj,obj.certificate,{'type':'hr_training_certificate','name':obj.certificate.name.rsplit('/',1)[-1] if obj.certificate else '','reference':f'TRAIN-{obj.id}','employee_id':obj.employee_id}
    if key=='hr_case_attachment':
        obj=get_object_or_404(HRComplaintIncident.objects.select_related('employee'),pk=resource_id)
        return obj,obj.attachment,{'type':'hr_case_attachment','name':obj.attachment.name.rsplit('/',1)[-1] if obj.attachment else '','reference':obj.reference,'employee_id':obj.employee_id}
    if key=='staff_profile_photo':
        obj=get_object_or_404(StaffSelfServiceProfile.objects.select_related('employee'),pk=resource_id)
        return obj,obj.profile_photo,{
            'type':'staff_profile_photo',
            'name':obj.profile_photo.name.rsplit('/',1)[-1] if obj.profile_photo else '',
            'reference':obj.employee.employee_id,
            'employee_id':obj.employee_id
        }
    if key=='staff_document':
        obj=get_object_or_404(StaffDocument.objects.select_related('employee','uploaded_by'),pk=resource_id)
        return obj,obj.file,{
            'type':'staff_document',
            'name':obj.title or (obj.file.name.rsplit('/',1)[-1] if obj.file else ''),
            'reference':obj.reference or obj.employee.employee_id,
            'employee_id':obj.employee_id,
            'uploaded_by_id':obj.uploaded_by_id,
            'confidential':obj.confidential
        }
    if key=='staff_application_attachment':
        obj=get_object_or_404(StaffApplication.objects.select_related('employee'),pk=resource_id)
        return obj,obj.attachment,{
            'type':'staff_application_attachment',
            'name':obj.attachment.name.rsplit('/',1)[-1] if obj.attachment else '',
            'reference':obj.application_no,
            'employee_id':obj.employee_id
        }
    if key in {'delivery_pod','delivery_signature','delivery_photo'}:
        obj=get_object_or_404(BuyerDeliverySLA.objects.select_related('order','confirmed_by'),pk=resource_id)
        field={'delivery_pod':obj.proof_of_delivery,'delivery_signature':obj.buyer_signature,'delivery_photo':obj.delivery_photo}[key]
        return obj, field, {
            'type':key,
            'name':field.name.rsplit('/',1)[-1] if field else '',
            'reference':obj.order.master_order_id,
            'uploaded_by_id':obj.confirmed_by_id
        }
    if key=='communication_attachment':
        obj=get_object_or_404(
            CommunicationAttachment.objects.select_related('message__thread','uploaded_by'),
            pk=resource_id
        )
        thread=obj.message.thread
        return obj, obj.file, {
            'type':'communication_attachment','name':obj.original_name or (obj.file.name.rsplit('/',1)[-1] if obj.file else ''),
            'reference':thread.thread_no,
            'thread':thread,
            'uploaded_by_id':obj.uploaded_by_id
        }
    raise Http404('Unknown file resource type.')

# ---------------------------------------------------------------------------
# Universal File Controls - access policy
#
# The previous implementation leaked (TECHNICAL_ASSESSMENT.md 4.3):
#   * any non-confidential DocumentRecord returned True for ANY authenticated
#     user, so an Operator could read the CEO's board pack;
#   * delivery proof, buyer signatures and delivery photographs returned True
#     for any authenticated user;
#   * "if user.is_superuser or user.is_staff: return True" sat above about ten
#     branches that each returned "user.is_staff or user.is_superuser", so those
#     branches could only ever return False - the production, QC, label and
#     CCTV file types were unreachable for everyone else by accident;
#   * nine types the resolver can produce (final_qc_*, iron_*) were not
#     classified at all and fell through to False;
#   * a confidential document was visible only to its uploader, so a Finance
#     Manager without Django's is_staff flag could not read their own
#     department's files.
#
# Policy is now expressed as role families over the resource type, with the
# genuinely special cases handled first. Unclassified types are denied, and
# FileAccessPolicyTests asserts that every one of the 65 types the resolver can
# return is classified deliberately rather than by falling through.
#
# Still outstanding: this is role-based, not scope-based. There is no company,
# country or factory link on DocumentRecord, so one factory's manager can still
# read another's documents. True need-to-know needs the tenancy work in Phase 4
# - see TECHNICAL_ASSESSMENT.md 6.2.
# ---------------------------------------------------------------------------

#: (type prefixes, roles permitted). Order matters: first match wins.
_FILE_ACCESS_FAMILIES = (
    (('cutting_', 'embroidery_', 'label_', 'hand_iron_', 'iron_', 'poly_',
      'packing_'), roles_mod.PRODUCTION),
    (('qc_', 'final_qc_'), roles_mod.QUALITY),
    (('shipping_',), roles_mod.SHIPPING),
    (('sourcing_', 'supplier_', 'purchase_'), roles_mod.PROCUREMENT),
    (('hr_',), roles_mod.HR),
    # Camera stills are workforce surveillance: HR owns the process, IT the kit.
    (('attendance_cctv_',), roles_mod.HR | roles_mod.IT),
    # Proof of delivery, buyer signature and delivery photographs.
    (('delivery_',), roles_mod.COMMERCIAL | roles_mod.SHIPPING),
)


def _file_access_decision(user, meta):
    """Return (allowed, reason). The reason is recorded on refusal."""
    if not user or not user.is_authenticated:
        return False, 'not authenticated'
    if user.is_superuser:
        return True, ''

    file_type = meta.get('type') or ''

    # --- staff's own records -----------------------------------------------
    # A member of staff always reaches their own documents, application
    # attachments and profile photo; otherwise HR authority is required.
    if file_type in {'staff_document', 'staff_application_attachment', 'staff_profile_photo'}:
        employee = _staff_employee_for_user(user)
        if employee and employee.id == meta.get('employee_id'):
            return True, ''
        if has_any_role(user, roles_mod.HR):
            return True, ''
        return False, 'staff record belongs to another employee and user lacks HR authority'

    # --- communication attachments -----------------------------------------
    # Confined to the conversation: sender, thread owner, assignee, participant.
    if file_type == 'communication_attachment':
        thread = meta.get('thread')
        if thread is None:
            return False, 'communication attachment has no resolvable thread'
        if meta.get('uploaded_by_id') == user.id:
            return True, ''
        if thread.created_by_id == user.id or thread.assigned_to_id == user.id:
            return True, ''
        if thread.participants.filter(pk=user.pk).exists():
            return True, ''
        return False, 'user is not a participant in this conversation'

    # --- corporate document store ------------------------------------------
    if file_type == 'document':
        if meta.get('uploaded_by_id') and meta.get('uploaded_by_id') == user.id:
            return True, ''
        if meta.get('confidential'):
            if has_any_role(user, roles_mod.EXECUTIVE | roles_mod.FINANCE):
                return True, ''
            return False, ('document is confidential and user is neither the '
                           'uploader nor executive/finance')
        if has_any_role(user, roles_mod.MANAGEMENT):
            return True, ''
        return False, 'corporate document requires a management role'

    # --- everything else, by family ----------------------------------------
    for prefixes, allowed in _FILE_ACCESS_FAMILIES:
        if file_type.startswith(prefixes):
            if has_any_role(user, allowed):
                return True, ''
            held = sorted(user_roles(user)) or ['none']
            return False, '%s requires one of %s; user holds %s' % (
                file_type, sorted(allowed), held)

    return False, 'resource type %r is not classified in the file access policy' % file_type


def _user_can_access_file(user, meta):
    """Backwards-compatible boolean wrapper around _file_access_decision."""
    allowed, _reason = _file_access_decision(user, meta)
    return allowed


def _visible_file_access_logs(user, limit=200):
    """File access history the given user is entitled to see.

    The Universal File Center previously handed every authenticated user the
    whole system's FileAccessLog, disclosing which confidential files other
    people had opened - filenames and references included. Only executive and
    IT authority sees the global log now; everyone else sees their own history.
    """
    qs = FileAccessLog.objects.select_related('user').order_by('-created_at')
    if user.is_superuser or has_any_role(user, roles_mod.EXECUTIVE | roles_mod.IT):
        return qs[:limit]
    return qs.filter(user=user)[:limit]


def _log_file_action(request, meta, resource_id, action, granted=True, denial_reason=''):
    FileAccessLog.objects.create(
        user=request.user if request.user.is_authenticated else None,
        resource_type=meta['type'],resource_id=resource_id,
        file_name=meta.get('name',''),action=action,
        reference=meta.get('reference',''),
        ip=_file_access_ip(request),
        user_agent=request.META.get('HTTP_USER_AGENT','')[:500],
        granted=granted,denial_reason=denial_reason[:255],
    )


def _log_authorization_refusal(request, detail):
    """Record a refused privileged action that is not a file download."""
    logging.getLogger('portal.authorization').warning(
        'refused: user=%s path=%s detail=%s',
        getattr(request.user,'username','anonymous'), request.path, detail,
    )

@login_required
def universal_file_action(request, resource_type, resource_id, action):
    action=(action or '').upper()
    if action not in {'VIEW','PREVIEW','DOWNLOAD','PRINT'}:
        raise Http404('Unsupported file action.')

    obj, field, meta=_resolve_file_resource(resource_type,resource_id)
    if not field:
        raise Http404('File not found.')
    allowed,denial_reason=_file_access_decision(request.user,meta)
    if not allowed:
        # Log the refusal too. Previously the log was written only after the
        # check passed, so denied attempts - the security-relevant ones - left
        # no audit trail at all.
        _log_file_action(request,meta,resource_id,action,granted=False,denial_reason=denial_reason)
        return HttpResponse('You do not have permission to access this file.',status=403)

    _log_file_action(request,meta,resource_id,action)
    filename=meta.get('name') or field.name.rsplit('/',1)[-1]

    if action=='VIEW':
        return render(request,'universal_file_view.html',{
            'resource_type':resource_type,'resource_id':resource_id,
            'file_name':filename,'reference':meta.get('reference',''),
            'preview_url':reverse('universal_file_action',args=[resource_type,resource_id,'preview']),
            'download_url':reverse('universal_file_action',args=[resource_type,resource_id,'download']),
            'print_url':reverse('universal_file_action',args=[resource_type,resource_id,'print']),
        })

    if action=='PRINT':
        return render(request,'universal_file_print.html',{
            'file_name':filename,'reference':meta.get('reference',''),
            'preview_url':reverse('universal_file_action',args=[resource_type,resource_id,'preview'])
        })

    disposition='attachment' if action=='DOWNLOAD' else 'inline'
    response=FileResponse(field.open('rb'),as_attachment=(action=='DOWNLOAD'),filename=filename)
    if action=='PREVIEW':
        response['Content-Disposition']=f'inline; filename="{filename}"'
    return response

@login_required
def universal_file_center(request):
    docs=DocumentRecord.objects.select_related('uploaded_by').order_by('-created_at')
    attachments=CommunicationAttachment.objects.select_related('message__thread','uploaded_by').order_by('-created_at')
    visible_docs=[]
    visible_attachments=[]
    for d in docs[:1000]:
        meta={'type':'document','confidential':d.confidential,'uploaded_by_id':d.uploaded_by_id}
        if _user_can_access_file(request.user,meta):
            visible_docs.append(d)
    for a in attachments[:1000]:
        meta={'type':'communication_attachment','uploaded_by_id':a.uploaded_by_id,'thread':a.message.thread}
        if _user_can_access_file(request.user,meta):
            visible_attachments.append(a)
    staff_docs=[]
    staff_apps=[]
    staff_profiles=[]
    delivery_files=[]
    employee=_staff_employee_for_user(request.user)
    if employee or request.user.is_staff or request.user.is_superuser:
        sd_qs=StaffDocument.objects.select_related('employee','uploaded_by').order_by('-created_at')
        sa_qs=StaffApplication.objects.select_related('employee').exclude(attachment='').order_by('-created_at')
        sp_qs=StaffSelfServiceProfile.objects.select_related('employee').exclude(profile_photo='').order_by('-updated_at')
        if employee and not (request.user.is_staff or request.user.is_superuser):
            sd_qs=sd_qs.filter(employee=employee); sa_qs=sa_qs.filter(employee=employee); sp_qs=sp_qs.filter(employee=employee)
        staff_docs=list(sd_qs[:500]); staff_apps=list(sa_qs[:500]); staff_profiles=list(sp_qs[:100])
    if request.user.is_staff or request.user.is_superuser:
        delivery_files=list(BuyerDeliverySLA.objects.select_related('order').filter(
            Q(proof_of_delivery__gt='')|Q(buyer_signature__gt='')|Q(delivery_photo__gt='')
        ).order_by('-updated_at')[:500])
    return render(request,'universal_file_center.html',{
        'documents':visible_docs,'attachments':visible_attachments,
        'staff_documents':staff_docs,'staff_applications':staff_apps,'staff_profiles':staff_profiles,
        'delivery_files':delivery_files,
        'access_logs':_visible_file_access_logs(request.user)
    })


@login_required
@require_http_methods(['GET','POST'])
def buyer_delivery_sla(request):
    from django.contrib import messages
    from django.db.models import Q
    if request.method=='POST':
        action=request.POST.get('action','create')
        try:
            with transaction.atomic():
                if action=='create':
                    order=get_object_or_404(MasterOrder,pk=request.POST.get('order_id'))
                    sla,_=BuyerDeliverySLA.objects.get_or_create(
                        order=order,
                        defaults={
                            'buyer_name':order.buyer,
                            'confirmed_at':order.confirmed_at or timezone.now(),
                        }
                    )
                    sla.buyer_name=request.POST.get('buyer_name','').strip() or order.buyer
                    sla.contact_person=request.POST.get('contact_person','').strip()
                    sla.phone=request.POST.get('phone','').strip()
                    sla.email=request.POST.get('email','').strip()
                    sla.street=request.POST.get('street','').strip()
                    sla.city=request.POST.get('city','').strip()
                    sla.state=request.POST.get('state','').strip()
                    sla.postal_code=request.POST.get('postal_code','').strip()
                    sla.country=request.POST.get('country','').strip()
                    sla.location_text=request.POST.get('location_text','').strip()
                    sla.max_delivery_days=15
                    sla.expected_delivery_date=request.POST.get('expected_delivery_date') or None
                    sla.courier=request.POST.get('courier','').strip()
                    sla.tracking_number=request.POST.get('tracking_number','').strip()
                    sla.shipping_cost=Decimal(request.POST.get('shipping_cost') or '0')
                    sla.save()
                    messages.success(request,f'Delivery SLA created for {order.master_order_id}. Deadline: {sla.delivery_deadline}.')
                elif action=='update_status':
                    sla=get_object_or_404(BuyerDeliverySLA,pk=request.POST.get('sla_id'))
                    new=request.POST.get('status',sla.status)
                    if new=='DISPATCHED' and not sla.actual_dispatch_at:
                        sla.actual_dispatch_at=timezone.now()
                    if new in {'DELIVERED','DELIVERY_CONFIRMED'} and not sla.actual_delivery_at:
                        sla.actual_delivery_at=timezone.now()
                    sla.status=new
                    sla.courier=request.POST.get('courier','').strip() or sla.courier
                    sla.tracking_number=request.POST.get('tracking_number','').strip() or sla.tracking_number
                    sla.expected_delivery_date=request.POST.get('expected_delivery_date') or sla.expected_delivery_date
                    sla.save()
                    messages.success(request,'Delivery status updated.')
                elif action=='exception':
                    sla=get_object_or_404(BuyerDeliverySLA,pk=request.POST.get('sla_id'))
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if not approval or approval.status!='APPROVED':
                        raise PermissionError('A senior APPROVED Approval Request is required for a delivery exception.')
                    sla.exception_required=True
                    sla.exception_reason=request.POST.get('exception_reason','').strip()
                    sla.exception_new_delivery_date=request.POST.get('exception_new_delivery_date') or None
                    sla.exception_approval=approval
                    sla.status='EXCEPTION_APPROVED'
                    sla.save()
                    Alert.objects.create(level='WARNING',title='Delivery SLA Exception Approved',message=f'{sla.order.master_order_id}: {sla.exception_reason}',reference=sla.order.master_order_id,actioned=False)
                    messages.success(request,'15-day delivery exception approved and recorded.')
                elif action=='confirm_delivery':
                    sla=get_object_or_404(BuyerDeliverySLA,pk=request.POST.get('sla_id'))
                    sla.receiver_name=request.POST.get('receiver_name','').strip()
                    sla.gps_location=request.POST.get('gps_location','').strip()
                    sla.courier_confirmation=request.POST.get('courier_confirmation','').strip()
                    if request.FILES.get('proof_of_delivery'): sla.proof_of_delivery=request.FILES['proof_of_delivery']
                    if request.FILES.get('buyer_signature'): sla.buyer_signature=request.FILES['buyer_signature']
                    if request.FILES.get('delivery_photo'): sla.delivery_photo=request.FILES['delivery_photo']
                    if not (sla.proof_of_delivery or sla.courier_confirmation):
                        raise PermissionError('Proof of Delivery or courier confirmation is required.')
                    sla.status='DELIVERY_CONFIRMED'
                    sla.actual_delivery_at=sla.actual_delivery_at or timezone.now()
                    sla.confirmed_delivery_at=timezone.now()
                    sla.confirmed_by=request.user
                    sla.save()
                    sla.order.status='COMPLETED'
                    sla.order.save(update_fields=['status','updated_at'])
                    messages.success(request,'Buyer delivery confirmed and Order Master marked COMPLETED.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('buyer_delivery_sla')

    rows=BuyerDeliverySLA.objects.select_related('order','exception_approval','confirmed_by').order_by('delivery_deadline','-updated_at')
    today=timezone.localdate()
    # Escalation creation: day 8/10/12/14/15 equivalents based on remaining days
    for sla in rows[:500]:
        rem=sla.days_remaining
        if rem is None or sla.status in {'DELIVERED','DELIVERY_CONFIRMED'}:
            continue
        if rem <= 7:
            title='Delivery SLA Manager Alert'
            level='WARNING'
            if rem <= 3:
                title='Delivery SLA Shipping Alert'
            if rem <= 1:
                title='Delivery SLA CEO Red Alert'; level='RED'
            if rem < 0:
                title='Delivery SLA OVERDUE'; level='RED'
            if not Alert.objects.filter(reference=sla.order.master_order_id,title=title,actioned=False).exists():
                Alert.objects.create(
                    level=level,title=title,
                    message=f'{sla.order.master_order_id}: {rem} day(s) remaining to buyer delivery at {sla.city}, {sla.country}.',
                    reference=sla.order.master_order_id,actioned=False
                )

    ctx={
        'page':DashboardPage.objects.filter(slug='buyer-delivery-sla').first(),
        'rows':rows[:500],
        'orders':MasterOrder.objects.order_by('-updated_at')[:500],
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-approved_at')[:300],
        'due_soon':sum(1 for x in rows[:500] if x.days_remaining is not None and 0 <= x.days_remaining <= 3 and x.status not in {'DELIVERED','DELIVERY_CONFIRMED'}),
        'overdue_count':sum(1 for x in rows[:500] if x.overdue),
        'delivered_count':rows.filter(status='DELIVERY_CONFIRMED').count(),
        'in_transit_count':rows.filter(status__in=['DISPATCHED','IN_TRANSIT','OUT_FOR_DELIVERY']).count(),
    }
    return render(request,'buyer_delivery_sla.html',ctx)

@login_required
def buyer_delivery_export_csv(request):
    import csv
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']='attachment; filename="buyer-delivery-sla.csv"'
    w=csv.writer(response)
    w.writerow(['Order','Buyer','Address','Confirmed','Dispatch Target','Deadline','Expected','Actual Delivery','Days Remaining','Courier','Tracking','Status','Exception','Receiver','GPS'])
    for x in BuyerDeliverySLA.objects.select_related('order').order_by('delivery_deadline'):
        w.writerow([
            x.order.master_order_id,x.buyer_name,f'{x.street}, {x.city}, {x.state}, {x.postal_code}, {x.country}',
            x.confirmed_at,x.dispatch_target_date,x.effective_deadline,x.expected_delivery_date or '',
            x.actual_delivery_at or '',x.days_remaining,x.courier,x.tracking_number,x.status,
            x.exception_reason if x.exception_required else '',x.receiver_name,x.gps_location
        ])
    return response

@login_required
def api_buyer_delivery_sla(request):
    rows=[{
        'order':x.order.master_order_id,'buyer':x.buyer_name,
        'address':{'street':x.street,'city':x.city,'state':x.state,'postal_code':x.postal_code,'country':x.country},
        'confirmed_at':x.confirmed_at.isoformat(),'dispatch_target_date':str(x.dispatch_target_date or ''),
        'delivery_deadline':str(x.effective_deadline or ''),'expected_delivery_date':str(x.expected_delivery_date or ''),
        'actual_delivery_at':x.actual_delivery_at.isoformat() if x.actual_delivery_at else None,
        'days_remaining':x.days_remaining,'status':x.status,'overdue':x.overdue,
        'courier':x.courier,'tracking_number':x.tracking_number,'can_complete_order':x.can_complete_order
    } for x in BuyerDeliverySLA.objects.select_related('order').order_by('delivery_deadline')[:500]]
    return JsonResponse({'count':len(rows),'results':rows})


@login_required
@require_http_methods(['GET','POST'])
def profit_before_spend(request):
    from django.contrib import messages
    from django.db.models import Q, Sum
    if request.method=='POST':
        action=request.POST.get('action','check')
        try:
            with transaction.atomic():
                if action=='check':
                    order=MasterOrder.objects.filter(pk=request.POST.get('order_id')).first()
                    opp=BuyerOpportunity.objects.filter(pk=request.POST.get('opportunity_id')).first()
                    if not order and opp and opp.converted_order_id:
                        order=opp.converted_order
                    gate=None
                    if opp:
                        gate=ProfitFeasibilityGate.objects.filter(opportunity=opp).first()
                    elif order and hasattr(order,'source_opportunity'):
                        opp=order.source_opportunity
                        gate=ProfitFeasibilityGate.objects.filter(opportunity=opp).first()

                    revenue=Decimal(request.POST.get('revenue_snapshot') or '0')
                    base_cost=Decimal(request.POST.get('base_cost_snapshot') or '0')
                    min_margin=Decimal(request.POST.get('minimum_margin_percent') or '10')
                    if gate:
                        if revenue<=0: revenue=gate.revenue
                        if base_cost<=0: base_cost=gate.total_cost
                        if not request.POST.get('minimum_margin_percent'): min_margin=gate.minimum_margin_percent
                    elif order and revenue<=0:
                        revenue=order.order_value

                    prior=ProfitBeforeSpendControl.objects.filter(
                        order=order,final_decision__in=['ALLOW','ALLOW_WITH_APPROVAL']
                    ).aggregate(v=Sum('committed_amount'))['v'] or Decimal('0')

                    seq=ProfitBeforeSpendControl.objects.filter(created_at__date=timezone.localdate()).count()+1
                    ref=request.POST.get('reference','').strip() or f'PBS-{timezone.now():%Y%m%d}-{seq:05d}'
                    obj=ProfitBeforeSpendControl.objects.create(
                        reference=ref,order=order,opportunity=opp,
                        spend_category=request.POST.get('spend_category','OTHER'),
                        description=request.POST.get('description','').strip(),
                        vendor=request.POST.get('vendor','').strip(),
                        currency=request.POST.get('currency','USD'),
                        requested_amount=Decimal(request.POST.get('requested_amount') or '0'),
                        revenue_snapshot=revenue,base_cost_snapshot=base_cost,
                        prior_approved_spend=prior,minimum_margin_percent=min_margin,
                        requested_by=request.user,reason=request.POST.get('reason','').strip()
                    )
                    messages.success(request,f'{obj.reference}: {obj.system_decision}; projected margin {obj.projected_margin_percent}%.')
                elif action=='decision':
                    obj=get_object_or_404(ProfitBeforeSpendControl,pk=request.POST.get('check_id'))
                    final=request.POST.get('final_decision','PENDING')
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if final=='ALLOW' and obj.system_decision in {'HOLD','BLOCK'}:
                        raise PermissionError('Cannot directly ALLOW a spend that the system has HOLD/BLOCK status. Revise cost or use a senior-approved exception workflow.')
                    if final=='ALLOW_WITH_APPROVAL' and (not approval or approval.status!='APPROVED'):
                        raise PermissionError('ALLOW WITH APPROVAL requires an APPROVED senior Approval Request.')
                    obj.final_decision=final
                    obj.approval=approval
                    obj.reviewed_by=request.user
                    obj.reviewed_at=timezone.now()
                    obj.reason=request.POST.get('decision_reason','').strip() or obj.reason
                    if final in {'ALLOW','ALLOW_WITH_APPROVAL'}:
                        obj.committed_amount=obj.requested_amount
                    obj.save()
                    if final in {'HOLD','BLOCK'}:
                        Alert.objects.create(
                            level='RED' if final=='BLOCK' else 'WARNING',
                            title='Profit Before Spend Control',
                            message=f'{obj.reference}: {final}; projected margin {obj.projected_margin_percent}%',
                            reference=obj.order.master_order_id if obj.order else obj.reference,
                            actioned=False
                        )
                    messages.success(request,f'Spend decision saved: {final}.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('profit_before_spend')

    q=request.GET.get('q','').strip()
    rows=ProfitBeforeSpendControl.objects.select_related('order','opportunity','approval','requested_by','reviewed_by').order_by('-created_at')
    if q:
        rows=rows.filter(Q(reference__icontains=q)|Q(description__icontains=q)|Q(vendor__icontains=q)|Q(order__master_order_id__icontains=q)|Q(opportunity__opportunity_no__icontains=q))
    ctx={
        'page':DashboardPage.objects.filter(slug='profit-before-spend').first(),
        'rows':rows[:500],
        'orders':MasterOrder.objects.order_by('-updated_at')[:500],
        'opportunities':BuyerOpportunity.objects.order_by('-updated_at')[:500],
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-approved_at')[:300],
        'categories':[x[0] for x in ProfitBeforeSpendControl.CATEGORIES],
        'allow_count':rows.filter(final_decision='ALLOW').count(),
        'approval_count':rows.filter(final_decision='ALLOW_WITH_APPROVAL').count(),
        'hold_count':rows.filter(final_decision='HOLD').count(),
        'block_count':rows.filter(final_decision='BLOCK').count(),
        'pending_count':rows.filter(final_decision='PENDING').count(),
    }
    return render(request,'profit_before_spend.html',ctx)

@login_required
def profit_before_spend_export_csv(request):
    import csv
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']='attachment; filename="profit-before-spend-control.csv"'
    w=csv.writer(response)
    w.writerow(['Reference','Order','Opportunity','Category','Description','Vendor','Requested','Revenue','Base Cost','Prior Spend','Projected Total Cost','Projected Profit','Projected Margin %','Minimum Margin %','System Decision','Final Decision','Approval','Can Spend'])
    for x in ProfitBeforeSpendControl.objects.select_related('order','opportunity','approval').order_by('-created_at'):
        w.writerow([x.reference,x.order.master_order_id if x.order else '',x.opportunity.opportunity_no if x.opportunity else '',x.spend_category,x.description,x.vendor,x.requested_amount,x.revenue_snapshot,x.base_cost_snapshot,x.prior_approved_spend,x.projected_total_cost,x.projected_profit,x.projected_margin_percent,x.minimum_margin_percent,x.system_decision,x.final_decision,x.approval.reference if x.approval else '',x.can_spend])
    return response

@login_required
def api_profit_before_spend(request):
    rows=[{
        'reference':x.reference,'order':x.order.master_order_id if x.order else None,
        'opportunity':x.opportunity.opportunity_no if x.opportunity else None,
        'category':x.spend_category,'requested_amount':str(x.requested_amount),
        'projected_total_cost':str(x.projected_total_cost),'projected_profit':str(x.projected_profit),
        'projected_margin_percent':str(x.projected_margin_percent),'minimum_margin_percent':str(x.minimum_margin_percent),
        'system_decision':x.system_decision,'final_decision':x.final_decision,'can_spend':x.can_spend
    } for x in ProfitBeforeSpendControl.objects.select_related('order','opportunity','approval').order_by('-created_at')[:500]]
    return JsonResponse({'count':len(rows),'results':rows})


def _staff_employee_for_user(user):
    try:
        return user.staff_self_service_profile.employee
    except Exception:
        pass
    emp=Employee.objects.filter(employee_id__iexact=user.username).first()
    if emp: return emp
    if user.email:
        prof=StaffSelfServiceProfile.objects.filter(email__iexact=user.email).select_related('employee').first()
        if prof: return prof.employee
    if user.is_staff or user.is_superuser:
        return Employee.objects.filter(status='ACTIVE').order_by('employee_id').first()
    return None

@login_required
@require_http_methods(['GET','POST'])
def staff_self_service(request):
    from django.contrib import messages
    from django.db.models import Sum, Q
    from django.utils.timezone import now
    from datetime import date
    employee=_staff_employee_for_user(request.user)
    if not employee:
        return HttpResponse('No employee record is linked to this login. Ask HR/IT to connect the user account to an Employee ID.',status=403)

    profile=StaffSelfServiceProfile.objects.filter(employee=employee).select_related('employee__department').first()
    if not profile:
        profile=StaffSelfServiceProfile.objects.create(
            user=request.user,employee=employee,
            designation=employee.role,
            email=request.user.email or '',
            factory_unit=''
        )

    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='application':
                    seq=StaffApplication.objects.filter(created_at__date=timezone.localdate()).count()+1
                    app=StaffApplication.objects.create(
                        application_no=f'SSA-{timezone.now():%Y%m%d}-{seq:05d}',
                        employee=employee,
                        application_type=request.POST.get('application_type','OTHER'),
                        subject=request.POST.get('subject','').strip(),
                        details=request.POST.get('details','').strip(),
                        status='SUBMITTED',
                        attachment=request.FILES.get('attachment') or None
                    )
                    StaffNotification.objects.create(employee=employee,title='Application Submitted',body=f'{app.application_no} · {app.subject}',level='SUCCESS',reference=app.application_no)
                    messages.success(request,f'{app.application_no} submitted.')
                elif action=='upload_document':
                    dtype=request.POST.get('document_type','OTHER')
                    ref=request.POST.get('reference','').strip() or f'{employee.employee_id}-{dtype}-{timezone.now():%Y%m%d%H%M}'
                    doc=StaffDocument.objects.create(
                        employee=employee,document_type=dtype,
                        title=request.POST.get('title','').strip() or dtype.replace('_',' ').title(),
                        reference=ref,version=request.POST.get('version','1.0'),
                        issue_date=request.POST.get('issue_date') or None,
                        expires_at=request.POST.get('expires_at') or None,
                        file=request.FILES.get('file') or None,
                        confidential=True,uploaded_by=request.user
                    )
                    messages.success(request,f'Document {doc.reference} uploaded.')
                elif action=='hr_support':
                    seq=StaffApplication.objects.filter(created_at__date=timezone.localdate()).count()+1
                    app=StaffApplication.objects.create(
                        application_no=f'HR-{timezone.now():%Y%m%d}-{seq:05d}',
                        employee=employee,application_type=request.POST.get('support_type','HR_SUPPORT'),
                        subject=request.POST.get('subject','').strip(),
                        details=request.POST.get('details','').strip(),
                        status='SUBMITTED',attachment=request.FILES.get('attachment') or None
                    )
                    # Create communication thread so HR support is visible in Communication Center / Chat 24/7.
                    thread=CommunicationThread.objects.create(
                        thread_no=f'COM-HR-{app.id:06d}',subject=app.subject or 'HR Support Request',
                        thread_type='HR',priority='NORMAL',reference=app.application_no,
                        department=employee.department,created_by=request.user
                    )
                    thread.participants.add(request.user)
                    CommunicationMessage.objects.create(
                        thread=thread,channel='CHAT_24_7',direction='INTERNAL',
                        sender_user=request.user,body=app.details,status='SENT'
                    )
                    messages.success(request,f'HR support request {app.application_no} submitted and Chat 24/7 thread opened.')
                elif action=='mark_notification':
                    n=get_object_or_404(StaffNotification,pk=request.POST.get('notification_id'),employee=employee)
                    n.read_at=timezone.now(); n.save(update_fields=['read_at','updated_at'])
                    messages.success(request,'Notification marked read.')
                elif action=='profile':
                    profile.phone=request.POST.get('phone','').strip()
                    profile.email=request.POST.get('email','').strip()
                    profile.emergency_contact=request.POST.get('emergency_contact','').strip()
                    if request.FILES.get('profile_photo'):
                        profile.profile_photo=request.FILES['profile_photo']
                    profile.save()
                    messages.success(request,'Profile updated.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('staff_self_service')

    today=timezone.localdate()
    month_start=today.replace(day=1)
    attendance=AttendanceDailySummary.objects.filter(employee=employee,work_date__gte=month_start,work_date__lte=today)
    worked_minutes=attendance.aggregate(v=Sum('worked_minutes'))['v'] or 0
    scheduled_minutes=attendance.aggregate(v=Sum('scheduled_minutes'))['v'] or 0

    applications=StaffApplication.objects.filter(employee=employee).select_related('approval').order_by('-submitted_at')
    documents=StaffDocument.objects.filter(employee=employee).order_by('-issue_date','-created_at')
    notifications=StaffNotification.objects.filter(employee=employee).order_by('-created_at')
    payroll=StaffPayrollSummary.objects.filter(employee=employee).select_related('payslip').order_by('-payroll_month')
    latest_payroll=payroll.first()
    duty=StaffDutySummary.objects.filter(employee=employee).order_by('-period_end').first()
    schedule=StaffScheduleEntry.objects.filter(employee=employee,date__gte=today).order_by('date','start_time')[:10]
    announcements=StaffAnnouncement.objects.filter(active=True).filter(
        Q(department__isnull=True)|Q(department=employee.department)
    ).order_by('-starts_at')[:10]
    events=StaffEvent.objects.filter(
        Q(employee=employee)|Q(employee__isnull=True,department=employee.department)
    ).filter(starts_at__gte=timezone.now()).order_by('starts_at')[:10]

    expiry_docs=[d for d in documents if d.days_to_expiry is not None and d.days_to_expiry <= 90]
    unread_notifications=notifications.filter(read_at__isnull=True).count()

    user_threads=CommunicationThread.objects.filter(
        Q(created_by=request.user)|Q(assigned_to=request.user)|Q(participants=request.user)
    ).distinct()
    unread_messages=CommunicationMessage.objects.filter(thread__in=user_threads).exclude(read_receipts__user=request.user).count()
    pending_tasks=ActionItem.objects.filter(assigned_to=request.user,status__in=ActionItem.OPEN_STATUSES).count()

    id_barcode,_=BarcodeAsset.objects.get_or_create(
        code=f'ERL-ID-{employee.employee_id}',
        defaults={'asset_type':'EMPLOYEE','reference':employee.employee_id,'payload':{'employee_id':employee.employee_id,'name':employee.name},'active':True}
    )
    id_doc=documents.filter(document_type='ID_CARD').first()
    appointment_doc=documents.filter(document_type='APPOINTMENT_LETTER').first()
    joining_doc=documents.filter(document_type='JOINING_LETTER').first()
    handbook_doc=documents.filter(document_type='COMPANY_HANDBOOK').first()
    payslip_doc=documents.filter(document_type='PAYSLIP').first()

    def hm(minutes):
        return f'{minutes//60} H {minutes%60:02d} M'

    ctx={
        'page':DashboardPage.objects.filter(slug='staff-self-service-portal').first(),
        'employee':employee,'profile':profile,'applications':applications[:100],
        'documents':documents[:100],'notifications':notifications[:50],
        'payroll_rows':payroll[:12],'latest_payroll':latest_payroll,
        'schedule_rows':schedule,'announcements':announcements,'events':events,
        'expiry_docs':expiry_docs[:20],
        'id_doc':id_doc,'appointment_doc':appointment_doc,'joining_doc':joining_doc,
        'handbook_doc':handbook_doc,'payslip_doc':payslip_doc,
        'application_count':applications.count(),'document_count':documents.count(),
        'notification_count':unread_notifications,'pending_tasks':pending_tasks,
        'announcement_count':announcements.count() if hasattr(announcements,'count') else len(announcements),
        'event_count':events.count() if hasattr(events,'count') else len(events),
        'unread_messages':unread_messages,
        'worked_minutes':worked_minutes,'worked_hm':hm(worked_minutes),
        'scheduled_minutes':scheduled_minutes,'scheduled_hm':hm(scheduled_minutes),
        'duty':duty,'today':today,'id_barcode_code':id_barcode.code,
        'application_types':[x[0] for x in StaffApplication.TYPES],
        'document_types':[x[0] for x in StaffDocument.TYPES],
    }
    return render(request,'staff_self_service.html',ctx)

@login_required
def api_staff_self_service(request):
    employee=_staff_employee_for_user(request.user)
    if not employee:
        return JsonResponse({'error':'No employee linked'},status=403)
    docs=StaffDocument.objects.filter(employee=employee)
    apps=StaffApplication.objects.filter(employee=employee)
    return JsonResponse({
        'employee_id':employee.employee_id,'name':employee.name,'role':employee.role,
        'department':employee.department.name if employee.department else None,
        'applications':apps.count(),'documents':docs.count(),
        'notifications_unread':StaffNotification.objects.filter(employee=employee,read_at__isnull=True).count(),
        'pending_tasks':ActionItem.objects.filter(assigned_to=request.user,status__in=ActionItem.OPEN_STATUSES).count(),
        'documents_expiring_90_days':sum(1 for d in docs if d.days_to_expiry is not None and d.days_to_expiry <= 90)
    })


@login_required
@require_http_methods(['GET','POST'])
def hr_dashboard(request):
    from django.contrib import messages
    from django.db.models import Q, Sum, Avg, Count
    from datetime import timedelta
    today=timezone.localdate()
    month_start=today.replace(day=1)
    year_start=today.replace(month=1,day=1)

    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='add_employee':
                    dep=Department.objects.filter(pk=request.POST.get('department_id')).first()
                    emp=Employee.objects.create(
                        employee_id=request.POST.get('employee_id','').strip(),
                        name=request.POST.get('name','').strip(),
                        department=dep,role=request.POST.get('role','STAFF'),
                        category=request.POST.get('category','STAFF'),status='ACTIVE'
                    )
                    messages.success(request,f'Employee {emp.employee_id} added.')
                elif action=='recruitment':
                    HRRecruitment.objects.create(
                        candidate_name=request.POST.get('candidate_name','').strip(),
                        email=request.POST.get('email','').strip(),phone=request.POST.get('phone','').strip(),
                        department=Department.objects.filter(pk=request.POST.get('department_id')).first(),
                        position=request.POST.get('position','').strip(),status='OPEN',
                        joining_date=request.POST.get('joining_date') or None,
                        cv=request.FILES.get('cv') or None,created_by=request.user
                    )
                    messages.success(request,'Recruitment candidate added.')
                elif action=='leave_decision':
                    leave=get_object_or_404(HRLeaveRequest,pk=request.POST.get('leave_id'))
                    leave.status=request.POST.get('status','PENDING')
                    leave.save()
                    messages.success(request,'Leave decision updated.')
                elif action=='case':
                    seq=HRComplaintIncident.objects.filter(created_at__date=today).count()+1
                    HRComplaintIncident.objects.create(
                        reference=f'HRCASE-{timezone.now():%Y%m%d}-{seq:04d}',
                        employee=Employee.objects.filter(pk=request.POST.get('employee_id')).first(),
                        case_type=request.POST.get('case_type','COMPLAINT'),
                        subject=request.POST.get('subject','').strip(),
                        details=request.POST.get('details','').strip(),
                        severity=request.POST.get('severity','MEDIUM'),
                        assigned_to=request.user,attachment=request.FILES.get('attachment') or None
                    )
                    messages.success(request,'HR case opened.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('hr_dashboard')

    employees=Employee.objects.select_related('department')
    active=employees.filter(status='ACTIVE')
    inactive=employees.exclude(status='ACTIVE')
    attendance=AttendanceDailySummary.objects.filter(work_date=today)
    leave=HRLeaveRequest.objects.select_related('employee').order_by('-created_at')
    docs=StaffDocument.objects.select_related('employee').order_by('-created_at')
    applications=StaffApplication.objects.select_related('employee').order_by('-created_at')
    recruitment=HRRecruitment.objects.select_related('department').order_by('-created_at')
    training=HRTrainingRecord.objects.select_related('employee').order_by('due_date')
    cases=HRComplaintIncident.objects.select_related('employee').order_by('-created_at')
    performance=HRPerformanceReview.objects.filter(created_at__date__gte=year_start)
    recognition=HRRecognitionReward.objects.filter(created_at__date__gte=month_start)
    mobility=HRInternalMobility.objects.filter(effective_date__gte=year_start)
    surveys=HRSurveyResult.objects.order_by('-created_at')
    workforce=HRWorkforcePlan.objects.order_by('period')

    expiring=[d for d in docs if d.days_to_expiry is not None and 0 <= d.days_to_expiry <= 30]
    self_service_active=StaffSelfServiceProfile.objects.filter(active=True).count()
    self_service_inactive=StaffSelfServiceProfile.objects.filter(active=False).count()
    pending_joining=recruitment.filter(status__in=['OFFERED','HIRED'],joining_date__gte=today).count()
    new_joiners=recruitment.filter(status='HIRED',joining_date__gte=month_start,joining_date__lte=today).count()
    pending_hr=applications.filter(status__in=['SUBMITTED','PENDING']).count()
    pending_docs=docs.filter(status__in=['PENDING','REVIEW']).count()
    training_due=training.filter(status='DUE',due_date__lte=today+timedelta(days=30)).count()
    open_complaints=cases.filter(case_type__in=['COMPLAINT','GRIEVANCE'],status__in=['OPEN','INVESTIGATING']).count()
    open_incidents=cases.filter(case_type__in=['INCIDENT','DISCIPLINARY'],status__in=['OPEN','INVESTIGATING']).count()
    suspended=employees.filter(status='SUSPENDED').count()

    approved_leave=leave.filter(status='APPROVED',start_date__lte=today,end_date__gte=today).count()
    absent=attendance.filter(status='ABSENT').count()
    late=attendance.filter(late_minutes__gt=0).count()
    early=attendance.filter(early_leave_minutes__gt=0).count()

    dept_counts=list(active.values('department__name').annotate(total=Count('id')).order_by('-total')[:8])
    monthly_joiners=[]
    for m in range(1,13):
        monthly_joiners.append(recruitment.filter(status='HIRED',joining_date__year=today.year,joining_date__month=m).count())

    total_leave=leave.filter(created_at__date__gte=month_start).count()
    leave_summary={s:leave.filter(created_at__date__gte=month_start,status=s).count() for s in ['APPROVED','PENDING','REJECTED','CANCELLED','RETURNED']}
    avg_perf=performance.aggregate(v=Avg('productivity_percent'))['v'] or 0
    survey_avg=surveys.aggregate(v=Avg('score'))['v'] or 0
    reward_total=recognition.aggregate(v=Sum('reward_value'))['v'] or 0
    policy_total=HRPolicyAcknowledgement.objects.count()
    policy_ack=HRPolicyAcknowledgement.objects.exclude(acknowledged_at=None).count()

    approval_qs=ApprovalRequest.objects.order_by('-created_at')
    approval_counts={
        'pending':approval_qs.filter(status='PENDING').count(),
        'approved':approval_qs.filter(status='APPROVED').count(),
        'rejected':approval_qs.filter(status='REJECTED').count(),
        'actioned':approval_qs.exclude(status='PENDING').count()
    }

    recent_audit=AuditLog.objects.select_related('user').order_by('-created_at')[:8]
    comm_threads=CommunicationThread.objects.order_by('-updated_at')[:8]
    alerts=Alert.objects.filter(actioned=False).order_by('-created_at')[:10]

    ctx={
      'page':DashboardPage.objects.filter(slug='hr-dashboard').first(),
      'today':today,'departments':Department.objects.all().order_by('name'),
      'employees':employees[:100],'recruitment':recruitment[:20],'leave_rows':leave[:20],
      'training_rows':training[:20],'case_rows':cases[:20],'docs':docs[:100],
      'total_employees':employees.count(),'active_employees':active.count(),'inactive_employees':inactive.count(),
      'pending_joining':pending_joining,'new_joiners':new_joiners,'on_leave':approved_leave,
      'absent_today':absent,'late_today':late,'early_today':early,'pending_hr':pending_hr,
      'pending_docs':pending_docs,'expiring_count':len(expiring),'expiring_docs':expiring[:8],
      'training_due':training_due,'open_complaints':open_complaints,'open_incidents':open_incidents,
      'suspended':suspended,'self_service_active':self_service_active,'self_service_inactive':self_service_inactive,
      'actioned_month':ActionItem.objects.filter(status='COMPLETED',updated_at__date__gte=month_start).count(),
      'dept_counts':dept_counts,'monthly_joiners':monthly_joiners,'total_leave':total_leave,
      'leave_summary':leave_summary,'avg_productivity':avg_perf,'survey_avg':survey_avg,
      'survey_responses':surveys.count(),'recognition_count':recognition.count(),'reward_total':reward_total,
      'mobility_count':mobility.count(),'policy_total':policy_total,'policy_ack':policy_ack,
      'approval_counts':approval_counts,'recent_audit':recent_audit,'comm_threads':comm_threads,
      'alerts':alerts,'workforce_rows':workforce[:12],
    }
    return render(request,'hr_dashboard.html',ctx)

@login_required
def api_hr_dashboard(request):
    today=timezone.localdate()
    return JsonResponse({
      'employees':Employee.objects.count(),
      'active':Employee.objects.filter(status='ACTIVE').count(),
      'pending_hr_approvals':ApprovalRequest.objects.filter(status='PENDING').count(),
      'open_cases':HRComplaintIncident.objects.filter(status__in=['OPEN','INVESTIGATING']).count(),
      'training_due':HRTrainingRecord.objects.filter(status='DUE').count(),
      'self_service_active':StaffSelfServiceProfile.objects.filter(active=True).count(),
      'unactioned_alerts':Alert.objects.filter(actioned=False).count(),
    })


def _hm(minutes):
    minutes=int(minutes or 0)
    return f'{minutes//60:,}:{minutes%60:02d}'

@login_required
@require_http_methods(['GET','POST'])
def attendance_dashboard(request):
    from django.contrib import messages
    from django.db.models import Q, Sum, Count
    from datetime import timedelta, datetime
    today=timezone.localdate()
    selected=request.GET.get('date')
    try:
        work_date=datetime.strptime(selected,'%Y-%m-%d').date() if selected else today
    except Exception:
        work_date=today

    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='recalculate':
                    for emp in Employee.objects.filter(status='ACTIVE'):
                        calculate_attendance_day(emp,work_date)
                    messages.success(request,f'Attendance recalculated for {work_date}.')
                elif action=='event':
                    emp=get_object_or_404(Employee,pk=request.POST.get('employee_id'))
                    event=request.POST.get('event','CHECK_IN')
                    occurred=request.POST.get('occurred_at')
                    when=timezone.now()
                    if occurred:
                        when=timezone.make_aware(datetime.fromisoformat(occurred),timezone.get_current_timezone())
                    manual=request.POST.get('manual')=='on'
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if manual and (not approval or approval.status!='APPROVED'):
                        raise PermissionError('Authorized manual attendance entry requires an APPROVED senior Approval Request.')
                    AttendanceEvent.objects.create(
                        employee=emp,event=event,occurred_at=when,
                        source='Authorized Manual Entry' if manual else 'Dashboard',
                        device_ref=request.POST.get('device_ref','').strip()
                    )
                    if manual:
                        AttendanceManualAdjustment.objects.create(
                            employee=emp,work_date=when.date(),field_name='ATTENDANCE_EVENT',
                            old_value='',new_value=event,reason=request.POST.get('reason','').strip() or 'Manual attendance event',
                            requested_by=request.user,approval=approval,applied_at=timezone.now()
                        )
                    calculate_attendance_day(emp,when.date())
                    messages.success(request,f'{event} recorded for {emp.employee_id}.')
                elif action=='gate_pass':
                    emp=get_object_or_404(Employee,pk=request.POST.get('employee_id'))
                    out_at=timezone.make_aware(datetime.fromisoformat(request.POST.get('out_at')),timezone.get_current_timezone())
                    in_raw=request.POST.get('in_at')
                    in_at=timezone.make_aware(datetime.fromisoformat(in_raw),timezone.get_current_timezone()) if in_raw else None
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    status='APPROVED' if approval and approval.status=='APPROVED' else 'PENDING'
                    AttendanceGatePass.objects.create(
                        employee=emp,pass_type=request.POST.get('pass_type','UNPAID'),out_at=out_at,in_at=in_at,
                        reason=request.POST.get('reason','').strip(),status=status,approval=approval,created_by=request.user
                    )
                    calculate_attendance_day(emp,out_at.date())
                    messages.success(request,'Gate pass recorded.')
                elif action=='overtime':
                    emp=get_object_or_404(Employee,pk=request.POST.get('employee_id'))
                    start_at=timezone.make_aware(datetime.fromisoformat(request.POST.get('start_at')),timezone.get_current_timezone())
                    end_at=timezone.make_aware(datetime.fromisoformat(request.POST.get('end_at')),timezone.get_current_timezone())
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if not approval or approval.status!='APPROVED':
                        raise PermissionError('Overtime requires an APPROVED authorization.')
                    schedule=attendance_schedule(emp.category,emp.role)
                    earliest=timezone.make_aware(datetime.combine(start_at.date(),schedule['ot_start']),timezone.get_current_timezone())
                    if start_at < earliest:
                        raise PermissionError(f'OT cannot start before {schedule["ot_start"].strftime("%H:%M")} for {emp.category}.')
                    AttendanceOvertime.objects.create(
                        employee=emp,work_date=start_at.date(),start_at=start_at,end_at=end_at,
                        reason=request.POST.get('reason','').strip(),status='APPROVED',approval=approval
                    )
                    calculate_attendance_day(emp,start_at.date())
                    messages.success(request,'Authorized overtime recorded.')
                elif action=='npt':
                    emp=Employee.objects.filter(pk=request.POST.get('employee_id')).first()
                    dep=Department.objects.filter(pk=request.POST.get('department_id')).first() or (emp.department if emp else None)
                    AttendanceNPT.objects.create(
                        employee=emp,department=dep,work_date=request.POST.get('work_date') or work_date,
                        category=request.POST.get('category','OTHER'),minutes=int(request.POST.get('minutes') or 0),
                        reason=request.POST.get('reason','').strip(),cost=Decimal(request.POST.get('cost') or '0')
                    )
                    if emp: calculate_attendance_day(emp,work_date)
                    messages.success(request,'NPT record added.')
                elif action=='leave':
                    emp=get_object_or_404(Employee,pk=request.POST.get('employee_id'))
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    HRLeaveRequest.objects.create(
                        employee=emp,leave_type=request.POST.get('leave_type','Annual Leave'),
                        start_date=request.POST.get('start_date'),end_date=request.POST.get('end_date'),
                        reason=request.POST.get('reason','').strip(),
                        status='APPROVED' if approval and approval.status=='APPROVED' else 'PENDING',
                        approval=approval,attachment=request.FILES.get('attachment') or None
                    )
                    messages.success(request,'Leave request recorded.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect(f"{reverse('attendance_dashboard')}?date={work_date.isoformat()}")

    employees=Employee.objects.select_related('department').filter(status='ACTIVE')
    summaries=AttendanceDailySummary.objects.select_related('employee__department').filter(work_date=work_date)
    summary_map={x.employee_id:x for x in summaries}
    # Ensure every active employee has a day summary so totals remain complete.
    if work_date <= today:
        missing=employees.exclude(id__in=summary_map.keys())
        for emp in missing:
            s=calculate_attendance_day(emp,work_date)
            summary_map[emp.id]=s
        summaries=AttendanceDailySummary.objects.select_related('employee__department').filter(work_date=work_date)

    total=employees.count()
    scheduled=summaries.count()
    present=summaries.filter(status='PRESENT').count()
    absent=summaries.filter(status='ABSENT').count()
    late=summaries.filter(late_minutes__gt=0).count()
    early=summaries.filter(early_leave_minutes__gt=0).count()

    sums=summaries.aggregate(
        mandatory=Sum('scheduled_minutes'),done=Sum('worked_minutes'),due=Sum('unpaid_minutes'),
        late_m=Sum('late_minutes'),paid_gp=Sum('office_gate_pass_paid_minutes'),
        unpaid_gp=Sum('gate_pass_unpaid_minutes'),npt=Sum('npt_minutes'),
        mandatory_cost=Sum('scheduled_cost'),done_cost=Sum('worked_cost'),due_cost=Sum('due_cost'),
        late_cost=Sum('late_cost'),paid_gp_cost=Sum('gate_pass_paid_cost'),
        unpaid_gp_cost=Sum('gate_pass_unpaid_cost'),npt_cost=Sum('npt_cost')
    )
    for k,v in list(sums.items()):
        sums[k]=v or 0

    dept_rows=[]
    for dep in Department.objects.all().order_by('name'):
        ds=summaries.filter(employee__department=dep)
        if not ds.exists(): continue
        sc=ds.count(); pr=ds.filter(status='PRESENT').count()
        dept_rows.append({
            'name':dep.name,'scheduled':sc,'present':pr,'absent':ds.filter(status='ABSENT').count(),
            'late':ds.filter(late_minutes__gt=0).count(),'early':ds.filter(early_leave_minutes__gt=0).count(),
            'pct':round((pr/sc*100),2) if sc else 0
        })

    categories=[]
    for cat in ['STAFF','OPERATOR','HELPER']:
        qs=summaries.filter(employee__category=cat); sc=qs.count(); pr=qs.filter(status='PRESENT').count()
        categories.append({'name':cat,'scheduled':sc,'present':pr,'absent':qs.filter(status='ABSENT').count(),
                           'late':qs.filter(late_minutes__gt=0).count(),'early':qs.filter(early_leave_minutes__gt=0).count(),
                           'pct':round((pr/sc*100),2) if sc else 0})

    week=[]
    monday=work_date-timedelta(days=work_date.weekday())
    for i in range(7):
        d=monday+timedelta(days=i)
        qs=AttendanceDailySummary.objects.filter(work_date=d)
        sc=qs.count(); pr=qs.filter(status='PRESENT').count()
        week.append({'date':d,'pct':round(pr/sc*100,2) if sc else 0,'present':pr,'scheduled':sc})

    month_qs=AttendanceDailySummary.objects.filter(work_date__year=work_date.year,work_date__month=work_date.month)
    month_sums=month_qs.aggregate(mandatory=Sum('scheduled_minutes'),done=Sum('worked_minutes'),due=Sum('unpaid_minutes'),late=Sum('late_minutes'),paid=Sum('office_gate_pass_paid_minutes'),unpaid=Sum('gate_pass_unpaid_minutes'),npt=Sum('npt_minutes'))
    month_sums={k:(v or 0) for k,v in month_sums.items()}

    devices=DeviceIntegration.objects.filter(active=True).order_by('device_type','name')
    cameras=AttendanceCCTVFeed.objects.filter(active=True).order_by('name')[:4]
    approvals=ApprovalRequest.objects.filter(status='APPROVED').order_by('-approved_at')[:300]
    gate_passes=AttendanceGatePass.objects.select_related('employee').filter(out_at__date=work_date).order_by('-out_at')[:20]
    ot_rows=AttendanceOvertime.objects.select_related('employee').filter(work_date=work_date).order_by('-start_at')[:20]
    npt_rows=AttendanceNPT.objects.select_related('employee','department').filter(work_date=work_date).order_by('-created_at')[:20]
    alerts=Alert.objects.filter(actioned=False).order_by('-created_at')[:20]

    attendance_pct=round(present/scheduled*100,2) if scheduled else 0
    ctx={
        'page':DashboardPage.objects.filter(slug='attendance-dashboard').first(),
        'work_date':work_date,'today':today,'employees':employees[:500],'departments':Department.objects.all().order_by('name'),
        'summaries':summaries[:500],'total_employees':total,'scheduled_employees':scheduled,
        'present':present,'absent':absent,'late':late,'early':early,'attendance_pct':attendance_pct,
        'sums':sums,'mandatory_hm':_hm(sums['mandatory']),'done_hm':_hm(sums['done']),'due_hm':_hm(sums['due']),
        'late_hm':_hm(sums['late_m']),'paid_gp_hm':_hm(sums['paid_gp']),'unpaid_gp_hm':_hm(sums['unpaid_gp']),
        'npt_hm':_hm(sums['npt']),'dept_rows':dept_rows,'category_rows':categories,'week':week,
        'month_sums':month_sums,'month_mandatory_hm':_hm(month_sums['mandatory']),'month_done_hm':_hm(month_sums['done']),
        'month_due_hm':_hm(month_sums['due']),'month_late_hm':_hm(month_sums['late']),
        'month_paid_hm':_hm(month_sums['paid']),'month_unpaid_hm':_hm(month_sums['unpaid']),'month_npt_hm':_hm(month_sums['npt']),
        'devices':devices,'cameras':cameras,'approvals':approvals,'gate_passes':gate_passes,
        'ot_rows':ot_rows,'npt_rows':npt_rows,'alerts':alerts,
        'staff_schedule':attendance_schedule('STAFF'),'operator_schedule':attendance_schedule('OPERATOR'),
        'leave_today':HRLeaveRequest.objects.filter(status='APPROVED',start_date__lte=work_date,end_date__gte=work_date).count(),
        'holiday_today':AttendanceHoliday.objects.filter(holiday_date=work_date).first(),
    }
    return render(request,'attendance_dashboard.html',ctx)

@login_required
def attendance_export_csv(request):
    import csv
    from datetime import datetime
    selected=request.GET.get('date')
    try: work_date=datetime.strptime(selected,'%Y-%m-%d').date() if selected else timezone.localdate()
    except Exception: work_date=timezone.localdate()
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']=f'attachment; filename="attendance-{work_date}.csv"'
    w=csv.writer(response)
    w.writerow(['Date','Employee ID','Employee','Category','Department','Status','Mandatory H/M','Done H/M','Due H/M','Late H/M','Early Leave H/M','Paid Gate Pass H/M','Unpaid Gate Pass H/M','NPT H/M','Mandatory Cost','Done Cost','Due Cost','Late Cost'])
    for x in AttendanceDailySummary.objects.select_related('employee__department').filter(work_date=work_date).order_by('employee__employee_id'):
        w.writerow([x.work_date,x.employee.employee_id,x.employee.name,x.employee.category,x.employee.department.name if x.employee.department else '',x.status,_hm(x.scheduled_minutes),_hm(x.worked_minutes),_hm(x.unpaid_minutes),_hm(x.late_minutes),_hm(x.early_leave_minutes),_hm(x.office_gate_pass_paid_minutes),_hm(x.gate_pass_unpaid_minutes),_hm(x.npt_minutes),x.scheduled_cost,x.worked_cost,x.due_cost,x.late_cost])
    return response

@login_required
def api_attendance_dashboard(request):
    d=request.GET.get('date') or str(timezone.localdate())
    rows=AttendanceDailySummary.objects.select_related('employee__department').filter(work_date=d).order_by('employee__employee_id')
    return JsonResponse({'date':d,'count':rows.count(),'results':[{
        'employee_id':x.employee.employee_id,'name':x.employee.name,'category':x.employee.category,
        'department':x.employee.department.name if x.employee.department else None,'status':x.status,
        'scheduled_minutes':x.scheduled_minutes,'worked_minutes':x.worked_minutes,'unpaid_minutes':x.unpaid_minutes,
        'late_minutes':x.late_minutes,'early_leave_minutes':x.early_leave_minutes,
        'overtime_minutes':x.overtime_minutes,'npt_minutes':x.npt_minutes,
        'scheduled_cost':str(x.scheduled_cost),'worked_cost':str(x.worked_cost),'due_cost':str(x.due_cost)
    } for x in rows[:1000]]})


def _cutting_auto_report_payload(work_date):
    from django.db.models import Sum
    plans=CuttingPlan.objects.filter(created_at__date__lte=work_date)
    prod=CuttingProductionEntry.objects.filter(work_date=work_date)
    fabric=CuttingFabricIssue.objects.filter(plan__in=plans)
    bundles=CuttingBundle.objects.filter(plan__in=plans)
    variances=CuttingVariance.objects.filter(plan__in=plans,actioned=False)
    return {
        'plans':plans.count(),
        'planned_qty':plans.aggregate(v=Sum('planned_qty'))['v'] or 0,
        'target_qty':prod.aggregate(v=Sum('target_qty'))['v'] or 0,
        'actual_qty':prod.aggregate(v=Sum('actual_qty'))['v'] or 0,
        'process_minutes':prod.aggregate(v=Sum('process_minutes'))['v'] or 0,
        'npt_minutes':prod.aggregate(v=Sum('npt_minutes'))['v'] or 0,
        'process_cost':str(prod.aggregate(v=Sum('process_cost'))['v'] or 0),
        'fabric_issued':str(fabric.aggregate(v=Sum('issued_qty'))['v'] or 0),
        'fabric_consumed':str(fabric.aggregate(v=Sum('consumed_qty'))['v'] or 0),
        'bundle_qty':bundles.aggregate(v=Sum('quantity'))['v'] or 0,
        'qc_pass_qty':bundles.aggregate(v=Sum('qc_pass_qty'))['v'] or 0,
        'reject_qty':bundles.aggregate(v=Sum('reject_qty'))['v'] or 0,
        'recut_qty':bundles.aggregate(v=Sum('recut_qty'))['v'] or 0,
        'unactioned_variances':variances.count(),
    }

@login_required
@require_http_methods(['GET','POST'])
def cutting_dashboard(request):
    from django.contrib import messages
    from django.db.models import Sum, Avg
    from datetime import datetime
    from decimal import Decimal
    today=timezone.localdate()
    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='plan':
                    order=get_object_or_404(MasterOrder,pk=request.POST.get('order_id'))
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    CuttingPlan.objects.create(
                        plan_no=request.POST.get('plan_no').strip(),order=order,
                        department=Department.objects.filter(pk=request.POST.get('department_id')).first(),
                        product=request.POST.get('product').strip(),colour=request.POST.get('colour','').strip(),
                        size_range=request.POST.get('size_range','').strip(),
                        planned_qty=int(request.POST.get('planned_qty') or 0),
                        target_date=request.POST.get('target_date') or None,
                        status='APPROVED' if approval and approval.status=='APPROVED' else 'PENDING_APPROVAL',
                        approval=approval,created_by=request.user
                    )
                    messages.success(request,'Cutting plan created.')
                elif action=='fabric':
                    plan=get_object_or_404(CuttingPlan,pk=request.POST.get('plan_id'))
                    scan=request.POST.get('stock_out_scan','').strip()
                    if not scan: raise PermissionError('Fabric STOCK OUT SCAN is mandatory.')
                    CuttingFabricIssue.objects.create(
                        plan=plan,stock_item=StockItem.objects.filter(pk=request.POST.get('stock_item_id')).first(),
                        roll_no=request.POST.get('roll_no').strip(),lot_no=request.POST.get('lot_no','').strip(),
                        shade=request.POST.get('shade','').strip(),unit=request.POST.get('unit','METRE'),
                        issued_qty=Decimal(request.POST.get('issued_qty') or '0'),stock_out_scan=scan,issued_by=request.user
                    )
                    messages.success(request,'Fabric issued with STOCK OUT SCAN.')
                elif action=='bundle':
                    plan=get_object_or_404(CuttingPlan,pk=request.POST.get('plan_id'))
                    barcode=request.POST.get('barcode','').strip()
                    if not barcode: raise PermissionError('Bundle barcode/QR is mandatory.')
                    CuttingBundle.objects.create(
                        plan=plan,lay=CuttingLay.objects.filter(pk=request.POST.get('lay_id')).first(),
                        bundle_no=request.POST.get('bundle_no').strip(),barcode=barcode,
                        size=request.POST.get('size','').strip(),colour=request.POST.get('colour','').strip(),
                        quantity=int(request.POST.get('quantity') or 0),
                        stock_in_scan=request.POST.get('stock_in_scan','').strip()
                    )
                    messages.success(request,'Cutting bundle created and barcode registered.')
                elif action=='production':
                    plan=get_object_or_404(CuttingPlan,pk=request.POST.get('plan_id'))
                    manual=request.POST.get('manual_entry')=='on'
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if manual and (not approval or approval.status!='APPROVED'):
                        raise PermissionError('Manual production entry requires senior APPROVED authorization.')
                    CuttingProductionEntry.objects.create(
                        plan=plan,work_date=request.POST.get('work_date') or today,
                        employee=Employee.objects.filter(pk=request.POST.get('employee_id')).first(),
                        machine=AssetMachine.objects.filter(pk=request.POST.get('machine_id')).first(),
                        target_qty=int(request.POST.get('target_qty') or 0),actual_qty=int(request.POST.get('actual_qty') or 0),
                        process_minutes=int(request.POST.get('process_minutes') or 0),
                        npt_minutes=int(request.POST.get('npt_minutes') or 0),
                        cost_per_minute=Decimal(request.POST.get('cost_per_minute') or '0'),
                        manual_entry=manual,approval=approval
                    )
                    messages.success(request,'Cutting production updated.')
                elif action=='generate_report':
                    slot=request.POST.get('slot')
                    if slot not in {'08:00','13:00','20:00'}: raise ValueError('Invalid automatic report slot.')
                    payload=_cutting_auto_report_payload(today)
                    CuttingAutoReport.objects.update_or_create(
                        report_date=today,slot=slot,department=None,
                        defaults={'summary':payload,'outstanding_alerts':Alert.objects.filter(actioned=False).count(),
                                  'pending_actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),
                                  'escalated_items':Alert.objects.filter(actioned=False,level='RED').count()}
                    )
                    messages.success(request,f'{slot} Cutting automatic report generated.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('cutting_dashboard')

    plans=CuttingPlan.objects.select_related('order','department').order_by('-created_at')
    prod=CuttingProductionEntry.objects.select_related('plan','employee','machine').filter(work_date=today)
    bundles=CuttingBundle.objects.select_related('plan').order_by('-created_at')
    fabric=CuttingFabricIssue.objects.select_related('plan','stock_item').order_by('-created_at')
    variances=CuttingVariance.objects.select_related('plan').filter(actioned=False).order_by('-created_at')
    reports=CuttingAutoReport.objects.filter(report_date=today).order_by('slot')
    payload=_cutting_auto_report_payload(today)
    target=payload['target_qty']; actual=payload['actual_qty']
    efficiency=round(actual/target*100,2) if target else 0
    marker=CuttingLay.objects.aggregate(v=Avg('marker_efficiency'))['v'] or 0
    ctx={
        'today':today,'plans':plans[:100],'orders':MasterOrder.objects.order_by('-created_at')[:300],
        'departments':Department.objects.all().order_by('name'),'stock_items':StockItem.objects.all()[:300],
        'employees':Employee.objects.filter(status='ACTIVE')[:500],'machines':AssetMachine.objects.all()[:300],
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-created_at')[:300],
        'prod':prod[:100],'bundles':bundles[:100],'fabric':fabric[:100],'variances':variances[:50],
        'reports':reports,'payload':payload,'efficiency':efficiency,'marker_efficiency':marker,
        'alerts':Alert.objects.filter(actioned=False).order_by('-created_at')[:10],
        'actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).order_by('-created_at')[:10],
    }
    return render(request,'cutting_dashboard.html',ctx)

@login_required
def cutting_report_csv(request):
    import csv
    today=timezone.localdate()
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']=f'attachment; filename="cutting-report-{today}.csv"'
    w=csv.writer(response)
    w.writerow(['Date','Plan','Order','Product','Employee','Machine','Target','Actual','Process Minutes','NPT Minutes','Cost/Minute','Process Cost'])
    for x in CuttingProductionEntry.objects.select_related('plan__order','employee','machine').filter(work_date=today):
        w.writerow([x.work_date,x.plan.plan_no,x.plan.order.master_order_id,x.plan.product,x.employee.employee_id if x.employee else '',x.machine.name if x.machine else '',x.target_qty,x.actual_qty,x.process_minutes,x.npt_minutes,x.cost_per_minute,x.process_cost])
    return response

@login_required
def api_cutting_dashboard(request):
    return JsonResponse(_cutting_auto_report_payload(timezone.localdate()))


def _embroidery_auto_report_payload(work_date):
    from django.db.models import Sum
    plans=EmbroideryPlan.objects.filter(created_at__date__lte=work_date)
    prod=EmbroideryProductionEntry.objects.filter(work_date=work_date)
    scans=EmbroideryBundleScan.objects.filter(scanned_at__date=work_date)
    qc=EmbroideryQC.objects.filter(created_at__date=work_date)
    material=EmbroideryMaterialIssue.objects.filter(plan__in=plans)
    variances=EmbroideryVariance.objects.filter(plan__in=plans,actioned=False)
    return {
        'plans':plans.count(),
        'planned_qty':plans.aggregate(v=Sum('planned_qty'))['v'] or 0,
        'target_qty':prod.aggregate(v=Sum('target_qty'))['v'] or 0,
        'actual_qty':prod.aggregate(v=Sum('actual_qty'))['v'] or 0,
        'process_minutes':prod.aggregate(v=Sum('process_minutes'))['v'] or 0,
        'npt_minutes':prod.aggregate(v=Sum('npt_minutes'))['v'] or 0,
        'process_cost':str(prod.aggregate(v=Sum('process_cost'))['v'] or 0),
        'total_cost':str(prod.aggregate(v=Sum('total_cost'))['v'] or 0),
        'bundle_in_scans':scans.filter(direction='IN',scan_status='VALID').count(),
        'bundle_out_scans':scans.filter(direction='OUT',scan_status='VALID').count(),
        'blocked_scans':scans.exclude(scan_status='VALID').count(),
        'qc_pass_qty':qc.aggregate(v=Sum('pass_qty'))['v'] or 0,
        'reject_qty':qc.aggregate(v=Sum('reject_qty'))['v'] or 0,
        'repair_qty':qc.aggregate(v=Sum('repair_qty'))['v'] or 0,
        'rework_qty':qc.aggregate(v=Sum('rework_qty'))['v'] or 0,
        'material_issued':str(material.aggregate(v=Sum('issued_qty'))['v'] or 0),
        'material_consumed':str(material.aggregate(v=Sum('consumed_qty'))['v'] or 0),
        'unactioned_variances':variances.count(),
    }

@login_required
@require_http_methods(['GET','POST'])
def embroidery_dashboard(request):
    from django.contrib import messages
    from decimal import Decimal
    today=timezone.localdate()

    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='plan':
                    order=get_object_or_404(MasterOrder,pk=request.POST.get('order_id'))
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    EmbroideryPlan.objects.create(
                        plan_no=request.POST.get('plan_no','').strip(),order=order,
                        department=Department.objects.filter(pk=request.POST.get('department_id')).first(),
                        product=request.POST.get('product','').strip(),style_no=request.POST.get('style_no','').strip(),
                        design_no=request.POST.get('design_no','').strip(),colour=request.POST.get('colour','').strip(),
                        size_range=request.POST.get('size_range','').strip(),stitch_count=int(request.POST.get('stitch_count') or 0),
                        planned_qty=int(request.POST.get('planned_qty') or 0),target_date=request.POST.get('target_date') or None,
                        status='APPROVED' if approval and approval.status=='APPROVED' else 'PENDING_APPROVAL',
                        approval=approval,artwork=request.FILES.get('artwork') or None,
                        program_file=request.FILES.get('program_file') or None,created_by=request.user
                    )
                    messages.success(request,'Embroidery plan created.')
                elif action=='bundle_scan':
                    plan=get_object_or_404(EmbroideryPlan,pk=request.POST.get('plan_id'))
                    bundle=get_object_or_404(CuttingBundle,pk=request.POST.get('bundle_id'))
                    direction=request.POST.get('direction','IN')
                    barcode=request.POST.get('barcode','').strip()
                    if barcode != bundle.barcode:
                        Alert.objects.create(level='RED',title='Embroidery Bundle Scan Mismatch',message=f'{bundle.bundle_no}: scanned barcode does not match registered barcode.',reference=bundle.bundle_no,actioned=False)
                        raise PermissionError('Bundle scan blocked: wrong barcode/QR.')
                    if EmbroideryBundleScan.objects.filter(plan=plan,bundle=bundle,direction=direction,scan_status='VALID').exists():
                        Alert.objects.create(level='RED',title='Duplicate Embroidery Bundle Scan',message=f'{bundle.bundle_no}: duplicate {direction} scan.',reference=bundle.bundle_no,actioned=False)
                        raise PermissionError('Bundle scan blocked: duplicate scan.')
                    actual=int(request.POST.get('actual_qty') or bundle.quantity)
                    status='VALID'
                    if actual != bundle.quantity:
                        status='MISMATCH'
                        Alert.objects.create(level='RED',title='Embroidery Bundle Quantity Mismatch',message=f'{bundle.bundle_no}: expected {bundle.quantity}, actual {actual}.',reference=bundle.bundle_no,actioned=False)
                    if direction=='OUT':
                        valid_in=EmbroideryBundleScan.objects.filter(plan=plan,bundle=bundle,direction='IN',scan_status='VALID').exists()
                        if not valid_in:
                            raise PermissionError('Bundle OUT blocked: no valid Embroidery BUNDLE IN SCAN.')
                        if not EmbroideryQC.objects.filter(plan=plan,bundle=bundle,status='PASS').exists():
                            raise PermissionError('Bundle OUT blocked: final Embroidery QC PASS is required.')
                    EmbroideryBundleScan.objects.create(
                        plan=plan,bundle=bundle,direction=direction,barcode=barcode,
                        expected_qty=bundle.quantity,actual_qty=actual,
                        source_department=request.POST.get('source_department','Cutting' if direction=='IN' else 'Embroidery'),
                        destination_department=request.POST.get('destination_department','Embroidery' if direction=='IN' else 'Sewing'),
                        employee=Employee.objects.filter(pk=request.POST.get('employee_id')).first(),
                        machine=AssetMachine.objects.filter(pk=request.POST.get('machine_id')).first(),
                        scan_status=status,scanned_by=request.user
                    )
                    if status!='VALID':
                        raise PermissionError('Bundle movement recorded as mismatch and blocked for downstream release.')
                    messages.success(request,f'Bundle {direction} scan accepted.')
                elif action=='material':
                    plan=get_object_or_404(EmbroideryPlan,pk=request.POST.get('plan_id'))
                    scan=request.POST.get('stock_out_scan','').strip()
                    if not scan: raise PermissionError('Thread/material STOCK OUT SCAN is mandatory.')
                    EmbroideryMaterialIssue.objects.create(
                        plan=plan,stock_item=StockItem.objects.filter(pk=request.POST.get('stock_item_id')).first(),
                        material_type=request.POST.get('material_type','THREAD'),colour=request.POST.get('colour','').strip(),
                        lot_no=request.POST.get('lot_no','').strip(),unit=request.POST.get('unit','PCS'),
                        issued_qty=Decimal(request.POST.get('issued_qty') or '0'),stock_out_scan=scan,issued_by=request.user
                    )
                    messages.success(request,'Embroidery material issued.')
                elif action=='production':
                    plan=get_object_or_404(EmbroideryPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    if bundle and not EmbroideryBundleScan.objects.filter(plan=plan,bundle=bundle,direction='IN',scan_status='VALID').exists():
                        raise PermissionError('Production blocked: mandatory valid BUNDLE IN SCAN is missing.')
                    manual=request.POST.get('manual_entry')=='on'
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if manual and (not approval or approval.status!='APPROVED'):
                        raise PermissionError('Manual Embroidery production entry requires senior APPROVED authorization.')
                    EmbroideryProductionEntry.objects.create(
                        plan=plan,bundle=bundle,work_date=request.POST.get('work_date') or today,
                        employee=Employee.objects.filter(pk=request.POST.get('employee_id')).first(),
                        machine=AssetMachine.objects.filter(pk=request.POST.get('machine_id')).first(),
                        machine_heads=int(request.POST.get('machine_heads') or 1),
                        target_qty=int(request.POST.get('target_qty') or 0),actual_qty=int(request.POST.get('actual_qty') or 0),
                        stitch_count=int(request.POST.get('stitch_count') or plan.stitch_count),
                        process_minutes=int(request.POST.get('process_minutes') or 0),npt_minutes=int(request.POST.get('npt_minutes') or 0),
                        cost_per_minute=Decimal(request.POST.get('cost_per_minute') or '0'),
                        thread_cost=Decimal(request.POST.get('thread_cost') or '0'),other_cost=Decimal(request.POST.get('other_cost') or '0'),
                        manual_entry=manual,approval=approval
                    )
                    messages.success(request,'Embroidery production updated.')
                elif action=='qc':
                    plan=get_object_or_404(EmbroideryPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    EmbroideryQC.objects.create(
                        plan=plan,bundle=bundle,inspected_qty=int(request.POST.get('inspected_qty') or 0),
                        pass_qty=int(request.POST.get('pass_qty') or 0),reject_qty=int(request.POST.get('reject_qty') or 0),
                        repair_qty=int(request.POST.get('repair_qty') or 0),rework_qty=int(request.POST.get('rework_qty') or 0),
                        status=request.POST.get('status','PASS'),defect_reason=request.POST.get('defect_reason','').strip(),
                        inspected_by=request.user
                    )
                    messages.success(request,'Embroidery QC recorded.')
                elif action=='generate_report':
                    slot=request.POST.get('slot')
                    if slot not in {'08:00','13:00','20:00'}: raise ValueError('Invalid report slot.')
                    payload=_embroidery_auto_report_payload(today)
                    EmbroideryAutoReport.objects.update_or_create(
                        report_date=today,slot=slot,department=None,
                        defaults={'summary':payload,'outstanding_alerts':Alert.objects.filter(actioned=False).count(),
                                  'pending_actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),
                                  'escalated_items':Alert.objects.filter(actioned=False,level='RED').count()}
                    )
                    messages.success(request,f'{slot} Embroidery automatic report generated.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('embroidery_dashboard')

    plans=EmbroideryPlan.objects.select_related('order','department').order_by('-created_at')
    scans=EmbroideryBundleScan.objects.select_related('plan','bundle','employee','machine').order_by('-scanned_at')
    prod=EmbroideryProductionEntry.objects.select_related('plan','bundle','employee','machine').filter(work_date=today)
    qc=EmbroideryQC.objects.select_related('plan','bundle').order_by('-created_at')
    payload=_embroidery_auto_report_payload(today)
    target=payload['target_qty']; actual=payload['actual_qty']
    efficiency=round(actual/target*100,2) if target else 0
    ctx={
        'today':today,'plans':plans[:100],'orders':MasterOrder.objects.order_by('-created_at')[:300],
        'bundles':CuttingBundle.objects.order_by('-created_at')[:500],
        'stock_items':StockItem.objects.all()[:300],'employees':Employee.objects.filter(status='ACTIVE')[:500],
        'machines':AssetMachine.objects.all()[:300],'departments':Department.objects.all().order_by('name'),
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-created_at')[:300],
        'scans':scans[:100],'prod':prod[:100],'qc_rows':qc[:100],
        'reports':EmbroideryAutoReport.objects.filter(report_date=today).order_by('slot'),
        'payload':payload,'efficiency':efficiency,
        'variances':EmbroideryVariance.objects.filter(actioned=False).order_by('-created_at')[:50],
        'alerts':Alert.objects.filter(actioned=False).order_by('-created_at')[:10],
    }
    return render(request,'embroidery_dashboard.html',ctx)

@login_required
def embroidery_report_csv(request):
    import csv
    today=timezone.localdate()
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']=f'attachment; filename="embroidery-report-{today}.csv"'
    w=csv.writer(response)
    w.writerow(['Date','Plan','Order','Bundle','Employee','Machine','Target','Actual','Stitches','Process Minutes','NPT','Process Cost','Thread Cost','Other Cost','Total Cost'])
    for x in EmbroideryProductionEntry.objects.select_related('plan__order','bundle','employee','machine').filter(work_date=today):
        w.writerow([x.work_date,x.plan.plan_no,x.plan.order.master_order_id,x.bundle.bundle_no if x.bundle else '',x.employee.employee_id if x.employee else '',x.machine.name if x.machine else '',x.target_qty,x.actual_qty,x.stitch_count,x.process_minutes,x.npt_minutes,x.process_cost,x.thread_cost,x.other_cost,x.total_cost])
    return response

@login_required
def api_embroidery_dashboard(request):
    return JsonResponse(_embroidery_auto_report_payload(timezone.localdate()))


def _label_auto_report_payload(work_date):
    from django.db.models import Sum
    plans=LabelPlan.objects.filter(created_at__date__lte=work_date)
    prod=LabelProductionEntry.objects.filter(work_date=work_date)
    qc=LabelQC.objects.filter(created_at__date=work_date)
    mats=LabelMaterialIssue.objects.filter(plan__in=plans)
    alloc=LabelAllocation.objects.filter(plan__in=plans)
    var=LabelVariance.objects.filter(plan__in=plans,actioned=False)
    return {
        'plans':plans.count(),
        'planned_qty':plans.aggregate(v=Sum('planned_qty'))['v'] or 0,
        'target_qty':prod.aggregate(v=Sum('target_qty'))['v'] or 0,
        'actual_qty':prod.aggregate(v=Sum('actual_qty'))['v'] or 0,
        'process_minutes':prod.aggregate(v=Sum('process_minutes'))['v'] or 0,
        'npt_minutes':prod.aggregate(v=Sum('npt_minutes'))['v'] or 0,
        'process_cost':str(prod.aggregate(v=Sum('process_cost'))['v'] or 0),
        'total_cost':str(prod.aggregate(v=Sum('total_cost'))['v'] or 0),
        'material_issued':str(mats.aggregate(v=Sum('issued_qty'))['v'] or 0),
        'material_consumed':str(mats.aggregate(v=Sum('consumed_qty'))['v'] or 0),
        'allocated_qty':alloc.aggregate(v=Sum('allocated_qty'))['v'] or 0,
        'issued_qty':alloc.aggregate(v=Sum('issued_qty'))['v'] or 0,
        'used_qty':alloc.aggregate(v=Sum('used_qty'))['v'] or 0,
        'returned_qty':alloc.aggregate(v=Sum('returned_qty'))['v'] or 0,
        'rejected_alloc_qty':alloc.aggregate(v=Sum('rejected_qty'))['v'] or 0,
        'qc_pass_qty':qc.aggregate(v=Sum('pass_qty'))['v'] or 0,
        'reject_qty':qc.aggregate(v=Sum('reject_qty'))['v'] or 0,
        'rework_qty':qc.aggregate(v=Sum('rework_qty'))['v'] or 0,
        'unactioned_variances':var.count(),
    }

@login_required
@require_http_methods(['GET','POST'])
def label_dashboard(request):
    from django.contrib import messages
    from decimal import Decimal
    today=timezone.localdate()

    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='plan':
                    order=get_object_or_404(MasterOrder,pk=request.POST.get('order_id'))
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    LabelPlan.objects.create(
                        plan_no=request.POST.get('plan_no','').strip(),order=order,
                        department=Department.objects.filter(pk=request.POST.get('department_id')).first(),
                        buyer=request.POST.get('buyer','').strip() or order.buyer,
                        brand=request.POST.get('brand','').strip(),style_no=request.POST.get('style_no','').strip(),
                        product=request.POST.get('product','').strip() or order.product,
                        colour=request.POST.get('colour','').strip(),size_range=request.POST.get('size_range','').strip(),
                        label_type=request.POST.get('label_type','MAIN_BRAND'),
                        label_code=request.POST.get('label_code','').strip(),
                        version=request.POST.get('version','1.0'),
                        planned_qty=int(request.POST.get('planned_qty') or 0),
                        target_date=request.POST.get('target_date') or None,
                        status='APPROVED' if approval and approval.status=='APPROVED' else 'PENDING_APPROVAL',
                        approval=approval,artwork=request.FILES.get('artwork') or None,
                        specification=request.FILES.get('specification') or None,created_by=request.user
                    )
                    messages.success(request,'Label plan created.')
                elif action=='proof':
                    plan=get_object_or_404(LabelPlan,pk=request.POST.get('plan_id'))
                    LabelProof.objects.create(
                        plan=plan,proof_no=request.POST.get('proof_no','').strip(),
                        version=request.POST.get('version',plan.version),
                        proof_file=request.FILES.get('proof_file') or None,
                        sample_image=request.FILES.get('sample_image') or None,
                        status=request.POST.get('status','PENDING'),
                        remarks=request.POST.get('remarks','').strip(),
                        approved_by=request.user if request.POST.get('status')=='APPROVED' else None,
                        approved_at=timezone.now() if request.POST.get('status')=='APPROVED' else None
                    )
                    messages.success(request,'Label proof/sample recorded.')
                elif action=='material':
                    plan=get_object_or_404(LabelPlan,pk=request.POST.get('plan_id'))
                    scan=request.POST.get('stock_out_scan','').strip()
                    if not scan: raise PermissionError('Label material STOCK OUT SCAN is mandatory.')
                    LabelMaterialIssue.objects.create(
                        plan=plan,stock_item=StockItem.objects.filter(pk=request.POST.get('stock_item_id')).first(),
                        supplier=request.POST.get('supplier','').strip(),batch_no=request.POST.get('batch_no','').strip(),
                        lot_no=request.POST.get('lot_no','').strip(),unit=request.POST.get('unit','PCS'),
                        issued_qty=Decimal(request.POST.get('issued_qty') or '0'),
                        stock_out_scan=scan,issued_by=request.user
                    )
                    messages.success(request,'Label material issued with STOCK OUT SCAN.')
                elif action=='production':
                    plan=get_object_or_404(LabelPlan,pk=request.POST.get('plan_id'))
                    manual=request.POST.get('manual_entry')=='on'
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if manual and (not approval or approval.status!='APPROVED'):
                        raise PermissionError('Manual label production entry requires senior APPROVED authorization.')
                    if not LabelProof.objects.filter(plan=plan,status='APPROVED',version=plan.version).exists():
                        raise PermissionError('Production blocked: current label artwork/proof version is not approved.')
                    LabelProductionEntry.objects.create(
                        plan=plan,work_date=request.POST.get('work_date') or today,
                        employee=Employee.objects.filter(pk=request.POST.get('employee_id')).first(),
                        machine=AssetMachine.objects.filter(pk=request.POST.get('machine_id')).first(),
                        target_qty=int(request.POST.get('target_qty') or 0),
                        actual_qty=int(request.POST.get('actual_qty') or 0),
                        process_minutes=int(request.POST.get('process_minutes') or 0),
                        npt_minutes=int(request.POST.get('npt_minutes') or 0),
                        cost_per_minute=Decimal(request.POST.get('cost_per_minute') or '0'),
                        material_cost=Decimal(request.POST.get('material_cost') or '0'),
                        other_cost=Decimal(request.POST.get('other_cost') or '0'),
                        manual_entry=manual,approval=approval
                    )
                    messages.success(request,'Label production updated.')
                elif action=='qc':
                    plan=get_object_or_404(LabelPlan,pk=request.POST.get('plan_id'))
                    checked_version=request.POST.get('checked_version','').strip()
                    status=request.POST.get('status','PASS')
                    if checked_version != plan.version:
                        Alert.objects.create(level='RED',title='Wrong Label Version',message=f'{plan.plan_no}: expected version {plan.version}, checked {checked_version}.',reference=plan.plan_no,actioned=False)
                        status='HOLD'
                    LabelQC.objects.create(
                        plan=plan,inspected_qty=int(request.POST.get('inspected_qty') or 0),
                        pass_qty=int(request.POST.get('pass_qty') or 0),
                        reject_qty=int(request.POST.get('reject_qty') or 0),
                        rework_qty=int(request.POST.get('rework_qty') or 0),
                        status=status,defect_reason=request.POST.get('defect_reason','').strip(),
                        checked_version=checked_version,checked_by=request.user
                    )
                    messages.success(request,'Label QC recorded.')
                elif action=='allocation':
                    plan=get_object_or_404(LabelPlan,pk=request.POST.get('plan_id'))
                    order=get_object_or_404(MasterOrder,pk=request.POST.get('order_id'))
                    if order.id != plan.order_id:
                        Alert.objects.create(level='RED',title='Wrong Order Label Allocation',message=f'{plan.plan_no}: label allocated to wrong Master Order.',reference=plan.plan_no,actioned=False)
                        raise PermissionError('Allocation blocked: label plan and Master Order do not match.')
                    if not LabelQC.objects.filter(plan=plan,status='PASS').exists():
                        raise PermissionError('Allocation blocked: Label QC PASS is required.')
                    in_scan=request.POST.get('label_in_scan','').strip()
                    out_scan=request.POST.get('label_out_scan','').strip()
                    if not in_scan: raise PermissionError('LABEL IN SCAN is mandatory.')
                    allocated=int(request.POST.get('allocated_qty') or 0)
                    issued=int(request.POST.get('issued_qty') or 0)
                    if issued > allocated:
                        raise PermissionError('Issue blocked: issued label quantity exceeds allocated quantity.')
                    duplicate=LabelAllocation.objects.filter(plan=plan,order=order,label_out_scan=out_scan).exists() if out_scan else False
                    if duplicate:
                        Alert.objects.create(level='RED',title='Duplicate Label Issue',message=f'{plan.plan_no}: duplicate LABEL OUT SCAN.',reference=plan.plan_no,actioned=False)
                        raise PermissionError('Duplicate label issue blocked.')
                    LabelAllocation.objects.create(
                        plan=plan,bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first(),
                        order=order,allocated_qty=allocated,issued_qty=issued,
                        used_qty=int(request.POST.get('used_qty') or 0),
                        returned_qty=int(request.POST.get('returned_qty') or 0),
                        rejected_qty=int(request.POST.get('rejected_qty') or 0),
                        label_in_scan=in_scan,label_out_scan=out_scan,
                        destination_department=request.POST.get('destination_department','Finishing'),
                        status='ISSUED' if issued else 'ALLOCATED',issued_by=request.user
                    )
                    messages.success(request,'Label allocation/issue recorded.')
                elif action=='generate_report':
                    slot=request.POST.get('slot')
                    if slot not in {'08:00','13:00','20:00'}: raise ValueError('Invalid report slot.')
                    payload=_label_auto_report_payload(today)
                    LabelAutoReport.objects.update_or_create(
                        report_date=today,slot=slot,department=None,
                        defaults={'summary':payload,'outstanding_alerts':Alert.objects.filter(actioned=False).count(),
                                  'pending_actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),
                                  'escalated_items':Alert.objects.filter(actioned=False,level='RED').count()}
                    )
                    messages.success(request,f'{slot} Label automatic report generated.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('label_dashboard')

    plans=LabelPlan.objects.select_related('order','department').order_by('-created_at')
    payload=_label_auto_report_payload(today)
    target=payload['target_qty']; actual=payload['actual_qty']
    efficiency=round(actual/target*100,2) if target else 0
    ctx={
        'today':today,'plans':plans[:100],'orders':MasterOrder.objects.order_by('-created_at')[:300],
        'bundles':CuttingBundle.objects.order_by('-created_at')[:500],
        'stock_items':StockItem.objects.all()[:300],'employees':Employee.objects.filter(status='ACTIVE')[:500],
        'machines':AssetMachine.objects.all()[:300],'departments':Department.objects.all().order_by('name'),
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-created_at')[:300],
        'proofs':LabelProof.objects.select_related('plan').order_by('-created_at')[:100],
        'allocations':LabelAllocation.objects.select_related('plan','order','bundle').order_by('-created_at')[:100],
        'prod':LabelProductionEntry.objects.select_related('plan','employee','machine').filter(work_date=today)[:100],
        'qc_rows':LabelQC.objects.select_related('plan').order_by('-created_at')[:100],
        'reports':LabelAutoReport.objects.filter(report_date=today).order_by('slot'),
        'payload':payload,'efficiency':efficiency,
        'variances':LabelVariance.objects.filter(actioned=False).order_by('-created_at')[:50],
        'alerts':Alert.objects.filter(actioned=False).order_by('-created_at')[:10],
        'label_types':[x[0] for x in LabelPlan.LABEL_TYPES],
    }
    return render(request,'label_dashboard.html',ctx)

@login_required
def label_report_csv(request):
    import csv
    today=timezone.localdate()
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']=f'attachment; filename="label-report-{today}.csv"'
    w=csv.writer(response)
    w.writerow(['Date','Plan','Order','Label Type','Code','Version','Employee','Machine','Target','Actual','Minutes','NPT','Material Cost','Process Cost','Other Cost','Total Cost'])
    for x in LabelProductionEntry.objects.select_related('plan__order','employee','machine').filter(work_date=today):
        w.writerow([x.work_date,x.plan.plan_no,x.plan.order.master_order_id,x.plan.label_type,x.plan.label_code,x.plan.version,x.employee.employee_id if x.employee else '',x.machine.name if x.machine else '',x.target_qty,x.actual_qty,x.process_minutes,x.npt_minutes,x.material_cost,x.process_cost,x.other_cost,x.total_cost])
    return response

@login_required
def api_label_dashboard(request):
    return JsonResponse(_label_auto_report_payload(timezone.localdate()))


def _qc_auto_report_payload(work_date):
    from django.db.models import Sum, Avg, Count
    plans=QCInspectionPlan.objects.filter(created_at__date__lte=work_date)
    ins=QCInspection.objects.filter(completed_at__date=work_date)
    defects=QCDefect.objects.filter(inspection__in=ins)
    scans=QCBundleScan.objects.filter(scanned_at__date=work_date)
    capas=QCCAPA.objects.filter(inspection__in=ins)
    inspected=ins.aggregate(v=Sum('inspected_qty'))['v'] or 0
    passed=ins.aggregate(v=Sum('pass_qty'))['v'] or 0
    total_defects=(ins.aggregate(v=Sum('critical_defects'))['v'] or 0)+(ins.aggregate(v=Sum('major_defects'))['v'] or 0)+(ins.aggregate(v=Sum('minor_defects'))['v'] or 0)
    return {
        'plans':plans.count(),
        'inspections':ins.count(),
        'inspected_qty':inspected,
        'pass_qty':passed,
        'critical_defects':ins.aggregate(v=Sum('critical_defects'))['v'] or 0,
        'major_defects':ins.aggregate(v=Sum('major_defects'))['v'] or 0,
        'minor_defects':ins.aggregate(v=Sum('minor_defects'))['v'] or 0,
        'rework_qty':ins.aggregate(v=Sum('rework_qty'))['v'] or 0,
        'reject_qty':ins.aggregate(v=Sum('reject_qty'))['v'] or 0,
        'measurement_fail_qty':ins.aggregate(v=Sum('measurement_fail_qty'))['v'] or 0,
        'label_fail_qty':ins.aggregate(v=Sum('label_fail_qty'))['v'] or 0,
        'packing_fail_qty':ins.aggregate(v=Sum('packing_fail_qty'))['v'] or 0,
        'bundle_in_scans':scans.filter(direction='IN',scan_status='VALID').count(),
        'bundle_out_scans':scans.filter(direction='OUT',scan_status='VALID').count(),
        'blocked_scans':scans.exclude(scan_status='VALID').count(),
        'fpq_percent':round(passed/inspected*100,2) if inspected else 0,
        'dhu':round(total_defects/inspected*100,2) if inspected else 0,
        'open_capa':capas.exclude(status='CLOSED').count(),
        'closed_capa':capas.filter(status='CLOSED').count(),
        'unactioned_defects':defects.filter(actioned=False).count(),
    }

@login_required
@require_http_methods(['GET','POST'])
def qc_dashboard(request):
    from django.contrib import messages
    from decimal import Decimal
    today=timezone.localdate()

    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='plan':
                    order=get_object_or_404(MasterOrder,pk=request.POST.get('order_id'))
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    QCInspectionPlan.objects.create(
                        plan_no=request.POST.get('plan_no','').strip(),
                        order=order,
                        stage=request.POST.get('stage','FINAL_INSPECTION'),
                        department=Department.objects.filter(pk=request.POST.get('department_id')).first(),
                        buyer=request.POST.get('buyer','').strip() or order.buyer,
                        style_no=request.POST.get('style_no','').strip(),
                        product=request.POST.get('product','').strip() or order.product,
                        colour=request.POST.get('colour','').strip(),
                        size_range=request.POST.get('size_range','').strip(),
                        specification_version=request.POST.get('specification_version','1.0'),
                        aql_level=request.POST.get('aql_level','2.5'),
                        lot_size=int(request.POST.get('lot_size') or 0),
                        sample_size=int(request.POST.get('sample_size') or 0),
                        planned_inspection_date=request.POST.get('planned_inspection_date') or None,
                        status='APPROVED' if approval and approval.status=='APPROVED' else 'PENDING_APPROVAL',
                        approval=approval,
                        specification_file=request.FILES.get('specification_file') or None,
                        approved_sample_file=request.FILES.get('approved_sample_file') or None,
                        created_by=request.user
                    )
                    messages.success(request,'QC inspection plan created.')
                elif action=='bundle_scan':
                    plan=get_object_or_404(QCInspectionPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    direction=request.POST.get('direction','IN')
                    barcode=request.POST.get('barcode','').strip()
                    expected=bundle.quantity if bundle else int(request.POST.get('expected_qty') or 0)
                    actual=int(request.POST.get('actual_qty') or expected)
                    status='VALID'
                    if bundle and barcode and barcode != bundle.barcode:
                        status='MISMATCH'
                    if bundle and QCBundleScan.objects.filter(plan=plan,bundle=bundle,direction=direction,scan_status='VALID').exists():
                        status='DUPLICATE'
                    if direction=='OUT':
                        if bundle and not QCBundleScan.objects.filter(plan=plan,bundle=bundle,direction='IN',scan_status='VALID').exists():
                            status='BLOCKED'
                        gate=QCReleaseGate.objects.filter(plan=plan).select_related('approval').first()
                        if not gate or not gate.gate_passed:
                            status='HOLD'
                    if actual != expected:
                        status='MISMATCH'
                    scan=QCBundleScan.objects.create(
                        plan=plan,bundle=bundle,barcode=barcode,direction=direction,
                        expected_qty=expected,actual_qty=actual,
                        source_department=request.POST.get('source_department','').strip(),
                        destination_department=request.POST.get('destination_department','').strip(),
                        scan_status=status,scanned_by=request.user
                    )
                    if status!='VALID':
                        Alert.objects.create(
                            level='RED',title='QC Bundle Scan Blocked',
                            message=f'{plan.plan_no}: {status} during {direction} scan.',
                            reference=plan.plan_no,actioned=False
                        )
                        raise PermissionError(f'QC bundle movement blocked: {status}.')
                    messages.success(request,f'QC BUNDLE {direction} SCAN accepted.')
                elif action=='inspection':
                    plan=get_object_or_404(QCInspectionPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    if bundle and not QCBundleScan.objects.filter(plan=plan,bundle=bundle,direction='IN',scan_status='VALID').exists():
                        raise PermissionError('Inspection blocked: valid QC BUNDLE IN SCAN is required.')
                    result=request.POST.get('result','HOLD')
                    critical=int(request.POST.get('critical_defects') or 0)
                    if critical > 0:
                        result='HOLD'
                    ins=QCInspection.objects.create(
                        plan=plan,bundle=bundle,inspector=request.user,
                        inspected_qty=int(request.POST.get('inspected_qty') or 0),
                        pass_qty=int(request.POST.get('pass_qty') or 0),
                        critical_defects=critical,
                        major_defects=int(request.POST.get('major_defects') or 0),
                        minor_defects=int(request.POST.get('minor_defects') or 0),
                        rework_qty=int(request.POST.get('rework_qty') or 0),
                        reject_qty=int(request.POST.get('reject_qty') or 0),
                        measurement_fail_qty=int(request.POST.get('measurement_fail_qty') or 0),
                        shade_fail_qty=int(request.POST.get('shade_fail_qty') or 0),
                        label_fail_qty=int(request.POST.get('label_fail_qty') or 0),
                        workmanship_fail_qty=int(request.POST.get('workmanship_fail_qty') or 0),
                        packing_fail_qty=int(request.POST.get('packing_fail_qty') or 0),
                        result=result,comments=request.POST.get('comments','').strip(),
                        photo=request.FILES.get('photo') or None,
                        inspection_sheet=request.FILES.get('inspection_sheet') or None
                    )
                    gate,_=QCReleaseGate.objects.get_or_create(plan=plan)
                    gate.latest_inspection=ins
                    gate.reviewed_by=request.user
                    gate.reviewed_at=timezone.now()
                    gate.save()
                    if result in {'HOLD','REWORK','REJECT'} or critical>0:
                        Alert.objects.create(
                            level='RED' if critical>0 or result=='REJECT' else 'WARNING',
                            title='QC Hold / Rework',
                            message=f'{plan.plan_no}: result={result}, critical={critical}, major={ins.major_defects}, minor={ins.minor_defects}.',
                            reference=plan.plan_no,actioned=False
                        )
                    messages.success(request,f'QC inspection recorded: {result}.')
                elif action=='defect':
                    ins=get_object_or_404(QCInspection,pk=request.POST.get('inspection_id'))
                    defect=QCDefect.objects.create(
                        inspection=ins,defect_code=request.POST.get('defect_code','').strip(),
                        category=request.POST.get('category','OTHER'),
                        severity=request.POST.get('severity','MINOR'),
                        description=request.POST.get('description','').strip(),
                        quantity=int(request.POST.get('quantity') or 1),
                        root_cause=request.POST.get('root_cause','').strip(),
                        corrective_action=request.POST.get('corrective_action','').strip(),
                        responsible_user=User.objects.filter(pk=request.POST.get('responsible_user_id')).first(),
                        due_at=request.POST.get('due_at') or None
                    )
                    if defect.severity=='CRITICAL':
                        Alert.objects.create(level='RED',title='Critical QC Defect',message=f'{ins.plan.plan_no}: {defect.description}',reference=ins.plan.plan_no,actioned=False)
                    messages.success(request,'QC defect recorded.')
                elif action=='capa':
                    ins=get_object_or_404(QCInspection,pk=request.POST.get('inspection_id'))
                    seq=QCCAPA.objects.filter(created_at__date=today).count()+1
                    QCCAPA.objects.create(
                        reference=f'CAPA-{timezone.now():%Y%m%d}-{seq:04d}',inspection=ins,
                        root_cause=request.POST.get('root_cause','').strip(),
                        corrective_action=request.POST.get('corrective_action','').strip(),
                        preventive_action=request.POST.get('preventive_action','').strip(),
                        responsible_user=User.objects.filter(pk=request.POST.get('responsible_user_id')).first(),
                        due_at=request.POST.get('due_at') or None,status='OPEN'
                    )
                    messages.success(request,'CAPA opened.')
                elif action=='release':
                    gate=get_object_or_404(QCReleaseGate,pk=request.POST.get('gate_id'))
                    final=request.POST.get('final_decision','PENDING')
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if final=='RELEASE_WITH_APPROVAL' and (not approval or approval.status!='APPROVED'):
                        raise PermissionError('Conditional release requires APPROVED senior authorization.')
                    if final=='RELEASE' and gate.system_decision!='RELEASE':
                        raise PermissionError(f'Direct release blocked. System decision is {gate.system_decision}.')
                    gate.final_decision=final
                    gate.approval=approval
                    gate.decision_reason=request.POST.get('decision_reason','').strip()
                    gate.reviewed_by=request.user
                    gate.reviewed_at=timezone.now()
                    gate.save()
                    messages.success(request,f'QC release decision saved: {final}.')
                elif action=='generate_report':
                    slot=request.POST.get('slot')
                    if slot not in {'08:00','13:00','20:00'}:
                        raise ValueError('Invalid report slot.')
                    payload=_qc_auto_report_payload(today)
                    QCAutoReport.objects.update_or_create(
                        report_date=today,slot=slot,stage='',
                        defaults={
                            'summary':payload,
                            'outstanding_alerts':Alert.objects.filter(actioned=False).count(),
                            'pending_actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),
                            'escalated_items':Alert.objects.filter(actioned=False,level='RED').count()
                        }
                    )
                    messages.success(request,f'{slot} QC automatic report generated.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('qc_dashboard')

    plans=QCInspectionPlan.objects.select_related('order','department').order_by('-created_at')
    inspections=QCInspection.objects.select_related('plan','bundle','inspector').order_by('-completed_at')
    gates=QCReleaseGate.objects.select_related('plan','latest_inspection','approval').order_by('-updated_at')
    payload=_qc_auto_report_payload(today)
    ctx={
        'today':today,'plans':plans[:150],'inspections':inspections[:150],'gates':gates[:150],
        'orders':MasterOrder.objects.order_by('-created_at')[:300],
        'bundles':CuttingBundle.objects.order_by('-created_at')[:500],
        'departments':Department.objects.all().order_by('name'),
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-created_at')[:300],
        'users':User.objects.filter(is_active=True).order_by('username')[:500],
        'scans':QCBundleScan.objects.select_related('plan','bundle').order_by('-scanned_at')[:100],
        'defects':QCDefect.objects.select_related('inspection__plan','responsible_user').order_by('-created_at')[:100],
        'capas':QCCAPA.objects.select_related('inspection__plan','responsible_user').order_by('-created_at')[:100],
        'reports':QCAutoReport.objects.filter(report_date=today).order_by('slot'),
        'payload':payload,'alerts':Alert.objects.filter(actioned=False).order_by('-created_at')[:12],
        'stages':[x[0] for x in QCInspectionPlan.STAGES],
        'defect_categories':[x[0] for x in QCDefect.CATEGORIES],
    }
    return render(request,'qc_dashboard.html',ctx)

@login_required
def qc_report_csv(request):
    import csv
    today=timezone.localdate()
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']=f'attachment; filename="qc-report-{today}.csv"'
    w=csv.writer(response)
    w.writerow(['Date','Plan','Order','Stage','Bundle','Inspected','Pass','Critical','Major','Minor','Rework','Reject','DHU','Result'])
    for x in QCInspection.objects.select_related('plan__order','bundle').filter(completed_at__date=today):
        w.writerow([x.completed_at,x.plan.plan_no,x.plan.order.master_order_id,x.plan.stage,x.bundle.bundle_no if x.bundle else '',x.inspected_qty,x.pass_qty,x.critical_defects,x.major_defects,x.minor_defects,x.rework_qty,x.reject_qty,x.dhu,x.result])
    return response

@login_required
def api_qc_dashboard(request):
    return JsonResponse(_qc_auto_report_payload(timezone.localdate()))


def _hand_iron_auto_report_payload(work_date):
    from django.db.models import Sum
    plans=HandIronPlan.objects.filter(created_at__date__lte=work_date)
    prod=HandIronProductionEntry.objects.filter(work_date=work_date)
    scans=HandIronBundleScan.objects.filter(scanned_at__date=work_date)
    qc=HandIronQC.objects.filter(checked_at__date=work_date)
    var=HandIronVariance.objects.filter(plan__in=plans,actioned=False)
    target=prod.aggregate(v=Sum('target_qty'))['v'] or 0
    actual=prod.aggregate(v=Sum('actual_qty'))['v'] or 0
    return {
        'plans':plans.count(),
        'planned_qty':plans.aggregate(v=Sum('planned_qty'))['v'] or 0,
        'target_qty':target,
        'actual_qty':actual,
        'efficiency_percent':round(actual/target*100,2) if target else 0,
        'process_minutes':prod.aggregate(v=Sum('process_minutes'))['v'] or 0,
        'npt_minutes':prod.aggregate(v=Sum('npt_minutes'))['v'] or 0,
        'labour_cost':str(prod.aggregate(v=Sum('labour_cost'))['v'] or 0),
        'utility_cost':str(prod.aggregate(v=Sum('utility_cost'))['v'] or 0),
        'process_cost':str(prod.aggregate(v=Sum('process_cost'))['v'] or 0),
        'total_cost':str(prod.aggregate(v=Sum('total_cost'))['v'] or 0),
        'bundle_in_scans':scans.filter(direction='IN',scan_status='VALID').count(),
        'bundle_out_scans':scans.filter(direction='OUT',scan_status='VALID').count(),
        'blocked_scans':scans.exclude(scan_status='VALID').count(),
        'qc_pass_qty':qc.aggregate(v=Sum('pass_qty'))['v'] or 0,
        'reject_qty':qc.aggregate(v=Sum('reject_qty'))['v'] or 0,
        'reiron_qty':qc.aggregate(v=Sum('reiron_qty'))['v'] or 0,
        'unactioned_variances':var.count(),
    }

@login_required
@require_http_methods(['GET','POST'])
def hand_iron_dashboard(request):
    from django.contrib import messages
    from decimal import Decimal
    today=timezone.localdate()

    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='plan':
                    order=get_object_or_404(MasterOrder,pk=request.POST.get('order_id'))
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    HandIronPlan.objects.create(
                        plan_no=request.POST.get('plan_no','').strip(),
                        order=order,
                        department=Department.objects.filter(pk=request.POST.get('department_id')).first(),
                        product=request.POST.get('product','').strip() or order.product,
                        style_no=request.POST.get('style_no','').strip(),
                        colour=request.POST.get('colour','').strip(),
                        size_range=request.POST.get('size_range','').strip(),
                        fabric_type=request.POST.get('fabric_type','').strip(),
                        min_temperature_c=Decimal(request.POST.get('min_temperature_c') or '0'),
                        max_temperature_c=Decimal(request.POST.get('max_temperature_c') or '0'),
                        planned_qty=int(request.POST.get('planned_qty') or 0),
                        target_date=request.POST.get('target_date') or None,
                        status='APPROVED' if approval and approval.status=='APPROVED' else 'PENDING_APPROVAL',
                        approval=approval,
                        instruction_file=request.FILES.get('instruction_file') or None,
                        created_by=request.user
                    )
                    messages.success(request,'Hand Iron plan created.')

                elif action=='bundle_scan':
                    plan=get_object_or_404(HandIronPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    direction=request.POST.get('direction','IN')
                    barcode=request.POST.get('barcode','').strip()
                    expected=bundle.quantity if bundle else int(request.POST.get('expected_qty') or 0)
                    actual=int(request.POST.get('actual_qty') or expected)
                    status='VALID'
                    if bundle and barcode and barcode != bundle.barcode:
                        status='MISMATCH'
                    if bundle and HandIronBundleScan.objects.filter(plan=plan,bundle=bundle,direction=direction,scan_status='VALID').exists():
                        status='DUPLICATE'
                    if actual != expected:
                        status='MISMATCH'
                    if direction=='OUT':
                        if bundle and not HandIronBundleScan.objects.filter(plan=plan,bundle=bundle,direction='IN',scan_status='VALID').exists():
                            status='BLOCKED'
                        if not HandIronQC.objects.filter(plan=plan,bundle=bundle,result='PASS').exists():
                            status='QC_HOLD'
                    HandIronBundleScan.objects.create(
                        plan=plan,bundle=bundle,direction=direction,barcode=barcode,
                        expected_qty=expected,actual_qty=actual,
                        source_department=request.POST.get('source_department','').strip(),
                        destination_department=request.POST.get('destination_department','').strip(),
                        operator=Employee.objects.filter(pk=request.POST.get('operator_id')).first(),
                        workstation=AssetMachine.objects.filter(pk=request.POST.get('workstation_id')).first(),
                        scan_status=status,scanned_by=request.user
                    )
                    if status!='VALID':
                        Alert.objects.create(
                            level='RED',title='Hand Iron Bundle Scan Blocked',
                            message=f'{plan.plan_no}: {status} during {direction} scan.',
                            reference=plan.plan_no,actioned=False
                        )
                        raise PermissionError(f'Bundle movement blocked: {status}.')
                    messages.success(request,f'Hand Iron BUNDLE {direction} SCAN accepted.')

                elif action=='production':
                    plan=get_object_or_404(HandIronPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    if bundle and not HandIronBundleScan.objects.filter(plan=plan,bundle=bundle,direction='IN',scan_status='VALID').exists():
                        raise PermissionError('Production blocked: valid Hand Iron BUNDLE IN SCAN is required.')
                    manual=request.POST.get('manual_entry')=='on'
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if manual and (not approval or approval.status!='APPROVED'):
                        raise PermissionError('Manual Hand Iron production entry requires senior APPROVED authorization.')
                    temp=Decimal(request.POST.get('actual_temperature_c') or '0')
                    if plan.max_temperature_c and temp > plan.max_temperature_c:
                        Alert.objects.create(level='RED',title='Hand Iron Temperature High',message=f'{plan.plan_no}: {temp}°C exceeds max {plan.max_temperature_c}°C.',reference=plan.plan_no,actioned=False)
                        raise PermissionError('Production blocked: actual iron temperature exceeds permitted maximum.')
                    if plan.min_temperature_c and temp < plan.min_temperature_c:
                        Alert.objects.create(level='WARNING',title='Hand Iron Temperature Low',message=f'{plan.plan_no}: {temp}°C below min {plan.min_temperature_c}°C.',reference=plan.plan_no,actioned=False)
                    HandIronProductionEntry.objects.create(
                        plan=plan,bundle=bundle,work_date=request.POST.get('work_date') or today,
                        operator=Employee.objects.filter(pk=request.POST.get('operator_id')).first(),
                        workstation=AssetMachine.objects.filter(pk=request.POST.get('workstation_id')).first(),
                        target_qty=int(request.POST.get('target_qty') or 0),
                        actual_qty=int(request.POST.get('actual_qty') or 0),
                        start_at=request.POST.get('start_at') or None,
                        end_at=request.POST.get('end_at') or None,
                        process_minutes=int(request.POST.get('process_minutes') or 0),
                        npt_minutes=int(request.POST.get('npt_minutes') or 0),
                        actual_temperature_c=temp,
                        cost_per_minute=Decimal(request.POST.get('cost_per_minute') or '0'),
                        labour_cost=Decimal(request.POST.get('labour_cost') or '0'),
                        utility_cost=Decimal(request.POST.get('utility_cost') or '0'),
                        manual_entry=manual,approval=approval
                    )
                    messages.success(request,'Hand Iron production entry saved.')

                elif action=='qc':
                    plan=get_object_or_404(HandIronPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    result=request.POST.get('result','PASS')
                    defect=request.POST.get('defect_type','')
                    reject=int(request.POST.get('reject_qty') or 0)
                    reiron=int(request.POST.get('reiron_qty') or 0)
                    if defect in {'SCORCH_BURN','COLOUR_CHANGE','SHAPE_DISTORTION'} and result=='PASS':
                        result='HOLD'
                    qc=HandIronQC.objects.create(
                        plan=plan,bundle=bundle,
                        inspected_qty=int(request.POST.get('inspected_qty') or 0),
                        pass_qty=int(request.POST.get('pass_qty') or 0),
                        reject_qty=reject,reiron_qty=reiron,
                        defect_type=defect,
                        defect_reason=request.POST.get('defect_reason','').strip(),
                        result=result,checked_by=request.user,
                        qc_photo=request.FILES.get('qc_photo') or None
                    )
                    if result in {'HOLD','REIRON','REJECT'} or defect in {'SCORCH_BURN','COLOUR_CHANGE'}:
                        Alert.objects.create(
                            level='RED' if result=='REJECT' or defect=='SCORCH_BURN' else 'WARNING',
                            title='Hand Iron QC Hold / Re-Iron',
                            message=f'{plan.plan_no}: result={result}, defect={defect}, reject={reject}, reiron={reiron}.',
                            reference=plan.plan_no,actioned=False
                        )
                    messages.success(request,f'Hand Iron QC recorded: {result}.')

                elif action=='generate_report':
                    slot=request.POST.get('slot')
                    if slot not in {'08:00','13:00','20:00'}:
                        raise ValueError('Invalid report slot.')
                    payload=_hand_iron_auto_report_payload(today)
                    HandIronAutoReport.objects.update_or_create(
                        report_date=today,slot=slot,department=None,
                        defaults={
                            'summary':payload,
                            'outstanding_alerts':Alert.objects.filter(actioned=False).count(),
                            'pending_actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),
                            'escalated_items':Alert.objects.filter(actioned=False,level='RED').count()
                        }
                    )
                    messages.success(request,f'{slot} Hand Iron automatic report generated.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('hand_iron_dashboard')

    plans=HandIronPlan.objects.select_related('order','department').order_by('-created_at')
    scans=HandIronBundleScan.objects.select_related('plan','bundle','operator','workstation').order_by('-scanned_at')
    prod=HandIronProductionEntry.objects.select_related('plan','bundle','operator','workstation').filter(work_date=today)
    qc=HandIronQC.objects.select_related('plan','bundle').order_by('-checked_at')
    payload=_hand_iron_auto_report_payload(today)

    ctx={
        'today':today,'plans':plans[:120],'orders':MasterOrder.objects.order_by('-created_at')[:300],
        'bundles':CuttingBundle.objects.order_by('-created_at')[:500],
        'employees':Employee.objects.filter(status='ACTIVE')[:500],
        'workstations':AssetMachine.objects.all()[:300],
        'departments':Department.objects.all().order_by('name'),
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-created_at')[:300],
        'scans':scans[:100],'prod':prod[:100],'qc_rows':qc[:100],
        'reports':HandIronAutoReport.objects.filter(report_date=today).order_by('slot'),
        'payload':payload,
        'variances':HandIronVariance.objects.filter(actioned=False).order_by('-created_at')[:50],
        'alerts':Alert.objects.filter(actioned=False).order_by('-created_at')[:10],
        'defect_types':[x[0] for x in HandIronQC.DEFECTS],
    }
    return render(request,'hand_iron_dashboard.html',ctx)

@login_required
def hand_iron_report_csv(request):
    import csv
    today=timezone.localdate()
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']=f'attachment; filename="hand-iron-report-{today}.csv"'
    w=csv.writer(response)
    w.writerow(['Date','Plan','Order','Bundle','Operator','Workstation','Target','Actual','Minutes','NPT','Temperature C','Cost/Minute','Labour Cost','Utility Cost','Process Cost','Total Cost'])
    for x in HandIronProductionEntry.objects.select_related('plan__order','bundle','operator','workstation').filter(work_date=today):
        w.writerow([
            x.work_date,x.plan.plan_no,x.plan.order.master_order_id,
            x.bundle.bundle_no if x.bundle else '',
            x.operator.employee_id if x.operator else '',
            x.workstation.name if x.workstation else '',
            x.target_qty,x.actual_qty,x.process_minutes,x.npt_minutes,x.actual_temperature_c,
            x.cost_per_minute,x.labour_cost,x.utility_cost,x.process_cost,x.total_cost
        ])
    return response

@login_required
def api_hand_iron_dashboard(request):
    return JsonResponse(_hand_iron_auto_report_payload(timezone.localdate()))


def _poly_auto_report_payload(work_date):
    from django.db.models import Sum
    plans=PolyPlan.objects.filter(created_at__date__lte=work_date)
    pack=PolyPackingEntry.objects.filter(work_date=work_date)
    scans=PolyBundleScan.objects.filter(scanned_at__date=work_date)
    qc=PolyQC.objects.filter(checked_at__date=work_date)
    stock=PolyStockIssue.objects.filter(plan__in=plans)
    var=PolyVariance.objects.filter(plan__in=plans,actioned=False)
    target=pack.aggregate(v=Sum('target_qty'))['v'] or 0
    actual=pack.aggregate(v=Sum('actual_qty'))['v'] or 0
    return {
        'plans':plans.count(),
        'planned_qty':plans.aggregate(v=Sum('planned_qty'))['v'] or 0,
        'target_qty':target,
        'actual_qty':actual,
        'efficiency_percent':round(actual/target*100,2) if target else 0,
        'stock_issued_qty':stock.aggregate(v=Sum('issued_qty'))['v'] or 0,
        'stock_returned_qty':stock.aggregate(v=Sum('returned_qty'))['v'] or 0,
        'stock_damaged_qty':stock.aggregate(v=Sum('damaged_qty'))['v'] or 0,
        'poly_used_qty':pack.aggregate(v=Sum('poly_used_qty'))['v'] or 0,
        'packing_damaged_qty':pack.aggregate(v=Sum('damaged_qty'))['v'] or 0,
        'packing_rejected_qty':pack.aggregate(v=Sum('rejected_qty'))['v'] or 0,
        'packing_returned_qty':pack.aggregate(v=Sum('returned_qty'))['v'] or 0,
        'process_minutes':pack.aggregate(v=Sum('process_minutes'))['v'] or 0,
        'npt_minutes':pack.aggregate(v=Sum('npt_minutes'))['v'] or 0,
        'process_cost':str(pack.aggregate(v=Sum('process_cost'))['v'] or 0),
        'labour_cost':str(pack.aggregate(v=Sum('labour_cost'))['v'] or 0),
        'sticker_barcode_cost':str(pack.aggregate(v=Sum('sticker_barcode_cost'))['v'] or 0),
        'wastage_cost':str(pack.aggregate(v=Sum('wastage_cost'))['v'] or 0),
        'total_cost':str(pack.aggregate(v=Sum('total_cost'))['v'] or 0),
        'bundle_in_scans':scans.filter(direction='IN',scan_status='VALID').count(),
        'bundle_out_scans':scans.filter(direction='OUT',scan_status='VALID').count(),
        'blocked_scans':scans.exclude(scan_status='VALID').count(),
        'qc_pass_qty':qc.aggregate(v=Sum('pass_qty'))['v'] or 0,
        'qc_reject_qty':qc.aggregate(v=Sum('reject_qty'))['v'] or 0,
        'qc_rework_qty':qc.aggregate(v=Sum('rework_qty'))['v'] or 0,
        'unactioned_variances':var.count(),
    }

@login_required
@require_http_methods(['GET','POST'])
def poly_dashboard(request):
    from django.contrib import messages
    from decimal import Decimal
    today=timezone.localdate()

    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='plan':
                    order=get_object_or_404(MasterOrder,pk=request.POST.get('order_id'))
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    PolyPlan.objects.create(
                        plan_no=request.POST.get('plan_no','').strip(),
                        order=order,
                        department=Department.objects.filter(pk=request.POST.get('department_id')).first(),
                        buyer=request.POST.get('buyer','').strip() or order.buyer,
                        brand=request.POST.get('brand','').strip(),
                        style_no=request.POST.get('style_no','').strip(),
                        product=request.POST.get('product','').strip() or order.product,
                        colour=request.POST.get('colour','').strip(),
                        size_range=request.POST.get('size_range','').strip(),
                        poly_type=request.POST.get('poly_type','INDIVIDUAL_POLY'),
                        poly_code=request.POST.get('poly_code','').strip(),
                        poly_size=request.POST.get('poly_size','').strip(),
                        thickness_micron=Decimal(request.POST.get('thickness_micron') or '0'),
                        material=request.POST.get('material','').strip(),
                        warning_text=request.POST.get('warning_text','').strip(),
                        barcode_required=request.POST.get('barcode_required')=='on',
                        planned_qty=int(request.POST.get('planned_qty') or 0),
                        target_date=request.POST.get('target_date') or None,
                        status='APPROVED' if approval and approval.status=='APPROVED' else 'PENDING_APPROVAL',
                        approval=approval,
                        packing_specification=request.FILES.get('packing_specification') or None,
                        artwork=request.FILES.get('artwork') or None,
                        created_by=request.user
                    )
                    messages.success(request,'Poly plan created.')

                elif action=='stock_issue':
                    plan=get_object_or_404(PolyPlan,pk=request.POST.get('plan_id'))
                    scan=request.POST.get('stock_out_scan','').strip()
                    if not scan:
                        raise PermissionError('POLY STOCK OUT SCAN is mandatory.')
                    PolyStockIssue.objects.create(
                        plan=plan,
                        stock_item=StockItem.objects.filter(pk=request.POST.get('stock_item_id')).first(),
                        supplier=request.POST.get('supplier','').strip(),
                        batch_no=request.POST.get('batch_no','').strip(),
                        lot_no=request.POST.get('lot_no','').strip(),
                        issued_qty=int(request.POST.get('issued_qty') or 0),
                        returned_qty=int(request.POST.get('returned_qty') or 0),
                        damaged_qty=int(request.POST.get('damaged_qty') or 0),
                        stock_out_scan=scan,
                        stock_in_return_scan=request.POST.get('stock_in_return_scan','').strip(),
                        issued_by=request.user
                    )
                    messages.success(request,'Poly stock issue recorded with STOCK OUT SCAN.')

                elif action=='bundle_scan':
                    plan=get_object_or_404(PolyPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    direction=request.POST.get('direction','IN')
                    barcode=request.POST.get('barcode','').strip()
                    expected=bundle.quantity if bundle else int(request.POST.get('expected_qty') or 0)
                    actual=int(request.POST.get('actual_qty') or expected)
                    status='VALID'

                    if bundle and barcode and barcode != bundle.barcode:
                        status='MISMATCH'
                    if bundle and PolyBundleScan.objects.filter(plan=plan,bundle=bundle,direction=direction,scan_status='VALID').exists():
                        status='DUPLICATE'
                    if actual != expected:
                        status='MISMATCH'
                    if direction=='OUT':
                        if bundle and not PolyBundleScan.objects.filter(plan=plan,bundle=bundle,direction='IN',scan_status='VALID').exists():
                            status='BLOCKED'
                        if not PolyQC.objects.filter(plan=plan,bundle=bundle,result='PASS').exists():
                            status='QC_HOLD'

                    PolyBundleScan.objects.create(
                        plan=plan,bundle=bundle,direction=direction,barcode=barcode,
                        expected_qty=expected,actual_qty=actual,
                        source_department=request.POST.get('source_department','').strip(),
                        destination_department=request.POST.get('destination_department','').strip(),
                        scan_status=status,scanned_by=request.user
                    )
                    if status!='VALID':
                        Alert.objects.create(
                            level='RED',
                            title='Poly Bundle/Garment Scan Blocked',
                            message=f'{plan.plan_no}: {status} during {direction} scan.',
                            reference=plan.plan_no,actioned=False
                        )
                        raise PermissionError(f'Poly garment/bundle movement blocked: {status}.')
                    messages.success(request,f'Poly GARMENT/BUNDLE {direction} SCAN accepted.')

                elif action=='packing':
                    plan=get_object_or_404(PolyPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    if bundle and not PolyBundleScan.objects.filter(plan=plan,bundle=bundle,direction='IN',scan_status='VALID').exists():
                        raise PermissionError('Poly packing blocked: valid GARMENT/BUNDLE IN SCAN is required.')
                    manual=request.POST.get('manual_entry')=='on'
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if manual and (not approval or approval.status!='APPROVED'):
                        raise PermissionError('Manual Poly packing entry requires senior APPROVED authorization.')
                    PolyPackingEntry.objects.create(
                        plan=plan,bundle=bundle,
                        work_date=request.POST.get('work_date') or today,
                        employee=Employee.objects.filter(pk=request.POST.get('employee_id')).first(),
                        target_qty=int(request.POST.get('target_qty') or 0),
                        actual_qty=int(request.POST.get('actual_qty') or 0),
                        process_minutes=int(request.POST.get('process_minutes') or 0),
                        npt_minutes=int(request.POST.get('npt_minutes') or 0),
                        poly_used_qty=int(request.POST.get('poly_used_qty') or 0),
                        damaged_qty=int(request.POST.get('damaged_qty') or 0),
                        rejected_qty=int(request.POST.get('rejected_qty') or 0),
                        returned_qty=int(request.POST.get('returned_qty') or 0),
                        poly_cost_per_piece=Decimal(request.POST.get('poly_cost_per_piece') or '0'),
                        sticker_barcode_cost=Decimal(request.POST.get('sticker_barcode_cost') or '0'),
                        labour_cost=Decimal(request.POST.get('labour_cost') or '0'),
                        cost_per_minute=Decimal(request.POST.get('cost_per_minute') or '0'),
                        wastage_cost=Decimal(request.POST.get('wastage_cost') or '0'),
                        manual_entry=manual,approval=approval
                    )
                    messages.success(request,'Poly packing/cost entry saved.')

                elif action=='qc':
                    plan=get_object_or_404(PolyPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    result=request.POST.get('result','PASS')
                    defect=request.POST.get('defect_type','')
                    if defect in {'WRONG_POLY','WRONG_ORDER_STYLE','WRONG_SIZE','WRONG_COLOUR','BARCODE_FAIL','WARNING_TEXT_FAIL'} and result=='PASS':
                        result='HOLD'
                    qc=PolyQC.objects.create(
                        plan=plan,bundle=bundle,
                        inspected_qty=int(request.POST.get('inspected_qty') or 0),
                        pass_qty=int(request.POST.get('pass_qty') or 0),
                        reject_qty=int(request.POST.get('reject_qty') or 0),
                        rework_qty=int(request.POST.get('rework_qty') or 0),
                        defect_type=defect,
                        defect_reason=request.POST.get('defect_reason','').strip(),
                        result=result,
                        checked_by=request.user,
                        qc_photo=request.FILES.get('qc_photo') or None
                    )
                    if result in {'HOLD','REWORK','REJECT'} or defect in {'WRONG_POLY','WRONG_ORDER_STYLE','BARCODE_FAIL'}:
                        Alert.objects.create(
                            level='RED' if result=='REJECT' or defect in {'WRONG_POLY','WRONG_ORDER_STYLE'} else 'WARNING',
                            title='Poly QC Hold / Wrong Packing',
                            message=f'{plan.plan_no}: result={result}, defect={defect}.',
                            reference=plan.plan_no,actioned=False
                        )
                    messages.success(request,f'Poly QC recorded: {result}.')

                elif action=='generate_report':
                    slot=request.POST.get('slot')
                    if slot not in {'08:00','13:00','20:00'}:
                        raise ValueError('Invalid report slot.')
                    payload=_poly_auto_report_payload(today)
                    PolyAutoReport.objects.update_or_create(
                        report_date=today,slot=slot,department=None,
                        defaults={
                            'summary':payload,
                            'outstanding_alerts':Alert.objects.filter(actioned=False).count(),
                            'pending_actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),
                            'escalated_items':Alert.objects.filter(actioned=False,level='RED').count()
                        }
                    )
                    messages.success(request,f'{slot} Poly automatic report generated.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('poly_dashboard')

    plans=PolyPlan.objects.select_related('order','department').order_by('-created_at')
    scans=PolyBundleScan.objects.select_related('plan','bundle').order_by('-scanned_at')
    pack=PolyPackingEntry.objects.select_related('plan','bundle','employee').filter(work_date=today)
    qc=PolyQC.objects.select_related('plan','bundle').order_by('-checked_at')
    payload=_poly_auto_report_payload(today)

    ctx={
        'today':today,'plans':plans[:120],
        'orders':MasterOrder.objects.order_by('-created_at')[:300],
        'bundles':CuttingBundle.objects.order_by('-created_at')[:500],
        'stock_items':StockItem.objects.all()[:300],
        'employees':Employee.objects.filter(status='ACTIVE')[:500],
        'departments':Department.objects.all().order_by('name'),
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-created_at')[:300],
        'scans':scans[:100],'pack':pack[:100],'qc_rows':qc[:100],
        'stock_issues':PolyStockIssue.objects.select_related('plan','stock_item').order_by('-created_at')[:100],
        'reports':PolyAutoReport.objects.filter(report_date=today).order_by('slot'),
        'payload':payload,
        'variances':PolyVariance.objects.filter(actioned=False).order_by('-created_at')[:50],
        'alerts':Alert.objects.filter(actioned=False).order_by('-created_at')[:10],
        'poly_types':[x[0] for x in PolyPlan.POLY_TYPES],
        'defect_types':[x[0] for x in PolyQC.DEFECTS],
    }
    return render(request,'poly_dashboard.html',ctx)

@login_required
def poly_report_csv(request):
    import csv
    today=timezone.localdate()
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']=f'attachment; filename="poly-report-{today}.csv"'
    w=csv.writer(response)
    w.writerow([
        'Date','Plan','Order','Poly Type','Poly Code','Bundle','Employee','Target','Actual',
        'Poly Used','Damaged','Rejected','Returned','Process Minutes','NPT',
        'Poly Cost/Piece','Sticker/Barcode Cost','Labour Cost','Process Cost','Wastage Cost','Total Cost'
    ])
    for x in PolyPackingEntry.objects.select_related('plan__order','bundle','employee').filter(work_date=today):
        w.writerow([
            x.work_date,x.plan.plan_no,x.plan.order.master_order_id,x.plan.poly_type,x.plan.poly_code,
            x.bundle.bundle_no if x.bundle else '',
            x.employee.employee_id if x.employee else '',
            x.target_qty,x.actual_qty,x.poly_used_qty,x.damaged_qty,x.rejected_qty,x.returned_qty,
            x.process_minutes,x.npt_minutes,x.poly_cost_per_piece,x.sticker_barcode_cost,
            x.labour_cost,x.process_cost,x.wastage_cost,x.total_cost
        ])
    return response

@login_required
def api_poly_dashboard(request):
    return JsonResponse(_poly_auto_report_payload(timezone.localdate()))


def _iron_auto_report_payload(work_date):
    from django.db.models import Sum
    plans=IronPlan.objects.filter(created_at__date__lte=work_date)
    prod=IronProductionEntry.objects.filter(work_date=work_date)
    scans=IronBundleScan.objects.filter(scanned_at__date=work_date)
    qc=IronQC.objects.filter(checked_at__date=work_date)
    var=IronVariance.objects.filter(plan__in=plans,actioned=False)
    target=prod.aggregate(v=Sum('target_qty'))['v'] or 0
    actual=prod.aggregate(v=Sum('actual_qty'))['v'] or 0
    return {
        'plans':plans.count(),
        'planned_qty':plans.aggregate(v=Sum('planned_qty'))['v'] or 0,
        'target_qty':target,
        'actual_qty':actual,
        'efficiency_percent':round(actual/target*100,2) if target else 0,
        'process_minutes':prod.aggregate(v=Sum('process_minutes'))['v'] or 0,
        'npt_minutes':prod.aggregate(v=Sum('npt_minutes'))['v'] or 0,
        'downtime_minutes':prod.aggregate(v=Sum('downtime_minutes'))['v'] or 0,
        'electricity_kwh':str(prod.aggregate(v=Sum('electricity_kwh'))['v'] or 0),
        'steam_kg':str(prod.aggregate(v=Sum('steam_kg'))['v'] or 0),
        'labour_cost':str(prod.aggregate(v=Sum('labour_cost'))['v'] or 0),
        'helper_cost':str(prod.aggregate(v=Sum('helper_cost'))['v'] or 0),
        'machine_cost':str(prod.aggregate(v=Sum('machine_cost'))['v'] or 0),
        'utility_cost':str(prod.aggregate(v=Sum('utility_cost'))['v'] or 0),
        'process_cost':str(prod.aggregate(v=Sum('process_cost'))['v'] or 0),
        'downtime_cost':str(prod.aggregate(v=Sum('downtime_cost'))['v'] or 0),
        'total_cost':str(prod.aggregate(v=Sum('total_cost'))['v'] or 0),
        'bundle_in_scans':scans.filter(direction='IN',scan_status='VALID').count(),
        'bundle_out_scans':scans.filter(direction='OUT',scan_status='VALID').count(),
        'blocked_scans':scans.exclude(scan_status='VALID').count(),
        'qc_pass_qty':qc.aggregate(v=Sum('pass_qty'))['v'] or 0,
        'qc_reject_qty':qc.aggregate(v=Sum('reject_qty'))['v'] or 0,
        'reiron_qty':qc.aggregate(v=Sum('reiron_qty'))['v'] or 0,
        'rework_qty':qc.aggregate(v=Sum('rework_qty'))['v'] or 0,
        'unactioned_variances':var.count(),
    }

@login_required
@require_http_methods(['GET','POST'])
def iron_dashboard(request):
    from django.contrib import messages
    from decimal import Decimal
    today=timezone.localdate()

    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='plan':
                    order=get_object_or_404(MasterOrder,pk=request.POST.get('order_id'))
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    IronPlan.objects.create(
                        plan_no=request.POST.get('plan_no','').strip(),
                        order=order,
                        department=Department.objects.filter(pk=request.POST.get('department_id')).first(),
                        product=request.POST.get('product','').strip() or order.product,
                        style_no=request.POST.get('style_no','').strip(),
                        colour=request.POST.get('colour','').strip(),
                        size_range=request.POST.get('size_range','').strip(),
                        fabric_type=request.POST.get('fabric_type','').strip(),
                        min_temperature_c=Decimal(request.POST.get('min_temperature_c') or '0'),
                        max_temperature_c=Decimal(request.POST.get('max_temperature_c') or '0'),
                        min_steam_pressure_bar=Decimal(request.POST.get('min_steam_pressure_bar') or '0'),
                        max_steam_pressure_bar=Decimal(request.POST.get('max_steam_pressure_bar') or '0'),
                        planned_qty=int(request.POST.get('planned_qty') or 0),
                        target_date=request.POST.get('target_date') or None,
                        status='APPROVED' if approval and approval.status=='APPROVED' else 'PENDING_APPROVAL',
                        approval=approval,
                        instruction_file=request.FILES.get('instruction_file') or None,
                        created_by=request.user
                    )
                    messages.success(request,'Industrial Iron plan created.')

                elif action=='bundle_scan':
                    plan=get_object_or_404(IronPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    machine=AssetMachine.objects.filter(pk=request.POST.get('machine_id')).first()
                    direction=request.POST.get('direction','IN')
                    barcode=request.POST.get('barcode','').strip()
                    expected=bundle.quantity if bundle else int(request.POST.get('expected_qty') or 0)
                    actual=int(request.POST.get('actual_qty') or expected)
                    status='VALID'
                    if machine and machine.status in {'UNDER_MAINTENANCE','BREAKDOWN','HOLD','RETIRED','DISPOSED'}:
                        status='MACHINE_HOLD'
                    if bundle and barcode and barcode != bundle.barcode:
                        status='MISMATCH'
                    if bundle and IronBundleScan.objects.filter(plan=plan,bundle=bundle,direction=direction,scan_status='VALID').exists():
                        status='DUPLICATE'
                    if actual != expected:
                        status='MISMATCH'
                    if direction=='OUT':
                        if bundle and not IronBundleScan.objects.filter(plan=plan,bundle=bundle,direction='IN',scan_status='VALID').exists():
                            status='BLOCKED'
                        if not IronQC.objects.filter(plan=plan,bundle=bundle,result='PASS').exists():
                            status='QC_HOLD'
                    IronBundleScan.objects.create(
                        plan=plan,bundle=bundle,direction=direction,barcode=barcode,
                        expected_qty=expected,actual_qty=actual,
                        source_department=request.POST.get('source_department','').strip(),
                        destination_department=request.POST.get('destination_department','').strip(),
                        operator=Employee.objects.filter(pk=request.POST.get('operator_id')).first(),
                        machine=machine,scan_status=status,scanned_by=request.user
                    )
                    if status!='VALID':
                        Alert.objects.create(
                            level='RED',title='Industrial Iron Bundle Scan Blocked',
                            message=f'{plan.plan_no}: {status} during {direction} scan.',
                            reference=plan.plan_no,actioned=False
                        )
                        raise PermissionError(f'Industrial Iron bundle movement blocked: {status}.')
                    messages.success(request,f'Industrial Iron BUNDLE {direction} SCAN accepted.')

                elif action=='production':
                    plan=get_object_or_404(IronPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    machine=get_object_or_404(AssetMachine,pk=request.POST.get('machine_id'))
                    if machine.status in {'UNDER_MAINTENANCE','BREAKDOWN','HOLD','RETIRED','DISPOSED'}:
                        raise PermissionError(f'Production blocked: machine status is {machine.status}.')
                    if AssetMaintenance.objects.filter(asset=machine,status__in=['PLANNED','IN_PROGRESS'],scheduled_date__lte=today).exists():
                        raise PermissionError('Production blocked: machine has due/in-progress maintenance.')
                    if bundle and not IronBundleScan.objects.filter(plan=plan,bundle=bundle,direction='IN',scan_status='VALID').exists():
                        raise PermissionError('Production blocked: valid Industrial Iron BUNDLE IN SCAN is required.')
                    manual=request.POST.get('manual_entry')=='on'
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if manual and (not approval or approval.status!='APPROVED'):
                        raise PermissionError('Manual Industrial Iron production entry requires senior APPROVED authorization.')
                    temp=Decimal(request.POST.get('actual_temperature_c') or '0')
                    pressure=Decimal(request.POST.get('steam_pressure_bar') or '0')
                    if plan.max_temperature_c and temp > plan.max_temperature_c:
                        Alert.objects.create(level='RED',title='Industrial Iron Temperature High',message=f'{plan.plan_no}: {temp}°C exceeds max {plan.max_temperature_c}°C.',reference=plan.plan_no,actioned=False)
                        raise PermissionError('Production blocked: temperature exceeds permitted maximum.')
                    if plan.min_temperature_c and temp < plan.min_temperature_c:
                        Alert.objects.create(level='WARNING',title='Industrial Iron Temperature Low',message=f'{plan.plan_no}: {temp}°C below min {plan.min_temperature_c}°C.',reference=plan.plan_no,actioned=False)
                    if plan.max_steam_pressure_bar and pressure > plan.max_steam_pressure_bar:
                        Alert.objects.create(level='RED',title='Industrial Iron Steam Pressure High',message=f'{plan.plan_no}: {pressure} bar exceeds max {plan.max_steam_pressure_bar} bar.',reference=plan.plan_no,actioned=False)
                        raise PermissionError('Production blocked: steam pressure exceeds permitted maximum.')
                    if plan.min_steam_pressure_bar and pressure < plan.min_steam_pressure_bar:
                        Alert.objects.create(level='WARNING',title='Industrial Iron Steam Pressure Low',message=f'{plan.plan_no}: {pressure} bar below min {plan.min_steam_pressure_bar} bar.',reference=plan.plan_no,actioned=False)

                    open_downtime=AssetDowntime.objects.filter(asset=machine,ended_at__isnull=True)
                    if open_downtime.exists():
                        raise PermissionError('Production blocked: machine has active downtime.')

                    IronProductionEntry.objects.create(
                        plan=plan,bundle=bundle,work_date=request.POST.get('work_date') or today,
                        operator=Employee.objects.filter(pk=request.POST.get('operator_id')).first(),
                        helper=Employee.objects.filter(pk=request.POST.get('helper_id')).first(),
                        machine=machine,
                        target_qty=int(request.POST.get('target_qty') or 0),
                        actual_qty=int(request.POST.get('actual_qty') or 0),
                        start_at=request.POST.get('start_at') or None,
                        end_at=request.POST.get('end_at') or None,
                        process_minutes=int(request.POST.get('process_minutes') or 0),
                        npt_minutes=int(request.POST.get('npt_minutes') or 0),
                        downtime_minutes=int(request.POST.get('downtime_minutes') or 0),
                        actual_temperature_c=temp,
                        steam_pressure_bar=pressure,
                        electricity_kwh=Decimal(request.POST.get('electricity_kwh') or '0'),
                        steam_kg=Decimal(request.POST.get('steam_kg') or '0'),
                        cost_per_minute=Decimal(request.POST.get('cost_per_minute') or '0'),
                        labour_cost=Decimal(request.POST.get('labour_cost') or '0'),
                        helper_cost=Decimal(request.POST.get('helper_cost') or '0'),
                        machine_cost=Decimal(request.POST.get('machine_cost') or '0'),
                        utility_cost=Decimal(request.POST.get('utility_cost') or '0'),
                        downtime_cost=Decimal(request.POST.get('downtime_cost') or '0'),
                        manual_entry=manual,approval=approval
                    )
                    messages.success(request,'Industrial Iron production/cost entry saved.')

                elif action=='qc':
                    plan=get_object_or_404(IronPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    result=request.POST.get('result','PASS')
                    defect=request.POST.get('defect_type','')
                    if defect in {'SCORCH_BURN','COLOUR_CHANGE','SHAPE_DISTORTION','MEASUREMENT_CHANGE','FABRIC_DAMAGE'} and result=='PASS':
                        result='HOLD'
                    IronQC.objects.create(
                        plan=plan,bundle=bundle,
                        inspected_qty=int(request.POST.get('inspected_qty') or 0),
                        pass_qty=int(request.POST.get('pass_qty') or 0),
                        reject_qty=int(request.POST.get('reject_qty') or 0),
                        reiron_qty=int(request.POST.get('reiron_qty') or 0),
                        rework_qty=int(request.POST.get('rework_qty') or 0),
                        defect_type=defect,
                        defect_reason=request.POST.get('defect_reason','').strip(),
                        result=result,checked_by=request.user,
                        qc_photo=request.FILES.get('qc_photo') or None
                    )
                    if result in {'HOLD','REIRON','REWORK','REJECT'} or defect in {'SCORCH_BURN','FABRIC_DAMAGE','MEASUREMENT_CHANGE'}:
                        Alert.objects.create(
                            level='RED' if result=='REJECT' or defect in {'SCORCH_BURN','FABRIC_DAMAGE'} else 'WARNING',
                            title='Industrial Iron QC Hold / Rework',
                            message=f'{plan.plan_no}: result={result}, defect={defect}.',
                            reference=plan.plan_no,actioned=False
                        )
                    messages.success(request,f'Industrial Iron QC recorded: {result}.')

                elif action=='downtime':
                    machine=get_object_or_404(AssetMachine,pk=request.POST.get('machine_id'))
                    ref=request.POST.get('reference','').strip() or f'IRON-DT-{timezone.now():%Y%m%d%H%M%S}'
                    AssetDowntime.objects.create(
                        asset=machine,reference=ref,
                        reason=request.POST.get('reason','BREAKDOWN'),
                        description=request.POST.get('description','').strip(),
                        production_impact_qty=Decimal(request.POST.get('production_impact_qty') or '0'),
                        recorded_by=request.user
                    )
                    machine.status='BREAKDOWN' if request.POST.get('reason')=='BREAKDOWN' else 'HOLD'
                    machine.save(update_fields=['status','updated_at'])
                    Alert.objects.create(level='RED',title='Industrial Iron Machine Downtime',message=f'{machine.asset_code}: {request.POST.get("reason")}.',reference=ref,actioned=False)
                    messages.success(request,'Machine downtime opened and machine held.')

                elif action=='generate_report':
                    slot=request.POST.get('slot')
                    if slot not in {'08:00','13:00','20:00'}:
                        raise ValueError('Invalid report slot.')
                    payload=_iron_auto_report_payload(today)
                    IronAutoReport.objects.update_or_create(
                        report_date=today,slot=slot,department=None,
                        defaults={
                            'summary':payload,
                            'outstanding_alerts':Alert.objects.filter(actioned=False).count(),
                            'pending_actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),
                            'escalated_items':Alert.objects.filter(actioned=False,level='RED').count()
                        }
                    )
                    messages.success(request,f'{slot} Industrial Iron automatic report generated.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('iron_dashboard')

    plans=IronPlan.objects.select_related('order','department').order_by('-created_at')
    scans=IronBundleScan.objects.select_related('plan','bundle','operator','machine').order_by('-scanned_at')
    prod=IronProductionEntry.objects.select_related('plan','bundle','operator','helper','machine').filter(work_date=today)
    qc=IronQC.objects.select_related('plan','bundle').order_by('-checked_at')
    payload=_iron_auto_report_payload(today)
    machines=AssetMachine.objects.filter(asset_type__in=['MACHINE','EQUIPMENT']).order_by('asset_code')

    ctx={
        'today':today,'plans':plans[:120],
        'orders':MasterOrder.objects.order_by('-created_at')[:300],
        'bundles':CuttingBundle.objects.order_by('-created_at')[:500],
        'employees':Employee.objects.filter(status='ACTIVE')[:500],
        'machines':machines[:500],
        'departments':Department.objects.all().order_by('name'),
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-created_at')[:300],
        'scans':scans[:100],'prod':prod[:100],'qc_rows':qc[:100],
        'downtime_rows':AssetDowntime.objects.select_related('asset').filter(asset__in=machines).order_by('-started_at')[:50],
        'maintenance_rows':AssetMaintenance.objects.select_related('asset').filter(asset__in=machines).order_by('-scheduled_date')[:50],
        'reports':IronAutoReport.objects.filter(report_date=today).order_by('slot'),
        'payload':payload,
        'variances':IronVariance.objects.filter(actioned=False).order_by('-created_at')[:50],
        'alerts':Alert.objects.filter(actioned=False).order_by('-created_at')[:10],
        'defect_types':[x[0] for x in IronQC.DEFECTS],
        'downtime_reasons':[x[0] for x in AssetDowntime.REASONS],
    }
    return render(request,'iron_dashboard.html',ctx)

@login_required
def iron_report_csv(request):
    import csv
    today=timezone.localdate()
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']=f'attachment; filename="industrial-iron-report-{today}.csv"'
    w=csv.writer(response)
    w.writerow([
        'Date','Plan','Order','Bundle','Operator','Helper','Machine','Target','Actual',
        'Process Minutes','NPT','Downtime','Temperature C','Steam Pressure Bar','Electricity kWh',
        'Steam kg','Cost/Minute','Labour Cost','Helper Cost','Machine Cost','Utility Cost',
        'Process Cost','Downtime Cost','Total Cost'
    ])
    for x in IronProductionEntry.objects.select_related('plan__order','bundle','operator','helper','machine').filter(work_date=today):
        w.writerow([
            x.work_date,x.plan.plan_no,x.plan.order.master_order_id,
            x.bundle.bundle_no if x.bundle else '',
            x.operator.employee_id if x.operator else '',
            x.helper.employee_id if x.helper else '',
            x.machine.asset_code if x.machine else '',
            x.target_qty,x.actual_qty,x.process_minutes,x.npt_minutes,x.downtime_minutes,
            x.actual_temperature_c,x.steam_pressure_bar,x.electricity_kwh,x.steam_kg,
            x.cost_per_minute,x.labour_cost,x.helper_cost,x.machine_cost,x.utility_cost,
            x.process_cost,x.downtime_cost,x.total_cost
        ])
    return response

@login_required
def api_iron_dashboard(request):
    return JsonResponse(_iron_auto_report_payload(timezone.localdate()))


def _final_qc_auto_report_payload(work_date):
    from django.db.models import Sum
    plans=FinalQCPlan.objects.filter(created_at__date__lte=work_date)
    ins=FinalQCInspection.objects.filter(completed_at__date=work_date)
    scans=FinalQCUnitScan.objects.filter(scanned_at__date=work_date)
    defects=FinalQCDefect.objects.filter(inspection__in=ins)
    capas=FinalQCCAPA.objects.filter(inspection__in=ins)
    releases=FinalQCRelease.objects.filter(plan__in=plans)
    inspected=ins.aggregate(v=Sum('inspected_qty'))['v'] or 0
    passed=ins.aggregate(v=Sum('pass_qty'))['v'] or 0
    total_defects=(ins.aggregate(v=Sum('critical_defects'))['v'] or 0)+(ins.aggregate(v=Sum('major_defects'))['v'] or 0)+(ins.aggregate(v=Sum('minor_defects'))['v'] or 0)
    shipment_qty=plans.aggregate(v=Sum('shipment_qty'))['v'] or 0
    released_qty=sum((r.latest_inspection.pass_qty if r.released and r.latest_inspection else 0) for r in releases)
    return {
        'plans':plans.count(),
        'inspections':ins.count(),
        'inspected_qty':inspected,
        'pass_qty':passed,
        'critical_defects':ins.aggregate(v=Sum('critical_defects'))['v'] or 0,
        'major_defects':ins.aggregate(v=Sum('major_defects'))['v'] or 0,
        'minor_defects':ins.aggregate(v=Sum('minor_defects'))['v'] or 0,
        'rework_qty':ins.aggregate(v=Sum('rework_qty'))['v'] or 0,
        'reject_qty':ins.aggregate(v=Sum('reject_qty'))['v'] or 0,
        'measurement_fail_qty':ins.aggregate(v=Sum('measurement_fail_qty'))['v'] or 0,
        'appearance_fail_qty':ins.aggregate(v=Sum('appearance_fail_qty'))['v'] or 0,
        'workmanship_fail_qty':ins.aggregate(v=Sum('workmanship_fail_qty'))['v'] or 0,
        'label_fail_qty':ins.aggregate(v=Sum('label_fail_qty'))['v'] or 0,
        'barcode_fail_qty':ins.aggregate(v=Sum('barcode_fail_qty'))['v'] or 0,
        'poly_fail_qty':ins.aggregate(v=Sum('poly_fail_qty'))['v'] or 0,
        'packing_fail_qty':ins.aggregate(v=Sum('packing_fail_qty'))['v'] or 0,
        'carton_fail_qty':ins.aggregate(v=Sum('carton_marking_fail_qty'))['v'] or 0,
        'quantity_fail_qty':ins.aggregate(v=Sum('quantity_fail_qty'))['v'] or 0,
        'in_scans':scans.filter(direction='IN',scan_status='VALID').count(),
        'out_scans':scans.filter(direction='OUT',scan_status='VALID').count(),
        'blocked_scans':scans.exclude(scan_status='VALID').count(),
        'fpq_percent':round(passed/inspected*100,2) if inspected else 0,
        'dhu':round(total_defects/inspected*100,2) if inspected else 0,
        'shipment_qty':shipment_qty,
        'released_qty':released_qty,
        'shipment_readiness_percent':round(released_qty/shipment_qty*100,2) if shipment_qty else 0,
        'ready_to_ship_orders':releases.filter(final_decision='READY_TO_SHIP').count(),
        'conditional_releases':releases.filter(final_decision='CONDITIONAL_RELEASE').count(),
        'hold_orders':releases.filter(final_decision__in=['HOLD','REWORK','REJECT']).count(),
        'open_capa':capas.exclude(status='CLOSED').count(),
        'unactioned_defects':defects.filter(actioned=False).count(),
    }

@login_required
@require_http_methods(['GET','POST'])
def final_qc_dashboard(request):
    from django.contrib import messages
    today=timezone.localdate()

    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='plan':
                    order=get_object_or_404(MasterOrder,pk=request.POST.get('order_id'))
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    FinalQCPlan.objects.create(
                        plan_no=request.POST.get('plan_no','').strip(),
                        order=order,
                        department=Department.objects.filter(pk=request.POST.get('department_id')).first(),
                        buyer=request.POST.get('buyer','').strip() or order.buyer,
                        style_no=request.POST.get('style_no','').strip(),
                        product=request.POST.get('product','').strip() or order.product,
                        colour=request.POST.get('colour','').strip(),
                        size_range=request.POST.get('size_range','').strip(),
                        specification_version=request.POST.get('specification_version','1.0'),
                        aql_level=request.POST.get('aql_level','2.5'),
                        lot_size=int(request.POST.get('lot_size') or 0),
                        sample_size=int(request.POST.get('sample_size') or 0),
                        shipment_qty=int(request.POST.get('shipment_qty') or order.quantity),
                        inspection_date=request.POST.get('inspection_date') or None,
                        status='APPROVED' if approval and approval.status=='APPROVED' else 'PENDING_APPROVAL',
                        approval=approval,
                        specification_file=request.FILES.get('specification_file') or None,
                        approved_sample_file=request.FILES.get('approved_sample_file') or None,
                        packing_spec_file=request.FILES.get('packing_spec_file') or None,
                        created_by=request.user
                    )
                    messages.success(request,'Final QC plan created.')

                elif action=='scan':
                    plan=get_object_or_404(FinalQCPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    direction=request.POST.get('direction','IN')
                    barcode=request.POST.get('barcode','').strip()
                    expected=bundle.quantity if bundle else int(request.POST.get('expected_qty') or 0)
                    actual=int(request.POST.get('actual_qty') or expected)
                    status='VALID'
                    if bundle and barcode and barcode != bundle.barcode:
                        status='MISMATCH'
                    if bundle and FinalQCUnitScan.objects.filter(plan=plan,bundle=bundle,direction=direction,scan_status='VALID').exists():
                        status='DUPLICATE'
                    if actual != expected:
                        status='MISMATCH'
                    if direction=='OUT':
                        if bundle and not FinalQCUnitScan.objects.filter(plan=plan,bundle=bundle,direction='IN',scan_status='VALID').exists():
                            status='BLOCKED'
                        release=FinalQCRelease.objects.filter(plan=plan).select_related('approval').first()
                        if not release or not release.released:
                            status='QC_HOLD'
                    FinalQCUnitScan.objects.create(
                        plan=plan,bundle=bundle,direction=direction,barcode=barcode,
                        expected_qty=expected,actual_qty=actual,
                        source_department=request.POST.get('source_department','').strip(),
                        destination_department=request.POST.get('destination_department','Ready for Shipment').strip(),
                        scan_status=status,scanned_by=request.user
                    )
                    if status!='VALID':
                        Alert.objects.create(
                            level='RED',title='Final QC Movement Blocked',
                            message=f'{plan.plan_no}: {status} during {direction} scan.',
                            reference=plan.plan_no,actioned=False
                        )
                        raise PermissionError(f'Final QC movement blocked: {status}.')
                    messages.success(request,f'Final QC {direction} scan accepted.')

                elif action=='inspection':
                    plan=get_object_or_404(FinalQCPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    if bundle and not FinalQCUnitScan.objects.filter(plan=plan,bundle=bundle,direction='IN',scan_status='VALID').exists():
                        raise PermissionError('Final inspection blocked: valid Final QC IN SCAN is required.')
                    result=request.POST.get('result','HOLD')
                    critical=int(request.POST.get('critical_defects') or 0)
                    label_fail=int(request.POST.get('label_fail_qty') or 0)
                    barcode_fail=int(request.POST.get('barcode_fail_qty') or 0)
                    measurement_fail=int(request.POST.get('measurement_fail_qty') or 0)
                    packing_fail=int(request.POST.get('packing_fail_qty') or 0)
                    qty_fail=int(request.POST.get('quantity_fail_qty') or 0)
                    if critical>0 or label_fail>0 or barcode_fail>0 or measurement_fail>0 or packing_fail>0 or qty_fail>0:
                        if result=='PASS':
                            result='HOLD'

                    ins=FinalQCInspection.objects.create(
                        plan=plan,bundle=bundle,inspector=request.user,
                        inspected_qty=int(request.POST.get('inspected_qty') or 0),
                        pass_qty=int(request.POST.get('pass_qty') or 0),
                        critical_defects=critical,
                        major_defects=int(request.POST.get('major_defects') or 0),
                        minor_defects=int(request.POST.get('minor_defects') or 0),
                        measurement_fail_qty=measurement_fail,
                        appearance_fail_qty=int(request.POST.get('appearance_fail_qty') or 0),
                        workmanship_fail_qty=int(request.POST.get('workmanship_fail_qty') or 0),
                        label_fail_qty=label_fail,
                        barcode_fail_qty=barcode_fail,
                        poly_fail_qty=int(request.POST.get('poly_fail_qty') or 0),
                        packing_fail_qty=packing_fail,
                        carton_marking_fail_qty=int(request.POST.get('carton_marking_fail_qty') or 0),
                        quantity_fail_qty=qty_fail,
                        rework_qty=int(request.POST.get('rework_qty') or 0),
                        reject_qty=int(request.POST.get('reject_qty') or 0),
                        result=result,
                        buyer_inspection_result=request.POST.get('buyer_inspection_result','').strip(),
                        comments=request.POST.get('comments','').strip(),
                        inspection_sheet=request.FILES.get('inspection_sheet') or None,
                        photo=request.FILES.get('photo') or None
                    )
                    release,_=FinalQCRelease.objects.get_or_create(plan=plan)
                    release.latest_inspection=ins
                    release.shipment_readiness_percent=(
                        min((ins.pass_qty/plan.shipment_qty*100),100) if plan.shipment_qty else 0
                    )
                    release.save()

                    plan.status='PASSED' if result=='PASS' else ('REWORK' if result=='REWORK' else ('REJECTED' if result=='REJECT' else 'HOLD'))
                    plan.save(update_fields=['status','updated_at'])

                    if result in {'HOLD','REWORK','REJECT'} or critical>0 or label_fail or barcode_fail or measurement_fail or packing_fail or qty_fail:
                        Alert.objects.create(
                            level='RED',
                            title='Final QC Hold / Shipment Block',
                            message=f'{plan.plan_no}: result={result}; critical={critical}; measurement={measurement_fail}; label={label_fail}; barcode={barcode_fail}; packing={packing_fail}; qty={qty_fail}.',
                            reference=plan.plan_no,actioned=False
                        )
                    messages.success(request,f'Final inspection recorded: {result}.')

                elif action=='defect':
                    ins=get_object_or_404(FinalQCInspection,pk=request.POST.get('inspection_id'))
                    defect=FinalQCDefect.objects.create(
                        inspection=ins,
                        defect_code=request.POST.get('defect_code','').strip(),
                        category=request.POST.get('category','OTHER'),
                        severity=request.POST.get('severity','MINOR'),
                        description=request.POST.get('description','').strip(),
                        quantity=int(request.POST.get('quantity') or 1),
                        root_cause=request.POST.get('root_cause','').strip(),
                        corrective_action=request.POST.get('corrective_action','').strip(),
                        responsible_user=User.objects.filter(pk=request.POST.get('responsible_user_id')).first(),
                        due_at=request.POST.get('due_at') or None
                    )
                    if defect.severity=='CRITICAL':
                        Alert.objects.create(
                            level='RED',title='Critical Final QC Defect',
                            message=f'{ins.plan.plan_no}: {defect.description}',
                            reference=ins.plan.plan_no,actioned=False
                        )
                    messages.success(request,'Final QC defect recorded.')

                elif action=='capa':
                    ins=get_object_or_404(FinalQCInspection,pk=request.POST.get('inspection_id'))
                    seq=FinalQCCAPA.objects.filter(created_at__date=today).count()+1
                    FinalQCCAPA.objects.create(
                        reference=f'FQC-CAPA-{timezone.now():%Y%m%d}-{seq:04d}',
                        inspection=ins,
                        root_cause=request.POST.get('root_cause','').strip(),
                        corrective_action=request.POST.get('corrective_action','').strip(),
                        preventive_action=request.POST.get('preventive_action','').strip(),
                        responsible_user=User.objects.filter(pk=request.POST.get('responsible_user_id')).first(),
                        due_at=request.POST.get('due_at') or None,
                        status='OPEN'
                    )
                    messages.success(request,'Final QC CAPA opened.')

                elif action=='release':
                    release=get_object_or_404(FinalQCRelease,pk=request.POST.get('release_id'))
                    decision=request.POST.get('final_decision','PENDING')
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if decision=='READY_TO_SHIP' and release.system_decision!='READY_TO_SHIP':
                        raise PermissionError(f'Ready-for-Shipment blocked. System decision is {release.system_decision}.')
                    if decision=='CONDITIONAL_RELEASE' and (not approval or approval.status!='APPROVED'):
                        raise PermissionError('Conditional Final QC release requires APPROVED senior authorization.')
                    release.final_decision=decision
                    release.approval=approval
                    release.decision_reason=request.POST.get('decision_reason','').strip()
                    if decision in {'READY_TO_SHIP','CONDITIONAL_RELEASE'}:
                        release.pre_shipment_signoff_by=request.user
                        release.pre_shipment_signoff_at=timezone.now()
                        release.released_at=timezone.now()
                    release.save()
                    if release.released:
                        order=release.plan.order
                        order.status='READY_TO_SHIP'
                        order.save(update_fields=['status','updated_at'])
                        if hasattr(order,'delivery_sla'):
                            order.delivery_sla.status='PACKED'
                            order.delivery_sla.save(update_fields=['status','updated_at'])
                    messages.success(request,f'Final QC release decision saved: {decision}.')

                elif action=='generate_report':
                    slot=request.POST.get('slot')
                    if slot not in {'08:00','13:00','20:00'}:
                        raise ValueError('Invalid report slot.')
                    payload=_final_qc_auto_report_payload(today)
                    FinalQCAutoReport.objects.update_or_create(
                        report_date=today,slot=slot,
                        defaults={
                            'summary':payload,
                            'outstanding_alerts':Alert.objects.filter(actioned=False).count(),
                            'pending_actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),
                            'escalated_items':Alert.objects.filter(actioned=False,level='RED').count()
                        }
                    )
                    messages.success(request,f'{slot} Final QC automatic report generated.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('final_qc_dashboard')

    plans=FinalQCPlan.objects.select_related('order','department').order_by('-created_at')
    inspections=FinalQCInspection.objects.select_related('plan','bundle','inspector').order_by('-completed_at')
    releases=FinalQCRelease.objects.select_related('plan__order','latest_inspection','approval').order_by('-updated_at')
    payload=_final_qc_auto_report_payload(today)

    ctx={
        'today':today,'plans':plans[:150],'inspections':inspections[:150],'releases':releases[:150],
        'orders':MasterOrder.objects.order_by('-created_at')[:300],
        'bundles':CuttingBundle.objects.order_by('-created_at')[:500],
        'departments':Department.objects.all().order_by('name'),
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-created_at')[:300],
        'users':User.objects.filter(is_active=True).order_by('username')[:500],
        'scans':FinalQCUnitScan.objects.select_related('plan','bundle').order_by('-scanned_at')[:100],
        'defects':FinalQCDefect.objects.select_related('inspection__plan','responsible_user').order_by('-created_at')[:100],
        'capas':FinalQCCAPA.objects.select_related('inspection__plan','responsible_user').order_by('-created_at')[:100],
        'reports':FinalQCAutoReport.objects.filter(report_date=today).order_by('slot'),
        'payload':payload,
        'alerts':Alert.objects.filter(actioned=False).order_by('-created_at')[:12],
        'defect_categories':[x[0] for x in FinalQCDefect.CATEGORIES],
    }
    return render(request,'final_qc_dashboard.html',ctx)

@login_required
def final_qc_report_csv(request):
    import csv
    today=timezone.localdate()
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']=f'attachment; filename="final-qc-report-{today}.csv"'
    w=csv.writer(response)
    w.writerow([
        'Date','Plan','Order','Buyer','Style','Bundle','AQL','Inspected','Pass',
        'Critical','Major','Minor','Measurement Fail','Appearance Fail','Workmanship Fail',
        'Label Fail','Barcode Fail','Poly Fail','Packing Fail','Carton Fail','Qty Fail',
        'Rework','Reject','DHU','Result','Buyer Inspection Result'
    ])
    for x in FinalQCInspection.objects.select_related('plan__order','bundle').filter(completed_at__date=today):
        w.writerow([
            x.completed_at,x.plan.plan_no,x.plan.order.master_order_id,x.plan.buyer,x.plan.style_no,
            x.bundle.bundle_no if x.bundle else '',x.plan.aql_level,x.inspected_qty,x.pass_qty,
            x.critical_defects,x.major_defects,x.minor_defects,x.measurement_fail_qty,
            x.appearance_fail_qty,x.workmanship_fail_qty,x.label_fail_qty,x.barcode_fail_qty,
            x.poly_fail_qty,x.packing_fail_qty,x.carton_marking_fail_qty,x.quantity_fail_qty,
            x.rework_qty,x.reject_qty,x.dhu,x.result,x.buyer_inspection_result
        ])
    return response

@login_required
def api_final_qc_dashboard(request):
    return JsonResponse(_final_qc_auto_report_payload(timezone.localdate()))

def _finishing_payload(work_date):
    from django.db.models import Sum
    p=FinishingProduction.objects.filter(work_date=work_date); q=FinishingQC.objects.filter(checked_at__date=work_date); s=FinishingScan.objects.filter(scanned_at__date=work_date)
    target=p.aggregate(v=Sum("target_qty"))["v"] or 0; actual=p.aggregate(v=Sum("actual_qty"))["v"] or 0
    return {"plans":FinishingPlan.objects.count(),"target":target,"actual":actual,"wip":p.aggregate(v=Sum("wip_qty"))["v"] or 0,"efficiency":round(actual/target*100,2) if target else 0,"pass_qty":q.aggregate(v=Sum("pass_qty"))["v"] or 0,"rework_qty":q.aggregate(v=Sum("rework_qty"))["v"] or 0,"reject_qty":q.aggregate(v=Sum("reject_qty"))["v"] or 0,"in_scans":s.filter(direction="IN",scan_status="VALID").count(),"out_scans":s.filter(direction="OUT",scan_status="VALID").count(),"blocked":s.exclude(scan_status="VALID").count(),"total_cost":str(p.aggregate(v=Sum("total_cost"))["v"] or 0)}

@login_required
@require_http_methods(["GET","POST"])
def finishing_dashboard(request):
    from django.contrib import messages
    from decimal import Decimal
    today=timezone.localdate()
    if request.method=="POST":
        a=request.POST.get("action","")
        try:
            if a=="plan":
                o=get_object_or_404(MasterOrder,pk=request.POST.get("order_id")); ap=ApprovalRequest.objects.filter(pk=request.POST.get("approval_id")).first()
                FinishingPlan.objects.create(plan_no=request.POST.get("plan_no","").strip(),order=o,planned_qty=int(request.POST.get("planned_qty") or 0),target_date=request.POST.get("target_date") or None,status="APPROVED" if ap and ap.status=="APPROVED" else "PENDING_APPROVAL",approval=ap,instruction_file=request.FILES.get("instruction_file") or None,created_by=request.user)
            elif a=="scan":
                p=get_object_or_404(FinishingPlan,pk=request.POST.get("plan_id")); b=CuttingBundle.objects.filter(pk=request.POST.get("bundle_id")).first(); d=request.POST.get("direction","IN"); bc=request.POST.get("barcode","").strip()
                exp=b.quantity if b else int(request.POST.get("expected_qty") or 0); act=int(request.POST.get("actual_qty") or exp); st="VALID"
                if b and bc and bc!=b.barcode:st="MISMATCH"
                if b and FinishingScan.objects.filter(plan=p,bundle=b,direction=d,scan_status="VALID").exists():st="DUPLICATE"
                if act!=exp:st="MISMATCH"
                if d=="OUT":
                    if b and not FinishingScan.objects.filter(plan=p,bundle=b,direction="IN",scan_status="VALID").exists():st="BLOCKED"
                    if not FinishingQC.objects.filter(plan=p,bundle=b,result="PASS").exists():st="QC_HOLD"
                FinishingScan.objects.create(plan=p,bundle=b,direction=d,barcode=bc,expected_qty=exp,actual_qty=act,scan_status=st,scanned_by=request.user)
                if st!="VALID":Alert.objects.create(level="RED",title="Finishing Movement Blocked",message=f"{p.plan_no}: {st}",reference=p.plan_no,actioned=False);raise PermissionError(st)
            elif a=="production":
                p=get_object_or_404(FinishingPlan,pk=request.POST.get("plan_id")); b=CuttingBundle.objects.filter(pk=request.POST.get("bundle_id")).first()
                if b and not FinishingScan.objects.filter(plan=p,bundle=b,direction="IN",scan_status="VALID").exists():raise PermissionError("FINISHING IN SCAN required.")
                manual=request.POST.get("manual_entry")=="on"; ap=ApprovalRequest.objects.filter(pk=request.POST.get("approval_id")).first()
                if manual and (not ap or ap.status!="APPROVED"):raise PermissionError("Manual entry requires senior approval.")
                n=lambda k:int(request.POST.get(k) or 0); m=lambda k:Decimal(request.POST.get(k) or 0)
                FinishingProduction.objects.create(plan=p,bundle=b,work_date=request.POST.get("work_date") or today,operator=Employee.objects.filter(pk=request.POST.get("operator_id")).first(),helper=Employee.objects.filter(pk=request.POST.get("helper_id")).first(),machine=AssetMachine.objects.filter(pk=request.POST.get("machine_id")).first(),target_qty=n("target_qty"),actual_qty=n("actual_qty"),wip_qty=n("wip_qty"),process_minutes=n("process_minutes"),npt_minutes=n("npt_minutes"),downtime_minutes=n("downtime_minutes"),trimmed_qty=n("trimmed_qty"),stain_checked_qty=n("stain_checked_qty"),cleaned_qty=n("cleaned_qty"),measurement_checked_qty=n("measurement_checked_qty"),accessory_checked_qty=n("accessory_checked_qty"),appearance_checked_qty=n("appearance_checked_qty"),label_checked_qty=n("label_checked_qty"),folding_ready_qty=n("folding_ready_qty"),labour_cost=m("labour_cost"),process_cost=m("process_cost"),utility_cost=m("utility_cost"),rework_cost=m("rework_cost"),manual_entry=manual,approval=ap)
            elif a=="qc":
                p=get_object_or_404(FinishingPlan,pk=request.POST.get("plan_id")); b=CuttingBundle.objects.filter(pk=request.POST.get("bundle_id")).first(); r=request.POST.get("result","HOLD"); dq=int(request.POST.get("defect_qty") or 0)
                if dq and r=="PASS":r="HOLD"
                FinishingQC.objects.create(plan=p,bundle=b,inspected_qty=int(request.POST.get("inspected_qty") or 0),pass_qty=int(request.POST.get("pass_qty") or 0),rework_qty=int(request.POST.get("rework_qty") or 0),reject_qty=int(request.POST.get("reject_qty") or 0),defect_type=request.POST.get("defect_type",""),defect_qty=dq,comments=request.POST.get("comments",""),result=r,checked_by=request.user,qc_photo=request.FILES.get("qc_photo") or None)
                if r!="PASS":Alert.objects.create(level="RED" if r=="REJECT" else "WARNING",title="Finishing QC Hold",message=f"{p.plan_no}: {r}",reference=p.plan_no,actioned=False)
            elif a=="generate_report":
                slot=request.POST.get("slot")
                if slot not in {"08:00","13:00","20:00"}:raise ValueError("Invalid report slot.")
                payload=_finishing_payload(today);FinishingAutoReport.objects.update_or_create(report_date=today,slot=slot,defaults={"summary":payload,"outstanding_alerts":Alert.objects.filter(actioned=False).count(),"pending_actions":ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),"escalated_items":Alert.objects.filter(actioned=False,level="RED").count()})
            messages.success(request,"Finishing action completed.")
        except Exception as e:messages.error(request,str(e))
        return redirect("finishing_dashboard")
    return render(request,"finishing_dashboard.html",{"today":today,"payload":_finishing_payload(today),"plans":FinishingPlan.objects.select_related("order").order_by("-created_at")[:150],"orders":MasterOrder.objects.order_by("-created_at")[:300],"bundles":CuttingBundle.objects.order_by("-created_at")[:500],"employees":Employee.objects.filter(status="ACTIVE")[:500],"machines":AssetMachine.objects.filter(asset_type__in=["MACHINE","EQUIPMENT"])[:500],"approvals":ApprovalRequest.objects.filter(status="APPROVED").order_by("-created_at")[:300],"production":FinishingProduction.objects.select_related("plan","bundle").filter(work_date=today)[:100],"qc_rows":FinishingQC.objects.select_related("plan","bundle").order_by("-checked_at")[:100],"reports":FinishingAutoReport.objects.filter(report_date=today).order_by("slot"),"defect_types":[x[0] for x in FinishingQC.DEFECTS],"production_fields":FINISHING_PRODUCTION_FIELDS})

@login_required
def finishing_report_csv(request):
    import csv
    today=timezone.localdate();response=HttpResponse(content_type="text/csv");response["Content-Disposition"]=f'attachment; filename="finishing-report-{today}.csv"';w=csv.writer(response)
    w.writerow(["Date","Plan","Order","Bundle","Target","Actual","WIP","Process Minutes","NPT","Downtime","Total Cost"])
    for x in FinishingProduction.objects.select_related("plan__order","bundle").filter(work_date=today):w.writerow([x.work_date,x.plan.plan_no,x.plan.order.master_order_id,x.bundle.bundle_no if x.bundle else "",x.target_qty,x.actual_qty,x.wip_qty,x.process_minutes,x.npt_minutes,x.downtime_minutes,x.total_cost])
    return response

@login_required
def api_finishing_dashboard(request):return JsonResponse(_finishing_payload(timezone.localdate()))


def _packing_payload(work_date):
    from django.db.models import Sum
    prod=PackingProduction.objects.filter(work_date=work_date)
    qc=PackingQC.objects.filter(checked_at__date=work_date)
    scans=PackingScan.objects.filter(scanned_at__date=work_date)
    cartons=PackingCarton.objects.filter(created_at__date=work_date)
    target=prod.aggregate(v=Sum('target_qty'))['v'] or 0
    actual=prod.aggregate(v=Sum('actual_qty'))['v'] or 0
    return {
        'plans':PackingPlan.objects.count(),
        'target':target,'actual':actual,
        'wip':prod.aggregate(v=Sum('wip_qty'))['v'] or 0,
        'efficiency':round(actual/target*100,2) if target else 0,
        'cartons':cartons.count(),
        'packed_qty':cartons.aggregate(v=Sum('packed_qty'))['v'] or 0,
        'gross_weight':str(cartons.aggregate(v=Sum('gross_weight_kg'))['v'] or 0),
        'net_weight':str(cartons.aggregate(v=Sum('net_weight_kg'))['v'] or 0),
        'cbm':str(cartons.aggregate(v=Sum('cbm'))['v'] or 0),
        'pass_qty':qc.aggregate(v=Sum('pass_qty'))['v'] or 0,
        'rework_qty':qc.aggregate(v=Sum('rework_qty'))['v'] or 0,
        'reject_qty':qc.aggregate(v=Sum('reject_qty'))['v'] or 0,
        'in_scans':scans.filter(direction='IN',scan_status='VALID').count(),
        'out_scans':scans.filter(direction='OUT',scan_status='VALID').count(),
        'blocked':scans.exclude(scan_status='VALID').count(),
        'total_cost':str(prod.aggregate(v=Sum('total_cost'))['v'] or 0),
    }

@login_required
@require_http_methods(['GET','POST'])
def packing_dashboard(request):
    from django.contrib import messages
    from decimal import Decimal
    import json as pyjson
    today=timezone.localdate()

    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='plan':
                    order=get_object_or_404(MasterOrder,pk=request.POST.get('order_id'))
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    PackingPlan.objects.create(
                        plan_no=request.POST.get('plan_no','').strip(),order=order,
                        department=Department.objects.filter(pk=request.POST.get('department_id')).first(),
                        buyer=request.POST.get('buyer','').strip() or order.buyer,
                        style_no=request.POST.get('style_no','').strip(),
                        product=request.POST.get('product','').strip() or order.product,
                        colour=request.POST.get('colour','').strip(),
                        size_range=request.POST.get('size_range','').strip(),
                        packing_ratio=request.POST.get('packing_ratio','').strip(),
                        carton_marking=request.POST.get('carton_marking','').strip(),
                        planned_qty=int(request.POST.get('planned_qty') or 0),
                        target_date=request.POST.get('target_date') or None,
                        status='APPROVED' if approval and approval.status=='APPROVED' else 'PENDING_APPROVAL',
                        approval=approval,packing_spec_file=request.FILES.get('packing_spec_file') or None,
                        created_by=request.user
                    )
                    messages.success(request,'Packing plan created.')

                elif action=='scan':
                    plan=get_object_or_404(PackingPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    direction=request.POST.get('direction','IN')
                    barcode=request.POST.get('barcode','').strip()
                    expected=bundle.quantity if bundle else int(request.POST.get('expected_qty') or 0)
                    actual=int(request.POST.get('actual_qty') or expected)
                    status='VALID'

                    if direction=='IN':
                        final_release=FinalQCRelease.objects.filter(plan__order=plan.order).select_related('approval').order_by('-updated_at').first()
                        if not final_release or not final_release.released:
                            status='FINAL_QC_HOLD'
                    if bundle and barcode and barcode!=bundle.barcode:
                        status='MISMATCH'
                    if bundle and PackingScan.objects.filter(plan=plan,bundle=bundle,direction=direction,scan_status='VALID').exists():
                        status='DUPLICATE'
                    if actual!=expected:
                        status='MISMATCH'
                    if direction=='OUT':
                        if bundle and not PackingScan.objects.filter(plan=plan,bundle=bundle,direction='IN',scan_status='VALID').exists():
                            status='BLOCKED'
                        if not PackingQC.objects.filter(plan=plan,result='PASS').exists():
                            status='PACKING_QC_HOLD'

                    PackingScan.objects.create(plan=plan,bundle=bundle,direction=direction,barcode=barcode,
                        expected_qty=expected,actual_qty=actual,scan_status=status,scanned_by=request.user)

                    if status!='VALID':
                        Alert.objects.create(level='RED',title='Packing Movement Blocked',
                            message=f'{plan.plan_no}: {status} during {direction} scan.',
                            reference=plan.plan_no,actioned=False)
                        raise PermissionError(f'Packing movement blocked: {status}.')
                    messages.success(request,f'PACKING {direction} SCAN accepted.')

                elif action=='carton':
                    plan=get_object_or_404(PackingPlan,pk=request.POST.get('plan_id'))
                    barcode=request.POST.get('barcode','').strip()
                    if not barcode:
                        raise PermissionError('Carton barcode/QR is mandatory.')
                    if PackingCarton.objects.filter(barcode=barcode).exists():
                        raise PermissionError('Duplicate carton barcode/QR blocked.')
                    matrix={}
                    raw=request.POST.get('size_colour_matrix','').strip()
                    if raw:
                        try: matrix=pyjson.loads(raw)
                        except Exception: matrix={'text':raw}
                    carton=PackingCarton.objects.create(
                        plan=plan,carton_no=request.POST.get('carton_no','').strip(),
                        barcode=barcode,size_colour_matrix=matrix,
                        packed_qty=int(request.POST.get('packed_qty') or 0),
                        gross_weight_kg=Decimal(request.POST.get('gross_weight_kg') or 0),
                        net_weight_kg=Decimal(request.POST.get('net_weight_kg') or 0),
                        length_cm=Decimal(request.POST.get('length_cm') or 0),
                        width_cm=Decimal(request.POST.get('width_cm') or 0),
                        height_cm=Decimal(request.POST.get('height_cm') or 0),
                        carton_marking=request.POST.get('carton_marking','').strip(),
                        sealed=request.POST.get('sealed')=='on',
                        seal_no=request.POST.get('seal_no','').strip(),
                        created_by=request.user
                    )
                    if plan.carton_marking and carton.carton_marking and carton.carton_marking!=plan.carton_marking:
                        Alert.objects.create(level='RED',title='Wrong Carton Marking',
                            message=f'{plan.plan_no}: carton {carton.carton_no} marking mismatch.',
                            reference=carton.carton_no,actioned=False)
                    messages.success(request,'Carton created with barcode/QR and CBM.')

                elif action=='production':
                    plan=get_object_or_404(PackingPlan,pk=request.POST.get('plan_id'))
                    bundle=CuttingBundle.objects.filter(pk=request.POST.get('bundle_id')).first()
                    carton=PackingCarton.objects.filter(pk=request.POST.get('carton_id')).first()
                    if bundle and not PackingScan.objects.filter(plan=plan,bundle=bundle,direction='IN',scan_status='VALID').exists():
                        raise PermissionError('PACKING IN SCAN required before production.')
                    manual=request.POST.get('manual_entry')=='on'
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if manual and (not approval or approval.status!='APPROVED'):
                        raise PermissionError('Manual packing entry requires senior APPROVED authorization.')
                    money=lambda k: Decimal(request.POST.get(k) or 0)
                    PackingProduction.objects.create(
                        plan=plan,bundle=bundle,carton=carton,
                        work_date=request.POST.get('work_date') or today,
                        employee=Employee.objects.filter(pk=request.POST.get('employee_id')).first(),
                        target_qty=int(request.POST.get('target_qty') or 0),
                        actual_qty=int(request.POST.get('actual_qty') or 0),
                        wip_qty=int(request.POST.get('wip_qty') or 0),
                        process_minutes=int(request.POST.get('process_minutes') or 0),
                        npt_minutes=int(request.POST.get('npt_minutes') or 0),
                        carton_cost=money('carton_cost'),poly_cost=money('poly_cost'),
                        label_sticker_cost=money('label_sticker_cost'),
                        hanger_tissue_accessory_cost=money('hanger_tissue_accessory_cost'),
                        labour_cost=money('labour_cost'),process_cost=money('process_cost'),
                        utility_cost=money('utility_cost'),rework_wastage_cost=money('rework_wastage_cost'),
                        manual_entry=manual,approval=approval
                    )
                    messages.success(request,'Packing production/cost saved.')

                elif action=='qc':
                    plan=get_object_or_404(PackingPlan,pk=request.POST.get('plan_id'))
                    carton=PackingCarton.objects.filter(pk=request.POST.get('carton_id')).first()
                    result=request.POST.get('result','HOLD')
                    defect=request.POST.get('defect_type','')
                    defect_qty=int(request.POST.get('defect_qty') or 0)
                    if defect_qty and result=='PASS':
                        result='HOLD'
                    if defect in {'WRONG_ORDER_STYLE','WRONG_SIZE_COLOUR','WRONG_CARTON_MARKING','WRONG_ASSORTMENT','QUANTITY_MISMATCH'}:
                        if result=='PASS': result='HOLD'
                    PackingQC.objects.create(
                        plan=plan,carton=carton,
                        inspected_qty=int(request.POST.get('inspected_qty') or 0),
                        pass_qty=int(request.POST.get('pass_qty') or 0),
                        rework_qty=int(request.POST.get('rework_qty') or 0),
                        reject_qty=int(request.POST.get('reject_qty') or 0),
                        defect_type=defect,defect_qty=defect_qty,
                        comments=request.POST.get('comments','').strip(),
                        result=result,checked_by=request.user,
                        qc_photo=request.FILES.get('qc_photo') or None
                    )
                    if result!='PASS':
                        Alert.objects.create(level='RED' if result=='REJECT' else 'WARNING',
                            title='Packing QC Hold',message=f'{plan.plan_no}: {result} / {defect}.',
                            reference=plan.plan_no,actioned=False)
                    messages.success(request,f'Packing QC recorded: {result}.')

                elif action=='generate_report':
                    slot=request.POST.get('slot')
                    if slot not in {'08:00','13:00','20:00'}:
                        raise ValueError('Invalid report slot.')
                    payload=_packing_payload(today)
                    PackingAutoReport.objects.update_or_create(
                        report_date=today,slot=slot,
                        defaults={'summary':payload,
                                  'outstanding_alerts':Alert.objects.filter(actioned=False).count(),
                                  'pending_actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),
                                  'escalated_items':Alert.objects.filter(actioned=False,level='RED').count()}
                    )
                    messages.success(request,f'{slot} Packing automatic report generated.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('packing_dashboard')

    plans=PackingPlan.objects.select_related('order').order_by('-created_at')
    ctx={
        'today':today,'payload':_packing_payload(today),'plans':plans[:150],
        'orders':MasterOrder.objects.order_by('-created_at')[:300],
        'bundles':CuttingBundle.objects.order_by('-created_at')[:500],
        'employees':Employee.objects.filter(status='ACTIVE')[:500],
        'departments':Department.objects.all().order_by('name'),
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-created_at')[:300],
        'cartons':PackingCarton.objects.select_related('plan').order_by('-created_at')[:200],
        'production':PackingProduction.objects.select_related('plan','bundle','carton','employee').filter(work_date=today)[:100],
        'qc_rows':PackingQC.objects.select_related('plan','carton').order_by('-checked_at')[:100],
        'reports':PackingAutoReport.objects.filter(report_date=today).order_by('slot'),
        'defect_types':[x[0] for x in PackingQC.DEFECTS],
    }
    ctx['cost_fields']=PACKING_COST_FIELDS
    return render(request,'packing_dashboard.html',ctx)

@login_required
def packing_report_csv(request):
    import csv
    today=timezone.localdate()
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']=f'attachment; filename="packing-report-{today}.csv"'
    w=csv.writer(response)
    w.writerow(['Date','Plan','Order','Bundle','Carton','Carton Barcode','Target','Actual','WIP','Minutes','NPT','Total Cost'])
    for x in PackingProduction.objects.select_related('plan__order','bundle','carton').filter(work_date=today):
        w.writerow([x.work_date,x.plan.plan_no,x.plan.order.master_order_id,
                    x.bundle.bundle_no if x.bundle else '',
                    x.carton.carton_no if x.carton else '',
                    x.carton.barcode if x.carton else '',
                    x.target_qty,x.actual_qty,x.wip_qty,x.process_minutes,x.npt_minutes,x.total_cost])
    return response

@login_required
def api_packing_dashboard(request):
    return JsonResponse(_packing_payload(timezone.localdate()))


def _shipping_payload(work_date):
    from django.db.models import Sum
    from django.utils import timezone
    plans=ShippingPlan.objects.all()
    scans=ShippingCartonScan.objects.filter(scanned_at__date=work_date)
    costs=ShippingCost.objects.filter(plan__in=plans)
    today=timezone.localdate()
    at_risk=0; overdue=0
    for p in plans.select_related('order'):
        sla=BuyerDeliverySLA.objects.filter(order=p.order).first()
        if sla and sla.delivery_deadline:
            days=(sla.delivery_deadline-today).days
            if days < 0 and sla.status not in {'DELIVERED','DELIVERY_CONFIRMED','EXCEPTION_APPROVED'}:
                overdue+=1
            elif days <= 3 and sla.status not in {'DELIVERED','DELIVERY_CONFIRMED'}:
                at_risk+=1
    return {
        'plans':plans.count(),
        'ready':plans.filter(status__in=['APPROVED','READY','BOOKED']).count(),
        'loading':plans.filter(status='LOADING').count(),
        'dispatched':plans.filter(status='DISPATCHED').count(),
        'in_transit':plans.filter(status='IN_TRANSIT').count(),
        'delivered':plans.filter(status__in=['DELIVERED','CLOSED']).count(),
        'shipping_in':scans.filter(scan_type='SHIPPING_IN',scan_status='VALID').count(),
        'loaded':scans.filter(scan_type='LOADING',scan_status='VALID').count(),
        'gate_out':scans.filter(scan_type='GATE_OUT',scan_status='VALID').count(),
        'blocked':scans.exclude(scan_status='VALID').count(),
        'planned_cartons':plans.aggregate(v=Sum('planned_cartons'))['v'] or 0,
        'planned_pieces':plans.aggregate(v=Sum('planned_pieces'))['v'] or 0,
        'gross_weight':str(plans.aggregate(v=Sum('gross_weight_kg'))['v'] or 0),
        'cbm':str(plans.aggregate(v=Sum('total_cbm'))['v'] or 0),
        'total_shipping_cost':str(costs.aggregate(v=Sum('total_cost'))['v'] or 0),
        'at_risk':at_risk,'overdue':overdue,
    }

def _shipping_sla_info(plan):
    from django.utils import timezone
    from datetime import timedelta
    sla=BuyerDeliverySLA.objects.filter(order=plan.order).first()
    if not sla:
        confirmed=plan.order.confirmed_at or timezone.now()
        deadline=(confirmed+timedelta(days=15)).date()
        sla=BuyerDeliverySLA.objects.create(
            order=plan.order,buyer_name=plan.order.buyer,confirmed_at=confirmed,
            max_delivery_days=15,delivery_deadline=deadline,status='PACKED')
    elif not sla.delivery_deadline:
        sla.delivery_deadline=(sla.confirmed_at+timedelta(days=sla.max_delivery_days or 15)).date()
        sla.save(update_fields=['delivery_deadline','updated_at'])
    today=timezone.localdate()
    days_remaining=(sla.delivery_deadline-today).days if sla.delivery_deadline else None
    return sla,days_remaining

@login_required
@require_http_methods(['GET','POST'])
def shipping_dashboard(request):
    from django.contrib import messages
    from decimal import Decimal
    from django.db.models import Sum
    from django.utils import timezone
    today=timezone.localdate()

    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='plan':
                    order=get_object_or_404(MasterOrder,pk=request.POST.get('order_id'))
                    packing_pass=PackingQC.objects.filter(plan__order=order,result='PASS').exists()
                    if not packing_pass:
                        raise PermissionError('Shipping plan blocked: Packing QC PASS is required.')
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    sla=BuyerDeliverySLA.objects.filter(order=order).first()
                    address=request.POST.get('delivery_address','').strip()
                    country=request.POST.get('country','').strip()
                    if sla:
                        if not address:
                            address=', '.join([x for x in [sla.street,sla.city,sla.state,sla.postal_code,sla.country] if x])
                        if not country: country=sla.country
                    cartons=PackingCarton.objects.filter(plan__order=order)
                    ShippingPlan.objects.create(
                        plan_no=request.POST.get('plan_no','').strip(),order=order,
                        department=Department.objects.filter(pk=request.POST.get('department_id')).first(),
                        buyer=request.POST.get('buyer','').strip() or order.buyer,
                        consignee=request.POST.get('consignee','').strip(),
                        delivery_address=address,country=country,
                        incoterm=request.POST.get('incoterm','').strip(),
                        shipment_mode=request.POST.get('shipment_mode','ROAD'),
                        forwarder=request.POST.get('forwarder','').strip(),
                        carrier=request.POST.get('carrier','').strip(),
                        booking_no=request.POST.get('booking_no','').strip(),
                        awb_bl_cmr_tracking_no=request.POST.get('awb_bl_cmr_tracking_no','').strip(),
                        container_no=request.POST.get('container_no','').strip(),
                        seal_no=request.POST.get('seal_no','').strip(),
                        vehicle_no=request.POST.get('vehicle_no','').strip(),
                        driver_name=request.POST.get('driver_name','').strip(),
                        driver_phone=request.POST.get('driver_phone','').strip(),
                        planned_cartons=int(request.POST.get('planned_cartons') or cartons.count()),
                        planned_pieces=int(request.POST.get('planned_pieces') or order.quantity),
                        gross_weight_kg=Decimal(request.POST.get('gross_weight_kg') or (cartons.aggregate(v=Sum('gross_weight_kg'))['v'] or 0)),
                        net_weight_kg=Decimal(request.POST.get('net_weight_kg') or (cartons.aggregate(v=Sum('net_weight_kg'))['v'] or 0)),
                        total_cbm=Decimal(request.POST.get('total_cbm') or (cartons.aggregate(v=Sum('cbm'))['v'] or 0)),
                        etd=request.POST.get('etd') or None,eta=request.POST.get('eta') or None,
                        status='APPROVED' if approval and approval.status=='APPROVED' else 'PENDING_APPROVAL',
                        approval=approval,shipping_instruction=request.FILES.get('shipping_instruction') or None,
                        created_by=request.user
                    )
                    messages.success(request,'Shipping plan created.')

                elif action=='scan':
                    plan=get_object_or_404(ShippingPlan,pk=request.POST.get('plan_id'))
                    carton=get_object_or_404(PackingCarton,pk=request.POST.get('carton_id'))
                    scan_type=request.POST.get('scan_type','SHIPPING_IN')
                    barcode=request.POST.get('barcode','').strip()
                    actual_qty=int(request.POST.get('actual_qty') or carton.packed_qty)
                    status='VALID'

                    if carton.plan.order_id != plan.order_id:
                        status='MISMATCH'
                    if barcode != carton.barcode:
                        status='MISMATCH'
                    if ShippingCartonScan.objects.filter(plan=plan,carton=carton,scan_type=scan_type,scan_status='VALID').exists():
                        status='DUPLICATE'
                    if actual_qty != carton.packed_qty:
                        status='MISMATCH'
                    if scan_type=='SHIPPING_IN':
                        if not PackingQC.objects.filter(plan=carton.plan,carton=carton,result='PASS').exists():
                            status='PACKING_QC_HOLD'
                    elif scan_type=='CARTON_VERIFY':
                        if not ShippingCartonScan.objects.filter(plan=plan,carton=carton,scan_type='SHIPPING_IN',scan_status='VALID').exists():
                            status='BLOCKED'
                    elif scan_type=='LOADING':
                        if not ShippingCartonScan.objects.filter(plan=plan,carton=carton,scan_type='SHIPPING_IN',scan_status='VALID').exists():
                            status='BLOCKED'
                        required={'COMMERCIAL_INVOICE','PACKING_LIST','SHIPPING_INSTRUCTION'}
                        verified=set(ShippingDocument.objects.filter(plan=plan,verified=True).values_list('document_type',flat=True))
                        if not required.issubset(verified):
                            status='APPROVAL_HOLD'
                    elif scan_type=='GATE_OUT':
                        if not ShippingCartonScan.objects.filter(plan=plan,carton=carton,scan_type='LOADING',scan_status='VALID').exists():
                            status='BLOCKED'
                        if not plan.approval_id or plan.approval.status!='APPROVED':
                            status='APPROVAL_HOLD'
                        actual_seal=request.POST.get('actual_seal_no','').strip()
                        if plan.seal_no and actual_seal != plan.seal_no:
                            status='MISMATCH'
                    elif scan_type=='DELIVERY':
                        if not ShippingCartonScan.objects.filter(plan=plan,carton=carton,scan_type='GATE_OUT',scan_status='VALID').exists():
                            status='BLOCKED'

                    ShippingCartonScan.objects.create(
                        plan=plan,carton=carton,scan_type=scan_type,barcode=barcode,
                        expected_qty=carton.packed_qty,actual_qty=actual_qty,
                        expected_seal_no=plan.seal_no,
                        actual_seal_no=request.POST.get('actual_seal_no','').strip(),
                        scan_status=status,scanned_by=request.user
                    )
                    if status!='VALID':
                        Alert.objects.create(level='RED',title='Shipping Scan Blocked',
                            message=f'{plan.plan_no}: {scan_type} / {status} / carton {carton.carton_no}.',
                            reference=plan.plan_no,actioned=False)
                        raise PermissionError(f'Shipping scan blocked: {status}.')

                    if scan_type=='LOADING': plan.status='LOADING'
                    if scan_type=='GATE_OUT':
                        plan.status='DISPATCHED'; plan.actual_dispatch_at=timezone.now()
                        order=plan.order; order.status='SHIPPED'; order.save(update_fields=['status','updated_at'])
                        sla,_=_shipping_sla_info(plan)
                        sla.status='DISPATCHED'; sla.actual_dispatch_at=timezone.now()
                        sla.courier=plan.carrier or plan.forwarder; sla.tracking_number=plan.awb_bl_cmr_tracking_no
                        sla.save(update_fields=['status','actual_dispatch_at','courier','tracking_number','updated_at'])
                    plan.save()
                    messages.success(request,f'{scan_type} accepted for carton {carton.carton_no}.')

                elif action=='document':
                    plan=get_object_or_404(ShippingPlan,pk=request.POST.get('plan_id'))
                    doc=ShippingDocument.objects.create(
                        plan=plan,document_type=request.POST.get('document_type','OTHER'),
                        document_no=request.POST.get('document_no','').strip(),
                        file=request.FILES.get('file'),
                        verified=request.POST.get('verified')=='on',
                        verified_by=request.user if request.POST.get('verified')=='on' else None,
                        verified_at=timezone.now() if request.POST.get('verified')=='on' else None,
                        uploaded_by=request.user
                    )
                    messages.success(request,f'Shipping document saved: {doc.document_type}.')

                elif action=='cost':
                    plan=get_object_or_404(ShippingPlan,pk=request.POST.get('plan_id'))
                    approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    money=lambda k: Decimal(request.POST.get(k) or 0)
                    ShippingCost.objects.update_or_create(
                        plan=plan,defaults={
                            'freight':money('freight'),'forwarder':money('forwarder'),
                            'customs':money('customs'),'port_airport':money('port_airport'),
                            'truck_vehicle':money('truck_vehicle'),'loading':money('loading'),
                            'documentation':money('documentation'),'insurance':money('insurance'),
                            'duty_tax':money('duty_tax'),'handling':money('handling'),
                            'demurrage_detention':money('demurrage_detention'),'courier':money('courier'),
                            'other_approved':money('other_approved'),'approval':approval,'updated_by':request.user
                        })
                    cost=ShippingCost.objects.get(plan=plan)
                    sla,_=_shipping_sla_info(plan)
                    sla.shipping_cost=cost.total_cost; sla.save(update_fields=['shipping_cost','updated_at'])
                    messages.success(request,'Shipping cost updated.')

                elif action=='status':
                    plan=get_object_or_404(ShippingPlan,pk=request.POST.get('plan_id'))
                    status=request.POST.get('status')
                    if status not in dict(ShippingPlan.STATUS):
                        raise ValueError('Invalid shipping status.')
                    plan.status=status
                    if status=='IN_TRANSIT':
                        sla,_=_shipping_sla_info(plan); sla.status='IN_TRANSIT'; sla.save(update_fields=['status','updated_at'])
                    elif status=='OUT_FOR_DELIVERY':
                        sla,_=_shipping_sla_info(plan); sla.status='OUT_FOR_DELIVERY'; sla.save(update_fields=['status','updated_at'])
                    plan.save(update_fields=['status','updated_at'])
                    messages.success(request,f'Shipment status updated: {status}.')

                elif action=='pod':
                    plan=get_object_or_404(ShippingPlan,pk=request.POST.get('plan_id'))
                    if not ShippingCartonScan.objects.filter(plan=plan,scan_type='GATE_OUT',scan_status='VALID').exists():
                        raise PermissionError('POD blocked: valid GATE OUT scan is required.')
                    pod,_=ShippingPOD.objects.update_or_create(
                        plan=plan,defaults={
                            'receiver_name':request.POST.get('receiver_name','').strip(),
                            'delivered_at':request.POST.get('delivered_at') or timezone.now(),
                            'proof_of_delivery':request.FILES.get('proof_of_delivery') or None,
                            'buyer_signature':request.FILES.get('buyer_signature') or None,
                            'delivery_photo':request.FILES.get('delivery_photo') or None,
                            'courier_confirmation':request.POST.get('courier_confirmation','').strip(),
                            'gps_location':request.POST.get('gps_location','').strip(),
                            'confirmed_by':request.user
                        })
                    plan.status='DELIVERED'; plan.actual_delivery_at=pod.delivered_at; plan.save()
                    order=plan.order; order.status='DELIVERED'; order.save(update_fields=['status','updated_at'])
                    sla,_=_shipping_sla_info(plan)
                    sla.status='DELIVERY_CONFIRMED'; sla.actual_delivery_at=pod.delivered_at
                    sla.receiver_name=pod.receiver_name; sla.proof_of_delivery=pod.proof_of_delivery
                    sla.buyer_signature=pod.buyer_signature; sla.delivery_photo=pod.delivery_photo
                    sla.gps_location=pod.gps_location; sla.courier_confirmation=pod.courier_confirmation
                    sla.confirmed_by=request.user; sla.confirmed_delivery_at=timezone.now()
                    sla.save()
                    messages.success(request,'Proof of Delivery confirmed and order marked DELIVERED.')

                elif action=='generate_report':
                    slot=request.POST.get('slot')
                    if slot not in {'08:00','13:00','20:00'}:
                        raise ValueError('Invalid report slot.')
                    payload=_shipping_payload(today)
                    ShippingAutoReport.objects.update_or_create(
                        report_date=today,slot=slot,
                        defaults={'summary':payload,
                                  'outstanding_alerts':Alert.objects.filter(actioned=False).count(),
                                  'pending_actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),
                                  'escalated_items':Alert.objects.filter(actioned=False,level='RED').count()}
                    )
                    messages.success(request,f'{slot} Shipping automatic report generated.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('shipping_dashboard')

    plans=ShippingPlan.objects.select_related('order','approval').order_by('-created_at')
    sla_rows=[]
    for p in plans[:150]:
        sla,days=_shipping_sla_info(p)
        sla_rows.append({'plan':p,'sla':sla,'days_remaining':days})
    ctx={
        'today':today,'payload':_shipping_payload(today),'plans':plans[:150],'sla_rows':sla_rows,
        'orders':MasterOrder.objects.order_by('-created_at')[:300],
        'cartons':PackingCarton.objects.select_related('plan__order').order_by('-created_at')[:500],
        'departments':Department.objects.all().order_by('name'),
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-created_at')[:300],
        'documents':ShippingDocument.objects.select_related('plan').order_by('-created_at')[:150],
        'scans':ShippingCartonScan.objects.select_related('plan','carton').order_by('-scanned_at')[:150],
        'costs':ShippingCost.objects.select_related('plan').order_by('-updated_at')[:100],
        'pods':ShippingPOD.objects.select_related('plan').order_by('-delivered_at')[:100],
        'reports':ShippingAutoReport.objects.filter(report_date=today).order_by('slot'),
        'doc_types':[x[0] for x in ShippingDocument.DOC_TYPES],
        'shipment_modes':[x[0] for x in ShippingPlan.MODES],
        'statuses':[x[0] for x in ShippingPlan.STATUS],
    }
    ctx['cost_fields']=SHIPPING_COST_FIELDS
    return render(request,'shipping_dashboard.html',ctx)

@login_required
def shipping_report_csv(request):
    import csv
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']=f'attachment; filename="shipping-report-{timezone.localdate()}.csv"'
    w=csv.writer(response)
    w.writerow(['Shipment Plan','Order','Buyer','Consignee','Mode','Carrier','Forwarder','Booking','Tracking/AWB/BL/CMR','Container','Seal','Vehicle','Cartons','Pieces','Gross kg','Net kg','CBM','ETD','ETA','Status','Dispatch','Delivery'])
    for p in ShippingPlan.objects.select_related('order').order_by('-created_at'):
        w.writerow([p.plan_no,p.order.master_order_id,p.buyer,p.consignee,p.shipment_mode,p.carrier,p.forwarder,p.booking_no,
                    p.awb_bl_cmr_tracking_no,p.container_no,p.seal_no,p.vehicle_no,p.planned_cartons,p.planned_pieces,
                    p.gross_weight_kg,p.net_weight_kg,p.total_cbm,p.etd,p.eta,p.status,p.actual_dispatch_at,p.actual_delivery_at])
    return response

@login_required
def api_shipping_dashboard(request):
    return JsonResponse(_shipping_payload(timezone.localdate()))


def _supplier_payload():
    from django.db.models import Sum
    from django.utils import timezone
    suppliers=SupplierMaster.objects.all(); pos=SupplierPurchaseOrder.objects.all(); inv=SupplierInvoice.objects.all()
    expired=SupplierDocument.objects.filter(expiry_date__lt=timezone.localdate()).count()
    return {'suppliers':suppliers.count(),'approved':suppliers.filter(status='APPROVED').count(),'pending':suppliers.filter(status__in=['PENDING_KYC','PENDING_APPROVAL']).count(),
            'hold_blocked':suppliers.filter(status__in=['ON_HOLD','BLOCKED','BLACKLISTED']).count(),'po_count':pos.count(),'po_value':str(pos.aggregate(v=Sum('total_value'))['v'] or 0),
            'pending_invoices':inv.filter(status__in=['PENDING','VERIFIED','APPROVED']).count(),'outstanding':str(inv.exclude(status='PAID').aggregate(v=Sum('amount'))['v'] or 0),'expired_docs':expired}

def _recalc_supplier_performance(supplier):
    from django.db.models import Sum
    from django.utils import timezone
    from decimal import Decimal
    pos=SupplierPurchaseOrder.objects.filter(supplier=supplier); receipts=SupplierReceipt.objects.filter(po__supplier=supplier)
    total_receipts=receipts.count(); ontime=0; accepted=Decimal('0'); rejected=Decimal('0'); received=Decimal('0'); short=Decimal('0'); lead_variance=[]
    for r in receipts.select_related('po'):
        received+=r.received_qty; accepted+=r.accepted_qty; rejected+=r.rejected_qty
        if r.po.expected_delivery:
            delta=(r.received_at.date()-r.po.expected_delivery).days; lead_variance.append(delta)
            if delta<=0: ontime+=1
        if r.received_qty < r.po.quantity: short += (r.po.quantity-r.received_qty)
    total_ordered=pos.aggregate(v=Sum('quantity'))['v'] or Decimal('0')
    purchase=pos.aggregate(v=Sum('total_value'))['v'] or Decimal('0')
    outstanding=SupplierInvoice.objects.filter(supplier=supplier).exclude(status='PAID').aggregate(v=Sum('amount'))['v'] or Decimal('0')
    ontime_pct=Decimal(ontime*100/total_receipts) if total_receipts else Decimal('0')
    qpass=(accepted*100/received) if received else Decimal('0'); reject=(rejected*100/received) if received else Decimal('0')
    short_pct=(short*100/total_ordered) if total_ordered else Decimal('0'); lead=Decimal(sum(lead_variance)/len(lead_variance)) if lead_variance else Decimal('0')
    score=max(Decimal('0'),min(Decimal('100'),ontime_pct*Decimal('.35')+qpass*Decimal('.45')+(Decimal('100')-short_pct)*Decimal('.20')))
    risk='HIGH' if score<60 or reject>10 else ('MEDIUM' if score<80 else 'LOW')
    obj,_=SupplierPerformance.objects.update_or_create(supplier=supplier,defaults={'on_time_delivery_pct':ontime_pct,'quality_pass_pct':qpass,'reject_pct':reject,'short_delivery_pct':short_pct,'lead_time_variance_days':lead,'total_purchase_value':purchase,'outstanding_payable':outstanding,'supplier_score':score,'risk_level':risk,'calculated_at':timezone.now()})
    return obj

@login_required
@require_http_methods(['GET','POST'])
def supplier_dashboard(request):
    from django.contrib import messages
    from django.utils import timezone
    from decimal import Decimal
    today=timezone.localdate()
    if request.method=='POST':
        a=request.POST.get('action','')
        try:
            if a=='supplier':
                approval=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                SupplierMaster.objects.create(supplier_id=request.POST.get('supplier_id','').strip(),company_name=request.POST.get('company_name','').strip(),
                    contact_person=request.POST.get('contact_person',''),email=request.POST.get('email',''),phone=request.POST.get('phone',''),country=request.POST.get('country',''),address=request.POST.get('address',''),
                    vat_tax_tin=request.POST.get('vat_tax_tin',''),registration_no=request.POST.get('registration_no',''),categories=request.POST.get('categories',''),materials=request.POST.get('materials',''),
                    moq=request.POST.get('moq',''),lead_time_days=int(request.POST.get('lead_time_days') or 0),currency=request.POST.get('currency','BDT'),payment_terms=request.POST.get('payment_terms',''),
                    bank_name=request.POST.get('bank_name',''),bank_account_name=request.POST.get('bank_account_name',''),bank_account_no=request.POST.get('bank_account_no',''),bank_swift=request.POST.get('bank_swift',''),
                    capacity_notes=request.POST.get('capacity_notes',''),certifications=request.POST.get('certifications',''),status='APPROVED' if approval and approval.status=='APPROVED' else 'PENDING_KYC',approval=approval,created_by=request.user)
            elif a=='document':
                s=get_object_or_404(SupplierMaster,pk=request.POST.get('supplier_id')); verified=request.POST.get('verified')=='on'
                SupplierDocument.objects.create(supplier=s,document_type=request.POST.get('document_type','OTHER'),document_no=request.POST.get('document_no',''),file=request.FILES.get('file'),
                    expiry_date=request.POST.get('expiry_date') or None,verified=verified,verified_by=request.user if verified else None,verified_at=timezone.now() if verified else None,uploaded_by=request.user)
            elif a=='rfq':
                s=get_object_or_404(SupplierMaster,pk=request.POST.get('supplier_id'))
                if s.status!='APPROVED': raise PermissionError('RFQ blocked: supplier must be APPROVED.')
                SupplierRFQ.objects.create(rfq_no=request.POST.get('rfq_no','').strip(),supplier=s,order=MasterOrder.objects.filter(pk=request.POST.get('order_id')).first(),material=MaterialMaster.objects.filter(pk=request.POST.get('material_id')).first(),
                    item_description=request.POST.get('item_description',''),quantity=Decimal(request.POST.get('quantity') or 0),unit=request.POST.get('unit',''),quoted_unit_price=Decimal(request.POST.get('quoted_unit_price') or 0),
                    currency=request.POST.get('currency','BDT'),quoted_lead_days=int(request.POST.get('quoted_lead_days') or 0),valid_until=request.POST.get('valid_until') or None,sample_approved=request.POST.get('sample_approved')=='on',
                    quality_approved=request.POST.get('quality_approved')=='on',status=request.POST.get('status','QUOTED'),quotation_file=request.FILES.get('quotation_file') or None)
            elif a=='po':
                s=get_object_or_404(SupplierMaster,pk=request.POST.get('supplier_id')); ap=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                if s.status!='APPROVED': raise PermissionError('PO blocked: supplier not approved.')
                if not ap or ap.status!='APPROVED': raise PermissionError('Profit-before-spend / senior APPROVED authorization is required before PO.')
                rfq=SupplierRFQ.objects.filter(pk=request.POST.get('rfq_id')).first()
                if rfq and (not rfq.sample_approved or not rfq.quality_approved): raise PermissionError('PO blocked: sample and quality approval required.')
                SupplierPurchaseOrder.objects.create(po_no=request.POST.get('po_no','').strip(),supplier=s,order=MasterOrder.objects.filter(pk=request.POST.get('order_id')).first(),rfq=rfq,
                    material=MaterialMaster.objects.filter(pk=request.POST.get('material_id')).first(),description=request.POST.get('description',''),quantity=Decimal(request.POST.get('quantity') or 0),unit=request.POST.get('unit',''),
                    unit_price=Decimal(request.POST.get('unit_price') or 0),currency=request.POST.get('currency','BDT'),expected_delivery=request.POST.get('expected_delivery') or None,status='APPROVED',approval=ap,po_file=request.FILES.get('po_file') or None,created_by=request.user)
            elif a=='receipt':
                po=get_object_or_404(SupplierPurchaseOrder,pk=request.POST.get('po_id'))
                rq=Decimal(request.POST.get('received_qty') or 0); aq=Decimal(request.POST.get('accepted_qty') or 0); rej=Decimal(request.POST.get('rejected_qty') or 0); barcode=request.POST.get('stock_in_barcode','').strip()
                if not barcode: raise PermissionError('Mandatory STOCK IN SCAN barcode is required.')
                r=SupplierReceipt.objects.create(po=po,grn_no=request.POST.get('grn_no','').strip(),delivery_note_no=request.POST.get('delivery_note_no',''),received_qty=rq,accepted_qty=aq,rejected_qty=rej,stock_in_barcode=barcode,stock_in_scanned=True,inspection_pass=request.POST.get('inspection_pass')=='on',received_by=request.user,delivery_note=request.FILES.get('delivery_note') or None,grn_file=request.FILES.get('grn_file') or None)
                if rej>0 or rq<po.quantity:
                    Alert.objects.create(level='RED',title='Supplier Delivery Variance',message=f'{po.po_no}: received {rq}, ordered {po.quantity}, rejected {rej}.',reference=po.po_no,actioned=False)
                _recalc_supplier_performance(po.supplier)
            elif a=='invoice':
                po=get_object_or_404(SupplierPurchaseOrder,pk=request.POST.get('po_id')); ap=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                if SupplierInvoice.objects.filter(supplier=po.supplier,invoice_no=request.POST.get('invoice_no','').strip()).exists(): raise PermissionError('Duplicate supplier invoice blocked.')
                SupplierInvoice.objects.create(supplier=po.supplier,po=po,invoice_no=request.POST.get('invoice_no','').strip(),invoice_date=request.POST.get('invoice_date') or today,amount=Decimal(request.POST.get('amount') or 0),currency=request.POST.get('currency','BDT'),status='APPROVED' if ap and ap.status=='APPROVED' else 'PENDING',approval=ap,invoice_file=request.FILES.get('invoice_file') or None)
                _recalc_supplier_performance(po.supplier)
            elif a=='bank_change':
                s=get_object_or_404(SupplierMaster,pk=request.POST.get('supplier_id')); ap=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                if not ap or ap.status!='APPROVED': raise PermissionError('Bank-detail change blocked: senior approval required.')
                s.bank_name=request.POST.get('bank_name','');s.bank_account_name=request.POST.get('bank_account_name','');s.bank_account_no=request.POST.get('bank_account_no','');s.bank_swift=request.POST.get('bank_swift','');s.save()
                Alert.objects.create(level='WARNING',title='Supplier Bank Details Changed',message=f'{s.supplier_id} bank details changed with approval {ap.reference}.',reference=s.supplier_id,actioned=False)
            elif a=='status':
                s=get_object_or_404(SupplierMaster,pk=request.POST.get('supplier_id')); st=request.POST.get('status')
                if st not in dict(SupplierMaster.STATUS): raise ValueError('Invalid supplier status.')
                s.status=st;s.save(update_fields=['status','updated_at'])
            elif a=='generate_report':
                slot=request.POST.get('slot')
                if slot not in {'08:00','13:00','20:00'}: raise ValueError('Invalid report slot.')
                SupplierAutoReport.objects.update_or_create(report_date=today,slot=slot,defaults={'summary':_supplier_payload(),'outstanding_alerts':Alert.objects.filter(actioned=False).count(),'pending_actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),'escalated_items':Alert.objects.filter(actioned=False,level='RED').count()})
            messages.success(request,'Supplier action completed.')
        except Exception as e: messages.error(request,str(e))
        return redirect('supplier_dashboard')
    for s in SupplierMaster.objects.all():
        try:_recalc_supplier_performance(s)
        except Exception:pass
    return render(request,'supplier_dashboard.html',{'today':today,'payload':_supplier_payload(),'suppliers':SupplierMaster.objects.order_by('-created_at')[:300],'orders':MasterOrder.objects.order_by('-created_at')[:300],
        'materials':MaterialMaster.objects.order_by('name')[:500],'rfqs':SupplierRFQ.objects.select_related('supplier').order_by('-created_at')[:300],'pos':SupplierPurchaseOrder.objects.select_related('supplier').order_by('-created_at')[:300],
        'receipts':SupplierReceipt.objects.select_related('po__supplier').order_by('-received_at')[:150],'invoices':SupplierInvoice.objects.select_related('supplier','po').order_by('-created_at')[:150],
        'performances':SupplierPerformance.objects.select_related('supplier').order_by('-supplier_score')[:300],'documents':SupplierDocument.objects.select_related('supplier').order_by('-created_at')[:150],
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-created_at')[:300],'reports':SupplierAutoReport.objects.filter(report_date=today).order_by('slot'),
        'doc_types':[x[0] for x in SupplierDocument.TYPES],'supplier_statuses':[x[0] for x in SupplierMaster.STATUS]})

@login_required
def supplier_report_csv(request):
    import csv
    response=HttpResponse(content_type='text/csv');response['Content-Disposition']=f'attachment; filename="supplier-report-{timezone.localdate()}.csv"';w=csv.writer(response)
    w.writerow(['Supplier ID','Company','Status','Country','Categories','Lead Days','Currency','On-Time %','Quality Pass %','Reject %','Short %','Purchase Value','Outstanding','Score','Risk'])
    for s in SupplierMaster.objects.all():
        p=_recalc_supplier_performance(s);w.writerow([s.supplier_id,s.company_name,s.status,s.country,s.categories,s.lead_time_days,s.currency,p.on_time_delivery_pct,p.quality_pass_pct,p.reject_pct,p.short_delivery_pct,p.total_purchase_value,p.outstanding_payable,p.supplier_score,p.risk_level])
    return response

@login_required
def api_supplier_dashboard(request): return JsonResponse(_supplier_payload())


def _procurement_payload():
    from django.db.models import Sum
    reqs=ProcurementRequest.objects.all()
    commits=ProcurementCommitment.objects.all()
    pos=SupplierPurchaseOrder.objects.all()
    receipts=SupplierReceipt.objects.all()
    return {
        'requests':reqs.count(),
        'shortage_requests':reqs.filter(shortage_qty__gt=0).count(),
        'pending_approval':reqs.filter(status='PENDING_APPROVAL').count(),
        'approved':reqs.filter(status='APPROVED').count(),
        'ordered':reqs.filter(status='ORDERED').count(),
        'received':reqs.filter(status__in=['RECEIVED','CLOSED']).count(),
        'budget_value':str(reqs.aggregate(v=Sum('budget_value'))['v'] or 0),
        'committed_value':str(commits.aggregate(v=Sum('committed_value'))['v'] or 0),
        'po_value':str(pos.aggregate(v=Sum('total_value'))['v'] or 0),
        'received_qty':str(receipts.aggregate(v=Sum('received_qty'))['v'] or 0),
    }

@login_required
@require_http_methods(['GET','POST'])
def procurement_dashboard(request):
    from django.contrib import messages
    from decimal import Decimal
    from django.utils import timezone
    today=timezone.localdate()
    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='request':
                    material=MaterialMaster.objects.filter(pk=request.POST.get('material_id')).first()
                    order=MasterOrder.objects.filter(pk=request.POST.get('order_id')).first()
                    required=Decimal(request.POST.get('required_qty') or 0)
                    stock_item=StockItem.objects.filter(sku=material.material_code).first() if material else None
                    available=stock_item.qty if stock_item else Decimal('0')
                    reserved=stock_item.reserved_qty if stock_item else Decimal('0')
                    free=max(available-reserved,Decimal('0'))
                    shortage=max(required-free,Decimal('0'))
                    req=ProcurementRequest.objects.create(
                        request_no=request.POST.get('request_no','').strip(),order=order,material=material,
                        description=request.POST.get('description','').strip() or (material.name if material else ''),
                        required_qty=required,uom=request.POST.get('uom','') or (material.uom if material else ''),
                        required_date=request.POST.get('required_date') or None,
                        stock_available=available,reserved_qty=reserved,shortage_qty=shortage,
                        budget_value=Decimal(request.POST.get('budget_value') or 0),
                        status='RFQ' if shortage>0 else 'STOCK_CHECK',requested_by=request.user
                    )
                    if shortage<=0:
                        messages.success(request,'Stock check passed. Purchase not required unless approved exception.')
                    else:
                        messages.success(request,f'Procurement request created. Shortage: {shortage} {req.uom}.')

                elif action=='comparison':
                    req=get_object_or_404(ProcurementRequest,pk=request.POST.get('request_id'))
                    supplier=get_object_or_404(SupplierMaster,pk=request.POST.get('supplier_id'))
                    if supplier.status!='APPROVED':
                        raise PermissionError('Supplier comparison blocked: supplier must be APPROVED.')
                    ProcurementComparison.objects.create(
                        request=req,supplier=supplier,rfq=SupplierRFQ.objects.filter(pk=request.POST.get('rfq_id')).first(),
                        unit_price=Decimal(request.POST.get('unit_price') or 0),
                        freight_cost=Decimal(request.POST.get('freight_cost') or 0),
                        tax_duty_cost=Decimal(request.POST.get('tax_duty_cost') or 0),
                        other_cost=Decimal(request.POST.get('other_cost') or 0),
                        lead_time_days=int(request.POST.get('lead_time_days') or supplier.lead_time_days),
                        payment_terms=request.POST.get('payment_terms','').strip() or supplier.payment_terms,
                        reason=request.POST.get('reason','').strip()
                    )
                    req.status='EVALUATION'; req.save(update_fields=['status','updated_at'])
                    messages.success(request,'Supplier comparison added.')

                elif action=='select':
                    comp=get_object_or_404(ProcurementComparison,pk=request.POST.get('comparison_id'))
                    ap=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if not ap or ap.status!='APPROVED':
                        raise PermissionError('Supplier selection requires APPROVED authorization.')
                    ProcurementComparison.objects.filter(request=comp.request).update(selected=False)
                    comp.selected=True; comp.save(update_fields=['selected','updated_at'])
                    comp.request.status='PENDING_APPROVAL'; comp.request.approval=ap; comp.request.save(update_fields=['status','approval','updated_at'])
                    messages.success(request,'Supplier selected and request moved to approval.')

                elif action=='commit':
                    req=get_object_or_404(ProcurementRequest,pk=request.POST.get('request_id'))
                    comp=get_object_or_404(ProcurementComparison,request=req,selected=True)
                    ap=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if not ap or ap.status!='APPROVED':
                        raise PermissionError('Profit-before-spend approval is mandatory.')
                    committed=Decimal(request.POST.get('committed_value') or (comp.landed_unit_cost*req.required_qty))
                    if req.budget_value and committed > req.budget_value:
                        Alert.objects.create(level='RED',title='Procurement Budget Variance',
                            message=f'{req.request_no}: commitment {committed} exceeds budget {req.budget_value}.',
                            reference=req.request_no,actioned=False)
                    ProcurementCommitment.objects.update_or_create(
                        request=req,defaults={'selected_comparison':comp,'supplier':comp.supplier,
                            'approved_budget':req.budget_value,'committed_value':committed,
                            'profit_before_spend_pass':True,'approval':ap,'committed_by':request.user}
                    )
                    req.status='APPROVED';req.approval=ap;req.save(update_fields=['status','approval','updated_at'])
                    messages.success(request,'Procurement commitment approved.')

                elif action=='create_po':
                    req=get_object_or_404(ProcurementRequest,pk=request.POST.get('request_id'))
                    commitment=get_object_or_404(ProcurementCommitment,request=req,profit_before_spend_pass=True)
                    if not commitment.approval_id or commitment.approval.status!='APPROVED':
                        raise PermissionError('PO blocked: valid profit-before-spend approval required.')
                    comp=commitment.selected_comparison
                    po=SupplierPurchaseOrder.objects.create(
                        po_no=request.POST.get('po_no','').strip(),supplier=commitment.supplier,order=req.order,
                        rfq=comp.rfq if comp else None,material=req.material,description=req.description,
                        quantity=req.required_qty,unit=req.uom,unit_price=comp.unit_price if comp else Decimal('0'),
                        currency=commitment.supplier.currency,expected_delivery=request.POST.get('expected_delivery') or req.required_date,
                        status='APPROVED',approval=commitment.approval,po_file=request.FILES.get('po_file') or None,created_by=request.user
                    )
                    commitment.po=po;commitment.save(update_fields=['po','updated_at'])
                    req.status='ORDERED';req.save(update_fields=['status','updated_at'])
                    messages.success(request,f'Purchase Order {po.po_no} created.')

                elif action=='delivery_status':
                    po=get_object_or_404(SupplierPurchaseOrder,pk=request.POST.get('po_id'))
                    received=sum((r.received_qty for r in po.receipts.all()),Decimal('0'))
                    if received<=0: po.status='SENT'
                    elif received<po.quantity: po.status='PART_RECEIVED'
                    else: po.status='RECEIVED'
                    po.save(update_fields=['status','updated_at'])
                    if hasattr(po,'procurement_commitments'):
                        pass
                    req=ProcurementRequest.objects.filter(commitment__po=po).first()
                    if req:
                        req.status='PART_RECEIVED' if po.status=='PART_RECEIVED' else ('RECEIVED' if po.status=='RECEIVED' else req.status)
                        req.save(update_fields=['status','updated_at'])
                    messages.success(request,'Procurement delivery status recalculated.')

                elif action=='generate_report':
                    slot=request.POST.get('slot')
                    if slot not in {'08:00','13:00','20:00'}:
                        raise ValueError('Invalid report slot.')
                    ProcurementAutoReport.objects.update_or_create(report_date=today,slot=slot,defaults={
                        'summary':_procurement_payload(),
                        'outstanding_alerts':Alert.objects.filter(actioned=False).count(),
                        'pending_actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),
                        'escalated_items':Alert.objects.filter(actioned=False,level='RED').count()
                    })
                    messages.success(request,f'{slot} Procurement automatic report generated.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('procurement_dashboard')

    return render(request,'procurement_dashboard.html',{
        'today':today,'payload':_procurement_payload(),
        'requests':ProcurementRequest.objects.select_related('order','material','approval').order_by('-created_at')[:250],
        'comparisons':ProcurementComparison.objects.select_related('request','supplier','rfq').order_by('-created_at')[:300],
        'commitments':ProcurementCommitment.objects.select_related('request','supplier','po','approval').order_by('-created_at')[:200],
        'suppliers':SupplierMaster.objects.filter(status='APPROVED').order_by('company_name')[:500],
        'rfqs':SupplierRFQ.objects.order_by('-created_at')[:500],
        'orders':MasterOrder.objects.order_by('-created_at')[:300],
        'materials':MaterialMaster.objects.filter(status='ACTIVE').order_by('name')[:500],
        'pos':SupplierPurchaseOrder.objects.select_related('supplier').order_by('-created_at')[:300],
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-created_at')[:300],
        'reports':ProcurementAutoReport.objects.filter(report_date=today).order_by('slot')
    })

@login_required
def procurement_report_csv(request):
    import csv
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']=f'attachment; filename="procurement-report-{timezone.localdate()}.csv"'
    w=csv.writer(response)
    w.writerow(['Request','Order','Material','Required','Stock','Reserved','Shortage','Budget','Status','Selected Supplier','Committed','PO'])
    for r in ProcurementRequest.objects.select_related('order','material').order_by('-created_at'):
        c=ProcurementCommitment.objects.filter(request=r).select_related('supplier','po').first()
        w.writerow([r.request_no,r.order.master_order_id if r.order else '',r.material.material_code if r.material else '',r.required_qty,r.stock_available,r.reserved_qty,r.shortage_qty,r.budget_value,r.status,c.supplier.supplier_id if c else '',c.committed_value if c else '',c.po.po_no if c and c.po else ''])
    return response

@login_required
def api_procurement_dashboard(request):
    return JsonResponse(_procurement_payload())


def _purchase_payload():
    from django.db.models import Sum
    tx=PurchaseTransaction.objects.all(); matches=PurchaseThreeWayMatch.objects.all()
    return {'purchases':tx.count(),'open':tx.filter(status__in=['OPEN','ACKNOWLEDGED']).count(),'partial':tx.filter(status='PART_DELIVERED').count(),
        'delivered':tx.filter(status='DELIVERED').count(),'match_pending':tx.filter(status__in=['INSPECTION','MATCH_PENDING']).count(),
        'payment_pending':tx.filter(status='PAYMENT_PENDING').count(),'closed':tx.filter(status='CLOSED').count(),
        'hold':tx.filter(status='HOLD').count(),'po_value':str(SupplierPurchaseOrder.objects.aggregate(v=Sum('total_value'))['v'] or 0),
        'variance':str(matches.filter(status__in=['VARIANCE','BLOCKED']).aggregate(v=Sum('value_variance'))['v'] or 0)}

@login_required
@require_http_methods(['GET','POST'])
def purchases_dashboard(request):
    from django.contrib import messages
    from decimal import Decimal
    from django.utils import timezone
    today=timezone.localdate()
    if request.method=='POST':
        a=request.POST.get('action','')
        try:
            if a=='open':
                po=get_object_or_404(SupplierPurchaseOrder,pk=request.POST.get('po_id'))
                if po.status not in {'APPROVED','SENT','PART_RECEIVED','RECEIVED'}: raise PermissionError('Purchase blocked: PO must be approved.')
                commitment=ProcurementCommitment.objects.filter(po=po,profit_before_spend_pass=True,approval__status='APPROVED').first()
                if not commitment: raise PermissionError('Purchase blocked: approved Procurement + Profit-Before-Spend commitment required.')
                PurchaseTransaction.objects.get_or_create(po=po,defaults={'procurement_request':commitment.request,'promised_delivery':po.expected_delivery,'managed_by':request.user})
            elif a=='ack':
                p=get_object_or_404(PurchaseTransaction,pk=request.POST.get('purchase_id'));p.supplier_acknowledged=True;p.acknowledged_at=timezone.now();p.promised_delivery=request.POST.get('promised_delivery') or p.promised_delivery;p.status='ACKNOWLEDGED';p.save()
            elif a=='sync_receipt':
                p=get_object_or_404(PurchaseTransaction,pk=request.POST.get('purchase_id')); rs=p.po.receipts.all()
                received=sum((r.received_qty for r in rs),Decimal('0'));accepted=sum((r.accepted_qty for r in rs),Decimal('0'));rejected=sum((r.rejected_qty for r in rs),Decimal('0'))
                p.actual_received_qty=received;p.accepted_qty=accepted;p.rejected_qty=rejected;p.short_qty=max(p.po.quantity-received,Decimal('0'))
                p.status='PART_DELIVERED' if received<p.po.quantity else 'DELIVERED'
                if rejected>0 or p.short_qty>0: Alert.objects.create(level='RED',title='Purchase Delivery Variance',message=f'{p.po.po_no}: rejected {rejected}, short {p.short_qty}.',reference=p.po.po_no,actioned=False)
                p.save()
            elif a=='match':
                p=get_object_or_404(PurchaseTransaction,pk=request.POST.get('purchase_id'));inv=get_object_or_404(SupplierInvoice,pk=request.POST.get('invoice_id'),po=p.po)
                received=sum((r.accepted_qty for r in p.po.receipts.all()),Decimal('0')); grn_value=received*p.po.unit_price
                qvar=received-p.po.quantity;vvar=inv.amount-grn_value
                status='MATCHED' if qvar==0 and vvar==0 else 'VARIANCE'
                ap=ApprovalRequest.objects.filter(pk=request.POST.get('exception_approval_id')).first()
                if status=='VARIANCE':
                    if abs(vvar)>Decimal('10'): Alert.objects.create(level='RED',title='Purchase 3-Way Match Variance',message=f'{p.po.po_no}: PO/GRN/Invoice variance {vvar}.',reference=p.po.po_no,actioned=False)
                    status='APPROVED_EXCEPTION' if ap and ap.status=='APPROVED' else 'BLOCKED'
                PurchaseThreeWayMatch.objects.create(purchase=p,invoice=inv,po_value=p.po.total_value,grn_value=grn_value,invoice_value=inv.amount,quantity_variance=qvar,value_variance=vvar,status=status,exception_approval=ap,checked_by=request.user)
                p.status='PAYMENT_PENDING' if status in {'MATCHED','APPROVED_EXCEPTION'} else 'HOLD';p.save(update_fields=['status','updated_at'])
            elif a=='amend':
                p=get_object_or_404(PurchaseTransaction,pk=request.POST.get('purchase_id'));ap=get_object_or_404(ApprovalRequest,pk=request.POST.get('approval_id'),status='APPROVED')
                PurchaseAmendment.objects.create(purchase=p,amendment_no=request.POST.get('amendment_no',''),amendment_type=request.POST.get('amendment_type','OTHER'),old_value=request.POST.get('old_value',''),new_value=request.POST.get('new_value',''),reason=request.POST.get('reason',''),approval=ap,document=request.FILES.get('document') or None,created_by=request.user)
            elif a=='return':
                p=get_object_or_404(PurchaseTransaction,pk=request.POST.get('purchase_id'));ap=get_object_or_404(ApprovalRequest,pk=request.POST.get('approval_id'),status='APPROVED')
                PurchaseReturn.objects.create(return_no=request.POST.get('return_no',''),purchase=p,quantity=Decimal(request.POST.get('quantity') or 0),reason=request.POST.get('reason',''),debit_note_no=request.POST.get('debit_note_no',''),value=Decimal(request.POST.get('value') or 0),approval=ap,return_document=request.FILES.get('return_document') or None,status='APPROVED',created_by=request.user)
            elif a=='close':
                p=get_object_or_404(PurchaseTransaction,pk=request.POST.get('purchase_id'))
                if not p.three_way_matches.filter(status__in=['MATCHED','APPROVED_EXCEPTION']).exists(): raise PermissionError('Purchase close blocked: successful 3-way match required.')
                if p.po.invoices.exclude(status='PAID').exists(): raise PermissionError('Purchase close blocked: supplier invoice is not PAID.')
                p.status='CLOSED';p.save(update_fields=['status','updated_at'])
            elif a=='report':
                slot=request.POST.get('slot')
                if slot not in {'08:00','13:00','20:00'}: raise ValueError('Invalid report slot.')
                PurchaseAutoReport.objects.update_or_create(report_date=today,slot=slot,defaults={'summary':_purchase_payload(),'outstanding_alerts':Alert.objects.filter(actioned=False).count(),'pending_actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),'escalated_items':Alert.objects.filter(actioned=False,level='RED').count()})
            messages.success(request,'Purchase action completed.')
        except Exception as e: messages.error(request,str(e))
        return redirect('purchases_dashboard')
    return render(request,'purchases_dashboard.html',{'payload':_purchase_payload(),'purchases':PurchaseTransaction.objects.select_related('po__supplier','procurement_request').order_by('-created_at')[:300],
        'eligible_pos':SupplierPurchaseOrder.objects.filter(status__in=['APPROVED','SENT','PART_RECEIVED','RECEIVED']).select_related('supplier').order_by('-created_at')[:300],
        'invoices':SupplierInvoice.objects.select_related('po','supplier').order_by('-created_at')[:300],'matches':PurchaseThreeWayMatch.objects.select_related('purchase__po','invoice').order_by('-created_at')[:150],
        'returns':PurchaseReturn.objects.select_related('purchase__po').order_by('-created_at')[:100],'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-created_at')[:300],
        'reports':PurchaseAutoReport.objects.filter(report_date=today).order_by('slot')})

@login_required
def purchase_report_csv(request):
    import csv
    response=HttpResponse(content_type='text/csv');response['Content-Disposition']=f'attachment; filename="purchase-register-{timezone.localdate()}.csv"';w=csv.writer(response)
    w.writerow(['PO','Supplier','Order','PO Qty','PO Value','Received','Accepted','Rejected','Short','Status','Promised Delivery'])
    for p in PurchaseTransaction.objects.select_related('po__supplier','po__order'):
        w.writerow([p.po.po_no,p.po.supplier.supplier_id,p.po.order.master_order_id if p.po.order else '',p.po.quantity,p.po.total_value,p.actual_received_qty,p.accepted_qty,p.rejected_qty,p.short_qty,p.status,p.promised_delivery])
    return response

@login_required
def api_purchases_dashboard(request): return JsonResponse(_purchase_payload())


def _sourcing_payload():
    from django.db.models import Sum
    req=SourcingRequest.objects.all()
    quotes=SourcingQuotation.objects.all()
    evals=SourcingEvaluation.objects.all()
    return {
        'requests':req.count(),
        'shortages':req.filter(shortage_qty__gt=0).count(),
        'supplier_search':req.filter(status='SUPPLIER_SEARCH').count(),
        'rfq':req.filter(status='RFQ').count(),
        'sample':req.filter(status='SAMPLE').count(),
        'evaluation':req.filter(status__in=['EVALUATION','NEGOTIATION']).count(),
        'nominated':req.filter(status='NOMINATED').count(),
        'handoff':req.filter(status='HANDED_TO_PROCUREMENT').count(),
        'quote_count':quotes.count(),
        'avg_landed':str(quotes.aggregate(v=Sum('landed_unit_cost'))['v'] or 0),
        'high_price_variance':evals.filter(price_variance_pct__gt=10).count(),
    }

def _sourcing_last_purchase_price(request_obj, supplier=None):
    qs=SupplierPurchaseOrder.objects.filter(material=request_obj.material).order_by('-created_at') if request_obj.material_id else SupplierPurchaseOrder.objects.none()
    if supplier:
        qs=qs.filter(supplier=supplier)
    po=qs.first()
    return po.unit_price if po else 0

@login_required
@require_http_methods(['GET','POST'])
def sourcing_dashboard(request):
    from django.contrib import messages
    from decimal import Decimal
    from django.utils import timezone
    today=timezone.localdate()

    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='request':
                    material=MaterialMaster.objects.filter(pk=request.POST.get('material_id')).first()
                    order=MasterOrder.objects.filter(pk=request.POST.get('order_id')).first()
                    required=Decimal(request.POST.get('required_qty') or 0)
                    stock_item=StockItem.objects.filter(sku=material.material_code).first() if material else None
                    available=stock_item.qty if stock_item else Decimal('0')
                    reserved=stock_item.reserved_qty if stock_item else Decimal('0')
                    free=max(available-reserved,Decimal('0'))
                    shortage=max(required-free,Decimal('0'))
                    sr=SourcingRequest.objects.create(
                        request_no=request.POST.get('request_no','').strip(),
                        order=order,material=material,
                        category=request.POST.get('category','FABRIC'),
                        description=request.POST.get('description','').strip() or (material.name if material else ''),
                        specification=request.POST.get('specification','').strip(),
                        required_qty=required,
                        uom=request.POST.get('uom','').strip() or (material.uom if material else ''),
                        required_date=request.POST.get('required_date') or None,
                        stock_available=available,stock_reserved=reserved,shortage_qty=shortage,
                        target_price=Decimal(request.POST.get('target_price') or 0),
                        target_currency=request.POST.get('target_currency','BDT'),
                        status='SUPPLIER_SEARCH' if shortage>0 else 'STOCK_CHECK',
                        specification_file=request.FILES.get('specification_file') or None,
                        created_by=request.user
                    )
                    if shortage<=0:
                        messages.success(request,'Stock check passed. Sourcing is not required unless an approved exception is created.')
                    else:
                        messages.success(request,f'Sourcing request created. Shortage: {shortage} {sr.uom}.')

                elif action=='candidate':
                    sr=get_object_or_404(SourcingRequest,pk=request.POST.get('request_id'))
                    supplier=SupplierMaster.objects.filter(pk=request.POST.get('supplier_id')).first()
                    source_type=request.POST.get('source_type','EXISTING')
                    if source_type=='EXISTING' and (not supplier or supplier.status!='APPROVED'):
                        raise PermissionError('Existing supplier sourcing requires an APPROVED supplier.')
                    SourcingCandidate.objects.create(
                        request=sr,supplier=supplier,
                        supplier_name=request.POST.get('supplier_name','').strip() or (supplier.company_name if supplier else ''),
                        supplier_country=request.POST.get('supplier_country','').strip() or (supplier.country if supplier else ''),
                        source_type=source_type,
                        contact_details=request.POST.get('contact_details','').strip(),
                        compliance_status=request.POST.get('compliance_status','PENDING'),
                        capacity_per_month=Decimal(request.POST.get('capacity_per_month') or 0),
                        moq=Decimal(request.POST.get('moq') or 0),
                        lead_time_days=int(request.POST.get('lead_time_days') or (supplier.lead_time_days if supplier else 0)),
                        payment_terms=request.POST.get('payment_terms','').strip() or (supplier.payment_terms if supplier else ''),
                        notes=request.POST.get('notes','').strip(),created_by=request.user
                    )
                    sr.status='RFQ';sr.save(update_fields=['status','updated_at'])
                    messages.success(request,'Sourcing candidate added.')

                elif action=='quotation':
                    sr=get_object_or_404(SourcingRequest,pk=request.POST.get('request_id'))
                    cand=get_object_or_404(SourcingCandidate,pk=request.POST.get('candidate_id'),request=sr)
                    quote=SourcingQuotation.objects.create(
                        request=sr,candidate=cand,
                        rfq=SupplierRFQ.objects.filter(pk=request.POST.get('rfq_id')).first(),
                        quote_no=request.POST.get('quote_no','').strip(),
                        unit_price=Decimal(request.POST.get('unit_price') or 0),
                        currency=request.POST.get('currency','BDT'),
                        freight=Decimal(request.POST.get('freight') or 0),
                        duty_tax=Decimal(request.POST.get('duty_tax') or 0),
                        other_cost=Decimal(request.POST.get('other_cost') or 0),
                        quoted_lead_days=int(request.POST.get('quoted_lead_days') or cand.lead_time_days),
                        moq=Decimal(request.POST.get('moq') or cand.moq),
                        payment_terms=request.POST.get('payment_terms','').strip() or cand.payment_terms,
                        valid_until=request.POST.get('valid_until') or None,
                        status='SAMPLE_PENDING',
                        quotation_file=request.FILES.get('quotation_file') or None
                    )
                    if sr.target_price and quote.landed_unit_cost > sr.target_price:
                        Alert.objects.create(level='WARNING',title='Sourcing Target Price Variance',
                            message=f'{sr.request_no}: landed {quote.landed_unit_cost} exceeds target {sr.target_price}.',
                            reference=sr.request_no,actioned=False)
                    sr.status='SAMPLE';sr.save(update_fields=['status','updated_at'])
                    messages.success(request,'Sourcing quotation recorded.')

                elif action=='sample':
                    quote=get_object_or_404(SourcingQuotation,pk=request.POST.get('quotation_id'))
                    sample=SourcingSample.objects.create(
                        quotation=quote,sample_no=request.POST.get('sample_no','').strip(),
                        received_at=request.POST.get('received_at') or timezone.now(),
                        quality_result=request.POST.get('quality_result','PENDING'),
                        compliance_result=request.POST.get('compliance_result','PENDING'),
                        lab_test_result=request.POST.get('lab_test_result','PENDING'),
                        comments=request.POST.get('comments','').strip(),
                        sample_file=request.FILES.get('sample_file') or None,checked_by=request.user
                    )
                    if sample.quality_result=='PASS' and sample.compliance_result=='PASS' and sample.lab_test_result in {'PASS','PENDING'}:
                        quote.status='SAMPLE_APPROVED'
                    elif 'REJECT' in {sample.quality_result,sample.compliance_result,sample.lab_test_result}:
                        quote.status='REJECTED'
                        Alert.objects.create(level='RED',title='Sourcing Sample Rejected',
                            message=f'{quote.request.request_no}: sample {sample.sample_no} rejected.',
                            reference=quote.request.request_no,actioned=False)
                    else:
                        quote.status='SAMPLE_PENDING'
                    quote.save(update_fields=['status','updated_at'])
                    quote.request.status='EVALUATION';quote.request.save(update_fields=['status','updated_at'])
                    messages.success(request,'Sample evaluation saved.')

                elif action=='evaluate':
                    quote=get_object_or_404(SourcingQuotation,pk=request.POST.get('quotation_id'))
                    supplier=quote.candidate.supplier
                    perf=getattr(supplier,'performance',None) if supplier else None
                    last_price=Decimal(str(_sourcing_last_purchase_price(quote.request,supplier) or 0))
                    price_score=Decimal(request.POST.get('price_score') or 100)
                    quality_score=Decimal(request.POST.get('quality_score') or (perf.quality_pass_pct if perf else 0))
                    delivery_score=Decimal(request.POST.get('delivery_score') or (perf.on_time_delivery_pct if perf else 0))
                    compliance_score=Decimal(request.POST.get('compliance_score') or (100 if quote.candidate.compliance_status=='APPROVED' else 0))
                    capacity_score=Decimal(request.POST.get('capacity_score') or 0)
                    ev=SourcingEvaluation.objects.create(
                        request=quote.request,quotation=quote,
                        price_score=price_score,quality_score=quality_score,delivery_score=delivery_score,
                        compliance_score=compliance_score,capacity_score=capacity_score,
                        last_purchase_price=last_price,current_quote_price=quote.unit_price,
                        nomination_reason=request.POST.get('nomination_reason','').strip(),
                        evaluated_by=request.user
                    )
                    if abs(ev.price_variance_pct)>Decimal('10'):
                        Alert.objects.create(level='RED',title='Sourcing Price Variance',
                            message=f'{quote.request.request_no}: current price variance {ev.price_variance_pct}%.',
                            reference=quote.request.request_no,actioned=False)
                    quote.request.status='NEGOTIATION';quote.request.save(update_fields=['status','updated_at'])
                    messages.success(request,'Sourcing evaluation calculated.')

                elif action=='nominate':
                    ev=get_object_or_404(SourcingEvaluation,pk=request.POST.get('evaluation_id'))
                    supplier=ev.quotation.candidate.supplier
                    if not supplier or supplier.status!='APPROVED':
                        raise PermissionError('Nomination blocked: supplier must be APPROVED in Supplier Master.')
                    samples=ev.quotation.samples.all()
                    if samples.exists() and not samples.filter(quality_result='PASS',compliance_result='PASS').exists():
                        raise PermissionError('Nomination blocked: approved quality/compliance sample is required.')
                    ap=ApprovalRequest.objects.filter(pk=request.POST.get('approval_id')).first()
                    if not ap or ap.status!='APPROVED':
                        raise PermissionError('Supplier nomination requires APPROVED authorization.')
                    SourcingEvaluation.objects.filter(request=ev.request).update(nominated=False)
                    ev.nominated=True;ev.approval=ap
                    ev.nomination_reason=request.POST.get('nomination_reason','').strip() or ev.nomination_reason
                    ev.save(update_fields=['nominated','approval','nomination_reason','updated_at'])
                    ev.request.status='NOMINATED';ev.request.approval=ap;ev.request.save(update_fields=['status','approval','updated_at'])
                    messages.success(request,'Supplier nominated for sourcing request.')

                elif action=='handoff':
                    sr=get_object_or_404(SourcingRequest,pk=request.POST.get('request_id'))
                    ev=get_object_or_404(SourcingEvaluation,request=sr,nominated=True)
                    if not ev.approval_id or ev.approval.status!='APPROVED':
                        raise PermissionError('Procurement handoff blocked: approved sourcing nomination required.')
                    supplier=ev.quotation.candidate.supplier
                    pr=ProcurementRequest.objects.create(
                        request_no=request.POST.get('procurement_request_no','').strip(),
                        order=sr.order,material=sr.material,description=sr.description,
                        required_qty=sr.shortage_qty or sr.required_qty,uom=sr.uom,
                        required_date=sr.required_date,stock_available=sr.stock_available,
                        reserved_qty=sr.stock_reserved,shortage_qty=sr.shortage_qty,
                        budget_value=Decimal(request.POST.get('budget_value') or (ev.quotation.landed_unit_cost*(sr.shortage_qty or sr.required_qty))),
                        status='RFQ',requested_by=request.user
                    )
                    SourcingHandoff.objects.create(
                        request=sr,evaluation=ev,supplier=supplier,procurement_request=pr,
                        approved_price=ev.quotation.unit_price,currency=ev.quotation.currency,
                        approved_lead_days=ev.quotation.quoted_lead_days,approval=ev.approval,handed_off_by=request.user
                    )
                    sr.status='HANDED_TO_PROCUREMENT';sr.save(update_fields=['status','updated_at'])
                    messages.success(request,f'Sourcing handed to Procurement as {pr.request_no}.')

                elif action=='report':
                    slot=request.POST.get('slot')
                    if slot not in {'08:00','13:00','20:00'}:
                        raise ValueError('Invalid report slot.')
                    SourcingAutoReport.objects.update_or_create(
                        report_date=today,slot=slot,
                        defaults={'summary':_sourcing_payload(),
                                  'outstanding_alerts':Alert.objects.filter(actioned=False).count(),
                                  'pending_actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).count(),
                                  'escalated_items':Alert.objects.filter(actioned=False,level='RED').count()}
                    )
                    messages.success(request,f'{slot} Sourcing automatic report generated.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('sourcing_dashboard')

    return render(request,'sourcing_dashboard.html',{
        'today':today,'payload':_sourcing_payload(),
        'requests':SourcingRequest.objects.select_related('order','material','approval').order_by('-created_at')[:300],
        'candidates':SourcingCandidate.objects.select_related('request','supplier').order_by('-created_at')[:300],
        'quotations':SourcingQuotation.objects.select_related('request','candidate__supplier').order_by('-created_at')[:300],
        'samples':SourcingSample.objects.select_related('quotation__request').order_by('-created_at')[:200],
        'evaluations':SourcingEvaluation.objects.select_related('request','quotation__candidate__supplier','approval').order_by('-created_at')[:300],
        'handoffs':SourcingHandoff.objects.select_related('request','supplier','procurement_request').order_by('-created_at')[:200],
        'suppliers':SupplierMaster.objects.filter(status='APPROVED').order_by('company_name')[:500],
        'orders':MasterOrder.objects.order_by('-created_at')[:300],
        'materials':MaterialMaster.objects.filter(status='ACTIVE').order_by('name')[:500],
        'rfqs':SupplierRFQ.objects.order_by('-created_at')[:500],
        'approvals':ApprovalRequest.objects.filter(status='APPROVED').order_by('-created_at')[:300],
        'categories':[x[0] for x in SourcingRequest.CATEGORIES],
        'reports':SourcingAutoReport.objects.filter(report_date=today).order_by('slot')
    })

@login_required
def sourcing_report_csv(request):
    import csv
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']=f'attachment; filename="sourcing-report-{timezone.localdate()}.csv"'
    w=csv.writer(response)
    w.writerow(['Request','Order','Category','Material','Required','Stock','Reserved','Shortage','Target Price','Status','Nominated Supplier','Approved Price','Lead Days','Procurement Request'])
    for sr in SourcingRequest.objects.select_related('order','material').order_by('-created_at'):
        h=SourcingHandoff.objects.filter(request=sr).select_related('supplier','procurement_request').first()
        w.writerow([sr.request_no,sr.order.master_order_id if sr.order else '',sr.category,sr.material.material_code if sr.material else '',
                    sr.required_qty,sr.stock_available,sr.stock_reserved,sr.shortage_qty,sr.target_price,sr.status,
                    h.supplier.supplier_id if h else '',h.approved_price if h else '',h.approved_lead_days if h else '',
                    h.procurement_request.request_no if h and h.procurement_request else ''])
    return response

@login_required
def api_sourcing_dashboard(request):
    return JsonResponse(_sourcing_payload())


#: Production models contributing to the daily output figure. Costs on these
#: models are denominated in BDT (the default on every production and material
#: model), which is why they must be converted before joining any consolidated
#: total. See TECHNICAL_ASSESSMENT.md 5.6.
_CEO_PRODUCTION_MODELS = [
    ('Cutting', 'CuttingProductionEntry', 'process_cost'),
    ('Embroidery', 'EmbroideryProductionEntry', 'total_cost'),
    ('Label', 'LabelProductionEntry', 'total_cost'),
    ('Hand Iron', 'HandIronProductionEntry', 'total_cost'),
    ('Industrial Iron', 'IronProductionEntry', 'total_cost'),
    ('Poly', 'PolyPackingEntry', 'total_cost'),
    ('Finishing', 'FinishingProduction', 'total_cost'),
    ('Packing', 'PackingProduction', 'total_cost'),
]

#: Currency production costs are recorded in.
PRODUCTION_COST_CURRENCY = 'BDT'


def _ceo_production_summary(today):
    """Daily production output and cost.

    One aggregate per model instead of three: this was 24 round trips for eight
    models (TECHNICAL_ASSESSMENT.md 6.7).
    """
    from django.db.models import Sum

    rows = []
    total_actual = 0
    total_target = 0
    total_cost = Decimal('0')
    for label, model_name, cost_field in _CEO_PRODUCTION_MODELS:
        model = globals().get(model_name)
        if not model:
            continue
        totals = model.objects.filter(work_date=today).aggregate(
            actual=Sum('actual_qty'), target=Sum('target_qty'), cost=Sum(cost_field))
        actual = totals['actual'] or 0
        target = totals['target'] or 0
        cost = totals['cost'] or Decimal('0')
        rows.append({'name': label, 'actual': actual, 'target': target,
                     'cost': str(cost), 'currency': PRODUCTION_COST_CURRENCY,
                     'efficiency': round(actual / target * 100, 2) if target else 0})
        total_actual += actual
        total_target += target
        total_cost += cost
    return rows, total_actual, total_target, total_cost


def _ceo_summary(today):
    """Consolidated executive KPIs, expressed in settings.BASE_CURRENCY.

    Every cross-entity money figure here used to be a raw sum across records in
    different currencies. MasterOrder.order_value had no currency column at all,
    FinanceTransaction defaults to EUR while production, stock and supplier
    models default to BDT, and profit_today subtracted one from the other. At
    roughly 130 BDT to the euro the headline figures were wrong by orders of
    magnitude (TECHNICAL_ASSESSMENT.md 5.6).

    Amounts are now converted to the base currency before being combined. Where
    a rate is missing the amount is NOT silently dropped or counted at 1.0:
    the currency is reported in ``fx_unconvertible`` so the dashboard can say the
    figure is incomplete instead of presenting a wrong number as authoritative.
    """
    from django.db.models import Sum, F, ExpressionWrapper, DecimalField

    base = currency_base()

    orders = MasterOrder.objects.all()
    prod_rows, prod_actual, prod_target, prod_cost_bdt = _ceo_production_summary(today)

    missing_rates = set()

    def to_base(amount, code):
        """Convert, recording any currency we have no rate for."""
        if not amount:
            return Decimal('0.00')
        converted = convert_or_none(amount, code, base, today)
        if converted is None:
            missing_rates.add((code or '').upper())
            return Decimal('0.00')
        return converted

    # --- order book, converted per currency ---------------------------------
    order_value = Decimal('0.00')
    for row in orders.values('currency').annotate(total=Sum('order_value')):
        order_value += to_base(row['total'], row['currency'] or base)

    # --- finance, converted per currency ------------------------------------
    def finance_total(**filters):
        total = Decimal('0.00')
        rows = (FinanceTransaction.objects.filter(**filters)
                .values('currency').annotate(total=Sum('amount')))
        for row in rows:
            total += to_base(row['total'], row['currency'] or base)
        return total

    income = finance_total(created_at__date=today, transaction_type='INCOME')
    expense = finance_total(created_at__date=today, transaction_type='EXPENSE')
    receivable = finance_total(transaction_type='RECEIVABLE')
    payable = finance_total(transaction_type='PAYABLE')

    prod_cost = to_base(prod_cost_bdt, PRODUCTION_COST_CURRENCY)

    # --- workforce ----------------------------------------------------------
    # Employee.daily_cost has no currency column; it is BDT, like the rest of
    # the Bangladesh workforce data.
    att = AttendanceDailySummary.objects.filter(work_date=today)
    workforce = att.aggregate(cost=Sum('worked_cost'), ot=Sum('overtime_minutes'))
    staff_cost = to_base(workforce['cost'] or Decimal('0'), PRODUCTION_COST_CURRENCY)
    present = att.filter(worked_minutes__gt=0).count()
    overtime = workforce['ot'] or 0

    # --- stock, converted per currency --------------------------------------
    stock_expr = ExpressionWrapper(F('qty') * F('unit_cost'),
                                   output_field=DecimalField(max_digits=20, decimal_places=2))
    stock_value = Decimal('0.00')
    for row in StockItem.objects.values('currency').annotate(total=Sum(stock_expr)):
        stock_value += to_base(row['total'], row['currency'] or PRODUCTION_COST_CURRENCY)

    # --- purchasing, converted per currency --------------------------------
    po_value = Decimal('0.00')
    for row in SupplierPurchaseOrder.objects.values('currency').annotate(total=Sum('total_value')):
        po_value += to_base(row['total'], row['currency'] or PRODUCTION_COST_CURRENCY)

    purchase_outstanding = Decimal('0.00')
    for row in (SupplierInvoice.objects.exclude(status='PAID')
                .values('currency').annotate(total=Sum('amount'))):
        purchase_outstanding += to_base(row['total'], row['currency'] or PRODUCTION_COST_CURRENCY)

    sourcing_open = SourcingRequest.objects.exclude(
        status__in=['CLOSED', 'HANDED_TO_PROCUREMENT']).count()
    procurement_open = ProcurementRequest.objects.exclude(
        status__in=['RECEIVED', 'CLOSED']).count()
    shipping_holds = ShippingPlan.objects.filter(status='HOLD').count()

    profit_today = income - expense - prod_cost

    return {
        'base_currency': base,
        # Currencies present in the data with no usable rate. A non-empty list
        # means the money figures below are incomplete and must be shown as such.
        'fx_unconvertible': sorted(c for c in missing_rates if c),
        'orders': orders.count(), 'order_value': str(order_value),
        'production_actual': prod_actual, 'production_target': prod_target,
        'production_efficiency': round(prod_actual / prod_target * 100, 2) if prod_target else 0,
        'production_cost': str(prod_cost),
        'production_cost_local': str(prod_cost_bdt),
        'production_cost_local_currency': PRODUCTION_COST_CURRENCY,
        'income_today': str(income), 'expense_today': str(expense),
        'profit_today': str(profit_today),
        'receivable': str(receivable), 'payable': str(payable),
        'stock_value': str(stock_value), 'po_value': str(po_value),
        'purchase_outstanding': str(purchase_outstanding),
        'present_staff': present, 'staff_cost': str(staff_cost),
        'overtime_minutes': overtime,
        'ready_to_ship': orders.filter(status='READY_TO_SHIP').count(),
        'shipped': orders.filter(status='SHIPPED').count(),
        'delivered': orders.filter(status__in=['DELIVERED', 'COMPLETED']).count(),
        'sourcing_open': sourcing_open, 'procurement_open': procurement_open,
        'shipping_holds': shipping_holds,
        'alerts': Alert.objects.filter(actioned=False).count(),
        'red_alerts': Alert.objects.filter(actioned=False, level='RED').count(),
        'pending_actions': ActionItem.objects.filter(
            status__in=ActionItem.OPEN_STATUSES).count(),
        'pending_approvals': ApprovalRequest.objects.filter(status='PENDING').count(),
        'production_rows': prod_rows,
    }


@login_required
@require_http_methods(['GET','POST'])
def ceo_dashboard(request):
    from django.contrib import messages
    today=timezone.localdate()
    if request.method=='POST':
        action=request.POST.get('action','')
        try:
            with transaction.atomic():
                if action=='report':
                    slot=request.POST.get('slot')
                    if slot not in {'08:00','13:00','20:00','MANUAL'}: raise ValueError('Invalid report slot.')
                    payload=_ceo_summary(today)
                    CEOAutoReport.objects.update_or_create(
                        report_date=today,slot=slot,
                        defaults={'summary':payload,'outstanding_alerts':payload['alerts'],
                                  'pending_actions':payload['pending_actions'],'escalated_items':payload['red_alerts']}
                    )
                    messages.success(request,f'CEO Executive Report generated: {slot}.')
                elif action=='action_alert':
                    alert=get_object_or_404(Alert,pk=request.POST.get('alert_id'))
                    alert.actioned=True;alert.actioned_by=request.user;alert.actioned_at=timezone.now();alert.save()
                    messages.success(request,'CEO alert marked Actioned.')
        except Exception as exc:
            _handle_post_error(request, exc)
        return redirect('ceo_dashboard')

    summary=_ceo_summary(today)
    return render(request,'ceo_dashboard.html',{
        'today':today,'summary':summary,
        'orders':MasterOrder.objects.order_by('-created_at')[:50],
        'alerts':Alert.objects.filter(actioned=False).order_by('-created_at')[:25],
        'actions':ActionItem.objects.filter(status__in=ActionItem.OPEN_STATUSES).order_by('-created_at')[:25],
        'approvals':ApprovalRequest.objects.filter(status='PENDING').order_by('-created_at')[:25],
        'supplier_perf':SupplierPerformance.objects.select_related('supplier').order_by('supplier_score')[:20],
        'shipping':ShippingPlan.objects.select_related('order').order_by('-created_at')[:25],
        'reports':CEOAutoReport.objects.filter(report_date=today).order_by('slot'),
        'communications':Communication.objects.order_by('-created_at')[:20],
        'report_snapshots':ReportSnapshot.objects.order_by('-generated_at')[:15],
    })

@login_required
def ceo_report_csv(request):
    import csv
    today=timezone.localdate()
    summary=_ceo_summary(today)
    response=HttpResponse(content_type='text/csv')
    response['Content-Disposition']=f'attachment; filename="ceo-executive-report-{today}.csv"'
    w=csv.writer(response)
    w.writerow(['CEO EXECUTIVE REPORT',today])
    for k,v in summary.items():
        if k!='production_rows': w.writerow([k,v])
    w.writerow([])
    w.writerow(['Production Stage','Actual','Target','Efficiency %','Cost'])
    for r in summary['production_rows']:
        w.writerow([r['name'],r['actual'],r['target'],r['efficiency'],r['cost']])
    return response

@login_required
def api_ceo_dashboard(request):
    return JsonResponse(_ceo_summary(timezone.localdate()))

@login_required
def account_master(request):
    return render(request,'account_master.html')
