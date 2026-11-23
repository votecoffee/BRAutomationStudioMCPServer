
"""Read the UTF-8 JSON used by requests and retained receipts."""

from hashlib import sha256
import json


def unique_object(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f"Duplicate JSON key {key!r}")
        data[key] = value
    return data


def invalid_constant(value):
    raise ValueError(f"Invalid JSON constant {value!r}")


def _read_json_bytes(path, limit):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"JSON input exceeds {limit} bytes")
    return data


def read_json(path, limit):
    return decode_json(_read_json_bytes(path, limit))


def json_snapshot(path, limit):
    data = _read_json_bytes(path, limit)
    return decode_json(data), {"path": str(path.resolve()), "sha256": sha256(data).hexdigest()}


def decode_json(data):
    if data.startswith(b"\xef\xbb\xbf"):
        raise ValueError("Write JSON as UTF-8 without a BOM")
    return json.loads(data.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=invalid_constant)
