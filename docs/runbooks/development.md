# Development runbook

## Supported baseline

- Frappe/ERPNext v15 (validated against the current v15 deployment line)
- Python 3.10+
- Node 20 for asset builds
- Current Chrome and Edge desktop; responsive tablet width

Install the app into a dedicated development site, set a development-only HMAC
secret, run migrations, and build assets. Keep `CCD Portal Settings.enabled = 0`.
Create only named administrator preview rows. Do not add ordinary staff.

```bash
bench --site <dev-site> install-app ccd_portal
bench --site <dev-site> migrate
bench build --app ccd_portal
python scripts/scan_repository.py
python -m unittest discover -s tests -v
```

Use only fictional `SYNTHETIC-*` records in automated tests. Named administrators
may exercise the masked portal against records already present in development;
never copy those records, API responses, screenshots, logs, or exports into Git.

Before a pull request, run Python compilation/tests, the frontend production
build, the offline repository scan, and `git diff --check`. CI also runs gitleaks.
Use translation-ready UI strings; English is V1 and Traditional Chinese is
deferred.

The Studio prototype is a reference only. Do not alter/unpublish it during
development. After accepted production cutover, unpublish and retain it.
