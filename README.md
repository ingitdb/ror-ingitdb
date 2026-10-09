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

The checker requires an explicit original source commit and an authority
resolver backed by a local provider Git repository. It verifies regular bounded
Git blobs for the original receipt, model/binding/source/attribution metadata
and exact generator script, cross-links the preserved generation/proof, and
requires the complete distributable artifact set. CI supplies the original
source revision and fetches its immutable history. A copied bundle alone cannot
prove code/source authority; missing authority fails closed. These checks verify
provenance associations and do not issue semantic acceptance.

The model's two files (`model/ror.modelspec.json` and `.hcl`) are the one exception
to the byte-for-byte comparison with the source authority. They were accepted there
in ModelSpec's earlier vocabulary (`entity`, `property`); ModelSpec has since renamed
those words to `record` and `field`, and the files in this repository are written
in the current vocabulary. The packager and the checker accept the two files in
exactly two states: the authority's bytes, or the exact rename of those bytes, both
files in the same state. The rename is recomputed by `scripts/modelspec_reader.py`
from the authority's bytes and compared with the SHA-256 of what the reference tool
(`modelspec rewrite` 0.2.0) writes, so one file renamed without the other, a rename
with any other change, or a model that mixes the two vocabularies is refused. The
source authority itself did not move. The repository's owner approved this rule on
2026-10-09.

The package was rebuilt once for the renamed model, at packaging tool revision
`60e9905c7834d5f48d184a7dbb9a755d717d6ccc`, from the SQLite reconstructed from the
committed chunks. The chunks, the source pin, the licence and the meaning file are
byte for byte what they were. `source/artifact-snapshot.json` now pins the renamed
model files, and the `native_key` in `source/validation.json` names the renamed
model's SHA-256; the embedded original generation snapshot is unchanged.

The deployment wrappers written by `scripts/generate_deployment.py` name the provider
revision whose files they describe. That revision moved to
`c706831d1e73b9ac9913940e0c5bbdb485f68501`, the commit that holds the renamed model
and the rebuilt package, so the chunk links in `metadata/artifact.json` and the model
links in `ovdb-database.json` serve the files this repository describes. That
generator's own check is unchanged: every input must equal its Git blob at the
revision it names.

```sh
python3 scripts/package_artifacts.py build --database /private/path/ror.sqlite \
  --source-revision bbbec903248680caea04e68f94b9a957b6efc55b \
  --generator-revision 60e9905c7834d5f48d184a7dbb9a755d717d6ccc \
  --out /private/path/new-artifact-bundle
python3 scripts/package_artifacts.py check \
  --source-revision bbbec903248680caea04e68f94b9a957b6efc55b \
  --authority-repository /path/to/provider-git-repository
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

## Scoped representation metadata

[model/representations.json](model/representations.json) is bounded provider-local
execution metadata. [source/representation-attachment.json](source/representation-attachment.json)
contains the exact `path`/`sha256` envelope for future attachment through a reviewed
`ovdb.yaml`. Every own-provider reference is relative; the downstream Directory
record supplies the immutable provider commit.

The format 3 native identifier document names the exact accepted `affiliations.Affiliation.ror_id` schema and input file at `datatug/datatug-apps@e7362033ec79c6663d7dbe0483b62fab01f7b9cd`. `source.data` retains all four upstream coordinates, including raw-byte SHA256 `44a30dd260c74f43b2138934cca0bccd493c7b804e124c5ecbded3c41226e543`. The original decision stays at hub revision `17263dbacabdfe95e53fc3c6980177416bdab941`. Different input bytes or identity need separate reviewed acceptance. Native `organizations.id` is distinct from any future serving identity; no bridge or global key corpus is attached.

Reproduce the documents without downloads or native-data reconstruction:

```sh
python3 scripts/generate_representations.py
python3 scripts/generate_representations.py --check
```

The generator refuses changes to its reviewed local metadata inputs and compares
exact generated bytes with `--check`. It retains the existing artifact snapshot,
models, bindings, native data, source provenance, licences and attribution. This
is a local reproduction check: full structural verification uses the released
OVDB v0.27.0 `publisher/representation.Check` with regular committed-file readers
for all exact external pins. Format 3 source-data byte proof is a separate stage;
metadata resolution must never fetch source data, native SQLite or ordered chunks.

These files grant no semantic acceptance or production eligibility. Independent
review must tie the final provider commit and attachment path/hash to the original
decision and reviewed source/wrapper continuity before canonical admission.
Publisher/Directory companions and immutable dependencies, root manifest opt-in,
truthful deployment/licence metadata, canonical registry pins, faithful runtime
identity, capacity/CORS/read-only receipts, Directory publication and the real app
journey remain separate prerequisites. This metadata change supplies none of
those publication receipts.
