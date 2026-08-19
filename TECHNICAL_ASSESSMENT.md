# Emerald Rozalia — Project 1 Technical Assessment

**Prepared for:** Rozalia Limited — Development Team
**Date:** 18 August 2026
**Scope:** Full read of `core/`, `portal/`, `final_az/`, `templates/`, `data/`, `deploy/`, `scripts/`, Docker/Nginx stack, and the 34 approved reference screenshots.
**Method:** Static reading of all source, plus live execution — Django system checks, template compilation of all 43 templates, HTTP smoke tests of all 94 portal routes and all 95 registry pages, and targeted runtime tests of the approval, file-access, payroll and finance engines. Executed on Python 3.14.3 / Django 6.0.3 against a throwaway SQLite database in an isolated scratch directory. **No project file was modified.**

> **Confidentiality note.** This assessment was produced locally and written to the project directory only. No source code, data, screenshot or document from this project has been uploaded, published or transmitted to any external service, in line with company policy.

---

## 0. Remediation status

The findings below record the state of the package **as delivered**. They are kept intact as the evidence record. This section tracks what has since been fixed.

**Phase 0 — complete** (branch `phase-0/version-control-and-guards`)

| Item | Status |
|---|---|
| Version control established; `main` is a byte-exact as-received baseline | Done |
| `.gitignore` — `.env` verified absent from history before the first commit | Done |
| `.prettierignore` + `.vscode/` guards against HTML formatters | Done |
| `scripts/backup.sh` rewritten; verified against a stubbed Docker CLI | Done |
| Executable bit set on all three shell scripts | Done |
| A real database backup | **Not possible** — Docker not running, stack has never started, no data exists yet |

**Phase 1 — complete** (branch `phase-1/restore-running-application`)

| # | Finding | Status |
|---|---|---|
| 1 | `core/settings.py` fails to import under the installed Celery (§2.1) | **Fixed** — `from celery.schedules import crontab` |
| 2 | 30 of 39 templates do not compile; 34 routes and 91 pages return 500 (§2.2) | **Fixed** — 39/39 compile, 0 routes 5xx, 95/95 pages render 200 |
| 4 | `Decimal` not imported at module level (§2.3) | **Fixed** — three gate models now save |
| — | Four further `NameError`/`TypeError`/`FieldError` defects (§2.4) | **Fixed** |
| — | Four pre-existing `"literal".split` templates (§3.4) | **Fixed** — field lists moved to view constants; two dead loops removed |
| — | No migrations; `makemigrations` run on the production server (§6.3) | **Fixed** — `0001_initial.py` committed and reviewed; deploy now fails on drift instead |
| 5 | Operators/helpers scheduled on the Staff shift (§5.1) | **Fixed** — `test_operator_schedule` now passes |
| — | No regression gate | **Added** — template-integrity and route-smoke tests, verified to fail on reintroduced damage; `scripts/check.sh` |

Measured before and after, on a seeded database:

| | As delivered | After Phase 1 |
|---|---|---|
| Templates compiling | 9 / 39 | **39 / 39** |
| Portal routes without a 5xx | 60 / 94 | **94 / 94** |
| Registry pages rendering 200 | 4 / 95 | **95 / 95** |
| Test suite | 3 pass, 1 fail | **8 pass, 0 fail** |
| Committed migrations | 0 | **1 (171 models, 19 constraints)** |

**Phase 2 — complete** (branch `phase-2/authorisation-and-hardening`)

| # | Finding | Status |
|---|---|---|
| 3 | Self-approval bypass defeats every senior-approval control (§4.1) | **Fixed** — role-gated, no self-approval, settled requests locked, append-only `ApprovalDecisionLog` |
| — | No authorisation model at all; 20 roles unenforced (§4.2) | **Fixed** — roles as Django groups, one default-deny route table, `is_staff` no longer a business privilege |
| — | File access leaks: open documents, dead branches, unclassified types (§4.3) | **Fixed** — role families over all 65 resource types, refusals logged, access log scoped |
| — | Unhandled 500 on `/api/finance/overseas-preview/` (§4.4) | **Fixed** — 400 |
| — | Device endpoints exposed to all users (§4.5) | **Fixed** — IT roles only |
| — | `AUTH_PASSWORD_VALIDATORS=[]`, no TLS settings, no headers, no rate limit, root container, no `.dockerignore`, no healthchecks, no `LOGGING` (§4.6, §6.6) | **Fixed** — see below |

Verification: the exploit script that succeeded in Phase 0 now fails at every step.

| Attack | As delivered | After Phase 2 |
|---|---|---|
| Operator self-approves their own request | `status: APPROVED` | **403**, stays `PENDING` |
| Operator flips a REJECTED request to APPROVED | `status: APPROVED` | **403**, stays `REJECTED` |
| Operator downloads the CEO's board pack | HTTP 200 | **403**, refusal logged |
| Operator reads the global file-access log | payroll download visible | **not visible** |
| Operator lists CCTV/NVR network endpoints | RTSP URL returned | **403** |
| `?amount=abc` on the incentive endpoint | unhandled 500 + traceback | **400** |

Test suite: **45 tests, all passing, in 5.2 seconds.** `check --deploy` against a simulated TLS production configuration reports no remaining issues.

Two deliberate carry-overs: TLS is configured but **not enabled** (`DJANGO_SECURE_SSL=0`) because no certificate is provisioned — enabling it without a TLS listener would take the site down; and authorisation is **role-based, not scope-based**, so one factory's manager can still read another's data until the Phase 4 tenancy work lands.

---

**Phase 3 — complete** (branch `phase-3/correctness-and-performance`)

| # | Finding | Status |
|---|---|---|
| 5 | Payroll: unpaid gate passes never deducted, unpaid minutes understated, `AttendanceShift` never read (§5.1–5.4) | **Fixed** |
| 6 | Reporting clock was Dublin/UTC, not Bangladesh (§5.5) | **Fixed** — `Asia/Dhaka`, `CELERY_TIMEZONE` pinned, `.env` updated |
| 7 | CEO KPIs summed EUR and BDT with no FX (§5.6) | **Fixed** — `ExchangeRate` + conversion, EUR base, daily feed |
| — | Supplier price score hardcoded to 100 (§5.7) | **Fixed** — relative to lowest landed cost |
| — | Invalid `COMPLETED` order status; `ActionItem.status` had no choices (§5.8, §5.9) | **Fixed** — declared; `OPEN_STATUSES` used at 32 call sites |
| — | No transactions on multi-write handlers; bugs shown as validation errors (§6.5, §6.6) | **Fixed** — 25 handlers atomic, typed error reporting |
| 8 | Zero database indexes; per-request full employee scan (§6.1, §6.7) | **Fixed** — 235 indexes (483→896), cached aggregate control strip, audit retention |

