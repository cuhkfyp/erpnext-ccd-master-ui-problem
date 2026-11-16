# Monitoring and incident procedure

## Operational measures

Review at least daily during pilot and after every deployment:

- searches, zero-result searches, denials, and search-rate-limit events;
- reveals, reveal reasons, reveal denials, and reveal-rate-limit events;
- active centre/index coverage by source (target: 100% pilot cohort);
- correction states, especially Stale and Needs Review;
- failed audit inserts and application error logs;
- search p50/p95 (acceptance p95 at most 2 seconds) and detail p95 (at most 1 second);
- response count invariant (at most 20) and HTTP 429 volume.
- same-person schema/index readiness, assigned portal records, eligible
  multi-record people, revalidation suppressions, anomalies, and concern states;

Audit retention is indefinite until HKSR approves a duration. No automatic log
clearing hook is configured for portal events.

## Search processing limits

A Search audit event with outcome `Error` and metadata stage
`database_processing_limit` means the database exceeded both the 8-second
attempt and its single 4-second retry. This is separate from an hourly quota
denial and the browser's 15-second request deadline. The API publishes a safe
message through Frappe's message log; the browser also recognizes
`SearchProcessingLimitError` if only the exception type is returned.

For query investigations, inspect `EXPLAIN` with the active policy and centre
filters. Token aliases should use `ccd_portal_token_search_cover` with
`Using index`; extra exact criteria are intersected before record joins. Compare
uncached and repeated timings: fast repeats alone do not establish a reliable
p95. Do not flush the shared database cache to benchmark the portal.

The covering index is installed by migration and `ensure_external_schema` using
an online, in-place build with concurrent writes allowed and a five-second
metadata-lock wait. A failed build must be retried in a suitable window before
deploying code that forces this index. Existing source and record indexes stay
in place. This schema change does not require refreshing source records.

## Alert conditions

Disable the portal and investigate immediately if any raw PII/source key appears
in a masked response or log, an unmapped/cross-centre record becomes visible,
audit insertion fails during a protected action, coverage drops for the pilot,
the HMAC secret may be exposed, or ordinary staff can reach generic CCD Master.

## Incident sequence

1. Disable the global same-person flag for an identity-specific incident, or the
   portal feature flag for a wider incident. If necessary invalidate affected sessions.
2. Preserve database, audit, reverse-proxy, and application evidence with access
   restricted to the incident team. Never paste PII into tickets/chat.
3. Determine affected users, centres, opaque record IDs, event types, and time
   window using protected digests—not exported raw criteria.
4. Contain grants/profiles/aliases or deploy a reviewed fix. Rotate the HMAC
   secret and rebuild the index if compromise is possible.
5. Notify the designated HKSR privacy/security owners under the approved incident
   process. Legal notification decisions are theirs.
6. Test guest, wrong-centre, guessed-ID, masked-response, and audit-failure paths
   before controlled recovery.
7. Document cause, impact, remediation, and prevention without client PII.
