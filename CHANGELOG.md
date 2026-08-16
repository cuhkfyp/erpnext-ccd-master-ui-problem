# Changelog

## 0.1.12 - 2026-08-16

- Added a guided authoritative centre-column mapping to the portal Sources form
  for already-submitted registrations, covering per-record single- and
  multi-centre assignments without reopening the Desk registration.
- Added a fail-closed synchronization gate: changing the mapping clears derived
  centre keys and disables source access until a full agent sync has populated
  every record and **Complete sync and refresh** succeeds.
- Added aggregate mapping readiness to the Sources table and strict validation
  for manually entered source column identifiers.

## 0.1.11 - 2026-08-15

- Replaced the guest bootstrap failure with a clean sign-in card and prevented
  unauthenticated browsers from calling the session-only bootstrap method.
- Filtered raw HTML and internal Frappe method details from frontend error
  messages.

## 0.1.10 - 2026-08-15

- Added a read-only **View details** action and clickable version for every
  Draft, Active, and Retired policy in the portal Policies tab.
- Rendered policy field rules as a readable governance matrix while preserving
  immutability for Active and Retired versions.

## 0.1.9 - 2026-08-15

- Added a governed Contact Information tab to masked record detail, grouped into
  residential address, postal address, phone/email, and contact-person fields.
- Expanded the initial draft policy template to include every current CCD Master
  Contact Information field with masking and reason-based reveal controls.

## 0.1.8 - 2026-08-15

- Combined source-assignment save and reasoned index refresh into one atomic
  Administrator action, rolling back the assignment if the rebuild is not
  complete.
- Retained a separate **Refresh only** action for rebuilding an unchanged source
  after synchronization.

## 0.1.7 - 2026-08-15

- Fixed a post-deployment blank screen caused by private staging-directory
  permissions being propagated to the nginx portal asset directory.
- Normalized only CCD Portal asset directories/files to `0755`/`0644` during
  normal deployments and container-recovery deployments.

## 0.1.6 - 2026-08-15

- Added an audited Sources workflow for fixed single/multiple-centre assignments
  that works without modifying submitted CCD Registrations.
- Preserved per-record Exact, Delimited, and bounded Regex mapping for mixed
  sources, with fail-closed validation and independent centre relations.
- Source-assignment changes now invalidate old centre relations immediately and
  remain inaccessible until an audited index refresh succeeds.
- Added a reasoned, source-specific index refresh action for System Managers and
  expanded the mapping and two-centre UAT guidance.

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
