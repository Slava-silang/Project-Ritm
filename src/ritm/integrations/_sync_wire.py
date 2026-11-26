"""Internal JSON boundary shared by sync operations and snapshot pages."""

import json
import re
from functools import wraps
from uuid import UUID

from ritm.integrations.sync_codec import MAX_BYTES, MAX_INTEGER, SyncCodecError


def _keys(value, keys):
    if type(value) is not dict or value.keys() != set(keys):
        raise SyncCodecError("schema")


def _id(value):
    if type(value) is not str:
        raise SyncCodecError()
    result = UUID(value)
    if str(result) != value:
        raise SyncCodecError()
    return result


def _id_text(value):
    if type(value) is not UUID:
        raise SyncCodecError()
    return str(value)


def _number(value):
    if type(value) is not str or re.fullmatch(r"0|[1-9][0-9]{0,18}", value) is None:
        raise SyncCodecError()
    result = int(value)
    if result > MAX_INTEGER:
        raise SyncCodecError()
    return result


def _number_text(value):
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise SyncCodecError()
    return str(value)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SyncCodecError("schema")
        result[key] = value
    return result


def _reject(_value):
    raise SyncCodecError("json")


def _dump(value):
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise SyncCodecError("size")
    return raw


def _load(raw, kind):
    if type(raw) not in (str, bytes):
        raise SyncCodecError("json")
    if len(raw) > MAX_BYTES:
        raise SyncCodecError("size")
    raw = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(raw) > MAX_BYTES:
        raise SyncCodecError("size")
    value = json.loads(
        raw.decode("utf-8"), object_pairs_hook=_pairs, parse_float=_reject, parse_constant=_reject
    )
    _keys(value, {"version", "kind", "data"})
    if type(value["version"]) is not int or value["version"] != 1:
        raise SyncCodecError("version")
    if value["kind"] != kind:
        raise SyncCodecError("schema")
    return value["data"]


def _guard(function):
    @wraps(function)
    def boundary(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except SyncCodecError:
            raise
        except (ValueError, TypeError, AttributeError, KeyError, OverflowError, RecursionError):
            raise SyncCodecError() from None

    return boundary
