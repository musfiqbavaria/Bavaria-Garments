from django.core.management.base import BaseCommand
from final_az.models import FinalPage
from final_az.page_registry import PAGES
import re

class Command(BaseCommand):
    def handle(self,*args,**kwargs):
        for i,title in enumerate(PAGES,1):
            slug=re.sub(r"[^a-z0-9]+","-",title.lower()).strip("-")
            FinalPage.objects.update_or_create(
                code=f"PAGE-{i:03d}",
                defaults={"title":title,"path":f"/final/{slug}-{i:03d}/","category":"A-Z","active":True})
        self.stdout.write(self.style.SUCCESS(f"Final A-Z registry loaded: {FinalPage.objects.count()} views."))
