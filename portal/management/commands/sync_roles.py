"""Create a Django group for every Project 1 role and align memberships.

``data/roles.json`` listed the 20 roles but was never loaded by any code, and
``UserProfile.role`` was written once by the seeder and never read
(TECHNICAL_ASSESSMENT.md 4.2). Groups are the carrier the authorisation layer
resolves roles from, so they have to exist and stay in step.

Idempotent: safe to run on every deploy.

    python manage.py sync_roles
    python manage.py sync_roles --check     verify only, change nothing
"""
import json
from pathlib import Path

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from portal import roles as role_defs
from portal.models import UserProfile


class Command(BaseCommand):
    help = 'Create a Django group per role and align UserProfile.role to group membership.'

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true',
                            help='Report what would change without writing anything.')

    def handle(self, *args, **options):
        check_only = options['check']
        base = Path(__file__).resolve().parents[3]
        roles_file = base / 'data' / 'roles.json'

        if not roles_file.exists():
            raise CommandError(f'{roles_file} is missing.')

        declared = json.loads(roles_file.read_text(encoding='utf-8'))
        if not isinstance(declared, list) or not declared:
            raise CommandError('data/roles.json must contain a non-empty list of role names.')

        # The role constants in portal/roles.py drive every access decision, so
        # a mismatch with the data file is a real defect, not a warning.
        declared_set = {str(r).strip() for r in declared}
        if declared_set != set(role_defs.ALL_ROLES):
            only_file = sorted(declared_set - set(role_defs.ALL_ROLES))
            only_code = sorted(set(role_defs.ALL_ROLES) - declared_set)
            raise CommandError(
                'data/roles.json and portal/roles.py disagree.\n'
                f'  only in data/roles.json: {only_file}\n'
                f'  only in portal/roles.py: {only_code}\n'
                'Update both so access policy cannot drift from the role list.'
            )

        created = []
        for name in sorted(declared_set):
            if check_only:
                if not Group.objects.filter(name=name).exists():
                    created.append(name)
                continue
            _group, was_created = Group.objects.get_or_create(name=name)
            if was_created:
                created.append(name)

        # Mirror each profile's role into group membership so both sources agree.
        aligned = []
        unknown = []
        profiles = UserProfile.objects.select_related('user').all()
        for profile in profiles:
            canonical = role_defs.canonical_role(profile.role)
            if canonical is None:
                unknown.append(f'{profile.user.username} -> {profile.role!r}')
                continue
            if check_only:
                if not profile.user.groups.filter(name=canonical).exists():
                    aligned.append(f'{profile.user.username} -> {canonical}')
                continue
            group = Group.objects.get(name=canonical)
            if not profile.user.groups.filter(pk=group.pk).exists():
                profile.user.groups.add(group)
                aligned.append(f'{profile.user.username} -> {canonical}')

        verb = 'would create' if check_only else 'created'
        self.stdout.write(f'{verb} {len(created)} group(s)'
                          + (f': {", ".join(created)}' if created else ''))
        verb = 'would align' if check_only else 'aligned'
        self.stdout.write(f'{verb} {len(aligned)} membership(s)'
                          + (f': {", ".join(aligned)}' if aligned else ''))

        if unknown:
            # Do not fail: an unrecognised role simply grants nothing, because
            # user_roles() ignores names it cannot map. But it must be visible.
            self.stderr.write(self.style.WARNING(
                f'{len(unknown)} profile(s) carry a role not in data/roles.json '
                f'and therefore grant no access: {"; ".join(unknown)}'))

        self.stdout.write(self.style.SUCCESS(
            f'{len(declared_set)} roles synchronised across {profiles.count()} profile(s).'))
