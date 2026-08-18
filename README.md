# Emerald Rozalia Project 1 — Unified Website / Operations Platform

Server-ready Python/Django package for the approved Project 1 architecture.

## Included
- 70 registered primary pages/dashboards.
- 650 Forms Master definitions, seeded from `data/form_master_650.json`.
- Role-driven manager/department dashboards and common control strip.
- Order Master, production, stock, HR, finance, procurement, QC, franchise, POS, reports, documents, barcode/QR and ID-card foundations.
- Universal View / Preview / Edit / Remove / Download / Print controls.
- Bundle barcode and QR image generation.
- Bangladesh-only configurable 2.5% overseas incentive rule (configuration only; validate legal eligibility before posting as earned income).
- PostgreSQL + Redis + Celery + Gunicorn + Nginx + Docker Compose.
- Original uploaded Project 1 screenshots bundled under `references/` as visual targets.

## Quick start (Docker)
1. Copy `.env.example` to `.env` and replace all production secrets.
2. `docker compose up -d --build`
3. `docker compose exec web python manage.py makemigrations portal --noinput`
4. `docker compose exec web python manage.py migrate --run-syncdb --noinput`
5. `docker compose exec web python manage.py seed_project1`
6. `docker compose exec web python manage.py collectstatic --noinput`
7. Open `http://SERVER_IP/` (or the configured domain).

Default seed admin comes from `.env`; change it immediately.

## Important
This package provides a runnable unified core, data model, routing, dashboard framework, seeded page/form registry and shared operational engines. Specialized third-party integrations (WhatsApp Cloud API, Hikvision credentials, ZKTeco device access, SIP/PBX, payment gateway, SMTP) require real credentials/network endpoints before live use.

## Project 1 v2 operational engines
- Mandatory barcode/QR-backed `STOCK IN SCAN` and `STOCK OUT SCAN` service/API, with available-stock protection.
- Manual stock override requires a recorded senior approval and reason.
- Approval Request workflow with approve/reject decision endpoint and audit middleware coverage.
- Bangladesh BDT value variance engine: absolute variance greater than Tk 10 creates a RED alert automatically.
- Attendance daily summary engine for Operator/Helper 08:00–17:00 and Staff 08:00–20:00 schedules, including breaks, unpaid minutes and post-schedule OT calculation.
- Device Integration registry/API foundation for CCTV, NVR, attendance and IP-phone integrations without embedding live credentials in source.

### New authenticated API endpoints
- `POST /api/stock/scan/`
- `POST /api/approvals/`
- `POST /api/approvals/<id>/decision/`
- `POST /api/variance/`
- `GET /api/attendance/<employee_id>/summary/?date=YYYY-MM-DD`
- `GET /api/devices/`

## Stock & Material Master (Project 1 v3)
Open `/stock-material-master/` after login. The module manages material definitions, fabric/accessory lots and rolls, barcode/QR traceability, PO/customer-order references, stock location, QC status, reservations, valuation and physical movements. Physical movements require a barcode and reference. Manual adjustments require an approved `ApprovalRequest` and reason.

API: `GET /api/material-stock/` returns current lot-level stock balances.
CSV: `GET /stock-material-master/export.csv` downloads the stock/material ledger.


## Asset & Machine Master (Project 1 v4)
Open `/asset-machine-master/` after login. This module registers assets/machines with barcode/QR identity, serial/model/manufacturer, factory/location/department/employee assignment, operation capability, speed, available minutes, efficiency, power, valuation/depreciation metadata, warranty and service schedule. It includes preventive/corrective maintenance, downtime, transfers/assignment, retirement/disposal approval control, CSV export, API output and audit coverage.

API: `GET /api/assets/`
CSV: `GET /asset-machine-master/export.csv`


## Project 1 v5 — Buyer Enquiry / Order Opportunity Master
Page #34 now includes buyer enquiry capture, opportunity pipeline, quotation versioning with approval control, buyer activity/follow-up history, overdue follow-up visibility, weighted pipeline value, lost-reason tracking, CSV/API output, and controlled Won-to-Order-Master conversion.


