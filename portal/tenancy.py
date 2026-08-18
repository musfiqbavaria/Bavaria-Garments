"""Organisation scoping for Project 1.

The organisational requirement is multi-company, multi-country, multi-factory,
multi-unit, multi-warehouse and multi-store. ``OrganizationNode`` modelled that
tree from the start, but it was referenced by only five places - UserProfile.scope
and a handful of location fields on material and asset records. Orders,
employees, alerts, every production plan, every QC record, every supplier and
every finance row had no company, country or factory link at all, so there was no
way to attribute a record to a site and no query-level control stopping one
factory reading another's data. See TECHNICAL_ASSESSMENT.md 6.2.

How scoping works
-----------------
A user's scope is the ``OrganizationNode`` on their ``UserProfile``. Scoped
models expose ``objects`` as a :class:`ScopedManager`, which filters to the
active scope's subtree. The active scope is set per request by
``portal.middleware.TenancyMiddleware``, so existing view code becomes scoped
without every queryset being rewritten.

Subtree membership uses a materialised path (``OrganizationNode.path``) rather
than recursion, so "this factory and everything under it" is one indexed
``path__startswith`` predicate on any database.

Three deliberate choices
------------------------
1.  **No active scope means no filtering.** Management commands, Celery tasks and
    the shell run unfiltered, which is what a system context should do. Only a
    request establishes a scope.

2.  **A user without a scope sees everything.** That is head office. Assigning a
    scope is what narrows someone's view.

3.  **Records with no scope are visible to everyone unless strict mode is on.**
    Every row that exists today has ``scope=None``, so filtering them out would
    hide all existing data the moment this ships. ``TENANCY_STRICT=1`` flips that
    once the backfill is done; ``manage.py report_unscoped`` shows what is left to
    assign. This is the same staged approach used for the authorisation rollout.

``_base_manager`` and ``_default_manager`` are pinned to the unscoped manager on
every scoped model, because Django uses them for related-object descriptors and
cascade deletes. A scoped ``_base_manager`` would make ``order.cutting_plans``
silently incomplete and could leave a delete half-done.
"""

import contextlib
import contextvars

from django.conf import settings
from django.db import models

#: The organisation node whose subtree the current request may see.
#: None means "no scoping" - a system context, or head office.
_active_scope = contextvars.ContextVar('portal_active_scope', default=None)

#: Set while an explicit unscoped block is open, so a scope activated further out
#: is ignored for the duration.
_bypass = contextvars.ContextVar('portal_scope_bypass', default=False)


def current_scope():
    """The active OrganizationNode, or None."""
    if _bypass.get():
        return None
    return _active_scope.get()


def activate(node):
    """Activate a scope for this context. Returns the contextvar token."""
    return _active_scope.set(node)


def deactivate(token=None):
    if token is not None:
        _active_scope.reset(token)
    else:
        _active_scope.set(None)


@contextlib.contextmanager
def scope_context(node):
    """Run a block with ``node`` as the active scope."""
    token = _active_scope.set(node)
    try:
        yield node
    finally:
        _active_scope.reset(token)


@contextlib.contextmanager
def unscoped():
    """Run a block with scoping disabled.

    For deliberate cross-site work: consolidated reporting, the exchange-rate
    feed, the audit purge. Use it explicitly so a cross-site read is visible in
    the code rather than accidental.
    """
    token = _bypass.set(True)
    try:
        yield
    finally:
        _bypass.reset(token)


def strict_mode():
    return bool(getattr(settings, 'TENANCY_STRICT', False))


def resolve_user_scope(user):
    """The scope a user should see, or None for unrestricted.

    Superusers are never scoped: they are the break-glass account. A user with no
    UserProfile, or a profile with no scope, is treated as head office.
    """
    if not user or not user.is_authenticated or user.is_superuser:
        return None
    profile = getattr(user, 'profile', None)
    return getattr(profile, 'scope', None) if profile else None


def subtree_filter(node, field='scope'):
    """Q object matching records at ``node`` or any descendant of it."""
    from django.db.models import Q

    if node is None:
        return Q()
    # A materialised path keeps this to one indexed prefix match instead of a
    # recursive walk of the tree.
    prefix = node.path or f'/{node.pk}/'
    condition = Q(**{f'{field}__path__startswith': prefix})
    if not strict_mode():
        # Unassigned records stay visible until the backfill is done.
        condition |= Q(**{f'{field}__isnull': True})
    return condition


class ScopedQuerySet(models.QuerySet):
    """QuerySet that narrows to the active organisation scope."""

    def for_scope(self, node):
        if node is None:
            return self
        return self.filter(subtree_filter(node))

    def for_user(self, user):
        """Explicitly narrow to what ``user`` may see.

        Useful where a queryset is built outside a request, or where the caller
        wants the intent visible at the call site.
        """
        return self.for_scope(resolve_user_scope(user))

    def all_scopes(self):
        """Escape hatch that returns every row regardless of active scope."""
        return ScopedQuerySet(self.model, using=self._db)


class ScopedManager(models.Manager.from_queryset(ScopedQuerySet)):
    """Default manager for scoped models.

    ``get_queryset`` applies the active scope, so code that already reads
    ``Model.objects`` is scoped without modification. When no scope is active -
    a management command, a Celery task, the shell - nothing is filtered.
    """

    def get_queryset(self):
        queryset = super().get_queryset()
        node = current_scope()
        if node is None:
            return queryset
        return queryset.filter(subtree_filter(node))


#: Name of the deliberately unscoped manager every scoped model declares. Its
#: presence is what marks a model as scoped - NOT the presence of a field called
#: "scope". UserProfile.scope records which site a user is assigned to, which is
#: the definition of a scope rather than a scoped record, and scoping it would
#: make resolve_user_scope unable to read the very profile it needs.
UNSCOPED_MANAGER_NAME = 'all_objects'


def is_scoped_model(model):
    return hasattr(model, UNSCOPED_MANAGER_NAME) and any(
        f.name == 'scope' for f in model._meta.get_fields())


def scoped_models():
    """Every model that participates in organisation scoping."""
    from django.apps import apps

    return sorted(
        (m for m in apps.get_app_config('portal').get_models() if is_scoped_model(m)),
        key=lambda m: m.__name__,
    )
