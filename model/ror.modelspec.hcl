# Source-faithful physical projection of ROR schema 2 JSON.
# One logical ROR organizations recordset; locations/relationships retain array grain.
# Full ROR URL id is the source identity. Runtime-generated data.id is separate.
# raw_json is lossless JSON shape with canonical serialization, including names,
# external_ids, links, types, locations, relationships and administrative metadata.
# Model licence: CC0-1.0.
entity "organizations" {
  key = ["id"]
  property "id" {
    type = "string"
    required = true
  }
  property "status" {
    type = "string"
    required = true
  }
  property "raw_json" {
    type = "string"
    required = true
  }
}

entity "locations" {
  key = ["organization_id", "ordinal"]
  property "organization_id" {
    entity = "organizations"
    required = true
  }
  property "ordinal" {
    type = "int"
    required = true
  }
  property "geonames_id" {
    type = "int"
  }
  property "geonames_details_json" {
    type = "string"
    required = true
  }
}

entity "relationships" {
  key = ["organization_id", "ordinal"]
  property "organization_id" {
    entity = "organizations"
    required = true
  }
  property "ordinal" {
    type = "int"
    required = true
  }
  property "type" {
    type = "string"
    required = true
  }
  property "id" {
    entity = "organizations"
    required = true
  }
  property "label" {
    type = "string"
    required = true
  }
}
