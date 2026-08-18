from django.apps import AppConfig


class PortalConfig(AppConfig):
    default_auto_field='django.db.models.BigAutoField'
    name='portal'

    def ready(self):
        # attendance_schedule() caches the resolved shift per employee category
        # because calculate_attendance_day runs in a loop over every active
        # employee. Invalidate it whenever a shift is edited, so a change in
        # admin takes effect immediately rather than after a restart.
        from django.db.models.signals import post_delete, post_save
        from .models import AttendanceShift
        from .services import clear_shift_cache
        post_save.connect(clear_shift_cache, sender=AttendanceShift,
                          dispatch_uid='portal.clear_shift_cache.save')
        post_delete.connect(clear_shift_cache, sender=AttendanceShift,
                            dispatch_uid='portal.clear_shift_cache.delete')
