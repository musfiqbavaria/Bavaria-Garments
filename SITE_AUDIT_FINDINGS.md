# Site-wide audit — findings register

**Scope:** every URL (120 named routes), every page (49 templates), every stylesheet (10),
both JS files, the admin, the Celery schedule, the nginx layer and the test suite.

**Status:** remediated on branch `fix/site-audit`, except where marked
**OUT OF SCOPE** or **PARTIAL** below. The line references describe the code as it was
when the finding was raised (commit `c7f741e`); each entry now also records what was done.
The test suite grew from 124 to 185 tests, and every fix has a gate that fails if it
regresses.

**Two corrections to this register, found while fixing it — see the entries for detail:**

- **A4 overstated the problem.** 26 dashboards *did* render messages, with a bare
  `{% for message in messages %}` that this audit's grep for `{% if messages %}` missed.
  So 30 templates rendered them, not 4, and the calls were visible. The real defect was
  narrower: they rendered as a generic `.card` with no severity, so a failed save looked
  identical to a successful one.
- **B21 was wrong.** `RouteSmokeTests` already requested every non-parameterised route and
  all 95 registry pages — it discovers routes by regex at runtime rather than naming them,
  which is why grepping for URL strings found only six. The real gap was that it asserted
  only `status < 500`, so a 404 passed, and ran solely as a superuser, so no role-gated
  path was exercised. That is precisely why A1 and A6 shipped.

Two further claims were dropped before publication as false positives: an "encoding
corruption" that was the console mangling a valid `•`, and `portal_visual` as dead code —
it is the shared renderer for seven routes.

**Method:** machine-checkable integrity first (route/template/static/CSRF resolution), then
twelve targeted audit dimensions, then re-verification of every claim against the source.
Anything that could not be reproduced from the code was dropped rather than reported.

**Already clean — checked and found correct, recorded so it is not re-audited:**
every `{% url %}` name resolves; every `{% static %}` target exists; every routed view exists;
no duplicate route names or paths; every rendered template exists; every POST form carries
`{% csrf_token %}`; every route is classified in `ROUTE_POLICY` (0 unclassified); no
`FloatField` is used for money (297 `DecimalField`, 0 `FloatField`); 285 `db_index=True`;
the audit tables (`AuditLog`, `FileAccessLog`, `ApprovalDecisionLog`, `BarcodeScanEvent`)
are correctly locked read-only in admin; nginx sets CSP, `X-Frame-Options`,
`X-Content-Type-Options`, `Referrer-Policy`, COOP, `server_tokens off`, a login rate limit
and `return 404` on `/media/`; `references/` and `static/reference/` are correctly excluded
from the Docker image; no template loads an external font, script or stylesheet.

---

## Severity key

| | meaning |
|---|---|
| **A** | A user following a normal path reaches a broken, wrong or silently-failing result. |
| **B** | Inconsistent or misleading in ordinary use; correct behaviour is reachable but not obvious. |
| **C** | Cosmetic, maintenance or latent. |

---

# A — user-visible breakage

## A1. Report Master's entire drill-down returns 404

`templates/report_master.html:258` and `:337` link to `/page/<slug>/`. The placeholder route
is registered at `p/<slug:slug>/` — prefix `/p/`, not `/page/`.

```
portal/urls.py:45   path('p/<slug:slug>/',views.page_view,name='page'),
$ grep -c "path('page" portal/urls.py
0
```

`:337` sits inside a loop over `catalog`, built from `_report_master_department_catalog()`
(`portal/views.py:569`), which returns **26 entries**. With the static CEO link at `:258`
that is **27 dead links** — the whole "ALL DEPARTMENT REPORT DASHBOARDS" panel.

Report Master is in the navbar on **every** authenticated page (`templates/base.html:17`) and
is classified for all sixteen `MANAGEMENT` roles (`portal/authorization.py:110`). There is
currently no working drill-down from the central reporting hub to any department.

These are literal `href` strings, not `{% url %}` tags, which is why the route-integrity
check could not see them.

Second-order: even with `/p/` substituted, 5 of the 26 slugs are absent from
`data/page_registry.json` and would 404 at `get_object_or_404` (`views.py:557`) —
`buyer-enquiry-order-opportunity` (registry has `buyer-opportunity`), `production-dashboard`
(`production-master`), `print-dashboard` (`print`), `staff-self-service`
(`staff-self-service-portal`), `communication-center` (`communication-center-master`).

## A2. Nine fully-built department dashboards cannot be reached by clicking

Transitive closure over every `{% url %}` tag, every literal `href`, `static/js/*.js` and
every `page_view` redirect, starting from `/`, `/login/`, `/dashboard/` and
`/global-dashboard/`: **41 of 51 navigable routes are reachable, 10 are not.**

