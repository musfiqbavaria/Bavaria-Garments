from datetime import datetime, time, timedelta
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import Alert, ApprovalRequest, AttendanceDailySummary, AttendanceEvent, AttendanceGatePass, AttendanceNPT, AttendanceOvertime, AttendanceHoliday, StockMovement, StockScan, ValueVariance

VARIANCE_RED_ALERT_THRESHOLD_BDT = Decimal('10.00')

@transaction.atomic
def apply_stock_scan(*, item, direction, quantity, barcode, reference, user, source_location='', destination_location='', manual_override=False, override_reason='', approval=None):
    quantity=Decimal(str(quantity))
    if quantity <= 0:
        raise ValueError('Quantity must be greater than zero.')
    if direction not in {'IN','OUT'}:
        raise ValueError('Direction must be IN or OUT.')
    if not barcode:
        raise ValueError('Barcode/QR scan is mandatory.')
    if not reference:
        raise ValueError('Order/reference number is mandatory.')
    if manual_override:
        if not override_reason:
            raise ValueError('Manual override reason is mandatory.')
        if not approval or approval.status != 'APPROVED':
            raise PermissionError('Senior approval is required for manual stock entry.')
    if direction == 'OUT':
        available=item.qty-item.reserved_qty
        if quantity > available:
            raise ValueError(f'Insufficient available stock. Available: {available}.')
        item.qty -= quantity
        movement_type='STOCK_OUT_SCAN'
    else:
        item.qty += quantity
        movement_type='STOCK_IN_SCAN'
    item.save(update_fields=['qty','updated_at'])
    scan=StockScan.objects.create(item=item,direction=direction,quantity=quantity,barcode=barcode,reference=reference,source_location=source_location,destination_location=destination_location,scanned_by=user,manual_override=manual_override,override_reason=override_reason,approval=approval)
    StockMovement.objects.create(item=item,movement_type=movement_type,quantity=quantity,reference=reference,barcode=barcode,performed_by=user)
    return scan

@transaction.atomic
def record_variance(*, reference, expected, actual, department='', currency='BDT', reason='', user=None):
    expected=Decimal(str(expected)); actual=Decimal(str(actual)); variance=actual-expected
    obj=ValueVariance.objects.create(reference=reference,department=department,currency=currency,expected_value=expected,actual_value=actual,variance_amount=variance,reason=reason,recorded_by=user)
    if currency.upper() in {'BDT','TK','৳'} and abs(variance) > VARIANCE_RED_ALERT_THRESHOLD_BDT:
        Alert.objects.create(title='Value Variance Red Alert',message=f'{reference}: expected {expected} BDT, actual {actual} BDT, variance {variance} BDT.',level='RED',department=department,reference=reference)
    return obj

def attendance_schedule(role_or_category):
    role=(role_or_category or '').strip().lower()
    if role in {'operator','helper'}:
        return {
            'start':time(8,0),'break1_in':time(13,0),'break1_out':time(14,0),
            'break2_in':None,'break2_out':None,'end':time(17,0),
            'scheduled_minutes':480,'grace_minutes':10,'ot_start':time(17,30)
        }
    return {
        'start':time(8,0),'break1_in':time(13,0),'break1_out':time(14,0),
        'break2_in':time(17,0),'break2_out':time(17,15),'end':time(20,0),
        'scheduled_minutes':660,'grace_minutes':10,'ot_start':time(20,30)
    }

