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


class TenancyMiddleware:
    """Activate the signed-in user's organisation scope for the request.

    Scoped models read the active scope through portal.tenancy.ScopedManager, so
    establishing it here means the existing view code - hundreds of querysets -
    becomes site-scoped without being rewritten, and any queryset added later is
    scoped by default rather than by remembering to opt in.

    Outside a request there is no active scope, so management commands, Celery
    tasks and the shell see everything. That is intentional: they are system
    contexts. Use portal.tenancy.unscoped() to make a deliberate cross-site read
    inside a request visible at the call site.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .tenancy import activate, deactivate, resolve_user_scope

        token = activate(resolve_user_scope(getattr(request, 'user', None)))
        try:
            return self.get_response(request)
        finally:
            # Reset rather than clear: gunicorn reuses threads, so a leaked
            # scope would apply to whoever was served next on that worker.
            deactivate(token)
