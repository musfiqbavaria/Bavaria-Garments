# Emerald Rozalia — FINAL A-to-Z Build

This is the final consolidation package built from the latest Project 1 Python 3.14.3 server-ready baseline.

## Final additions
- 300 registered website/dashboard views.
- 650 Forms Master retained from the baseline.
- Step 370: Print Department Barcode Control.
- Print machine validation.
- Print operator validation.
- Print helper validation.
- Bundle process/route validation.
- Correct-way / wrong-way operator instruction.
- Blocked scan + Red Alert + audit/process event.
- Baseball Cap 19-process / 19-machine route.
- Bucket Hat 19-process / 19-machine route structure.
- Existing Project 1 finance, device, communication, production and barcode modules retained.

## Local start
1. Create/activate Python 3.14.3 virtual environment.
2. `pip install -r requirements.txt`
3. `python manage.py migrate`
4. `python manage.py seed_final_az`
5. `python manage.py runserver 0.0.0.0:8000`

## Step 370
Open:
`/step-370/print-department-barcode-control/`

The page contains the operator instruction and validation history. The underlying validation rule is:
**Bundle → current process → route sequence → correct machine → active machine → operator → helper.**
Any mismatch is blocked and logged.

## Production deployment
Set production `DEBUG=False`, configure PostgreSQL/secret credentials, run migrations and collectstatic, then run Gunicorn behind Nginx.