| unreachable route | has CSV export | has JSON API |
|---|---|---|
| `/final-qc-dashboard/` | yes | yes |
| `/finishing-dashboard/` | yes | yes |
| `/iron-dashboard/` | yes | yes |
| `/packing-dashboard/` | yes | yes |
| `/procurement-dashboard/` | yes | yes |
| `/purchases-dashboard/` | yes | yes |
| `/shipping-dashboard/` | yes | yes |
| `/sourcing-dashboard/` | yes | yes |
| `/supplier-dashboard/` | yes | yes |
| `/api/finance/overseas-preview/` | — | — |

Every one is complete and role-classified. `finishing_dashboard` and `sourcing_dashboard`
have **zero** inbound references anywhere; the other seven are linked only from pages that
are themselves unreachable, so the dead end is transitive — `/supplier-dashboard/` is linked
from `procurement_dashboard.html`, `purchases_dashboard.html` and `sourcing_dashboard.html`,
none of which can be reached either.

Production, Quality, Shipping and Procurement staff have no navigation path to their own
department. The 13-link navbar in `base.html` includes none of the nine.

## A3. 74 of the 95 modules in "All Modules" land on a generic stub badged ACTIVE

`templates/dashboard.html:22` emits one card per registry entry, with
`href="/p/{{ p.slug }}/"` except for four slugs special-cased inline
(`forms-master`, `report-master`, `bundle-barcode`, `stock-material-master`).

`page_view` (`portal/views.py:533–551`) then carries a hand-maintained chain of **19**
`if slug=='…': return redirect(…)` lines. The chain stops at `poly-dashboard`; every module
routed after line 21 of `urls.py` was never added.

**Result: 21 of 95 cards reach a purpose-built page. 74 render `page.html`.**

Of the 74, at least 15 have a fully built dashboard at a different URL — including
`ceo-dashboard`, `account-master`, `iron-dashboard`, `final-qc-dashboard`,
`finishing-dashboard`, `packing-dashboard`, `shipping-dashboard`, `supplier-dashboard`,
`procurement-dashboard`, `purchases-dashboard`, `sourcing-dashboard`.

So two URLs for the same module return two different applications. CEO Executive Command
opens the real dashboard from the navbar (`/ceo-dashboard/`) and a generic stub from the
module directory (`/p/ceo-dashboard/`).

What `page.html` shows (read it at `templates/page.html:1`):

- a green **"ACTIVE"** pill (`:9`) — on a module that is not built
- the strapline "Connected departmental/manager dashboard framework."
- six buttons — `View` `Preview` `Edit` `Remove` `Download` `Print` (`:27`) — with no
  `onclick`, no enclosing `<form>`, no `formaction`. None of them issues a request.
- the same ten most-recent `MasterOrder` rows on **every** placeholder, whatever module was
  clicked
- the same global alerts and actions

## A4. 139 of 151 status messages can never be displayed

`portal/views.py` makes **151** `messages.*` calls across 33 views. `templates/base.html`
contains **no** messages block. Only four templates render one — `asset_machine_master.html`,
`stock_material_master.html`, `careers.html`, `sewing_master.html` — accounting for 12 calls.

The remaining **139 are invisible**:

| messages calls that never render | page |
|---|---|
| 11 | `hr_dashboard.html` |
| 9 | `sourcing_dashboard.html` |
| 8 | `communication_center.html`, `procurement_dashboard.html` |
| 7 | `final_qc_dashboard.html`, `label_dashboard.html`, `qc_dashboard.html`, `shipping_dashboard.html` |
| 6 | `attendance_dashboard.html`, `embroidery_dashboard.html`, `iron_dashboard.html`, `packing_dashboard.html`, `poly_dashboard.html` |
| 5 | `cutting_dashboard.html`, `hand_iron_dashboard.html`, `staff_self_service.html` |
| 4 | `buyer_delivery_sla.html`, `buyer_opportunity.html` |
| 2 | `barcode_master.html`, `barcode_scan_control.html`, `ceo_dashboard.html`, `finishing_dashboard.html`, `free_capacity_opportunity.html`, `profit_before_spend.html`, `profit_feasibility_gate.html`, `purchases_dashboard.html`, `report_master.html`, `supplier_dashboard.html` |

An operator saves a production entry, records QC, issues material or posts a stock movement
and the page returns with **no indication whether it worked**. On the failure paths the
error is discarded too, so a rejected write is indistinguishable from a successful one.

## A5. The production CSP disables every inline script — locally everything works

`nginx/default.conf:33` sets `script-src 'self'` with no `'unsafe-inline'`. The comment
above it explains `'unsafe-inline'` was retained for **`style-src`** — script was not
considered.

The templates contain **41 inline event handlers across 34 of 49 templates** and **4 inline
`<script>` blocks**. All are blocked in production. What that costs:

