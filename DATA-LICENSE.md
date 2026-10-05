# Source data rights and attribution

The Research Organization Registry's [ROR data release v2.13, dated
2026-09-22](https://zenodo.org/records/22902037), states that ROR metadata is
provided under the [CC0 Public Domain Dedication](https://creativecommons.org/publicdomain/zero/1.0/).
Embedded location data comes from [GeoNames](https://www.geonames.org/) and
retains [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) attribution.
Copyright GeoNames. Neither ROR nor GeoNames endorses this provider.

Changes: JSON whitespace/object-key order are canonicalized; all source fields,
arrays, native keys and statuses remain available in `organizations.raw_json`.
Locations and relationships are also projected into SQLite rows, retaining
their original zero-based array order. Sorted distinct GeoNames IDs are a
closure input, not a replacement for source location multiplicity.

The private generated snapshot includes the source rights in `snapshot.json`.
Serving/download publication must carry this attribution file with the data.
Code, schema and meaning licences are separate from those source rights.