## Project 1 v6 — Communication Center Master
Page #71 provides Unified Inbox and Chat 24/7 with persistent conversation threads, user/department routing, buyer opportunity and Order Master links, attachments, read/unread history, notices, broadcasts and red alerts. Email/WhatsApp/SMS/voice/video channels are represented by connector-backed message queues and require provider credentials before external delivery is live.


## Project 1 v7 — Profit + Feasibility Gate
Page #72 calculates revenue, full cost build-up, gross profit and margin, combines capacity/material/workforce/machine/lead-time/quality/compliance/buyer-credit readiness, commercial risk and bottlenecks, provides a system recommendation, and requires a senior-approved final decision before a Buyer Opportunity can become WON or generate a confirmed Order Master. Decisions: ACCEPT, ACCEPT WITH RISK, HOLD, REJECT.


## Project 1 v8 — Free Capacity Opportunity / Quick Order
Page #73 identifies spare production capacity after confirmed-order reservations and a safety buffer. It compares required minutes against the lowest available machine/workforce/line capacity, checks material/accessories/QC/finishing readiness, current confirmed load, rush risk and incremental profit margin, and produces TAKE QUICK ORDER / TAKE WITH RISK / HOLD / REJECT recommendations. Taking a quick order requires senior approval and cannot consume protected capacity needed by confirmed orders.


## Project 1 v9 — Universal File Controls
Every persisted Project 1 document or communication attachment that the logged-in user is permitted to access is routed through the same VIEW, PREVIEW, DOWNLOAD and PRINT service. Confidential documents and communication attachments are permission checked before file delivery. Every file action records user, action, file, reference, timestamp, IP address and user-agent in FileAccessLog. Page #74 provides the Universal File Center and access history.


## Project 1 v10 — Master Order to Buyer Address Delivery (Max 15 Days)
Every delivery SLA uses a 15-calendar-day maximum from confirmed Master Order. The module records buyer delivery address, dispatch target, courier/tracking, expected and actual delivery, automatic due/overdue alerts, approved exceptions, receiver/GPS/courier confirmation and proof-of-delivery files. An Order Master is not marked COMPLETED until buyer delivery is confirmed.


## Project 1 v11 — Profit Before Spend Control
Page #76 recalculates projected order profit before money is committed. It combines revenue, approved base cost, prior committed spend and the proposed new spend, then checks projected profit and margin against the minimum margin. Decisions are ALLOW, ALLOW WITH APPROVAL, HOLD and BLOCK. A reusable assert_profit_before_spend() service is included so purchasing and expense workflows can enforce the same rule before posting a commitment.


## Project 1 v12 — Staff Self-Service Portal
Page #77 implements the Staff Self-Service Portal using `static/reference/staff-self-service-portal-reference.png` as the approved visual reference. Staff access is linked to a User and Employee through `StaffSelfServiceProfile`. The dashboard provides My Applications, My Documents, official ID card with employee QR, Appointment Letter, Joining Letter, Company Handbook, Payslip/Payroll, Attendance/Duty summary, Training, Schedule, Notifications, HR Support/Grievance, announcements/events and Communication Center access.

All permitted staff documents and application attachments use Project 1 Universal File Controls: VIEW, PREVIEW, DOWNLOAD and PRINT, with FileAccessLog history. HR support requests create a Communication Center / Chat 24/7 thread automatically. Administrators can manage staff profiles, documents, payroll summaries, duty summaries, schedules, notifications, announcements and events through Django Admin.


## Project 1 v13 — HR Dashboard
Page #78 implements the approved HR Dashboard reference. It includes HR operational masters and workforce intelligence, linked Staff Self-Service, Communication Center, approvals, alerts, audit history, document expiry and role-controlled HR file access. The approved screenshot is preserved at `static/reference/hr-dashboard-reference.png`.