**Correction to §5.2.** This assessment described paid gate passes as a 12.5% payroll over-charge. That was overstated: no code summed `worked_cost` and `gate_pass_paid_cost` into a single total. The real defects were that the two figures *overlap* while the dashboard presented them as adjacent cost lines (so totalling the column over-pays — now labelled), and that `unpaid_minutes` was arithmetically wrong, understating unpaid time by the length of any paid pass. Both are fixed; the stronger claim was mine and was inaccurate.

Test suite: **81 tests, all passing.** Migrations squashed to a single `0001_initial` — 173 `CreateModel`, 0 `AlterField`, 0 destructive operations. Nothing was deployed, so this is safe; if a database was created from the earlier `0001`, drop and re-migrate.

---

**Still outstanding — Phase 4 has not been started.** What remains is Phase 4: the tenancy backbone (§6.2 — no company, country or factory link on the transactional models, so authorisation cannot be scoped and per-site timezones are impossible), the design system (§3.6), the missing Sewing and Print departments (§3.2), Account Master (§3.3 — a static mock-up with no accounting models at all), the Forms Master schemas (§3.5), and the documentation reconciliation (§3.1, §5.11). **The platform runs, enforces access control, and its payroll and consolidated financial figures are now correct. It remains single-tenant in effect: any manager can see every factory's data, so do not onboard a second company or country until the tenancy work is done.**

---

## 1. Executive summary

The project is an ambitious, single-app Django platform: **172 models, 5,391 lines of views, 95 registered pages, 650 catalogued forms, 32 documented feature waves (v2–v32)**. The domain modelling is genuinely broad and, in places, well thought through — the bundle-scan traceability chain, the QC/Final-QC release gates and the material lot ledger are real, coherent designs.

However, **as delivered** the application was neither deliverable nor safely deployable. Three independent problems
compounded. Blockers 1, 2 and 4 have since been fixed in Phase 1 — see §0 for current status; the security and
correctness findings below are unchanged.

| # | Finding | Severity | Evidence |
|---|---|---|---|
| 1 | **The project will not start.** `core/settings.py` raises `AttributeError` on import with the installed Celery. | **Blocker** | Reproduced |
| 2 | **34 of 94 routes return HTTP 500. 91 of 95 registry pages return HTTP 500.** 30 of 39 templates fail to compile. | **Blocker** | Reproduced |
| 3 | **Every "senior approval required" control can be bypassed by the lowest-privileged user in one API call.** | **Critical security** | Reproduced |
| 4 | Five headline modules (v5, v7, v8, v11, v18/v22 metrics) crash on save with `NameError: Decimal`. | **Blocker** | Reproduced |
| 5 | Payroll engine mis-schedules operators/helpers and over-charges paid gate passes by 12.5%. | **High** | Reproduced |
| 6 | All reporting runs on the wrong clock — Dublin/UTC, not Bangladesh. | **High** | Verified in config |
| 7 | CEO financial KPIs sum EUR and BDT as one number with no FX conversion. | **High** | Verified in code |
| 8 | No version control, no migrations, no database indexes, no role enforcement. | **High** | Verified |

**The good news:** items 1, 2 and 4 are *mechanical* — narrow, well-understood defects with small, safe fixes. Roughly two days of focused work restores a running application. Items 3, 5, 6, 7 are design gaps that need deliberate work. Item 8 is process.

---

## 2. Blockers — the application cannot run

### 2.1 Settings module fails to import (whole project down)

`core/settings.py:25`

```python
'schedule': __import__('celery').schedules.crontab(hour=8, minute=0)
```

`celery.schedules` is a submodule, not an attribute. It is not auto-imported by `celery/__init__.py` in Celery 5.6.2 (which satisfies the `celery>=5.5,<6` pin in `requirements.txt`).

**Reproduced:**
```
AttributeError: module 'celery' has no attribute 'schedules'
```

Every entry point dies here: `manage.py`, `gunicorn core.wsgi`, `celery -A core worker`, `celery -A core beat`, and all ten `run_*_auto_report` cron commands.

**Fix** — add a real import at the top of `core/settings.py`:
```python
from celery.schedules import crontab
```
and use `crontab(hour=8, minute=0)`.

### 2.2 An HTML formatter was run over the Django templates

This is the single most damaging event in the project's history. Prettier (or an equivalent) reformatted the templates and, not understanding Django tag syntax, rewrote the inside of `{% %}` and `{{ }}` blocks.

Two distinct kinds of damage:

**(a) Newlines inserted inside tags — 57 occurrences across 33 files.**
Django's template lexer (`{%.*?%}` without `re.DOTALL`) does not match across newlines, so a wrapped tag stops being a tag and becomes literal text. Its closing partner is then orphaned.

```
templates/dashboard.html:1
  {% extends 'base.html' %}{% block title %}Project 1 Dashboard{% endblock %}{%
  block content %}
→ TemplateSyntaxError: Invalid block tag on line 30: 'endblock'
```

**(b) `==` comparisons rewritten as HTML attributes — 5 files.**
The formatter treated tag interiors as an HTML attribute list:

```
templates/forms_master.html:19
  {% if request.GET.department="" ="d" %}      ← was: {% if request.GET.department == d %}
```
Also in `stock_material_master.html`, `asset_machine_master.html`, `buyer_opportunity.html`, `communication_center.html`.

**Measured impact** — compiling every template with the Django engine:

```
TEMPLATES OK      :  9  (base, login, public_home, profit_feasibility_gate,
                         free_capacity_opportunity, staff_self_service,
                         universal_file_center / _view / _print)
TEMPLATES BROKEN  : 30  of 39
```

Because `templates/page.html` is among the broken, **all 62 placeholder registry pages 500 as well.**

Route smoke test, authenticated as superuser:
```
/p/<slug>/ pages : ok=4   failing=91  of 95
portal routes    : 34 of 94 returning 500
```

