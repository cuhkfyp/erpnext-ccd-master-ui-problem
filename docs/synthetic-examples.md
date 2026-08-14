# Synthetic examples

All examples use reserved `SYNTHETIC-*` sources and `.invalid` email domains.
They are instructional and must not be loaded into production.

- `SYNTHETIC-CENTRE-A` / `SYNTHETIC-A-0001` maps only to `CENTRE-A`.
- `SYNTHETIC-CENTRE-B` / `SYNTHETIC-B-0001` maps only to `CENTRE-B`.
- `SYNTHETIC-SHARED` / `SYNTHETIC-S-0001` parses into both centres and creates
  two independent record-centre relations.
- `SYNTHETIC-UNMAPPED` has no recognized centre alias and is invisible to normal
  portal users.
- Two records may be fuzzy-linked across centres for a test, but the link never
  creates a relation or changes either user's search scope.

The complete fictional rows are in `tests/fixtures/synthetic_records.json`.