## Project 1 v14 — Attendance Dashboard
Page #79 implements the approved Attendance Dashboard visual reference stored at `static/reference/attendance-dashboard-reference.png`.

The attendance calculation engine enforces Project 1 workforce rules:
- Saturday through Thursday are standard working days; Friday is the weekly closed day.
- Operator and Helper: 08:00 check-in, 13:00–14:00 break, 17:00 checkout, 480 mandatory minutes, authorized OT earliest 17:30.
- Staff: 08:00 check-in, 13:00–14:00 primary break, 17:00–17:15 second break event, 20:00 checkout, 660 mandatory minutes, authorized OT earliest 20:30.
- Normal payable duty is bounded by the scheduled shift. Time before 08:00 and ordinary time after scheduled checkout do not increase normal paid duty.
- Overtime contributes only from APPROVED AttendanceOvertime records.
- Authorized manual attendance entries require an APPROVED ApprovalRequest and are logged as AttendanceManualAdjustment.
- Daily summaries capture mandatory, worked, due/unpaid, late, early leave, paid gate pass, unpaid gate pass and NPT minutes plus costs.

The dashboard includes connected DeviceIntegration status, Attendance CCTV feed tiles, leave/gate-pass/overtime/NPT controls, payroll-facing cost totals, CSV export and JSON API.


## Project 1 v15 — Cutting Department
Page #80 is the functional Cutting Department dashboard. Workflow: Approved Master Order → Cutting Plan → Fabric Roll STOCK OUT SCAN → Lay/Marker/Pattern → Cutting → Cutting QC → Bundle Barcode/QR → Wastage/Reject/Recut reconciliation → Cut Panel WIP STOCK IN → Sewing handover STOCK OUT.

Cutting production records employee, machine, target/actual quantity, process minutes, NPT, cost/minute and calculated process cost. Manual production entry requires an approved senior authorization. Cutting automatic reports are controlled at the Project 1 Bangladesh-local reporting slots 08:00, 13:00 and 20:00 and include outstanding alerts/actions/escalations.


## Project 1 v16 — Embroidery Department
Page #81 implements the Embroidery Department workflow: approved order/plan → cutting bundle BUNDLE IN SCAN → order/style/size/colour/quantity verification → machine/operator assignment → thread/material STOCK OUT → embroidery production → inline/final QC → repair/rework reconciliation → BUNDLE OUT SCAN → next department.

Bundle movement is enforced: wrong barcode, duplicate scan, quantity mismatch, missing valid IN scan, or missing final QC PASS blocks downstream release and creates/uses Red Alert controls. Production can be linked to a bundle only after a valid Embroidery BUNDLE IN scan. Manual production entry requires senior approved authorization.

Embroidery automatic reports use the Project 1 Bangladesh-local slots 08:00, 13:00 and 20:00 and include bundle movement, target/actual, process minutes/NPT, QC, material consumption, cost, outstanding alerts/actions and escalations.


## Project 1 v17 — Label Department
Page #82 implements the Label Department. It supports main/brand, size, care/wash, composition, country-of-origin, woven, printed, heat-transfer, barcode, QR, price/hang-tag and custom labels.

Production is blocked until the current label proof version is approved. QC verifies the active version; a wrong version places the record on HOLD and creates a Red Alert. Label allocation must match the plan's Master Order, requires QC PASS, requires LABEL IN SCAN, prevents issue quantity above allocation, and blocks duplicate LABEL OUT scans.

The module reconciles allocated, issued, used, returned, rejected and remaining balance quantities, and includes material consumption, process minutes, NPT, cost/minute, material cost, process cost, total label cost, variance controls, automatic 08:00/13:00/20:00 reports, CSV/API, and universal file controls for artwork/specification/proofs/sample images/report files.


## Project 1 v18 — QC / Quality Control
Page #83 implements production-wide QC. The inspection stages are Incoming Material, Cutting, Embroidery, Sewing Inline, Label/Accessory, Finishing, Final Inspection, Packing and Pre-Shipment.

