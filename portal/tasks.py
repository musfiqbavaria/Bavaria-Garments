from celery import shared_task
from django.utils import timezone
from .models import ReportSnapshot, Alert, ActionItem, MasterOrder
@shared_task
def scheduled_report_snapshot():
    data={'alerts':Alert.objects.filter(actioned=False).count(),'red_alerts':Alert.objects.filter(actioned=False,level='RED').count(),'actions':ActionItem.objects.exclude(status='DONE').count(),'open_orders':MasterOrder.objects.exclude(status='DELIVERED').count()}
    ReportSnapshot.objects.create(snapshot_type='SCHEDULED',generated_at=timezone.now(),data=data)
    return data