Not one department dashboard renders — cutting, embroidery, label, QC, hand iron, poly, iron, final QC, finishing, packing, shipping, supplier, procurement, purchases, sourcing, HR, attendance, CEO, Report Master, Account Master, Stock & Material, Asset & Machine, Buyer Opportunity, Communication Center, Buyer Delivery SLA, Profit Before Spend are all HTTP 500.

**Fix, in order:**

1. **Reverse (a) mechanically.** Collapse whitespace-newlines inside every `{% … %}` / `{{ … }}`. I verified this repairs **21 of the 30** broken templates (30 compile afterwards, 9 remain).
2. **Repair (b) by hand** — 5 files. The operand survives inside the injected quotes (`="" ="d"` → `== d`), so reconstruction is unambiguous.
3. **Fix the 4 pre-existing `.split` bugs** exposed once (a) is undone — see §3.4.
4. **Prevent recurrence.** Add `.prettierignore` containing `templates/` and `**/*.html`, or configure the `prettier-plugin-jinja-template`. Add a CI step that calls `get_template()` on every template so a broken template can never merge again.

There is **no `.git` directory, no `.gitignore`, no backup and no `.bak`/`.orig` file** anywhere in the tree, so the pre-formatter originals cannot be recovered. Repair is the only path.

### 2.3 `Decimal` is never imported at module level in `models.py`

`portal/models.py` imports only `timedelta`, `models`, `User`, `timezone`. Ten methods import `Decimal` locally; **sixteen other sites use it as a bare global.**

**Reproduced — 4/4 tests fail with `NameError: name 'Decimal' is not defined`:**

| Broken member | Line | Module killed |
|---|---|---|
| `BuyerOpportunity.weighted_value` | 373 | v5 — Page #34 pipeline value |
| `ProfitFeasibilityGate.save()` → `revenue`, `margin_percent`, `feasibility_score` | 518–557 | v7 — Page #72 **cannot be created at all** |
| `FreeCapacityOpportunity.save()` → `margin_percent`, `capacity_fit_percent` | 609–630 | v8 — Page #73 **cannot be created at all** |
| `ProfitBeforeSpendControl.save()` → `projected_*` | 779–804 | v11 — Page #76 **cannot be created at all**; `assert_profit_before_spend()` unusable |
| `QCInspection.defect_rate_percent`, `.dhu` | 1573–1580 | v18 — DHU / FPQ metrics |
| `FinalQCInspection.dhu` | 2128 | v22 — Final QC metrics |

Because `save()` calls the calculator, the three gate models raise before their first row is ever written. Every control that depends on them — Profit + Feasibility Gate, Free Capacity gate, Profit-Before-Spend guard, and the procurement commitment path that calls it — is inoperable.

**Fix:** add `from decimal import Decimal` at the top of `portal/models.py` and delete the ten redundant local imports.

### 2.4 Four more runtime `NameError` / `FieldError` defects

Found by AST scan and confirmed by HTTP test:

| Location | Defect | Effect |
|---|---|---|
| `portal/views.py:68` | `@login_required` decorating `_report_master_department_catalog()`, a **zero-argument private helper**; called at line 182 with no args | `TypeError` → `/report-master/` (the central reporting hub, promoted in the top nav) is 500 for everyone |
| `portal/views.py:2010, 2126` | `attendance_schedule(...)` used, but line 16 imports only `apply_stock_scan, record_variance, calculate_attendance_day` | `NameError` → `/attendance-dashboard/` 500 |
| `portal/views.py:4730` | `MaterialMaster.objects.order_by('item_name')` — field is `name` | `FieldError` → `/supplier-dashboard/` 500 |
| `portal/views.py:4424–4426` | `Sum` used inside `shipping_dashboard`, but imported at line 4329 inside a *different* function | Latent `NameError` whenever a shipping plan is created without a manually typed gross weight |

An AST sweep of the whole codebase found **no other undefined names** and, apart from `item_name`, **no other unresolvable ORM field reference** — the rest of the ORM usage is consistent. These four are the complete set.

### 2.5 `final_az` is an orphaned application

- Not in `INSTALLED_APPS`.
- Its URLs live in `final_az/urls_fragment.txt` — a text file, never `include()`d by `core/urls.py`.
- No `migrations/` package at all.
- All four of its templates fail to load (`TemplateDoesNotExist`) because the app-dirs loader cannot see an uninstalled app.
- `views.page` (the 300-view renderer) has no route even in the fragment.

Consequently `FINAL_A_TO_Z_README.md`'s instruction to open `/step-370/print-department-barcode-control/` cannot work, and `seed_final_az` cannot run.

Separately: **Step 370's validation logic does not exist.** `FINAL_A_TO_Z_MANIFEST.json` claims validation of bundle → process → route sequence → machine → machine-active → operator → helper with BLOCK + Red Alert. The app contains only a `PrintValidationEvent` *log table* and a static instructional template. There is no validation function anywhere in the codebase.

---

## 3. What is actually built versus what is claimed

### 3.1 Page count

`BUILD_REPORT.txt` and `PROJECT1_MANIFEST.json` both state **70 primary pages**. `data/page_registry.json` contains **95**. Classifying each by whether a real view exists:

| | Count |
|---|---|
| Registry entries | 95 |
| **Genuinely implemented** (dedicated view + template + POST handlers) | **33** |
| **Placeholder** — resolve to the same generic `page.html` with an identical order table and alert list | **62** |

The 62 placeholders include every customer-facing and commercial page: Product Detail, Product Comparison, Wishlist, Shopping Cart & Checkout, Order Confirmation, Returns & Refunds, Investor Relations, Virtual Try-On, Franchise Partner Dashboard, Franchise Application, POS Master, Franchise Retail, Tenant Management, Incident Management, Task Management, Planning, Budget Planning, Merchandising, Sample, Recruitment, Labour Force, IT Master, CCTV Master, IP Phone Master, Factory Master, Document Storage, Page Setup, ID Card Master.

### 3.2 Two critical departments are entirely missing

The documented production flow is *Cutting → Sewing → Print/Embroidery → Label → …*. In practice:

- **Sewing (#42)** — placeholder only. No `SewingPlan`, no production entry, no QC, no bundle scan. This is the highest-labour operation in a cap factory and the largest cost centre; it has no module.
- **Print (#39)** — placeholder only. The Print department control was supposed to be Step 370, which lives in the orphaned `final_az` app and has no logic (§2.5).

So the bundle traceability chain has two holes in the middle of it. A bundle cannot be tracked from cutting to finishing.

### 3.3 Account Master (v32) is a static mock-up

`ACCOUNT_MASTER_SPECIFICATION.md` specifies double-entry bookkeeping, Chart of Accounts, Journal, General Ledger, sub-ledgers, Trial Balance, P&L, Balance Sheet, Cash Flow, 30 bill types, 20 invoice types, AP/AR, multi-currency, payroll finance, banking reconciliation, tax compliance, a decision engine and a built-in user manual.

`portal/views.py:5390`:
```python
@login_required
def account_master(request):
    return render(request, 'account_master.html')
```

A model search for `Account`, `Journal`, `Ledger`, `ChartOf`, `Invoice`, `Bill`, `Payment`, `Receipt`, `Voucher`, `TrialBalance`, `Tax`, `Payroll`, `Bank` returns **zero classes**. The only finance model in the system is `FinanceTransaction` — a flat row with `transaction_type`, `country`, `currency`, `amount`, `reference` and the three incentive fields.

The template is hardcoded HTML: `TOTAL IN<b>Ledger</b>`, `PROFIT<b>Calculated</b>` — captions where numbers should be. No accounting exists. (And the page is 500 anyway — §2.2, plus its own `.split` bug.)

The same applies to POS, Retail, Franchise, E-commerce and Product Catalogue: **no models exist for any of them.**

### 3.4 Four pre-existing invalid-syntax templates

Independent of the formatter, these four use `{% for x in "a b c".split %}`, which the Django template parser rejects (a string literal cannot take `.attribute` in a filter expression):

- `account_master.html:56` — and the loop body is empty, followed by hardcoded `<span>`s, so it was never functional
- `finishing_dashboard.html:314` — cost-field input generator
- `packing_dashboard.html:640` — cost-field input generator
- `shipping_dashboard.html:650` — cost-field input generator

Each needs to become a view-supplied context list, or literal markup.

### 3.5 The 650 Forms Master is a title catalogue, not 650 forms

`data/form_master_650.json` is machine-generated: **65 departments × 10 categories**, names built as `"<Department> <Category> Form <NNN>"` (e.g. `EXEC-F001 "Executive Request Form 001"`). Codes and names are unique; nothing is duplicated. But:

- **No form has a field schema.** `FormDefinition` has no schema column; `FormSubmission.data` is an unvalidated `JSONField`.
- **There is no submit, approve or print route.** `FormSubmission` is reachable only through Django admin.
- `forms_master` renders a read-only table with six decorative buttons that only `console.log` (`static/js/app.js`).

`ACCOUNT_MASTER_SPECIFICATION.md` requires 60 Account & Finance forms inside the 650; the data contains 30 generic titles across Accounts / Finance / Budget.

### 3.6 Design fidelity against the approved references

I examined the approved screenshots. They describe a **light enterprise theme**: white cards on `#f3f6f5`, dark-green sidebar, emerald accents, and a header carrying Red Alert / Actioned / Chat 24/7 / WhatsApp / Email chips plus **Multi-Country, Language and Currency (BDT ৳) selectors**, a scope row (Country / Company / Factory-Unit / Department), period tabs (Today / This Week / This Month / This Year), and a rich chart grid — donut, line, bar, gauge, trend.

Against that:

- `static/css/app.css` is a **dark theme** (`--bg:#06110e`) — the inverse of every reference.
- The three reference-driven pages (`hr_dashboard`, `attendance_dashboard`, `staff_self_service`) do match the light reference — but each achieves it by `display:none !important` on the global chrome and re-implementing its own layout in an inline `<style>` block.
- **24 templates carry their own `<style>` block with 16–83 hardcoded hex colours each.** There is no design system and no shared token set.
- **No charting anywhere** except a little hand-rolled SVG in `hr_dashboard` and `attendance_dashboard`. None of the reference charts exist.
- **No country / language / currency selector exists on any page**, and no model backs one.
- `.hero` loads a **2.9 MB PNG** (`static/img/hero_ref.png`) as a full-bleed background.
- Only 3 of the 34 references were promoted to `static/reference/`; the other 31 remain unused targets. The `references/` folder is **~85 MB** and, with `COPY . .` and no `.dockerignore`, ships inside the Docker image.

---

## 4. Security findings

All six were reproduced at runtime, acting as a freshly created non-staff user named `operator` — the lowest privilege level in the system.

### 4.1 Any user can self-approve any approval request — **Critical**

`portal/views.py:290–297`

```python
@login_required
@require_http_methods(['POST'])
def api_approval_decision(request, pk):
    obj = get_object_or_404(ApprovalRequest, pk=pk)
    ...
    obj.status = decision; obj.approved_by = request.user; obj.save()
```

No seniority check. No role check. No requester ≠ approver check. No guard against re-deciding a settled request.

**Reproduced:**
```
create approval  -> 200 {'ok': True, 'approval_id': 1, 'status': 'PENDING'}
SELF-APPROVE by non-staff operator -> 200 {'status': 'APPROVED'}
requested_by = operator | approved_by = operator | status = APPROVED

flip a REJECTED request -> APPROVED (http 200)
```

Because the whole platform gates on `approval.status == 'APPROVED'`, **two POSTs by an operator unlock every documented control**: manual stock override, manual material adjustment, asset retirement/disposal, manual production entry in all eight production modules, conditional QC release, conditional Final QC release, Profit + Feasibility ACCEPT, quick-order acceptance, Profit-Before-Spend authorisation, delivery-SLA exception, OT approval and authorised manual attendance entry.

The `ProfitFeasibilityGate` decision handler compounds this: it never checks `system_recommendation`, so `ACCEPT_WITH_RISK` can be set on a gate the system says `REJECT`, provided an approval row exists. (`FinalQCRelease` does check `system_decision` — that one is correct, and is the pattern to copy.)

**Fix:** enforce approver seniority from `UserProfile.role` (or Django groups/permissions) per `approval_type`; reject `requested_by == approved_by`; reject decisions on non-`PENDING` rows; record an immutable decision audit row.

### 4.2 There is no authorisation model at all — **Critical**

Across `portal/views.py`:

```
@login_required        : 99
permission_required    :  0
user_passes_test       :  0
has_perm               :  0
UserProfile / role use  :  0
```

`data/roles.json` lists 20 roles (CEO … Helper). It is **never read by any code.** `UserProfile.role` is written once by `seed_project1` and never consulted. The only access distinction that exists anywhere is Django's `is_staff` / `is_superuser` flag.

Practical consequence: a Helper can open the CEO dashboard, the HR dashboard, payroll costs, supplier bank details and the finance CSV exports, and can POST to every dashboard — creating cutting plans, issuing fabric, recording production, releasing QC, generating official reports.

### 4.3 Universal File Controls leak documents — **High**

`_user_can_access_file` (`portal/views.py:1215`):

- Non-confidential `DocumentRecord` → `return True` for **any** authenticated user. **Reproduced:** operator got HTTP 200 on the CEO's board pack.
- `delivery_pod`, `delivery_signature`, `delivery_photo` → `return True` for any authenticated user. Buyer signatures, delivery photographs and GPS locations are readable by all staff.
- Confidential documents are visible **only to the uploader** (plus Django staff). A Finance Manager without the Django `is_staff` flag cannot read their own department's confidential files. There is no department, role or org-scope check.
- **Dead code:** `if user.is_superuser or user.is_staff: return True` sits above ~10 branches that each `return user.is_staff or user.is_superuser`. Those branches can only ever return `False` — the poly / hand-iron / QC / label / embroidery / cutting / CCTV file types are unreachable for everyone else.
- **Denied attempts are never logged.** `_log_file_action` runs *after* the permission check, so the security-relevant events — the refusals — leave no audit trail.

`universal_file_center` additionally passes **the entire system's `FileAccessLog`** (last 200 rows, unfiltered) to every authenticated user. **Reproduced:** the operator's `/files/` page disclosed the CEO's download of `payroll-2026.xlsx` with reference `HR-CONF`.

### 4.4 Unhandled exception on public-facing input — **Medium**

`finance_overseas_preview` does `Decimal(request.GET.get('amount','0') or '0')` with no validation.

**Reproduced:** `/api/finance/overseas-preview/?amount=abc` → `decimal.InvalidOperation`, uncaught, HTTP 500. With `DEBUG=1` (§4.6) that returns a full traceback including source and settings.

### 4.5 Device network endpoints exposed to all users — **Medium**

**Reproduced:** `/api/devices/` returned `'endpoint': 'rtsp://10.0.5.11:554/admin'` to the operator. CCTV/NVR/attendance/IP-phone internal addresses should be restricted to IT/Security roles.

### 4.6 Deployment hardening

| Issue | Detail |
|---|---|
| **`AUTH_PASSWORD_VALIDATORS = []`** | Password validation entirely disabled. Any user may set `1` as a password. |
| **`.env` shipped, identical to `.env.example`** | `DJANGO_DEBUG=1`, published `DJANGO_SECRET_KEY=local-dev-change-before-production`, `DEFAULT_ADMIN_PASSWORD=ChangeMeNow-2026`. `docker-compose.yml` uses `env_file: .env`, so a deploy straight from this package runs in DEBUG with a public secret key. Given that most pages currently 500, DEBUG exposes the full codebase and environment to anyone who loads a page. |
| **No TLS anywhere** | Nginx listens on `:80` only. `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, `SECURE_HSTS_SECONDS` are all unset. Session and CSRF cookies for a system holding payroll and banking data travel in cleartext. |
| **No security headers, no rate limiting** | No CSP, `X-Content-Type-Options`, `Referrer-Policy`. No throttling on `/login/` or any API. |
| **Container runs as root** | No `USER` directive; `build-essential` and `libpq-dev` remain in the final image (no multi-stage build). |
| **No `.dockerignore` / `.gitignore`** | `.env`, `__pycache__`, `media/` and 85 MB of reference PNGs are baked into the image. |
| **No healthchecks** | `depends_on: [db, redis]` without `condition: service_healthy` — `web` races Postgres on first boot. |
| **`scripts/backup.sh` is broken** | Uses `$POSTGRES_USER` / `$POSTGRES_DB` from the shell, but they live in `.env` and are never exported. Under `set -u` the script aborts with *unbound variable*. **There is no working backup.** |

**Positives worth recording:** no raw SQL, no `.extra()`, no `eval`/`exec`/`pickle`/`subprocess`, no `|safe`, no `mark_safe`, no `autoescape off`. Django's ORM parameterisation and template auto-escaping are intact throughout, so there is no SQL-injection or stored-XSS surface. All 24 POST forms carry `{% csrf_token %}` (the un-tokened `<form>` elements are GET filters). `apply_stock_scan`, `record_variance` and `apply_material_movement` are correctly `@transaction.atomic`.

---

## 5. Correctness defects in the business engines

### 5.1 Payroll: operators and helpers get the Staff shift — **High**

`portal/services.py:139`
```python
schedule = attendance_schedule(getattr(employee, 'category', None) or employee.role)
```
`Employee.category` **defaults to `'STAFF'`**, which is always truthy, so `employee.role` is never consulted.

**Reproduced:**
```
Employee(role='Operator')  → category stored = 'STAFF'
schedule chosen           → 660 min, checkout 20:00:00      (should be 480 min, 17:00)
```
The project's own test asserts the opposite and **fails**:
```
FAIL: test_operator_schedule — AssertionError: 660 != 480
```

Every operator or helper whose `category` was not explicitly set is scheduled 660 minutes to 20:00 instead of 480 to 17:00. That corrupts worked minutes, late minutes, early-leave minutes, unpaid minutes, per-minute cost and the OT eligibility window (20:30 instead of 17:30) — for the majority of the factory workforce.

### 5.2 Payroll: paid gate passes are charged twice — **High**

A gate pass sits *between* check-in and check-out, so its minutes are already inside `worked_minutes` (only `BREAK_IN`/`BREAK_OUT` are deducted). `gate_pass_paid_cost` then charges them again.

**Reproduced** — an operator, full 08:00–17:00 day, one-hour approved PAID gate pass, `daily_cost = 480.00`:
```
scheduled=480  worked=480  paid_gate=60  unpaid=0
worked_cost=480.00 + gate_pass_paid_cost=60.00 = 540.00   vs daily_cost 480.00
```
**12.5% payroll overstatement per paid gate pass.** The same double-count inflates `unpaid = scheduled - min(worked + paid_gate, scheduled)`, hiding genuine unpaid time.

### 5.3 Payroll: unpaid gate passes cost the worker nothing — **High**

`gate_pass_unpaid_minutes` is recorded but never subtracted from `worked_minutes`.

**Reproduced** — three-hour approved UNPAID gate pass:
```
worked=480  unpaid_gate=180  unpaid_minutes=0  worked_cost=480.00
                                    (expected worked 300, paid 300/480)
```
The employee is paid in full for a three-hour unpaid absence.

### 5.4 Configured shifts are ignored — **High**

`AttendanceShift` exists as a model, is registered in Django admin, and carries `check_in`, `break1_in/out`, `break2_in/out`, `check_out`, `mandatory_minutes`, `grace_minutes`, `ot_break_minutes` per category. **No code ever reads it.** `services.attendance_schedule()` hard-codes both schedules in Python. Configuring a shift in admin has zero effect — a hard blocker for multi-factory, multi-country operation where shifts differ by site and by law.

### 5.5 All reporting runs on the wrong clock — **High**

Every module is documented as reporting at **08:00 / 13:00 / 20:00 Bangladesh local time**, and the attendance engine anchors 08:00 / 13:00 / 17:00 / 20:00 via `timezone.get_current_timezone()`.

But `TIME_ZONE=Europe/Dublin` in both `.env` and `.env.example`. Therefore:

- **Attendance anchors are Irish time.** A Dhaka operator clocking in at 08:00 local is recorded against an Irish 08:00 anchor — 4–5 hours off — so late minutes, early-leave minutes, worked minutes and payroll cost are all wrong.
- **`CELERY_TIMEZONE` is never set.** `app.config_from_object('django.conf:settings', namespace='CELERY')` reads only `CELERY_*` keys, so Celery keeps its default UTC. The three beat entries fire at **08:00 / 13:00 / 20:00 UTC = 14:00 / 19:00 / 02:00 Dhaka.** The 20:00 slot lands after midnight — on the wrong calendar date.
- **The cron examples contradict the settings.** `deploy/*.cron.example` instructs setting the server TZ to `Asia/Dhaka`, which would then disagree with the `Europe/Dublin` that `timezone.localdate()` uses to stamp `report_date`.
- **DST drift.** Dublin observes DST; Dhaka does not. The offset silently changes twice a year.
- **No per-factory timezone field exists** on any model, despite the multi-country requirement.

### 5.6 Financial roll-ups mix currencies — **High**

`_ceo_summary()` (`portal/views.py:5290`) computes headline KPIs by summing raw amounts across records with different currencies, with no exchange-rate model anywhere in the project:

- `MasterOrder.order_value` has **no currency field at all** — `Sum('order_value')` adds USD, EUR and BDT order values together.
- `FinanceTransaction` has a `currency` field, but income and expense are summed with **no currency filter and no conversion**, then `profit_today = income − expense − prod_cost`. `FinanceTransaction` defaults to `EUR`; production entries derive from BDT-denominated models. At roughly 130 BDT to the euro, the CEO's "profit today" is wrong by orders of magnitude.
- `StockItem` has no currency field; `stock_value` sums `qty × unit_cost` across everything.
- `po_value` and `purchase_outstanding` sum supplier POs and invoices across currencies.

18 models carry a `currency` field. Nothing converts. **No `ExchangeRate` model exists.** "Multi-currency" is a label on a field, not a capability.

### 5.7 Supplier price scoring is hardcoded — **High**

`ProcurementComparison.save()` (`portal/models.py:2628`)
```python
price_score = Decimal('100')
self.total_score = (price_score*Decimal('.40')
                  + self.quality_score*Decimal('.35')
                  + self.delivery_score*Decimal('.25')).quantize(Decimal('0.01'))
```

Every supplier receives the full 40-point price score regardless of `landed_unit_cost`. `README.md` states *"Supplier comparison uses landed cost plus supplier quality and delivery performance"* — the landed cost is computed and then discarded. Ranking is driven only by quality and delivery, so **procurement will systematically fail to select the cheapest qualified supplier.**

### 5.8 Order lifecycle has two conflicting terminal states — **Medium**

`portal/views.py:1423` sets `sla.order.status = 'COMPLETED'`, but `MasterOrder.STATUS` is `['OPPORTUNITY','CONFIRMED','PLANNING','PRODUCTION','QC','PACKING','READY_TO_SHIP','SHIPPED','DELIVERED','HOLD']`. **`'COMPLETED'` is not a valid choice.** Meanwhile the Shipping module's POD path sets `'DELIVERED'` for the same lifecycle end.

`full_clean()` appears **0 times in the entire codebase**, so Django never validates `choices` on write. Consequences: `MasterOrder.objects.exclude(status='DELIVERED')` (the Celery open-orders count) treats completed orders as open forever, and the CEO `delivered` tile under-reports.

### 5.9 The "Action Required" counter can never go down — **Medium**

`ActionItem.status` has **no `choices`**. Writers use `'OPEN'` (seeder, `communication_center`) and one handler closes with `'COMPLETED'` (`views.py:646`). Readers split:

- `context_processors.py:12`, `tasks.py:6`, `views.py:57` → `exclude(status='DONE')`
- ~30 dashboards and all ten auto-report commands → `exclude(status='COMPLETED')`

Nothing ever writes `'DONE'`. So the **global control-strip counter shown on every page** (`base.html`), and the Celery `scheduled_report_snapshot`, count *all* action items including completed ones — while the dashboards on the same screen show the correct figure. Two different numbers for the same KPI, side by side.

### 5.10 `QCInspection.dhu` duplicates `defect_rate_percent` — **Low**

Both return `total_defects / inspected_qty × 100`. `README.md` presents FPQ and DHU as distinct metrics. DHU should count defect *occurrences* (`Sum(QCDefect.quantity)`), not the critical+major+minor unit tallies. As written the two properties are the same number under two names.

### 5.11 Documentation contradicts itself on the delivery SLA

`PROJECT1_MANIFEST.json` says `"delivery_target_days": 30`. Registry page #37 says *"Buyer Address Delivery ≤30 Days"*. `templates/dashboard.html` prints *"Delivery Target ≤30d"*. But v10 / page #75 / `BuyerDeliverySLA.max_delivery_days` all specify **15 days**. Both pages exist in the registry simultaneously. Which is contractual needs a decision.

---

## 6. Architecture, data model and performance

### 6.1 172 models, zero indexes

```
db_index=      : 0 occurrences
Meta.indexes=  : 0 occurrences
Meta.ordering  : 1 occurrence (FileAccessLog)
```

The only indexes are the implicit ones from `unique=True` and foreign-key columns. Yet every dashboard filters on `work_date`, `report_date`, `actioned`, `status`, `created_at` and `occurred_at__date` — none indexed. On a production dataset every dashboard load becomes a sequential scan.

`context_processors.global_portal` also uses `occurred_at__date=today` on a `DateTimeField`, which applies a function to the column and cannot use an index even once one is added. Use a half-open range instead.

### 6.2 No tenancy or scoping backbone

`OrganizationNode` models the Country → Company → Factory → Unit → Warehouse → Store hierarchy, but is referenced from only **five** places: `UserProfile.scope`, `MaterialLot.location`, `MaterialMovement` (source/destination), `AssetMachine.location`, `AssetMovement`.

Nothing else is scoped. `MasterOrder`, `Employee`, `Alert`, `ActionItem`, every production plan, every QC record, every supplier, every shipment, every finance row has **no company, country or factory link.** `Employee.factory_unit` is free text.

The organisational requirement is multi-company, multi-country, multi-factory, multi-unit, multi-warehouse, multi-store, multi-language, multi-currency, multi-user. **None of it is architecturally possible today**, and there is no query-level scoping to prevent one factory from reading another's data. Retrofitting a scope FK onto 172 models later is far more expensive than adding it now — this is the highest-leverage architectural decision outstanding.

### 6.3 No migrations, no version control

`portal/migrations/` contains only `__init__.py`. `scripts/deploy.sh` runs:
```bash
docker compose exec web python manage.py makemigrations portal --noinput
docker compose exec web python manage.py migrate --run-syncdb --noinput
```

Migrations are therefore **generated on the production server at every deploy**, are not version-controlled, and diverge between environments. Any field rename becomes an unreviewed auto-generated drop-and-create — silent data loss. Combined with the absence of `.git` and a non-functional `backup.sh` (§4.6), there is currently **no way to recover from a bad deploy.**

### 6.4 Business logic lives in the view layer

All ten `run_*_auto_report` management commands import private helpers out of the view module:
```python
from portal.views import _final_qc_auto_report_payload
```
Every cron run therefore imports the whole 5,391-line view module (and `from .models import *`). Reporting logic belongs in `services.py`; views should only adapt HTTP.

### 6.5 No transactions on multi-write handlers

`transaction.atomic` appears **0 times in `views.py`.** The Final QC release handler writes `FinalQCRelease`, then `MasterOrder.status`, then `BuyerDeliverySLA.status` as three separate commits — an exception between them leaves the order released but the SLA stale. Every dashboard POST handler has the same exposure.

### 6.6 Exception handling hides real errors

Each dashboard POST is wrapped in:
```python
except Exception as exc:
    messages.error(request, str(exc))
```

This catches genuine programming errors (`AttributeError`, `NameError`, `FieldError`) and shows them to the operator as if they were validation messages — the `Sum` bug in §2.4 surfaces to a shipping clerk as *"name 'Sum' is not defined"*. Nothing is logged; `LOGGING` is not configured at all, so there is no server-side record of any failure.

Related: `request.POST.get('plan_no').strip()` (no default) raises `AttributeError` on a missing field, then gets swallowed by the same handler.

### 6.7 Unbounded queries and truncated dropdowns

Every dashboard context is built from arbitrary slices — `orders[:300]`, `employees[:500]`, `machines[:300]`, `approvals[:300]`, `stock_items[:300]`, `forms[:650]` — with no search, filter or pagination. At scale a user simply cannot select order #301. This is a silent data-integrity trap, not just a UX issue.

`_ceo_summary` issues roughly **50 queries** per load (three separate `aggregate()` calls per model across eight production models, plus ~25 more). `universal_file_center` loads 1,000 documents and 1,000 attachments and filters permissions **in Python**.

`context_processors.global_portal` runs on **every authenticated page render**: five count queries plus a full load of all present employees to sum `daily_cost` in Python. At 5,000 workers that is a 5,000-row scan on every request. `login_no` is hardcoded to `1`.

`AuditMiddleware` writes one `AuditLog` row per authenticated request with **no index on `created_at` and no retention policy** — unbounded growth on the hot path.

### 6.8 Django admin is unusable at scale

`admin.py` performs 172 bare `admin.site.register(...)` calls with no `ModelAdmin` classes: no `list_display`, `search_fields`, `list_filter`, `raw_id_fields` or `autocomplete_fields`. Every change form will render a full `<select>` of every related row — every employee, every bundle, every order. With realistic data these pages will not load.

Worse, `AuditLog` and `FileAccessLog` are registered **without `readonly_fields`**, so any Django staff user can edit or delete audit history. That defeats the audit trail the compliance requirement depends on.

### 6.9 Barcode capability is QR-only

`barcode_png` uses `qrcode.make()` exclusively. There is **no 1D/linear barcode generator** (Code 128, EAN, ITF-14) anywhere, even though the entire platform is scanner-driven and handheld 1D scanners are the norm on a garment floor. `python-barcode` is installed in the local environment but absent from `requirements.txt`. `BUILD_REPORT.txt` claims *"Existing barcode / QR generator: RETAINED"*; only QR exists.

The endpoint also sets no cache headers and re-renders on every request — a label sheet with 100 codes triggers 100 PNG renders per page load. `barcode_master` uses `get_or_create`, so re-registering an existing code silently returns the old asset with the old reference instead of reporting a collision.

### 6.10 Environment / dependency mismatch

`requirements.txt` pins 9 packages. The local interpreter has neither **`python-dotenv`** (required by `core/settings.py:3`) nor **`psycopg`** (required by the Postgres backend) installed — so nothing runs locally without Docker. Meanwhile the environment carries a much richer stack that the project never uses: `djangorestframework`, `drf-spectacular`, `channels`, `daphne`, `django-celery-beat`, `django-celery-results`, `django-cors-headers`, `django-crispy-forms`, `django-filter`, `django-ratelimit`, `django-cleanup`, `reportlab`, `openpyxl`, `xlsxwriter`, `pandas`, `numpy`, `python-barcode`, `pycountry`, `simplejwt`. Either the target stack changed and `requirements.txt` was not updated, or the environment is polluted. This needs settling before anything is pinned for production.

---

## 7. Recommended sequence

### Phase 0 — Stop the bleeding (before any other work)

1. `git init`, commit the current tree as the baseline, add `.gitignore` (`.env`, `__pycache__`, `media/`, `staticfiles/`, `*.sqlite3`).
2. Add `.prettierignore` with `templates/` and `**/*.html`. **No formatter may ever touch a Django template again.**
3. Fix `scripts/backup.sh` and take a verified database + media backup.

### Phase 1 — Restore a running application (~2 days)

4. `core/settings.py` — `from celery.schedules import crontab` (§2.1).
5. `portal/models.py` — add module-level `from decimal import Decimal` (§2.3).
6. `portal/views.py` — remove `@login_required` from `_report_master_department_catalog`; import `attendance_schedule`; fix `item_name` → `name`; import `Sum` in `shipping_dashboard` (§2.4).
7. Repair the templates: mechanical newline collapse, then the 5 `==` files by hand, then the 4 `.split` templates (§2.2, §3.4).
8. Add a CI check that compiles every template and runs `manage.py check`, plus a smoke test that GETs all 94 routes and asserts no 5xx. Fix the failing `test_operator_schedule`.
9. Generate migrations **locally**, review them, commit them, and remove `makemigrations` from `scripts/deploy.sh`.

### Phase 2 — Close the security holes (~1 week)

10. Rewrite `api_approval_decision`: role-based approver authority per `approval_type`, no self-approval, no re-deciding settled requests, immutable decision audit (§4.1).
11. Introduce real authorisation. Map the 20 roles in `data/roles.json` to Django groups and permissions; add a `@require_role(...)` decorator and apply it to all 99 views (§4.2).
12. Rewrite `_user_can_access_file` around role + department + org scope; remove the dead branches; log denials; filter `FileAccessLog` per user in `universal_file_center` (§4.3).
13. Harden deployment: real `.env` with a fresh secret and `DEBUG=0`; enable password validators; TLS + HSTS + secure cookies at Nginx and in settings; security headers; login rate limiting; non-root container; `.dockerignore`; healthchecks; configure `LOGGING` (§4.6, §6.6).

### Phase 3 — Correct the engines (~2 weeks)

14. Fix the payroll engine: derive category correctly, stop double-counting paid gate passes, deduct unpaid gate passes, and read `AttendanceShift` from the database instead of hard-coding (§5.1–5.4).
15. Fix time: set `TIME_ZONE` and `CELERY_TIMEZONE` deliberately, add a per-`OrganizationNode` timezone, and make every report slot resolve in the factory's local time (§5.5).
16. Add an `ExchangeRate` model and a base-currency roll-up; add `currency` to `MasterOrder` and `StockItem`; make every cross-entity financial sum convert before adding (§5.6).
17. Implement real price scoring in `ProcurementComparison` (§5.7).
18. Settle the order lifecycle on one terminal state, add `choices` to `ActionItem.status`, unify the DONE/COMPLETED vocabulary, and call `full_clean()` on write paths (§5.8, §5.9).
19. Wrap every multi-write POST handler in `transaction.atomic`; replace blanket `except Exception` with typed handling plus real logging (§6.5, §6.6).
20. Add indexes on every filtered column; replace `occurred_at__date=` with range queries; batch the CEO aggregates; add an `AuditLog` retention job (§6.1, §6.7).

### Phase 4 — Architecture and scope decisions (needs your direction)

21. **Tenancy.** Add an `OrganizationNode` scope FK to the ~40 transactional models and enforce scoping in a manager. Doing this before more modules are written will save far more than it costs (§6.2).
22. **Design system.** One shared light-theme token set matching the approved references, plus a charting approach. Retire the dark `app.css`, and stop each page re-declaring its own palette (§3.6).
23. **Missing departments.** Build the Sewing module and a real Print module (with the Step 370 validation logic actually implemented), and fold `final_az` into `portal` or install it properly (§3.2, §2.5).
24. **Account Master.** The specification describes a full double-entry accounting system. Nothing exists. This is a multi-month project in its own right and should be planned separately (§3.3).
25. **Forms Master.** Decide whether the 650 forms need real field schemas and a submit/approve/print workflow, or whether the catalogue is sufficient for now (§3.5).
26. **Reconcile the documentation.** Page count (70 vs 95), delivery SLA (15 vs 30 days), the duplicated legacy/new page pairs, and the claims in `BUILD_REPORT.txt` that are not met (§3.1, §5.11).

---

## 8. Reference — verified module status

| Page | Module | Status |
|---|---|---|
| #32 | Stock & Material Master | Implemented · 500 (template) |
| #33 | Asset & Machine Master | Implemented · 500 (template) |
| #34 | Buyer Enquiry / Opportunity | Implemented · 500 (template) + `Decimal` |
| #52 | Report Master | Implemented · 500 (`TypeError`, decorator) |
| #53 | Forms Master | Read-only catalogue · 500 (template) |
| #56 | CEO Command Center | Implemented · 500 (template) + currency mixing |
| #68 | Bundle Barcode | Implemented · 500 (template) · QR only |
| #71 | Communication Center | Implemented · 500 (template) |
| #72 | Profit + Feasibility Gate | Template OK · **model cannot save** (`Decimal`) |
| #73 | Free Capacity / Quick Order | Template OK · **model cannot save** (`Decimal`) |
| #74 | Universal File Center | **Renders** · access-control defects |
| #75 | Buyer Delivery SLA | Implemented · 500 (template) · invalid `COMPLETED` |
| #76 | Profit Before Spend | Implemented · 500 (template) · **cannot save** |
| #77 | Staff Self-Service | **Renders** — most complete page |
| #78 | HR Dashboard | Implemented · 500 (template) |
| #79 | Attendance Dashboard | Implemented · 500 (`NameError`) · payroll defects |
| #80–89 | Cutting → Packing (8 modules) | Implemented · all 500 (template) |
| #90 | Shipping | Implemented · 500 (template) + latent `Sum` |
| #91 | Supplier Master | Implemented · 500 (`FieldError`) |
| #92 | Procurement | Implemented · 500 (template) · price scoring broken |
| #93 | Purchases Master | Implemented · 500 (template) |
| #94 | Sourcing Master | Implemented · 500 (template) |
| #95 | Account Master | **Static mock-up** · 500 · no models |
| #39 | Print Department | **Not built** |
| #42 | Sewing Department | **Not built** |
| — | POS / Retail / Franchise / E-commerce | **Not built** — no models |
| — | Step 370 Print Validation | **Not built** — log table + static page only, app orphaned |
| ×62 | Remaining registry pages | Generic placeholder · 500 (`page.html`) |

---

*Every finding above was reproduced or verified directly against the source in this directory. The assessment itself modified no project file; the fixes recorded in §0 were made afterwards, each in its own reviewable commit.*