def calculate_attendance_day(employee, work_date):
    from decimal import Decimal
    events=list(AttendanceEvent.objects.filter(employee=employee,occurred_at__date=work_date).order_by('occurred_at'))
    schedule=attendance_schedule(getattr(employee,'category',None) or employee.role)

    # Project 1 working week: Saturday-Thursday. Friday is weekly closed.
    is_weekly_closed=(work_date.weekday()==4)
    holiday=AttendanceHoliday.objects.filter(holiday_date=work_date).first()
    scheduled=0 if is_weekly_closed else schedule['scheduled_minutes']
    if holiday and not holiday.paid:
        scheduled=0

    daily_cost=Decimal(str(employee.daily_cost or 0))
    per_minute=(daily_cost/Decimal(schedule['scheduled_minutes'])) if schedule['scheduled_minutes'] else Decimal('0')

    if not events:
        status='WEEKEND' if is_weekly_closed else ('HOLIDAY' if holiday else 'ABSENT')
        return AttendanceDailySummary.objects.update_or_create(
            employee=employee,work_date=work_date,
            defaults={
                'scheduled_minutes':scheduled,'worked_minutes':0,'break_minutes':0,
                'overtime_minutes':0,'unpaid_minutes':0 if scheduled==0 else scheduled,
                'late_minutes':0,'early_leave_minutes':0,'office_gate_pass_paid_minutes':0,
                'gate_pass_unpaid_minutes':0,'npt_minutes':0,
                'scheduled_cost':daily_cost if scheduled else Decimal('0'),
                'worked_cost':0,'due_cost':daily_cost if scheduled else Decimal('0'),
                'late_cost':0,'gate_pass_paid_cost':0,'gate_pass_unpaid_cost':0,'npt_cost':0,
                'status':status,'calculation':{'events':0,'weekly_closed':is_weekly_closed,'holiday':holiday.name if holiday else None}
            }
        )[0]

    by_event={}
    for e in events:
        by_event.setdefault(e.event.upper(),[]).append(e.occurred_at)

    checkin=(by_event.get('CHECK_IN') or [events[0].occurred_at])[0]
    checkout=(by_event.get('CHECK_OUT') or [events[-1].occurred_at])[-1]

    tz=timezone.get_current_timezone()
    start_anchor=timezone.make_aware(datetime.combine(work_date,schedule['start']),tz)
    end_anchor=timezone.make_aware(datetime.combine(work_date,schedule['end']),tz)
    grace_end=start_anchor+timedelta(minutes=schedule['grace_minutes'])

    # Normal paid duty cannot start before the scheduled check-in or continue after scheduled checkout.
    bounded_in=max(checkin,start_anchor)
    bounded_out=min(checkout,end_anchor)
    raw_normal=max(0,int((bounded_out-bounded_in).total_seconds()//60))

    break_minutes=0
    break_ins=by_event.get('BREAK_IN',[])
    break_outs=by_event.get('BREAK_OUT',[])
    for a,b in zip(break_ins,break_outs):
        # Count only break time falling inside the normal scheduled window.
        aa=max(a,start_anchor); bb=min(b,end_anchor)
        if bb>aa:
            break_minutes += max(0,int((bb-aa).total_seconds()//60))

    # Project 1 scheduled net duty is authoritative (Staff 660m, Operator/Helper 480m).
    # Actual work is capped to the scheduled requirement; OT is separate and approval-controlled.
    worked=max(0,min(raw_normal-break_minutes,schedule['scheduled_minutes'] if scheduled else raw_normal))

    late=max(0,int((checkin-grace_end).total_seconds()//60)) if scheduled and checkin>grace_end else 0
    early=max(0,int((end_anchor-checkout).total_seconds()//60)) if scheduled and checkout<end_anchor else 0

    # Only authorized OT contributes to overtime minutes.
    overtime=sum(AttendanceOvertime.objects.filter(
        employee=employee,work_date=work_date,status='APPROVED'
    ).values_list('minutes',flat=True))

    paid_gate=sum(g.minutes for g in AttendanceGatePass.objects.filter(
        employee=employee,out_at__date=work_date,pass_type='PAID',status='APPROVED'
    ))
    unpaid_gate=sum(g.minutes for g in AttendanceGatePass.objects.filter(
        employee=employee,out_at__date=work_date,pass_type='UNPAID',status='APPROVED'
    ))
    npt=sum(AttendanceNPT.objects.filter(employee=employee,work_date=work_date).values_list('minutes',flat=True))

    unpaid=max(0,scheduled-min(worked+paid_gate,scheduled)) if scheduled else 0
    if scheduled:
        status='PRESENT' if worked or paid_gate else 'ABSENT'
    else:
        status='WEEKEND_WORK' if is_weekly_closed else ('HOLIDAY_WORK' if holiday else 'PRESENT')

    return AttendanceDailySummary.objects.update_or_create(
        employee=employee,work_date=work_date,
        defaults={
            'scheduled_minutes':scheduled,'worked_minutes':worked,'break_minutes':break_minutes,
            'overtime_minutes':overtime,'unpaid_minutes':unpaid,'late_minutes':late,
            'early_leave_minutes':early,'office_gate_pass_paid_minutes':paid_gate,
            'gate_pass_unpaid_minutes':unpaid_gate,'npt_minutes':npt,
            'scheduled_cost':daily_cost if scheduled else Decimal('0'),
            'worked_cost':(per_minute*Decimal(worked)).quantize(Decimal('0.01')),
            'due_cost':(per_minute*Decimal(unpaid)).quantize(Decimal('0.01')),
            'late_cost':(per_minute*Decimal(late)).quantize(Decimal('0.01')),
            'gate_pass_paid_cost':(per_minute*Decimal(paid_gate)).quantize(Decimal('0.01')),
            'gate_pass_unpaid_cost':(per_minute*Decimal(unpaid_gate)).quantize(Decimal('0.01')),
            'npt_cost':(per_minute*Decimal(npt)).quantize(Decimal('0.01')),
            'status':status,
            'calculation':{
                'check_in':checkin.isoformat(),'check_out':checkout.isoformat(),
                'events':len(events),'category':getattr(employee,'category',''),
                'grace_minutes':schedule['grace_minutes'],'weekly_closed':is_weekly_closed,
                'holiday':holiday.name if holiday else None,'approved_ot_minutes':overtime
            }
        }
    )[0]

@transaction.atomic
def apply_material_movement(*, lot, movement_type, quantity, barcode, reference, user, source_location=None, destination_location=None, order_reference='', purchase_order_no='', manual_entry=False, reason='', approval=None):
    from .models import MaterialMovement, MaterialReservation
    quantity=Decimal(str(quantity))
    if quantity <= 0: raise ValueError('Quantity must be greater than zero.')
    if not barcode: raise ValueError('Roll/material barcode scan is mandatory.')
    if not reference: raise ValueError('Reference number is mandatory.')
    if manual_entry:
        if not reason: raise ValueError('Reason is mandatory for manual entry.')
        if not approval or approval.status != 'APPROVED': raise PermissionError('Senior approval is required for manual material entry.')
    movement_type=movement_type.upper()
    valid={'STOCK_IN_SCAN','STOCK_OUT_SCAN','TRANSFER','RESERVE','RELEASE','WASTAGE','REJECT','RETURN','ADJUSTMENT'}
    if movement_type not in valid: raise ValueError('Invalid material movement type.')
    if movement_type in {'STOCK_OUT_SCAN','WASTAGE','REJECT'}:
        if quantity > lot.available_qty: raise ValueError(f'Insufficient available material. Available: {lot.available_qty}.')
        lot.current_qty -= quantity
        if movement_type=='WASTAGE': lot.stock_status='WASTAGE'
        if movement_type=='REJECT': lot.stock_status='REJECT'
    elif movement_type in {'STOCK_IN_SCAN','RETURN'}:
        lot.current_qty += quantity
        if movement_type=='RETURN': lot.stock_status='RETURN'
    elif movement_type=='RESERVE':
        if quantity > lot.available_qty: raise ValueError(f'Insufficient available material to reserve. Available: {lot.available_qty}.')
        lot.reserved_qty += quantity
        MaterialReservation.objects.create(material=lot.material,lot=lot,order_reference=order_reference or reference,quantity=quantity,reserved_by=user)
    elif movement_type=='RELEASE':
        if quantity > lot.reserved_qty: raise ValueError(f'Release exceeds reserved quantity. Reserved: {lot.reserved_qty}.')
        lot.reserved_qty -= quantity
    elif movement_type=='TRANSFER':
        if quantity > lot.current_qty: raise ValueError(f'Transfer exceeds lot balance. Balance: {lot.current_qty}.')
        if destination_location: lot.location=destination_location
    elif movement_type=='ADJUSTMENT':
        if not manual_entry: raise PermissionError('Adjustment must use approved manual entry.')
        lot.current_qty=quantity
    lot.save()
    movement=MaterialMovement.objects.create(material=lot.material,lot=lot,movement_type=movement_type,quantity=quantity,unit_cost=lot.unit_cost,barcode=barcode,reference=reference,order_reference=order_reference,purchase_order_no=purchase_order_no,source_location=source_location,destination_location=destination_location,performed_by=user,manual_entry=manual_entry,reason=reason,approval=approval,balance_after=lot.current_qty)
    if lot.material.reorder_level and lot.current_qty <= lot.material.reorder_level:
        Alert.objects.get_or_create(title='Material Reorder Alert',reference=lot.material.material_code,actioned=False,defaults={'message':f'{lot.material.material_code} {lot.material.name} is at/below reorder level. Current lot balance: {lot.current_qty} {lot.material.uom}.','level':'WARNING','department':'Stock'})
    return movement


def assert_profit_before_spend(check):
    """Central Project 1 spend authorization guard."""
    check.save()
    if check.final_decision=='BLOCK':
        raise PermissionError('Spend blocked: projected order profit becomes negative or revenue is unavailable.')
    if check.final_decision=='HOLD':
        raise PermissionError('Spend held: projected margin falls below the minimum permitted margin.')
    if check.final_decision=='ALLOW_WITH_APPROVAL':
        if not check.approval_id or check.approval.status!='APPROVED':
            raise PermissionError('Spend requires an APPROVED senior Approval Request.')
    if check.final_decision not in {'ALLOW','ALLOW_WITH_APPROVAL'}:
        raise PermissionError('Spend has not been authorized.')
    return True
