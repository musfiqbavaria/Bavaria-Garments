"""Recompute OrganizationNode.path and depth for the whole tree.

Subtree scoping is a prefix match on the materialised path, so a wrong path means
a site silently sees the wrong records. save() maintains it, but a path must be
rebuildable after a bulk import, a fixture load, or a direct SQL edit.

    python manage.py rebuild_org_paths
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from portal.models import OrganizationNode


class Command(BaseCommand):
    help = 'Recompute the materialised path and depth for every organisation node.'

    @transaction.atomic
    def handle(self, *args, **options):
        updated = 0
        # Breadth-first from the roots, so a parent's path is always set before
        # its children are computed.
        level = list(OrganizationNode.objects.filter(parent__isnull=True))
        depth = 0
        while level:
            next_level = []
            for node in level:
                path = f'{node.parent.path}{node.pk}/' if node.parent_id else f'/{node.pk}/'
                if node.path != path or node.depth != depth:
                    OrganizationNode.objects.filter(pk=node.pk).update(path=path, depth=depth)
                    updated += 1
                node.path = path
                node.depth = depth
                next_level.extend(OrganizationNode.objects.filter(parent_id=node.pk))
            level = next_level
            depth += 1
            if depth > 30:
                self.stderr.write(self.style.ERROR(
                    'Aborting: the organisation tree is deeper than 30 levels, '
                    'which suggests a parent cycle.'))
                raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(
            f'Rebuilt {updated} path(s) across {OrganizationNode.objects.count()} node(s).'))
