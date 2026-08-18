from .models import Alert, ActionItem, Employee, AttendanceEvent
from django.utils import timezone

def global_portal(request):
    if not request.user.is_authenticated: return {}
    today=timezone.localdate()
    present_ids=AttendanceEvent.objects.filter(occurred_at__date=today,event__iexact='CHECK_IN').values_list('employee_id',flat=True).distinct()
    present=Employee.objects.filter(id__in=present_ids)
    return {
      'global_alerts':Alert.objects.filter(actioned=False).count(),
      'global_red_alerts':Alert.objects.filter(actioned=False,level='RED').count(),
      'global_actions':ActionItem.objects.exclude(status='DONE').count(),
      'today_present_staff':present.count(),
      'today_staff_cost':sum((e.daily_cost for e in present),0),
      'login_no':1,
    }
