from django.db import models

class FinalPage(models.Model):
    code=models.CharField(max_length=30,unique=True)
    title=models.CharField(max_length=220)
    path=models.CharField(max_length=240,unique=True)
    category=models.CharField(max_length=120,default="A-Z")
    active=models.BooleanField(default=True)

class PrintValidationEvent(models.Model):
    bundle_no=models.CharField(max_length=120)
    process=models.CharField(max_length=120)
    machine=models.CharField(max_length=120)
    operator=models.CharField(max_length=120)
    helper=models.CharField(max_length=120)
    result=models.CharField(max_length=20)
    reason=models.CharField(max_length=500,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