| blocked | count | effect |
|---|---|---|
| `onclick="window.print()"` | 31 | every **Print** button on every dashboard |
| `onclick="this.closest('dialog').close()"` | 4 | modal **Close** buttons — an opened dialog can only be dismissed with Esc |
| `onclick="document.getElementById('…').showModal()"` | 3 | the modal **open** buttons (`assign`, `live`, `result`) |
| `onclick="openAction(…)"` + its `<script>` | 1 | the sewing production-entry dialog |
| `onclick="chooseVacancy(…)"` + its `<script>` | 1 | the public **Apply Now** button on `/careers/` |
| `onchange="this.form.submit()"` | 1 | an auto-submitting filter |
| `<script>setInterval(…30000)` | 1 | the 30-second auto-refresh on the country command centre |
| `<script>` building the Live Fields grid | 1 | see A7 |

There is no CSP under `manage.py runserver`, so all of this works locally and fails silently
on the server with only a browser-console error. This is the most likely explanation for any
"the button does nothing" report from the deployed site.

## A6. Six role-gated business pages render as unstyled stacked text

Seven layout classes are used by templates in which they are defined **nowhere** — not in
`app.css`, not in a page stylesheet, not in an inline `<style>` block: `page-head`, `kpis`,
`kpi`, `table-wrap`, `primary`, `form-grid`, `grid-2`.

Affected pages: `buyer_delivery_sla.html`, `buyer_opportunity.html`,
`communication_center.html`, `free_capacity_opportunity.html`, `profit_before_spend.html`,
`profit_feasibility_gate.html`.

No KPI cards, no page header, no table wrapper, no form grid — the markup falls back to
default block flow. This covers both profit gates, both buyer/commercial pages and the
Communication Center.

Across the whole tree: **15 undefined classes, 50 (class, page) pairs.** The rest are
`barcode_master.html` (`barcode-nav`, `barcode-hub`, `primary`),
`barcode_scan_control.html` (`scanner`, `assignment`), `sewing_master.html`
(`detail`, `finder`, `board`) and `careers.html` (`empty`).

## A7. Seven public-facing portals are a photograph of an interface

`portal/views.py:227` — `PORTAL_VISUALS` maps seven routes to seven PNGs.
`templates/portal_visual.html` renders the page body as:

```html
<img src="{% static image_path %}" alt="{{title}}" width="1024" height="1536">
```

| route | image | size |
|---|---|---|
| `/franchise/` | `img/portals/franchise.png` | 1024×1536, 2.2 MB |
| `/investor-relations/` | `img/portals/investor.png` | 1024×1536, 2.3 MB |
| `/limerick-factory/` | `img/portals/factory.png` | 1024×1536, 2.8 MB |
| `/corporate-bulk/` | `img/portals/corporate.png` | 1024×1536, 2.4 MB |
| `/virtual-try-on/` | `img/portals/try-on.png` | 1024×1536, 2.4 MB |
| `/returns-refunds/` | `img/portals/returns.png` | 1024×1536, 2.2 MB |
| `/wishlist/` | `img/portals/wishlist.png` | 1024×1536, 2.2 MB |

All seven are `PUBLIC` in `ROUTE_POLICY`, and five are linked from the public homepage. A
franchise applicant, corporate buyer or investor arriving at these pages is served a
screenshot. There is no form, no submit, no interaction.

The single interactive element — a "Live Fields" dialog listing field values as text — is
opened by an inline `onclick`, closed by an inline `onclick` and populated by an inline
`<script>`, so **all three are blocked by the CSP** (A5). On the server these pages are a
static image and a nav bar.

Two authenticated pages follow the same pattern: `factory_resource_core.html`
(`img/factory_resource_core.png`, 1536×1024, 2.0 MB) and `bundle_traceability_finder.html`
(`img/bundle_traceability_finder.png`, 1536×1024, 2.4 MB).

`sewing_master.html` is **not** in this category — it is a real 8,925-line interface.

`static/img/` totals **50 MB** and ships inside the Docker image, because these pages need
it. Each page load transfers 2–3 MB.

## A8. The sixteen "automatic report" families never run

`core/settings.py:215` — `CELERY_BEAT_SCHEDULE` contains five entries, and they schedule
exactly three tasks:

```
portal.tasks.scheduled_report_snapshot    08:00, 13:00, 20:00
portal.tasks.refresh_exchange_rates       06:30
portal.tasks.purge_expired_audit_logs     Fri 03:15
```

`scheduled_report_snapshot` (`portal/tasks.py:14`) counts alerts, red alerts, open actions
and open orders. That is all it does.

Separately there are **16 `*AutoReport` models**, **10 `manage.py run_*_auto_report`
commands** and **10 `deploy/*.cron.example`** files. Nothing connects them:

