---
ovdb: 1
publish: []
---
# ROR provider publication prerequisites

`templates/ovdb.yaml` prepares source/model/licence/publisher metadata. It lacks an
approved canonical database URL, deployment URL and discovery endpoint and is
therefore not a valid publishable Directory manifest yet. The empty publish
list prevents accidental ingestion. The designated integration owner supplies
verified immutable hosting/runtime/Directory receipts before opting it in.

The native `organizations.id` and `relationships.id` columns stay intact. The
current runtime reserves `id`; its owner must land a faithful source-field
adapter before mounting this provider. The generated SQLite is larger than
the runtime's 25 MiB per-file guard. Publication must use its existing reviewed
ordered chunk mechanism, with separate serving checksums; no guard increase
or bulk Git/browser asset is implied.

The three physical tables expose one logical ROR organizations recordset.
No ROR user representation contract is published here: a fresh dedicated
reconciler must accept the exact user source/schema/property/namespace first.
Source identity and GeoNames reference bindings do not authorize user joins.