QC Bundle OUT is blocked until a QC Release Gate passes. Critical defects force HOLD and create a Red Alert. Rework requires a new QC result before release. Conditional release requires an approved senior authorization. AQL inspection records critical/major/minor defects, measurement, shade, label, workmanship and packing failures, plus pass/rework/reject quantities.

The QC dashboard calculates First Pass Quality and DHU, tracks open CAPA and unactioned defects, records root cause/corrective/preventive actions, and includes the standard Project 1 08:00, 13:00 and 20:00 Bangladesh-local automatic reporting slots.

QC specifications, approved samples, inspection photos, inspection sheets and generated report files use Project 1 Universal File Controls: VIEW, PREVIEW, DOWNLOAD and PRINT.


## Project 1 v19 — Hand Iron / Manual Ironing
Page #84 implements Hand Iron / Manual Ironing. Workflow: approved Master Order → Hand Iron Plan → BUNDLE IN SCAN → operator/workstation assignment → temperature verification → manual ironing → QC → re-iron/re-QC if required → QC PASS → BUNDLE OUT SCAN.

The system blocks Bundle OUT when a valid Bundle IN scan or QC PASS is missing. Barcode mismatch, duplicate scan and quantity mismatch are blocked and generate Red Alert records. Plan-level fabric type and min/max temperatures are enforced; temperature above the maximum blocks production.

Production tracks target/actual, start/end, process minutes, NPT, actual temperature, cost/minute, labour cost, utility cost, process cost and total Hand Iron cost. Manual production entry requires senior approved authorization.

Quality controls include scorch/burn, shine/glazing, colour change, water/steam marks, crease and shape distortion, with reject and re-iron quantities and mandatory re-QC before release. Automatic reports use 08:00, 13:00 and 20:00 Bangladesh-local slots. Hand Iron instruction files, QC photos and generated reports use Universal File Controls: VIEW, PREVIEW, DOWNLOAD and PRINT.


## Project 1 v20 — Poly / Polybag Department
Page #85 implements the Poly / Polybag Department. Workflow: approved Master Order → buyer packing specification → Poly Plan → POLY STOCK OUT SCAN → garment/bundle IN SCAN → poly packing → barcode/sticker/warning verification → Poly QC → rework/re-QC where required → QC PASS → garment/bundle OUT SCAN → carton/packing.

The module supports individual, printed, plain, recycled, recyclable, biodegradable, compostable, self-seal, zip and custom poly types. It records Poly Code, dimensions, thickness/micron, material, supplier/batch/lot, warning text, barcode requirement and planned quantity.

Packing tracks required/issued/used/damaged/rejected/returned quantities, process minutes, NPT, poly cost per piece, sticker/barcode cost, labour cost, process cost, wastage cost and total Poly cost. Wrong poly/order/style/size/colour, dimension/thickness failure, print/barcode/warning/seal problems, dirty/damaged packaging and quantity mismatches can HOLD or BLOCK release and create Red Alerts.

Automatic reports follow the Project 1 Bangladesh-local 08:00, 13:00 and 20:00 schedule. Packing specifications, artwork, QC photos and generated reports use Universal File Controls: VIEW, PREVIEW, DOWNLOAD and PRINT.


## Project 1 v21 — Iron / Industrial Ironing Department
Page #86 implements Industrial Ironing separately from the Hand Iron module. Workflow: approved Master Order → Industrial Iron Plan → BUNDLE IN SCAN → operator/helper/machine assignment → maintenance/downtime check → temperature and steam-pressure verification → industrial ironing → QC → re-iron/rework/re-QC → QC PASS → BUNDLE OUT SCAN → Poly/Packing.

Machine health is enforced before production. Machines with UNDER_MAINTENANCE, BREAKDOWN, HOLD, RETIRED or DISPOSED status, due/in-progress maintenance, or active downtime cannot be used. Over-limit temperature or steam pressure blocks production and creates Red Alerts.

