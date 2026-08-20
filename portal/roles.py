"""Role definitions and role resolution for Project 1.

The platform defines 20 roles in ``data/roles.json`` and stores one on
``UserProfile.role``, but before this module nothing read either of them: there
were 99 ``@login_required`` views and zero permission checks, so a Helper could
reach the CEO dashboard, payroll costs and supplier bank details, and could POST
to every department dashboard. See TECHNICAL_ASSESSMENT.md 4.2.

A user's effective roles are the union of:
  * ``UserProfile.role`` - the single role assigned to them, and
  * the Django groups they belong to, whose names mirror the role names.

Groups are kept in step by ``manage.py sync_roles``, which is also run by
``seed_project1``. Django groups are used as the carrier so roles can be
administered from the admin site and so a user can hold more than one.

Role names are compared case-insensitively and ignoring surrounding whitespace,
because they arrive from JSON, from a free-text CharField and from group names.
"""

CEO = 'CEO'
COUNTRY_MANAGER = 'Country Manager'
AREA_MANAGER = 'Area Manager'
OPERATION_MANAGER = 'Operation Manager'
FINANCE_MANAGER = 'Finance Manager'
IT_MANAGER = 'IT Manager'
UNIT_MANAGER = 'Unit Manager'
BRANCH_MANAGER = 'Branch Manager'
BOI_MANAGER = 'BOI Manager'
MERCHANDISING_MANAGER = 'Merchandising Manager'
HR_MANAGER = 'HR Manager'
SHIPPING_MANAGER = 'Shipping Manager'
IN_CHARGE = 'In-Charge'
FRANCHISE_MANAGER = 'Franchise Manager'
RETAIL_STORE_MANAGER = 'Retail Store Manager'
VENDOR_MANAGER = 'Vendor Manager'
DEVELOPER = 'Developer'
STAFF = 'Staff'
OPERATOR = 'Operator'
HELPER = 'Helper'

#: Every role the platform knows about. Must stay in step with data/roles.json;
#: ``sync_roles`` fails loudly if the two disagree.
ALL_ROLES = frozenset({
    CEO, COUNTRY_MANAGER, AREA_MANAGER, OPERATION_MANAGER, FINANCE_MANAGER,
    IT_MANAGER, UNIT_MANAGER, BRANCH_MANAGER, BOI_MANAGER, MERCHANDISING_MANAGER,
    HR_MANAGER, SHIPPING_MANAGER, IN_CHARGE, FRANCHISE_MANAGER,
    RETAIL_STORE_MANAGER, VENDOR_MANAGER, DEVELOPER, STAFF, OPERATOR, HELPER,
})

# --- functional groupings ---------------------------------------------------
# These are the vocabulary the route policy and the file-access rules are
# written in. Adjusting who can see what is a matter of editing these sets.

EXECUTIVE = frozenset({CEO, COUNTRY_MANAGER})

#: Every supervisory role. Excludes Developer, Staff, Operator and Helper.
MANAGEMENT = frozenset({
    CEO, COUNTRY_MANAGER, AREA_MANAGER, OPERATION_MANAGER, FINANCE_MANAGER,
    IT_MANAGER, UNIT_MANAGER, BRANCH_MANAGER, BOI_MANAGER, MERCHANDISING_MANAGER,
    HR_MANAGER, SHIPPING_MANAGER, IN_CHARGE, FRANCHISE_MANAGER,
    RETAIL_STORE_MANAGER, VENDOR_MANAGER,
})

