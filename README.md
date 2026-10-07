# CCD Portal

`ccd_portal` is a Frappe v15 app that provides a governed, standalone staff
portal at `/ccd-portal`. It uses the existing Frappe session but does not expose
ERPNext Desk, generic resource APIs, CCD source keys, fuzzy-match evidence,
printing, export, or direct writes to `CCD Master`.

V1 supports targeted exact search, policy-driven masking, reason-based temporary
PII reveal, correction requests, centre-scoped decisions, reconciliation after
source sync, coverage reporting, and immutable audit events. The feature flag is
disabled by default. Only `Administrator` or users explicitly placed in the
environment-owned preview list can use it while disabled.

V2 adds opt-in, policy-governed same-person grouping from the live db_connector
Unified Person registry, exact Unified Person Number/alias lookup, and encrypted
suspected wrong-link concerns. It has an independent global/pilot rollout gate
that defaults off; identity links never grant centre access, and an unavailable
or unsafe identity registry never removes an ordinary direct masked match.

See [the architecture decision](docs/adr/0001-portal-architecture.md),
[development guide](docs/runbooks/development.md), and
[deployment runbook](docs/runbooks/deployment.md). Production release is also
subject to a private security-readiness gate; no ordinary staff account may be
onboarded before it passes.

## Quick checks

```bash
python -m compileall -q ccd_portal tests
python -m unittest discover -s tests -v
cd frontend && npm ci && npm run build
```

No real centres, aliases, grants, user identities, site URLs, secrets, exports,
screenshots, or CCD records belong in this repository.