- no `run_*` command appears in `portal/tasks.py`
- no `run_*` command appears in `CELERY_BEAT_SCHEDULE`
- no crontab is installed by `Dockerfile`, `docker-compose.yml` or `scripts/deploy.sh`
- the cron examples target `/path/to/project && /path/to/venv/bin/python`, a layout that
  does not exist on a Docker host

And 6 of the 16 models have no command at all: `CuttingAutoReport`, `EmbroideryAutoReport`,
`HandIronAutoReport`, `IronAutoReport`, `LabelAutoReport`, `PolyAutoReport`.

The dashboards read these tables 3–7 times each, so those panels are permanently empty in
the deployed system. A `beat` container is running and healthy, which makes it look
scheduled.

## A9. The header strip is cached under one global key but computed with scoped managers

`portal/context_processors.py:24` — `CACHE_KEY = 'portal:global-control-strip'`, a single key
with a 30-second TTL, and its docstring says the cache is "global rather than per-user
because every figure here is organisation-wide."

That was true before organisation scoping. It is not true now: `_compute()` queries `Alert`,
`ActionItem` and `Employee`, and all three carry `objects = ScopedManager` in
`portal/models.py`. So the figures are scoped to whoever computes them, and then served from
one shared key to **everybody** for the next 30 seconds.

Whichever user's request populates the cache first — any organisation, any country, any
factory, any role including Helper — has their site's alert count, red-alert count, open
actions, present headcount and total staff cost displayed in the header of every other
user's every page.

`AttendanceEvent`, which supplies the present-employee list, has no `ScopedManager` and no
site foreign key at all.

## A10. No user can change their own password

```
$ grep -c password portal/urls.py core/urls.py
0
0
```

No `PasswordChangeView`, no `PasswordResetView`, no self-service password field. The only
path is `/admin/password_change/`, which requires `is_staff` — and `portal/roles.py:152`
deliberately documents that `is_staff` must not confer business access.

An Operator, Helper or Staff member cannot rotate a password, and there is no reset flow for
a forgotten one. Every credential change has to go through whoever holds admin.

## A11. "Staff Cost" appears on every page with no currency and no conversion

`templates/base.html:36` and `templates/page.html:20`:

```html
<span>Staff Cost <b>{{ today_staff_cost }}</b></span>
```

Traced back: `portal/context_processors.py:47` — `Employee.objects.filter(…).aggregate(cost=Sum('daily_cost'))`.
A raw `Sum` over every present employee, whatever site or country they belong to. No currency
label, no symbol, no conversion through `portal/currency.py`.

This is the defect `TECHNICAL_ASSESSMENT.md §5.6` describes for the CEO dashboard — BDT and
EUR amounts added together — still live in the header of every authenticated page. The
number cannot be interpreted, because nothing on screen says what unit it is in.

---

# B — inconsistent or misleading

## B12. Navigation is role-blind, and the 403 it produces is a bare page

`templates/base.html` links every authenticated user to pages they may not open:

| navbar link | `ROUTE_POLICY` group | roles that can open it |
|---|---|---|
| ACCOUNT MASTER | `roles.FINANCE` | 4 of 20 |
| REPORT MASTER | `roles.MANAGEMENT` | 16 of 20 |
| CEO REPORT | `roles.EXECUTIVE` | 2 of 20 |
| Stock & Material | `roles.STOCK` | 6 of 20 |
| SEWING MASTER | `roles.PRODUCTION` | 7 of 20 |

A Helper, Operator or Staff member (`roles.py:38–40`, in none of those groups) sees all five
and is refused by all five. What they land on (`portal/authorization.py:193`):

```python
return HttpResponseForbidden('You are not authorised to view this page.')
```

Plain text. No layout, no navbar, no logo, no link back. Compounded by B13.

## B13. No custom error pages

`templates/404.html`, `500.html`, `403.html`, `400.html` — none exist. With `DEBUG=0` every
404 and 500 is Django's unbranded default: no navigation, no branding, no way back.

## B14. The navbar disappears below 900px with nothing in its place

`static/css/app.css`:

```css
@media(max-width:900px){.topbar nav{display:none} …}
```

The nav is removed and not replaced — no hamburger, no drawer, no menu button. On a phone or
a narrow tablet the only remaining navigation on an authenticated page is the brand logo,
hardcoded to `/dashboard/`.

The grids do collapse correctly (`.stats`/`.split` → 2 columns at 900px, 1 at 600px) and the
nav is `overflow-x:auto` above 900px, so this is specifically the mobile nav that is missing.

## B15. Finance cannot maintain exchange rates, though the configuration says they do

`.env.example:84` states: *"Empty means no outbound request is made and rates are maintained
by Finance in admin."* `EXCHANGE_RATE_API_URL` is empty by default.

`ExchangeRate` is **not registered in `portal/admin.py`**. It is written only by
`seed_project1` (three seed rows) and by `manage.py fetch_exchange_rates`, which no-ops
unless the API URL is set.