Production records target/actual, start/end, process minutes, NPT, downtime, temperature, steam pressure, electricity kWh, steam kg, cost/minute, labour/helper/machine/utility/process/downtime costs and total Industrial Ironing cost. Manual entries require approved senior authorization.

Industrial Iron QC covers scorch/burn, shine/glazing, steam/water marks, crease, wrinkle, colour change, shape distortion, measurement change and fabric damage. Failed work cannot leave until QC PASS. Automatic reports use 08:00, 13:00 and 20:00 Bangladesh-local slots. Instruction files, QC photos and generated reports use Universal File Controls: VIEW, PREVIEW, DOWNLOAD and PRINT.


## Project 1 v22 — Final QC / Final Inspection
Page #87 is the final shipment-release quality gate. Flow: finished/packed unit → FINAL QC IN SCAN → buyer/order/style/specification verification → measurement/appearance/workmanship/label/barcode/poly/packing/carton/quantity checks → AQL → PASS/HOLD/REWORK/REJECT → CAPA/rework/re-Final-QC → pre-shipment sign-off → FINAL QC OUT SCAN → Ready for Shipment.

The Master Order is changed to `READY_TO_SHIP` only when the Final QC Release record is valid. Direct Ready-to-Ship release is blocked unless the system decision is Ready-to-Ship. Conditional release requires an approved senior ApprovalRequest. Critical defects and specified final-check failures create Red Alerts and prevent release.

A management command `python manage.py run_final_qc_auto_report --slot 08:00|13:00|20:00` and `deploy/final_qc_reports.cron.example` are included for real server-side automatic report scheduling in Bangladesh local time.

Final QC specifications, approved samples, packing specifications, inspection sheets, photos and generated reports use Universal File Controls: VIEW, PREVIEW, DOWNLOAD and PRINT.


## Project 1 v23 — Finishing Department
Page #88: Production Complete → FINISHING IN SCAN → trimming/stain/cleaning/measurement/accessory/appearance/iron-label-folding controls → Finishing QC → rework/re-QC → PASS → FINISHING OUT SCAN → Final QC. Includes WIP, NPT/downtime, costing, senior-approved manual entry, Red Alerts, CSV/API and 08:00/13:00/20:00 Bangladesh-local automatic reports.


## Project 1 v24 — Packing Department
Page #89 implements Packing as the controlled stage after Final QC release. PACKING IN is blocked unless the order has a valid Final QC release. PACKING OUT is blocked until Packing QC PASS.

The module includes order/style/size/colour verification, folding/poly/label/barcode checks, assortment and size/colour ratios, carton allocation, unique carton barcode/QR, carton weight/dimensions/CBM, carton marking/seal, quantity reconciliation, WIP and packing costs.

Automatic Packing reports run at 08:00, 13:00 and 20:00 Bangladesh local time via `run_packing_auto_report` and the included cron example. Packing specifications, QC photos and generated reports use Universal File Controls: VIEW, PREVIEW, DOWNLOAD and PRINT.


## Project 1 v25 — Shipping Department
Page #90 implements Shipping after Packing QC. Shipment creation requires Packing QC PASS. Carton flow is SHIPPING IN → CARTON VERIFY → LOADING → GATE OUT → DELIVERY, with duplicate/barcode/quantity/seal/approval checks. Loading requires verified Commercial Invoice, Packing List and Shipping Instruction. Gate Out requires approved shipment authorization.

The existing BuyerDeliverySLA is integrated with the maximum 15-day buyer-address delivery control. Dispatch updates MasterOrder to SHIPPED and SLA to DISPATCHED; POD confirmation updates MasterOrder to DELIVERED and SLA to DELIVERY_CONFIRMED.

Shipping includes carrier/forwarder/booking/AWB-BL-CMR tracking, container/seal/vehicle/driver, carton/piece counts, gross/net weight, CBM, ETD/ETA, shipping documents, detailed shipping costing, Proof of Delivery, automatic 08:00/13:00/20:00 Bangladesh-local reports, CSV/API and Universal File Controls.