FINANCE = frozenset({CEO, COUNTRY_MANAGER, FINANCE_MANAGER, BOI_MANAGER})
HR = frozenset({CEO, COUNTRY_MANAGER, HR_MANAGER, UNIT_MANAGER})
COMMERCIAL = frozenset({CEO, COUNTRY_MANAGER, AREA_MANAGER, MERCHANDISING_MANAGER})
PRODUCTION = frozenset({
    CEO, COUNTRY_MANAGER, AREA_MANAGER, OPERATION_MANAGER, UNIT_MANAGER,
    IN_CHARGE, MERCHANDISING_MANAGER,
})
QUALITY = frozenset({CEO, COUNTRY_MANAGER, OPERATION_MANAGER, UNIT_MANAGER, IN_CHARGE})
STOCK = frozenset({
    CEO, COUNTRY_MANAGER, OPERATION_MANAGER, UNIT_MANAGER, IN_CHARGE, FINANCE_MANAGER,
})
ASSETS = STOCK | {IT_MANAGER}
PROCUREMENT = frozenset({
    CEO, COUNTRY_MANAGER, OPERATION_MANAGER, FINANCE_MANAGER,
    MERCHANDISING_MANAGER, VENDOR_MANAGER, UNIT_MANAGER,
})
SHIPPING = frozenset({
    CEO, COUNTRY_MANAGER, SHIPPING_MANAGER, OPERATION_MANAGER, UNIT_MANAGER,
})
#: Developer is deliberately confined to the technical surface and is kept out
#: of finance, HR and payroll: separation of duties between the person who can
#: change the system and the people who authorise transactions in it.
IT = frozenset({CEO, IT_MANAGER, DEVELOPER})

#: Profit gates and capacity decisions sit across finance and commercial.
GATES = FINANCE | COMMERCIAL | {OPERATION_MANAGER}

# --- approval authority -----------------------------------------------------
#: Roles permitted to decide an ApprovalRequest - the "senior approval" that
#: gates manual stock overrides, asset retirement/disposal, manual production
#: entry, conditional QC release, profit-before-spend, quick-order acceptance
#: and delivery-SLA exceptions.
#:
#: Signed off as: all manager/In-Charge roles plus CEO. Developer is excluded so
#: that whoever maintains the system cannot authorise business transactions.
#: Staff, Operator and Helper cannot approve.
SENIOR_APPROVER_ROLES = MANAGEMENT

#: Optional narrowing for specific approval types. An approval_type absent from
#: this table falls back to SENIOR_APPROVER_ROLES. Keys are compared
#: case-insensitively. Tighten here rather than in the view code.
APPROVAL_AUTHORITY = {
    'PROFIT_BEFORE_SPEND': FINANCE | {OPERATION_MANAGER},
    'ASSET_DISPOSAL': FINANCE | {OPERATION_MANAGER},
    'ASSET_RETIREMENT': FINANCE | {OPERATION_MANAGER},
    'BANK_DETAIL_CHANGE': FINANCE,
    'SUPPLIER_PAYMENT': FINANCE,
    # Recruitment approvals carry a candidate's personal data and end in a real
    # Employee record and a portal account, so they are narrowed to HR rather
    # than left on the sixteen-role management default.
    'RECRUITMENT_APPLICATION': HR,
    'RECRUITMENT_HIRING': HR,
}


def _normalise(name):
    return (name or '').strip().casefold()


_BY_NORMALISED = {_normalise(r): r for r in ALL_ROLES}


def canonical_role(name):
    """Return the canonical spelling of a role name, or None if unknown."""
    return _BY_NORMALISED.get(_normalise(name))


def user_roles(user):
    """Resolve a user's effective roles as a set of canonical role names.

    Unknown role strings are ignored rather than trusted, so a typo in
    ``UserProfile.role`` cannot silently grant access.
    """
    if not user or not user.is_authenticated:
        return frozenset()
    names = set()
    profile = getattr(user, 'profile', None)
    if profile is not None:
        names.add(profile.role)
    try:
        names.update(user.groups.values_list('name', flat=True))
    except Exception:                                    # pragma: no cover
        pass
    return frozenset(filter(None, (canonical_role(n) for n in names)))


def has_any_role(user, allowed):
    """True if the user holds at least one of ``allowed``.

    Superusers always pass. Django's ``is_staff`` deliberately does not grant
    business access: it only means "may open the admin site", and treating it as
    a business privilege is what previously made the admin flag a de facto
    all-access pass over confidential documents.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return bool(user_roles(user) & frozenset(allowed))


def can_decide_approval(user, approval_type=''):
    """True if the user may approve or reject this kind of ApprovalRequest."""
    allowed = APPROVAL_AUTHORITY.get(
        (approval_type or '').strip().upper(), SENIOR_APPROVER_ROLES
    )
    return has_any_role(user, allowed)
