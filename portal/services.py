import logging
import zoneinfo
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import Alert, ApprovalRequest, AttendanceDailySummary, AttendanceEvent, AttendanceGatePass, AttendanceNPT, AttendanceOvertime, AttendanceHoliday, AttendanceShift, StockMovement, StockScan, ValueVariance

logger = logging.getLogger('portal.services')

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

#: Fallback shifts, used only when no AttendanceShift row is configured.
#: These reproduce the Project 1 defaults documented for v14.
_DEFAULT_SHIFTS = {
    'OPERATOR': {
        'start': time(8, 0), 'break1_in': time(13, 0), 'break1_out': time(14, 0),
        'break2_in': None, 'break2_out': None, 'end': time(17, 0),
        'scheduled_minutes': 480, 'grace_minutes': 10, 'ot_start': time(17, 30),
        'source': 'default',
    },
    'STAFF': {
        'start': time(8, 0), 'break1_in': time(13, 0), 'break1_out': time(14, 0),
        'break2_in': time(17, 0), 'break2_out': time(17, 15), 'end': time(20, 0),
        'scheduled_minutes': 660, 'grace_minutes': 10, 'ot_start': time(20, 30),
        'source': 'default',
    },
}
_DEFAULT_SHIFTS['HELPER'] = dict(_DEFAULT_SHIFTS['OPERATOR'])

#: Resolved shifts, keyed by category. calculate_attendance_day is called in a
#: loop over every active employee, so the lookup must not be a query per call.
#: Cleared by a signal whenever an AttendanceShift changes - see portal/apps.py.
_SHIFT_CACHE = {}


def clear_shift_cache(*args, **kwargs):
    """Drop the resolved-shift cache. Wired to AttendanceShift save/delete."""
    _SHIFT_CACHE.clear()


def resolve_employee_category(*hints):
    """Return 'OPERATOR', 'HELPER' or 'STAFF' from category and/or role hints.

    Employee.category defaults to 'STAFF' and is always truthy, so callers pass
    both category and role and the first hint that names a floor grade wins.
    """
    # Only a floor grade wins, and it wins from ANY hint. Returning early on
    # 'staff' would let category's always-truthy 'STAFF' default swallow the role
    # again, which is the original defect. STAFF is the fallback, never a match.
    for hint in hints:
        value = (hint or '').strip().casefold()
        if value == 'operator':
            return 'OPERATOR'
        if value == 'helper':
            return 'HELPER'
    return 'STAFF'


def _schedule_from_shift(shift):
    """Map an AttendanceShift row onto the schedule dict the engine uses.

    The model has no ot_start column. Overtime becomes payable ot_break_minutes
    after scheduled checkout, which reproduces the documented rule exactly:
    17:00 + 30 = 17:30 for operators, 20:00 + 30 = 20:30 for staff.
    """
    ot_start = (datetime.combine(date.min, shift.check_out)
                + timedelta(minutes=shift.ot_break_minutes)).time()
    return {
        'start': shift.check_in,
        'break1_in': shift.break1_in, 'break1_out': shift.break1_out,
        'break2_in': shift.break2_in, 'break2_out': shift.break2_out,
        'end': shift.check_out,
        'scheduled_minutes': shift.mandatory_minutes,
        'grace_minutes': shift.grace_minutes,
        'ot_start': ot_start,
        'source': f'AttendanceShift:{shift.code}',
    }


def attendance_schedule(*hints):
    """Resolve the working shift for an employee.

    Reads AttendanceShift from the database. The model already carried
    check-in, breaks, checkout, mandatory and grace minutes per category, was
    registered in admin, and was never read by anything: the two shifts were
    hardcoded in Python, so a shift configured by an administrator had no
    effect. That blocked multi-factory operation, where shifts differ by site
    and by law. See TECHNICAL_ASSESSMENT.md 5.4.

    Falls back to the documented Project 1 defaults when nothing is configured,
    so an unseeded database still calculates.
    """
    category = resolve_employee_category(*hints)
    if category in _SHIFT_CACHE:
        return _SHIFT_CACHE[category]

    shift = (AttendanceShift.objects
             .filter(employee_category=category, active=True)
             .order_by('code').first())
    if shift is None and category == 'HELPER':
        # Helpers share the operator shift unless one is configured for them.
        shift = (AttendanceShift.objects
                 .filter(employee_category='OPERATOR', active=True)
                 .order_by('code').first())

    schedule = _schedule_from_shift(shift) if shift else dict(_DEFAULT_SHIFTS[category])
    _SHIFT_CACHE[category] = schedule
    return schedule


