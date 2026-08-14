# Centre/source mapping and index coverage runbook

## Preconditions

1. Configure a unique random `ccd_portal_hmac_secret` of at least 32 characters
   in the site's private configuration. Never reuse an API key or commit it.
2. Create canonical `CCD Portal Centre` rows. Optional ERP Departments are
   descriptive links and never grant access.
3. Create one active `CCD Portal Source Profile` for each `ccd_reg_source`.
4. Add active aliases for every source centre code. An alias can be constrained
   to one source profile. Ambiguous aliases fail closed.
5. In each existing CCD Registration field mapping, map the authoritative centre
   value into hidden `CCD Master.ccd_portal_centre_key`. Do not derive it from
   fuzzy matches, names, user grants, ERP Departments, or network location.

Parser choices are exact, delimited, or bounded regular expression. For a
legitimately shared record, the canonical value may yield more than one centre;
the indexer creates an independent relation for each. This never relates the
centres themselves.

## Build and refresh

Run a full refresh after the secret, active policy, profiles, centres, and aliases
exist:

```bash
bench --site <site> execute ccd_portal.admin.refresh_index \
  --kwargs '{"reason":"Initial governed index build"}'
```

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