## Project 1 v26 — Supplier Master & Management
Page #91 adds Supplier registration/KYC/approval, RFQ/quotation/sample-quality approval, profit-before-spend controlled PO, mandatory STOCK IN barcode + GRN/inspection, invoice duplicate control, secure bank-change approval, performance/risk scoring, Red Alerts, files, CSV/API and 08:00/13:00/20:00 reports.


## Project 1 v27 — Purchase / Procurement Department
Page #92 adds a controlled procurement workflow: requirement → mandatory stock check → shortage → RFQ/quotation comparison → approved supplier selection → Profit-Before-Spend approval → budget commitment → PO → supplier delivery → STOCK IN / GRN status → invoice/payment handoff.

The module records available, reserved and shortage quantities before purchase. Supplier comparison uses landed cost plus supplier quality and delivery performance. PO creation is blocked without an approved supplier selection and Profit-Before-Spend approval. Budget overrun creates a Red Alert.

Automatic procurement reports run at 08:00, 13:00 and 20:00 Bangladesh local time. CSV/API are included, while PO/quotation/GRN/invoice files continue through the Supplier/Universal File controls.


## Project 1 v28 — Purchases Master
Page #93 adds operational purchase execution after Procurement approval: approved PO gate, supplier acknowledgement, delivery/GRN synchronization, accepted/rejected/short quantities, PO+GRN+Invoice 3-way match, variance blocking/approval, amendments, returns/debit notes, payment/close gate, auto reports, CSV/API and file controls.


## Project 1 v29 — Sourcing Master / Sourcing Department
Page #94 adds controlled sourcing before Procurement: order/material requirement → mandatory stock check → shortage → approved/new supplier search → RFQ/quotation → landed cost → sample/quality/compliance/lab evaluation → supplier scoring → price-history/variance control → nomination approval → Procurement handoff.

The module supports Fabric, Yarn, Accessories, Labels, Poly/Packaging, Trims, Printing, Embroidery, Machinery/Equipment and Service/Outsourcing sourcing. New suppliers can be researched as candidates, but final nomination/handoff is blocked until the supplier is approved in Supplier Master.

Automatic sourcing reports run at 08:00, 13:00 and 20:00 Bangladesh local time. Specification, quotation, sample/test and generated report files use Universal File Controls: VIEW, PREVIEW, DOWNLOAD and PRINT.


## Project 1 v30 — Report Master Rebuilt
The existing Page #52 Report Master has been completely rebuilt as the central Project 1 reporting hub. It is not duplicated; Page #52 remains the canonical Report Master and is now promoted prominently in the top navigation.

The rebuilt dashboard provides department drill-down, consolidated KPI reporting, Red Alerts, pending actions and approvals, order reporting, document and communication reporting, department automatic report aggregation, Report Master snapshot history, CSV/API export, and 08:00/13:00/20:00 Bangladesh-local central snapshot scheduling.

Newer Project 1 modules are included in the Report Master catalog: Supplier, Sourcing, Procurement, Purchases, Finishing, Final QC, Packing and Shipping, together with the production and core operational dashboards.


## Project 1 v31 — CEO Executive Command & Report Center Rebuilt
Existing Page #56 has been rebuilt, not duplicated. The new CEO dashboard consolidates Order, Production, Finance, Stock, Supplier, Sourcing, Procurement, Purchases, Workforce, Packing and Shipping metrics; Red Alerts; Action Required; Pending Approvals; Supplier Risk; and Report Master drill-down.

CEO automatic executive reports run at 08:00, 13:00 and 20:00 Bangladesh local time. CSV/API export and a prominent CEO REPORT navigation item are included.


## v32 Account Master
All agreed Account Master requirements consolidated in ACCOUNT_MASTER_SPECIFICATION.md; Account Master promoted in navigation.