So with the default configuration there is no way for Finance to enter or correct a rate.
`portal/currency.py` refuses to convert without one — correctly, by design — which means
every cross-currency figure stays unconvertible and every consolidated total stays
incomplete, silently.

## B16. The admin is the only editing UI for most models, and it is unconfigured

168 of 179 models are registered. Across all of them:

| | count |
|---|---|
| `list_display` | **0** |
| `search_fields` | **0** |
| `list_filter` | **0** |
| `date_hierarchy` | **0** |
| inlines | **0** |
| `ModelAdmin` subclasses | 1 (`ReadOnlyAuditAdmin`) |
| bare `admin.site.register(m)` | 33 statements |
| models with no `__str__` | **161 of 179** |
| — of those, used as a ForeignKey target | **36** |
| `verbose_name` on any model or field | **0** |
| `help_text` in 3,479 lines of models | 6 |
| `admin.site.site_header` | not set |

Consequences a user sees: every changelist is a column of `ModelName object (1)`; 36 models
appear that way in the dropdowns other models force you to pick from; a table with thousands
of rows has no search box; plurals are auto-generated (`Inventorys`); the page title reads
"Django administration"; and parent/child records (order + lines, plan + bundles, PO + items)
must be created on separate screens and joined by ID.

## B17. Eleven models have no UI at all — including the whole Iron module

| model | referenced by | in admin |
|---|---|---|
| `IronPlan`, `IronBundleScan`, `IronProductionEntry`, `IronQC`, `IronVariance`, `IronAutoReport` | `iron_dashboard` (7×) | no |
| `SewingBundleAssignment` | `views.py` (13×) | no |
| `FactoryProcessStandard` | `views.py` (1×) | no |
| `ExchangeRate` | `currency.py` (2×) | no |
| `StorefrontConfiguration` | `views.py` (3×) | no |
| `LocalizedContent` | **nothing** | no |

Every other department — Cutting, Embroidery, Label, QC, Hand Iron, Poly, Final QC,
Finishing, Packing, Shipping — is registered. **Iron is the only one that is not**, so its
plans, scans, production entries and QC records cannot be created or corrected anywhere.

`FactoryProcessStandard` carries `sam`, `smv` and `cost_per_minute` — the standards that
drive every efficiency figure — and there is no screen and no admin for entering them.

`SewingBundleAssignment` carries `standard_sam`, `efficiency_percent`,
`machine_cost_per_minute`, `labour_cost_per_minute` and `total_process_cost` per operator per
machine, and is read by the sewing dashboard, but has no create/edit UI.

`LocalizedContent` — a translations table keyed by language code — is referenced by no view,
no admin, no test and no template. Dead schema.

## B18. Multi-language is declared but not implemented anywhere

| check | result |
|---|---|
| `USE_I18N` | `True` |
| `LocaleMiddleware` in `MIDDLEWARE` | **absent** |
| `LANGUAGES` setting | **absent** |
| `LOCALE_PATHS` setting | **absent** |
| `locale/` directory or any `.po` file | **none** |
| `{% trans %}` / `{% blocktrans %}` across 49 templates | **0** |
| language switcher in the UI | **none** |
| `django.contrib.humanize` in `INSTALLED_APPS` | absent |
| `USE_THOUSAND_SEPARATOR` | unset |

Every user-facing string in all 49 templates is hardcoded English. `USE_I18N=True` with no
locale machinery is inert. Numbers render without grouping, so a Decimal appears as
`1234.5000`.

## B19. The default authenticated landing page is hardcoded to one country

`portal/views.py:463`, inside `dashboard` — the page every login redirects to
(`LOGIN_REDIRECT_URL='/dashboard/'`):

```python
country=(OrganizationNode.objects.filter(node_type='Country',name__icontains='Bangladesh').first())
tree=country.descendants() if country else OrganizationNode.objects.all()
```

A country matched by name substring; and if no such node exists the fallback is **every**
organisation node, unfiltered.

Also hardcoded: `templates/bangladesh_command_center.html:12` — the Country `<select>` has
exactly one `<option>Bangladesh</option>`; `views.py:846` — `request.GET.get('country','Bangladesh')`;
`models.py:1502` — `country=models.CharField(default='Bangladesh')`;
`attendance_dashboard.html:596`, `ceo_dashboard.html:386`, `cutting_dashboard.html:452` —
Bangladesh named in visible text.

## B20. Currency symbols hardcoded in the presentation layer

- `templates/sewing_master.html:5` — `<b>BDT {{kpis.machine_cost|floatformat:2}}</b>`,
  while `BASE_CURRENCY` is `EUR`
- `static/js/homepage.js` — `u==='EUR'?'€':…` and a `'€'`-prefixed revenue figure labelled
  "Revenue recorded in EUR" regardless of the configured base

