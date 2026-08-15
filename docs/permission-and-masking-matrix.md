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

Source profiles may use audited fixed centres only when every record from that
registration belongs to all selected centres. Mixed sources require per-record
authoritative centre keys. Changing a source assignment does not itself rebuild
the index; a System Manager performs a separately reasoned source refresh.

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
| Residential/postal address fields | No | Full | Operator, Steward | If source workflow permits |
| Contact-person names and phones | No | Initials / last 4 | Operator, Steward | If source workflow permits |
| Source keys, matching evidence, internal history | Never | Not returned | Never | Never |

Every result and detail field is masked according to the active versioned
policy. `mask_strategy` never has a clear-text option. Policy activation retires
the previous version; active and retired documents are immutable. The record
detail groups Contact-classified rules into residential address, postal
address, phone/email, and contact-person sections.

Access Administrators can inspect every Draft, Active, or Retired version in the
portal **Policies** tab by clicking its version or **View details**. The
read-only table shows every governed field rule and its order, classification,
mask, search/strong-ID flags, reveal authorities, and correction flag. Only a
Draft exposes the separate edit action; Active and Retired versions remain
immutable.

Policy inclusion controls whether a field may be returned; it does not create
or copy a value. A contact field remains empty until the authoritative source
query exposes its column, the submitted registration has an approved mapping to
the corresponding CCD Master field, and synchronization runs. Never infer a
missing contact value from a different record or fuzzy/person link.