def employee_timezone(employee):
    """The clock this employee's shift is measured against.

    Attendance anchors - 08:00 check-in, the break window, checkout - must resolve
    in the timezone the workforce actually works in, not the server's. Before the
    organisation scope existed there was nowhere to record that, so every site was
    measured against one global TIME_ZONE (TECHNICAL_ASSESSMENT.md 5.5).

    Falls back to settings.TIME_ZONE when the employee has no site assigned, so
    unscoped data keeps behaving exactly as it did.
    """
    node=getattr(employee,'scope',None)
    if node is not None:
        name=node.effective_timezone
        if name:
            try:
                return zoneinfo.ZoneInfo(name)
            except Exception:
                # A bad IANA name must not stop payroll; fall through and log.
                logger.warning('invalid timezone %r on organisation node %s; '
                               'using the project default', name, node.pk)
    return timezone.get_current_timezone()


def _overlap_minutes(start, end, window_start, window_end):
    """Whole minutes of [start, end] that fall inside the scheduled window."""
    if not start or not end:
        return 0
    lo = max(start, window_start)
    hi = min(end, window_end)
    if hi <= lo:
        return 0
    return max(0, int((hi - lo).total_seconds() // 60))


def calculate_attendance_day(employee, work_date):
    from decimal import Decimal
    events=list(AttendanceEvent.objects.filter(employee=employee,occurred_at__date=work_date).order_by('occurred_at'))
    schedule=attendance_schedule(getattr(employee,'category',None),getattr(employee,'role',None))

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

    tz=employee_timezone(employee)
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

    late=max(0,int((checkin-grace_end).total_seconds()//60)) if scheduled and checkin>grace_end else 0
    early=max(0,int((end_anchor-checkout).total_seconds()//60)) if scheduled and checkout<end_anchor else 0

    # Only authorized OT contributes to overtime minutes.
    overtime=sum(AttendanceOvertime.objects.filter(
        employee=employee,work_date=work_date,status='APPROVED'
    ).values_list('minutes',flat=True))

    # Gate-pass minutes are clipped to the scheduled window. A pass running past
    # checkout must not deduct time the employee was never scheduled for.
    gate_passes=list(AttendanceGatePass.objects.filter(
        employee=employee,out_at__date=work_date,status='APPROVED'
    ))
    paid_gate=sum(_overlap_minutes(g.out_at,g.in_at,start_anchor,end_anchor)
                  for g in gate_passes if g.pass_type=='PAID')
    unpaid_gate=sum(_overlap_minutes(g.out_at,g.in_at,start_anchor,end_anchor)
                    for g in gate_passes if g.pass_type=='UNPAID')
    npt=sum(AttendanceNPT.objects.filter(employee=employee,work_date=work_date).values_list('minutes',flat=True))

    # Paid duty = time inside the shift, less breaks, less authorised UNPAID
    # absence.
    #
    # Two arithmetic errors are corrected here (TECHNICAL_ASSESSMENT.md 5.2/5.3):
    #
    #  * An APPROVED UNPAID gate pass was recorded but never deducted from
    #    worked_minutes, so a three-hour unpaid absence still paid a full day.
    #  * unpaid_minutes was `scheduled - min(worked + paid_gate, scheduled)`.
    #    A paid gate pass sits between check-in and check-out, so its minutes
    #    were already inside worked_minutes; adding them again understated
    #    unpaid time. An employee attending 400 of 480 minutes with a 60-minute
    #    paid pass was billed 20 minutes unpaid instead of 80.
    #
    # paid_gate is now informational only: it reports how much paid duty was
    # spent off-site and is a SUBSET of worked_minutes, never an addition to it.
    attended=max(0,raw_normal-break_minutes)
    paid_duty=max(0,attended-unpaid_gate)
    worked=min(paid_duty,schedule['scheduled_minutes']) if scheduled else paid_duty

    unpaid=max(0,scheduled-worked) if scheduled else 0
    if scheduled:
        status='PRESENT' if attended else 'ABSENT'
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
                'holiday':holiday.name if holiday else None,'approved_ot_minutes':overtime,
                'shift_source':schedule.get('source','default'),
                'attended_minutes':attended,'paid_duty_minutes':paid_duty,
                'unpaid_gate_deducted':unpaid_gate,
                'paid_gate_within_worked':paid_gate
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
