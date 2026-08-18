from .models import AuditLog
class AuditMiddleware:
    def __init__(self,get_response): self.get_response=get_response
    def __call__(self,request):
        response=self.get_response(request)
        if request.user.is_authenticated and not request.path.startswith('/static/'):
            try:
                ip=request.META.get('HTTP_X_FORWARDED_FOR','').split(',')[0].strip() or request.META.get('REMOTE_ADDR')
                AuditLog.objects.create(user=request.user,method=request.method,path=request.path[:500],status_code=response.status_code,ip=ip or None)
            except Exception: pass
        return response
