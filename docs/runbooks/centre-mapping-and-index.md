# Centre/source mapping and index coverage runbook

## Preconditions

1. Configure a unique random `ccd_portal_hmac_secret` of at least 32 characters
   in the site's private configuration. Never reuse an API key or commit it.
2. Create canonical `CCD Portal Centre` rows. Optional ERP Departments are
   descriptive links and never grant access.
3. Create one active `CCD Portal Source Profile` for each `ccd_reg_source` and
   select exactly one centre-assignment mode:
   - **Fixed Centres** applies every selected centre to every record from the
     registration. Select one for a single-centre source, or several only when
     every record is legitimately shared by all of them. This mode works with
     submitted registrations and does not require a CCD Field Match change.
   - **Per-record Centre Key** is required when records within one registration
     belong to different centres. Map the authoritative source centre column to
     `CCD Portal Canonical Centre Key` (`ccd_portal_centre_key`) in the existing
     registration mapping. The portal migration exposes this target without
     changing saved mappings.
4. For per-record mode, add active aliases when a source value differs from its
   canonical centre code. An alias can be constrained to one source profile;
   ambiguous aliases fail closed.
5. Never derive centre access from fuzzy matches, registration names, client
   names, user grants, ERP Departments, or network location.

If the target is absent after deployment, run the site migration and clear the
browser/metadata cache. Do not type a registration name into a centre grant:
`CCD Registration` identifies a source, while `CCD Portal Centre` defines an
independent access boundary.

In fixed mode the selected centres are stored as governed child rows on the
source profile. In per-record mode, parser choices are exact, delimited, or
bounded regular expression. For a legitimately shared record, the canonical
value may yield more than one centre; the indexer creates an independent
relation for each. This never relates the centres themselves.

Use **Exact** when the synchronized value is already one complete centre code,
such as `12345`. Exact requires neither Delimiter nor Pattern. Use **Delimited**
only when a record legitimately carries multiple centre codes in one value.
Use **Regular Expression** only when a bounded extraction pattern is required;
Pattern is mandatory for that parser. The administration form hides irrelevant
parser fields and presents registrations, centres, profiles, and users as
governed selections rather than free-text identifiers.

## Build and refresh

Run a full refresh after the secret, active policy, profiles, centres, and aliases
exist:

```bash
bench --site <site> execute ccd_portal.admin.refresh_index \
  --kwargs '{"reason":"Initial governed index build"}'
```

A System Manager who is also the portal Access Administrator can instead enter
a reason and choose **Refresh index** for one saved source in the portal Sources
tab. Source-specific refresh is preferred while configuring a cohort because it
does not process unrelated registrations.

Saving a source assignment immediately deactivates the existing centre
relations for that source. This deliberately makes its records inaccessible
until the audited refresh succeeds, so an old assignment cannot remain usable
during a configuration change.

The post-sync integration must call the non-whitelisted server-side method
`ccd_portal.sync.after_agent_sync(source, source_keys, deleted_source_keys)`
after a delta raw-SQL source sync. After a full sync it calls the same method
with `full_sync=True`. This refreshes tokens and centre relations and deactivates
deleted identities without creating a browser-accessible endpoint.

The existing development agent endpoint remains unchanged for now, as directed.
Therefore this immediate completion call is a release gate, not an enabled
development behavior. Document-event hooks cover normal Frappe changes, while
the hourly reconciler is a fail-safe for recently modified rows and raw-SQL
deletes. Do not enable staff until the private agent remediation wires and tests
the completion call for full and delta sync.

Changing an active search policy requires a full refresh. HMAC tokens are tagged
with their policy version, so stale-version tokens do not match.

## Coverage gate

Open the Access Administrator coverage view. It reports aggregate totals and
source-level mapped counts, never record identities. For the selected UAT cohort:

- coverage must be 100%;
- no source profile or alias may be missing/inactive;
- unmapped records must remain invisible to Reader/Operator/Steward users;
- a two-centre shared fixture must create two relations;
- a fuzzy-only cross-centre link must create no relation.

Do not enable ordinary staff while coverage is below 100%. Investigate at the
source/profile/alias level using System Manager tools; do not export client rows.

## Secret rotation

Disable the feature flag, replace the site secret, clear all portal tokens and
record identifiers through an approved maintenance patch, rebuild the complete
index, retest the two-centre isolation matrix, and only then re-enable the pilot.
Existing bookmarked opaque IDs cease to work by design.
