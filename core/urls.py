from django.contrib import admin
from django.urls import path, include

# Media is deliberately NOT served by URL, in development or production.
# Uploads include confidential documents, payslips, QC photographs, buyer
# signatures and proof of delivery. Serving them from /media/ would bypass the
# per-file permission check in portal.views._file_access_decision and the
# FileAccessLog audit trail. Every file is delivered through
# /files/<resource_type>/<id>/<view|preview|download|print>/ instead, and
# nginx returns 404 for /media/. See TECHNICAL_ASSESSMENT.md 4.3.
urlpatterns=[path('admin/',admin.site.urls),path('',include('portal.urls'))]