Both contradict the configurable `BASE_CURRENCY` that `portal/currency.py` exists to serve.

## B21. Only 6 of 120 routes are ever requested by a test

124 test functions in 23 `TestCase` classes, and the test client is pointed at exactly six
routes: `login`, `careers`, `account_master`, `cutting_dashboard`, `api_approval_request`,
`api_ceo_dashboard`.

`assertTemplateUsed` appears **0** times. 114 routes and roughly 45 of the 49 templates are
never rendered by a test.

The suite tests business logic well — attendance arithmetic, scoping, currency, authorisation
— but nothing asserts that a page renders. A template that fails to compile, a missing
context variable, an undefined CSS class, a `/page/` prefix typo (A1) or a dead handler (A5)
all pass CI. Every finding in section A would have been caught by one smoke test per route.

## B22. The whole client-side layer is a 265-byte `console.log`

`static/js/app.js`, loaded on all 36 pages that extend `base.html`, in full:

```js
document.addEventListener('click',e=>{if(e.target.matches('button')&&['View','Preview','Edit','Remove','Download','Print'].includes(e.target.textContent.trim())&&e.target.textContent.trim()!=='Print'){console.log('Project 1 action:',e.target.textContent.trim())}})
```

It logs and does nothing else. It also contains a dead condition — `'Print'` is in the array
and then excluded by `!== 'Print'`.

So every `View`, `Preview`, `Edit`, `Remove` and `Download` button that is not inside a form
does nothing at all — independently of the CSP. Confirmed on `page.html` (6 buttons) and
`forms_master.html` (6 buttons).

## B23. Recruitment decisions throw the user off the page they are working on

`templates/hr_recruitment_applications.html` has two POST forms, both
`action="{% url 'hr_dashboard' %}"`, carrying seven distinct hidden `action` values:
`vacancy`, `recruitment_decision`, `recruitment_advance`, `request_hiring_approval`,
`create_recruitment_employee`, `grant_recruitment_portal`, `activate_recruitment_access`.

Its own view (`portal/views.py:334`) is GET-only. `hr_dashboard` handles all seven and ends
`return redirect('hr_dashboard')` (`:2698`).

Every decision navigates away from the application list to the HR dashboard, and the
confirmation message lands on a page the user did not ask for — where, per A4, it is not
rendered either. Processing ten applications means ten round trips and re-finding your place
each time.

## B24. Two upload paths, two standards

`portal/views.py:275` — recruitment uploads are validated: 10 MB cap and an extension
allowlist (`.pdf .doc .docx .jpg .jpeg .png`).

`portal/views.py:1294` and `:1310` — communication attachments have **no size check and no
type allowlist**, and store `mime_type=getattr(f,'content_type','')` — a value supplied by
the client.

Three different size limits are in play: nginx `client_max_body_size 50M`,
`DATA_UPLOAD_MAX_MEMORY_SIZE` 50 MB (from `DJANGO_MAX_UPLOAD_BYTES`), and the 10 MB literal
in the recruitment validator. The configured setting does not reach the validator.

## B25. Tables truncate at thirteen different limits with no indication

47 `|slice:` truncations across 14 templates, at 13 distinct limits:
`:14`×13, `:12`×8, `:5`×6, `:20`×5, `:8`×4, `:4`×2, `:3`×2, `:18`×2, and one each of
`:1 :6 :7 :10 :15`.

The same object is cut at different lengths on sibling pages — department plans are
`|slice:":12"` on cutting, embroidery and label, and `|slice:":14"` on iron, qc, final_qc,
poly, packing, shipping and hand_iron. `hand_iron_dashboard.html` uses two different limits
in one file.

`Paginator` appears **0** times in `views.py`. No template contains pagination markup, and
none prints a total next to a truncated table. A supervisor reading "12 plans" has no way to
know the list was cut.

## B26. Nine modules are listed twice in the directory, and one has no working link

Registry pairs with the same title under two slugs — one redirects to the real module, the
other lands on the stub:

`hr-master`/`hr-dashboard` · `attendance-master`/`attendance-dashboard` ·
`self-service`/`staff-self-service-portal` · `cutting`/`cutting-dashboard` ·
`embroidery`/`embroidery-dashboard` · `label`/`label-dashboard` · `poly`/`poly-dashboard` ·
`finishing`/`finishing-dashboard` · `packing`/`packing-dashboard`

And `page_view` contains one rule that can never fire:

```python
if slug=='sewing-dashboard': return redirect('sewing_master')
```

No registry entry has that slug — Sewing's slug is `sewing`. So Sewing has **no** working
link from the module directory; `/sewing-master/` is reachable only from the navbar.

## B27. Six of the thirteen header chips are decoration

`templates/base.html:28–36` — `Chat 24/7`, `WhatsApp`, `Email`, `Company Forms`,
`Documents`, `Reporting` are plain `<span>` elements. Not links, no handlers, no targets.

