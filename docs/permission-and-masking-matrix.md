# Permission and masking matrix

## Authority

| Action | Reader | Operator | Data Steward | Access Administrator | System Manager |
|---|---:|---:|---:|---:|---:|
| Targeted exact search | Yes, granted centres | Yes, granted centres | Yes, granted centres | No | Audited break glass only |
| Masked detail | Yes | Yes | Yes | No | Audited break glass only |
| Reason-based reveal | No | Policy fields | Policy fields | No | Audited break glass only |
| Submit correction | No | Yes | Yes | No | No ordinary submission |
| Approve/reject correction | No | No | Granted centres; never own request | No | Explicit audited override |
| Manage centres, aliases, source profiles, profiles, grants, reveal reasons | No | No | No | Yes | Existing administrative power |
| Draft field policy | No | No | No | Yes | Existing administrative power |
| Activate field policy/index refresh | No | No | No | No | Yes, with reason |
| Coverage report | No | No | No | Aggregate only | Existing administrative power |

Grade never grants a centre. Every ordinary client-data action requires an
active profile and an effective explicit user-centre grant. A record must have
at least one active/effective relation intersecting those grants.

## Default field policy guidance

This table is guidance for an environment-owned immutable policy; activation
requires HKSR approval and is not shipped as live configuration.

| Classification / example | Search | Default mask | Reveal | Correction |
|---|---|---|---|---|
| Membership/client number (`hksr_num`) | Exact strong ID | Last 4 | Operator, Steward | If source workflow permits |
| Validated HKID (`hkid`) | Exact strong ID only after check digit validation | Last 4 | Operator, Steward | If source workflow permits |
| Birth certificate number (`bc_num`) | Exact strong ID | Last 4 | Operator, Steward | If source workflow permits |
| Mapped staff ID | Exact strong ID | Last 4 | Operator, Steward | If source workflow permits |
| Phone (`phone_num`, `mobile`, `res_phone`) | Exact normalized digits | Last 4 | Operator, Steward | If source workflow permits |
| Email (`email`) | Exact case-folded address | First character and domain | Operator, Steward | If source workflow permits |
| English/Chinese name fields | Only with date of birth | Initials / first character | Operator, Steward | If source workflow permits |
| Date of birth (`birthday`) | Only with a name unless approved as a strong field | Year only | Operator, Steward | If source workflow permits |
| Address/contact-person fields | No in V1 unless separately approved | Full | Operator, Steward if approved | If source workflow permits |
| Source keys, matching evidence, internal history | Never | Not returned | Never | Never |

Every result and detail field is masked according to the active versioned
policy. `mask_strategy` never has a clear-text option. Policy activation retires
the previous version; active and retired documents are immutable.
