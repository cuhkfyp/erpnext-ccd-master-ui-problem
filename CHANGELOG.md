# Changelog

## 0.1.5 - 2026-08-14

- Distinguished logged-out Guests from authenticated accounts denied by the
  disabled feature flag, preventing the misleading sign-in redirect loop.
- Offered denied authenticated users a return to Desk while preserving the
  normal Frappe login redirect for Guests.

## 0.1.4 - 2026-08-14

- Restored native checked-state rendering for administration checkboxes after
  the Tailwind base styles made active records appear inactive.

## 0.1.3 - 2026-08-14

- Added a verified graceful Gunicorn worker reload to no-restart deployments so
  new frontend assets cannot call stale preloaded Python API modules.
- Added an operational check requiring new methods to be tested through the
  running HTTP server rather than only through a fresh Python process.

## 0.1.2 - 2026-08-14

- Replaced error-prone administration references with labelled selections.
- Added parser-specific guidance and validation; Exact no longer retains stale
  delimiter or regular-expression values.
- Made Active checkboxes visible and accessible in the administration forms.

## 0.1.1 - 2026-08-14

- Added the governed centre-key target to the existing CCD Registration field
  mapping dropdown through an idempotent system-generated property setter.
- Preserved all existing registration mappings and kept the portal disabled.

## 0.1.0 - 2026-08-14

- Added the disabled-by-default standalone `/ccd-portal` Vue 3/Frappe UI app.
- Added explicit single-authority profiles, effective centre grants, canonical
  centres/aliases/source profiles, and fail-closed record-centre relations.
- Added immutable versioned masking/search/reveal/correction policy records.
- Added site-secret HMAC search tokens, opaque IDs, 20-result ceiling, and
  per-user search/reveal limits.
- Added masked detail, reasoned reveal, encrypted correction proposals,
  separation-of-duty decisions, source-sync reconciliation, and audited System
  Manager override.
- Added aggregate coverage administration, tests, secret/PII scanning CI,
  deployment/runbooks, and two-centre UAT acceptance material.
- Added host-persistent Docker recovery and conditional restart-script restore.
- Added offline runtime registration, atomic `apps.txt` normalization, and
  volume-local frontend asset staging for safe container recreation.
- Hardened the persistent SSHFS remount safeguard against duplicate mount-layer
  growth and documented host-namespace verification and reboot recovery.
- Added a non-whitelisted adapter and scheduled fail-safe for raw-SQL sync
  updates and deletes without changing the existing agent endpoint contract.