They are styled identically to the live counters beside them (`Alerts`, `Action Required`,
`Red Alert`, `Present`, `Staff Cost`), so they read as controls. Meanwhile
`communication_center`, `forms_master` and `universal_file_center` all exist and are not
linked from here.

`login_no` (`:34`) renders a hardcoded `1` — documented as cosmetic at
`context_processors.py:58`.

## B28. `/barcode-instructions/<mode>/` silently serves the wrong sheet

`portal/views.py:766`:

```python
mode=mode if mode in groups else 'generation'
```

Any unrecognised mode returns **200** with the barcode *generation* instructions. Every other
parameterised route rejects bad input properly — `portal_visual` raises `Http404`
(`views.py:261`), `page_view` uses `get_object_or_404`.

An operator following a mistyped or stale workstation link — `/barcode-instructions/scanning/`
instead of `/scan/` — is shown generation instructions instead of scanning instructions, with
no sign the URL was wrong, on a page whose purpose is to be the printed operating procedure
at a workstation.

## B29. Model data is written into the DOM with `innerHTML`

`static/js/homepage.js` — on the **public, unauthenticated** homepage:

```js
`<article class="live-product"><strong>${i.name}<b>${m(i.unit_cost,i.currency)}</b></strong><small>${i.sku} · ${i.qty} available</small></article>`
```

assigned via `innerHTML`. `i.name` and `i.sku` are `StockItem` fields entered by staff.
`|json_script` (`public_home.html:23`) escapes for the *script* context, so `JSON.parse`
returns the raw string and `innerHTML` then parses it as HTML. `templates/portal_visual.html`
does the same with `e.innerHTML = \`…${k}…${v}\``.

Currently mitigated by the production CSP, which blocks the injected inline handlers — but
the mitigation is incidental, applies only behind nginx (not under `runserver`), and there is
no output encoding at the point of use.

## B30. Contact details and one external URL hardcoded in templates

- `templates/careers.html` footer — `WhatsApp: 089 978 8187`, `Email: urmos@rozalia.ie`
- `templates/bangladesh_command_center.html:27` — `https://wa.me/353899788187`

The `wa.me` link is the **only** external URL anywhere in the templates, CSS or JS. It routes
a user to a third party (Meta) and carries a company number in source rather than
configuration. Worth a policy check given the standing requirement for written authorisation
before any external service is used.

---

# Accessibility

Counted across all 49 templates.

| | count |
|---|---|
| form controls (`input`/`select`/`textarea`) | **1,404** |
| `<label for=…>` bound to a control | **0** |
| bare `<label>` (no `for`) | 413 |
| `aria-label` | 21 |
| `<th>` elements | **641** |
| `<th scope=…>` | **0** |
| `<caption>` | **0** |
| `<img>` | 11 |
| — missing `alt` | 4 |
| templates setting `<html lang>` | 10 of 49 — **`base.html` is not one**, so all 36 pages that extend it have no language |
| skip-to-content link | 1 (`public_home.html`) |
| favicon | 1 (`sewing_master.html`) |
| `:focus` / `:focus-visible` rules | 2, both in `homepage.css` — **`app.css` has none** |
| `outline:none` / `outline:0` | 0 — the browser default focus ring is at least not removed |
| `@media print` | 3, all in `country_command.css` — while **31 pages carry a Print button** |
| `prefers-color-scheme` | 0 — dark-only; `app.css` sets `--bg:#06110e` |

Beyond the counts:

- **Status shown only by colour.** `.red` for Red Alert (`base.html:32`), `.status{color:var(--green)}`
  in `app.css`. No text, icon or shape carries the same information, so a colour-blind user
  sees no distinction.
- **Glyphs as UI without accessible names** — `⚠ ✓ ● 💬 ◉ ✉ ▣ ▤ ▥` in the header strip. The
  box-drawing characters used for Forms/Documents/Reporting are not reliably present in
  common Windows fonts.
- **Keyboard cost** — a 13-link navbar on every page with no skip link means tabbing through
  all of it on every navigation.
- Contrast was computed and **passes**: `--muted #8ca79b` on `--panel #0d1d18` is 7.4:1.
  Not a finding.

---

# C — cosmetic, structural, maintenance

**C31. Internal registry numbers and a build note in page headings.**
`<h1>#33 Asset & Machine Master`, `#53 Forms Master Dashboard`, `#32 Stock & Material Master`,
`#{{ page.page_id }}` on `page.html`, and `report_master.html:268`:

```html
<h1>#52 REPORT MASTER — REBUILT
```

"REBUILT" is the only such marker in visible template text, on a page promoted in the navbar
of every authenticated page. 5 of 49 templates carry the `#NN` prefix; 44 do not.

