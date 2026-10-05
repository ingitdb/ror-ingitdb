# ror-ingitdb

Reproducible ROR source-provider tooling in the inGitDB organization, preparing
one logical organizations recordset for OpenVaultDB. Publication is pending the
runtime/native-id, immutable hosting and Directory prerequisites in [OVDB.md](OVDB.md).

The selected input is the complete ROR schema 2 JSON release **v2.13 dated
2026-09-22**: 141,528 organizations, all statuses retained. The JSON is the
format of record; the convenience CSV omits fields and is not imported.
[source/ror-v2.13.json](source/ror-v2.13.json) pins its archive and member hashes,
release/fetch dates and rights. No source bulk data is committed.

Build from that separately captured archive, entirely offline:

```sh
python3 scripts/import_ror.py --archive /private/path/v2.13-2026-09-22-ror-data.zip \
  --pin source/ror-v2.13.json --out /private/path/new-reviewed-snapshot
python3 -m unittest discover -s tests -v
```

The importer streams one JSON record at a time into disk-backed SQLite and
refuses source hash/count/key/resource failures, recursive duplicate JSON keys,
non-finite/overflow numbers and projected field type errors. Native ROR URLs
must match the [official identifier pattern](https://ror.readme.io/docs/identifier)
and checksum; no SQLite scalar coercion supplies missing type validity. It stages a fresh output and
publishes it by rename only after validation; failures preserve the prior
snapshot. Existing output directories are refused. Refresh is a monthly
reviewed snapshot; a failed refresh leaves the explicitly stale last-known-good
snapshot usable. There is no automatic fetch or query-triggered rebuild.

Outputs: `ror.sqlite`, complete numeric sorted LF `ror-geonames-ids.txt`,
deterministic `snapshot.json`, and separate measured `resources.json`.
SQLite bytes are reproducible with the recorded Python/SQLite toolchain;
resource timing is measured separately and is not deterministic. Ceilings are
256 MiB provider SQLite, 512 MiB peak RSS, 2 MiB metadata and 30 minutes.
The shared W1 download/temporary-disk budgets are checked across providers by
the coordinator; local measurements include this archive plus output bundle.

| Physical table | Native identity / grain | Preservation |
|---|---|---|
| `organizations` | full ROR URL `id` | status + complete original JSON shape in `raw_json` |
| `locations` | organization URL + original ordinal | native `geonames_id`, source GeoNames detail JSON |
| `relationships` | organization URL + original ordinal | native target `id`, type and label |

These are physical projections of the same one logical source recordset.
Native identifiers remain distinct from runtime serving identity. Missing
relationship/place targets stay visible exceptions; organizations are not
filtered by status or location count. Names never become identity. Real user
ROR eligibility requires dedicated acceptance of the exact user schema and
representation; no existing synthetic demo has that authorization.

The provider-local ModelSpec and MeaningGraph files bind only accepted public
source roles, referencing the pinned core organization concept. Validate with
the pinned tool revisions recorded in engineering evidence; full builds and
their checksums are retained as compact metadata, not raw source assets.

Original code/model/meaning metadata: CC0-1.0. ROR metadata: CC0-1.0. Embedded
GeoNames data: CC-BY-4.0, with [source attribution and transformation details](DATA-LICENSE.md).

## Native artifact companion

`scripts/package_artifacts.py` packages the reviewed source SQLite without
regenerating data or changing its schema. It checks the native
`organizations.id` constraint, every canonical URL/checksum, unique/count
closure and each native key/status against the full raw source record.
The bounded `native_key` extension in `source/validation.json` records those
checks and model/binding/data hashes. Its original embedded generation
snapshot stays unchanged, including historical measurements and gate state.

`source/artifact-snapshot.json` is separate packaging metadata. It pins an
exact committed packaging tool revision and binds the unchanged logical
`ror.sqlite` decoded artifact descriptor (`kind: reconstructed`), source models/binding, extended provenance, attribution
and ordered compressed chunks. Concatenating those chunks reconstructs one
gzip stream; decoding it yields the exact reviewed native SQLite. Chunks are
compressed stream segments, not individually decodable gzip files. Every
physical file remains within the existing 25 MiB guard. Encoded aggregate,
decoded SQLite and individual chunk sizes/checksums are all checked.

```sh
python3 scripts/package_artifacts.py build --database /private/path/ror.sqlite \
  --source-revision bbbec903248680caea04e68f94b9a957b6efc55b \
  --generator-revision 782df3e99a45a4fd8d3048cab28dbcb120cd0e29 \
  --out /private/path/new-artifact-bundle
python3 scripts/package_artifacts.py check
```

`source/artifact-packaging-resources.json` measures only this new packaging
operation, with zero new source downloads. Timing is separate from deterministic
metadata; it does not retroactively measure initial capture or generation.
For immutable source fetches, use the landed provider commit with
`https://raw.githubusercontent.com/ingitdb/ror-ingitdb/<commit>/<chunk path>`.
No release tag or unpinned main URL is an artifact authority.

Discovery consumers read only bounded snapshot/provenance/model metadata.
They do not download the SQLite, chunks or a global native key set to decide
eligibility; issued membership requires an explicit bounded live lookup.
Source native data and any future serving adapter output have distinct
descriptors and checksums. This artifact publication does not supply a deployed
API, Directory entry, production eligibility or a successful live query.
