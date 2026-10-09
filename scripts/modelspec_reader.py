"""Read a ModelSpec model's JSON form in either vocabulary.

A model's `modelspec` identifier decides its vocabulary: `1.0-draft` names the
record types `entities`, their members `properties` and a member's reference
`entity`; `1.0-draft-2` names them `records`, `fields` and `record`. Both are read.
A document whose keys disagree with its identifier, or that holds a removed or
reserved top-level word, is refused, as the reference CLI refuses it.

Only the model's own JSON form is read here. Representation descriptors and native
keys, which carry the `entity` and `property` fields of OpenVaultDB's frozen
representation contract, are not ModelSpec's keywords and do not pass through here.
"""

import hashlib
import json
import re

EARLIER = "1.0-draft"
CURRENT = "1.0-draft-2"
# The model's two files. They were accepted in the earlier vocabulary; the provenance
# checks accept them in exactly two states: the accepted bytes, or the exact rename of
# those bytes into the current vocabulary (`entity` to `record`, `property` to `field`).
MODEL_FILES = ("model/ror.modelspec.json", "model/ror.modelspec.hcl")
# SHA-256 of what the reference tool, `modelspec rewrite` 0.2.0, writes for the accepted
# bytes. The rename is recomputed here and compared with these, so a rename that
# differs from the reference tool's by one byte is refused.
RENAMED_SHA256 = {
    "model/ror.modelspec.json": "318cb63f7dfa7c69c46c457b0deae309f518ba9523e2c8d586951cd78688f907",
    "model/ror.modelspec.hcl": "0957bd47b4e1c210c6ac46cecb027649fb71676a96ba84daa7cc97b0310a8495",
}
# identifier -> (record types, members of a record type, reference on a member)
VOCABULARY = {EARLIER: ("entities", "properties", "entity"), CURRENT: ("records", "fields", "record")}
REMOVED = ("collections", "recordsets")
RESERVED = ("projections", "migrations")


def _objects(value):
    """The object values of an object, or nothing when `value` is not an object."""
    return [item for item in value.values() if isinstance(item, dict)] if isinstance(value, dict) else []


def _wrong_key(identifier, other, key, expected):
    return ValueError(f'"{key}" belongs to format {other}; this document says "{identifier}", '
                      f'where the key is "{expected}"')


def _vocabulary(model):
    """Check the identifier against the keys and return the document's vocabulary."""
    if not isinstance(model, dict):
        raise ValueError("a ModelSpec JSON document must be an object")
    identifier = model.get("modelspec")
    if not isinstance(identifier, str) or identifier not in VOCABULARY:
        raise ValueError(f'"modelspec" is {identifier!r}; the defined values are "{CURRENT}" and "{EARLIER}"')
    for word in REMOVED:
        if word in model:
            raise ValueError(f'"{word}" was removed (decision 0019); a model cannot use it')
    for word in RESERVED:
        if word in model:
            raise ValueError(f'"{word}" is a reserved word with no content yet (decision 0019); a model cannot use it')
    vocabulary = VOCABULARY[identifier]
    other = EARLIER if identifier == CURRENT else CURRENT
    records, members, reference = vocabulary
    other_records, other_members, other_reference = VOCABULARY[other]
    if other_records in model:
        raise _wrong_key(identifier, other, other_records, records)
    for record_type in _objects(model.get(records)):
        if other_members in record_type:
            raise _wrong_key(identifier, other, other_members, members)
    # Components keep `fields` under both identifiers; their members' references follow the vocabulary.
    groups = [record_type.get(members) for record_type in _objects(model.get(records))]
    groups += [component.get("fields") for component in _objects(model.get("components"))]
    for group in groups:
        for member in _objects(group):
            if other_reference in member:
                raise _wrong_key(identifier, other, other_reference, reference)
    return vocabulary


def json_to_current(data):
    """The bytes `modelspec rewrite` writes for an earlier-vocabulary JSON model (key order kept)."""
    model = json.loads(data.decode("utf-8"))
    records, members, reference = VOCABULARY[EARLIER]
    new_records, new_members, new_reference = VOCABULARY[CURRENT]

    def renamed_keys(value, old, new):
        return {(new if key == old else key): item for key, item in value.items()}

    def member(value):
        return renamed_keys(value, reference, new_reference) if isinstance(value, dict) else value

    def record_type(value):
        if not isinstance(value, dict):
            return value
        value = renamed_keys(value, members, new_members)
        if isinstance(value.get(new_members), dict):
            value[new_members] = {name: member(item) for name, item in value[new_members].items()}
        return value

    model = renamed_keys(model, records, new_records)
    model["modelspec"] = CURRENT
    if isinstance(model.get(new_records), dict):
        model[new_records] = {name: record_type(item) for name, item in model[new_records].items()}
    return (json.dumps(model, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def hcl_to_current(data):
    """The bytes `modelspec rewrite` writes for an earlier-vocabulary HCL model: three keywords, nothing else."""
    text = data.decode("utf-8")
    text = re.sub(r'(?m)^(\s*)entity(\s+"[^"\n]*"\s*\{)', r"\1record\2", text)
    text = re.sub(r'(?m)^(\s*)property(\s+"[^"\n]*"\s*\{)', r"\1field\2", text)
    text = re.sub(r"(?m)^(\s*)entity(\s*=)", r"\1record\2", text)
    return text.encode("utf-8")


def renamed(path, accepted):
    """Recompute the rename of an accepted model file and refuse a result the reference tool would not write."""
    if path not in RENAMED_SHA256 or not isinstance(accepted, bytes):
        raise ValueError("not one of the model's two files")
    data = (json_to_current if path.endswith(".json") else hcl_to_current)(accepted)
    if hashlib.sha256(data).hexdigest() != RENAMED_SHA256[path]:
        raise ValueError("rename of the accepted model does not reproduce the reference tool's output")
    return data


def model_state(accepted, current):
    """Which of the two accepted states the model is in: "accepted" or "renamed"; anything else is refused.

    `accepted` and `current` map each of MODEL_FILES to bytes. Both files must be in
    the same state: the accepted bytes, or their recomputed exact rename. One file
    renamed and the other not, a rename with one more change, or a model that mixes
    the two vocabularies is neither.
    """
    if set(accepted) != set(MODEL_FILES) or set(current) != set(MODEL_FILES):
        raise ValueError("both model files are required")
    if all(current[path] == accepted[path] for path in MODEL_FILES):
        return "accepted"
    try:
        if all(current[path] == renamed(path, accepted[path]) for path in MODEL_FILES):
            return "renamed"
    except (ValueError, UnicodeDecodeError, AttributeError, TypeError):
        pass
    raise ValueError("model is neither the accepted file nor its exact rename")


def record_types(model):
    """Return the model's record types, keyed by name; empty when it declares none."""
    key = _vocabulary(model)[0]
    found = model.get(key, {})
    if not isinstance(found, dict):
        raise ValueError(f'"{key}" must be an object keyed by name')
    return found


def members(model, record_type):
    """Return the members of one record type of `model`, keyed by name."""
    key = _vocabulary(model)[1]
    found = record_type.get(key, {})
    if not isinstance(found, dict):
        raise ValueError(f'"{key}" must be an object keyed by name')
    return found