**C32. Three names for the same control.** The submit button on a GET filter reads `Search`
(asset_machine_master, stock_material_master pane 1, bundle_traceability_finder ×4), `Filter`
(stock_material_master pane 2, forms_master, buyer_opportunity, communication_center,
free_capacity_opportunity, profit_before_spend) or `Apply` (bangladesh_command_center,
factory_resource_core). `stock_material_master.html` uses **both** on one page — "Search" at
`:43`, "Filter" at `:143`. Wrapper class is `filter` (4), `filters` (2) or absent (13).
`forms_master.html:10` and the four sewing find-forms omit `method` entirely.

None of the 16 department dashboards has a filter form at all — and those are the pages whose
tables are truncated to 12–14 rows (B25), so a plan outside the slice cannot be reached.

**C33. Nine of twelve navbar links are literal strings.** `base.html` mixes `{% url %}`
(`global_dashboard`, `sewing_master`, `universal_file_center`) with hardcoded paths
(`/dashboard/`, `/account-master/`, `/report-master/`, `/ceo-dashboard/`,
`/staff-self-service/`, `/stock-material-master/`, `/forms-master/`, `/bundle-barcode/`,
`/logout/`, plus the brand link). `dashboard.html:22` hardcodes four more inside a nested
`{% if %}`. A literal href cannot fail at template-compile time and is invisible to the
route-integrity check — which is precisely how A1 shipped.

**C34. Two dashboards, no explanation.** "Dashboard" (`/dashboard/`, the Bangladesh command
centre) and "All Modules" (`/global-dashboard/`, the 95-card directory) sit adjacent in the
navbar with nothing to distinguish them.

**C35. `/admin/` linked to every authenticated user**, who then meets an admin login form if
they lack `is_staff`.

**C36. Logout is a GET link** (`base.html:24`), so any prefetcher or crawler can end a
session.

**C37. Structural duplication.** 13 templates each ship their own `<!doctype>`, `<head>`,
`<title>`, viewport and stylesheet links. There are **0** `{% include %}` directives anywhere
— no partials at all — so the filter bar, KPI card row, export toolbar and data table are
copy-pasted across 14–20 templates each.

**C38. CSS in the wrong place.** 22 `<style>` blocks appear inside `{% block content %}`,
i.e. inside `<body>`, because `base.html` exposes only `{% block title %}` and
`{% block content %}` — no block for page CSS or JS. Plus 66 inline `style=` attributes.

**C39. `portal_visual` reads as dead code.** It is a view-shaped function with no leading
underscore and no route, which makes it look orphaned. It is in fact the shared renderer for
the seven portals (A7). Renaming it `_portal_visual` would say so.

**C40. Route and naming drift.** Two CSV families coexist — `/<x>-dashboard/report.csv` named
`<x>_report_csv`, and `/<x>/export.csv` named `<x>_export_csv` — with no rule distinguishing
them. Names diverge from paths: path `bundle-barcode/` → name `barcode_master`; path
`purchases-dashboard/report.csv` → name `purchase_report_csv` (singular against a plural
path). `finance_overseas_preview` returns JSON but is not under `/api/`, so
`_wants_json()` in `authorization.py:180` gives it an **HTML** 403 body — which breaks a
fetch client. Separately, an unauthenticated request to any `/api/` endpoint gets a **302** to
`/login/` rather than a 401, because `authorization.py:218–221` defers to `@login_required`.

**C41. `static/reference/` is dead weight in git** — three PNGs, correctly excluded from the
Docker image and referenced by no template, CSS or view.

---

# Cross-cutting observations

**One root cause explains most of section A.** There is no test that renders a page (B21).
A1, A5, A6 and B22 are all defects a single smoke test per route would have caught on the
day they were introduced — a `GET` of each of the 120 routes asserting `200` and
`assertTemplateUsed`.

**Three hand-maintained routing tables that disagree.** `portal/urls.py` (120 routes),
`page_view`'s 19-line if-chain, and `dashboard.html`'s 4-branch `{% if %}`. A1, A2, A3, B26
and the dead `sewing-dashboard` rule are all consequences of keeping the same mapping in
three places by hand.

**Dev and prod diverge in a way nothing detects.** The CSP exists only behind nginx, so every
inline handler works under `runserver` and fails on the server (A5). Nothing in the deploy
script checks for it.

**Several findings sharpen the earlier SMV/SAM report question.** `FactoryProcessStandard`
(`sam`, `smv`, `cost_per_minute`) and `SewingBundleAssignment` (`standard_sam`,
`efficiency_percent`, machine and labour cost per minute, `total_process_cost`) already model
exactly what was asked for, per operator and per machine — but neither has any UI for
entering the standards (B17), and the automatic-report machinery that would publish them on a
schedule is not wired up (A8).

---

*No file was modified in producing this register. Every citation is a file and line in the
current working tree at commit `c7f741e`.*
