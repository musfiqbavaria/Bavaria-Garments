from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import FinalPage, PrintValidationEvent

@login_required
def page(request, slug):
    p=FinalPage.objects.filter(path="/final/"+slug+"/",active=True).first()
    if not p: return render(request,"final_az/missing.html",status=404)
    return render(request,"final_az/page.html",{"page":p})

@login_required
def step370(request):
    events=PrintValidationEvent.objects.order_by("-created_at")[:200]
    return render(request,"final_az/step370.html",{"events":events})

def manifest(request):
    return render(request,"final_az/manifest.html",{"count":FinalPage.objects.count()})
