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

Audit retention is indefinite until HKSR approves a duration. No automatic log
clearing hook is configured for portal events.

## Alert conditions

Disable the portal and investigate immediately if any raw PII/source key appears
in a masked response or log, an unmapped/cross-centre record becomes visible,
audit insertion fails during a protected action, coverage drops for the pilot,
the HMAC secret may be exposed, or ordinary staff can reach generic CCD Master.

## Incident sequence

1. Disable the feature flag. If necessary invalidate affected Frappe sessions.
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
