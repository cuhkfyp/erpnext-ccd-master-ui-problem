# Two-centre UAT acceptance script

Use fictional or approved in-place UAT records only. Do not export results.

## Actors and records

Create Reader, Operator, and Data Steward accounts separately for Centre A and
Centre B; one explicitly multi-centre Operator; one Access Administrator; and one
System Manager. Every profile has exactly one authority. Create records that are
A-only, B-only, legitimately A+B, unmapped, deleted/reinserted, correction-stale,
and cross-centre fuzzy-linked without a centre relation.

## Matrix

For each row, retain only pass/fail evidence and opaque IDs.

| Test | Expected result |
|---|---|
| Guest calls bootstrap/search/detail | Authentication failure; no record existence leak |
| Inactive profile | Generic denial |
| Profile with no/expired grant | Generic denial |
| Reader A exact-searches A record | At most 20 masked results; no raw PII/source keys |
| Reader A exact-searches B record | Same response shape as no match |
| Reader A guesses B opaque ID | Generic denial and immutable denial event |
| Reader attempts reveal/correction | Denied |
| Operator A reveals A with valid reason | Only policy fields; temporary display clears; audit event |
| Operator uses missing/invalid reveal reason | Denied; no values |
| 21st reveal / 61st search in an hour | HTTP 429 and audit event |
| Operator submits correctable change | Encrypted proposal; CCD Master unchanged |
| Operator tries approval | Denied |
| Steward A reviews another A request | Reasoned temporary comparison available |
| Steward tries own request | Denied and audited |
| Steward A tries B request | Generic denial |
| Access Administrator opens portal | Administration/coverage only; no client search |
| Multi-centre user searches A and B | Sees only explicit A/B intersection |
| Any user searches fuzzy-linked cross-centre record | Fuzzy link provides no access |
| Shared A+B record | Accessible independently through valid A or B grant |
| Unmapped/inactive-key record | Invisible; aggregate coverage reports gap |
| SQL/XSS payload in criteria/context | Parameterized/escaped; no execution or stored script |
| Cross-site unsafe request without CSRF | Rejected by Frappe |
| Deliberate audit insertion failure | Protected action fails without returning data |
| System Manager override without reason | Rejected |
| System Manager override with reason | Distinct Break Glass Override audit event |

## Sync and correction reconciliation

1. Run a full synthetic source sync. Confirm tokens/relations are generated and
   stale source records are inactive.
2. Run delta insert/update/delete with changed keys. Confirm search reflects the
   new state and stale token values no longer match.
3. Delete and reinsert the same source identity. Confirm its opaque ID is stable
   and no stale relation leaks.
4. Approve a correction, manually update the fictional authoritative source, and
   sync. Confirm Applied when values match.
5. Modify source data between submission and approval; confirm Stale.
6. Sync a different value after approval; confirm Needs Review.

## Performance and browser acceptance

At current data scale, execute representative approved searches and details with
warm and cold samples. Search p95 must be at most 2 seconds, detail p95 at most 1
second, and result count never above 20. Coverage must be 100% for the cohort.
Repeat core flows on current Chrome and Edge desktop and a responsive tablet
viewport. Confirm no PII exists in localStorage, sessionStorage, IndexedDB,
browser history, URL query strings, or downloaded files.

After the private rollout gate, also prove ordinary staff cannot access `CCD
Master` through Desk or generic resource APIs.
