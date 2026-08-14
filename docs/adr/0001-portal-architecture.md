# ADR 0001: Standalone custom Frappe portal

Status: Accepted for V1

## Context

Staff need narrow, centre-governed access to selected `CCD Master` fields through
existing Frappe accounts and sessions. The interface must not expose Desk CRUD,
Studio controls, fuzzy-link evidence, browsing, export, printing, or direct
updates. Search, reveal, correction, administration, and override actions need
purpose-built authorization and immutable audit events.

## Options considered

| Option | Advantages | Reasons not selected |
|---|---|---|
| Studio page | Fast visual iteration | Runtime governance is difficult to review as code; generic controls and mutable Studio records increase exposure. |
| Desk Page | Native session and navigation | Keeps staff inside Desk and makes generic DocType/resource surfaces easier to reach. |
| Separate SPA/service | Strong deployment isolation | Duplicates authentication/session integration and adds another service/security boundary. |
| Custom Frappe portal with Vue 3 and Frappe UI | Existing sessions and CSRF, code-reviewed narrow APIs, standalone route, deployable as one v15 app | Requires app schemas, asset build, migrations, and explicit operations discipline. |

## Decision

Use a standard Frappe v15 app with a Vue 3/Frappe UI application served at
`/ccd-portal`. All client-data operations use named whitelisted methods that
construct allowlisted response objects. Every call reauthorizes the active user
profile, authority, explicit centre grants, and record-centre intersection.

The app maintains a site-secret HMAC token index and opaque record identifiers.
The secret is supplied through site configuration and never committed. Centre
scope comes only from active record-centre relations built from the hidden
canonical centre key. Fuzzy/person links are never read by portal authorization.

Studio remains a disposable visual reference during development. Its records
are retained unchanged until production acceptance, then unpublished (not
deleted) after cutover.

## Consequences

- Access Administrators configure governance records through the portal but
  cannot search client data.
- System Managers keep ERPNext break-glass CRUD and use a distinct reasoned,
  audited portal override when exercising portal powers.
- Source sync must populate `ccd_portal_centre_key` and notify or be followed by
  an index refresh. Incomplete mapping fails closed.
- Frontend code never writes revealed values to Web Storage/IndexedDB and clears
  them on timeout, tab hiding, or component teardown.
- The private security-readiness gate remains a release blocker for ordinary
  UAT/production users.
