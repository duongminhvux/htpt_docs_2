from __future__ import annotations

from copy import deepcopy
from itertools import groupby
from typing import Any


def op_len(op: dict[str, Any]) -> int:
    if "insert" in op:
        value = op["insert"]
        return len(value) if isinstance(value, str) else 1
    return int(op.get("retain", op.get("delete", 0)))


def is_insert(op: dict[str, Any]) -> bool:
    return "insert" in op


def is_delete(op: dict[str, Any]) -> bool:
    return "delete" in op


def is_retain(op: dict[str, Any]) -> bool:
    return "retain" in op


class OpIterator:
    def __init__(self, ops: list[dict[str, Any]]):
        self.ops = deepcopy(ops or [])
        self.index = 0
        self.offset = 0

    def has_next(self) -> bool:
        return self.peek_length() < float("inf")

    def peek(self) -> dict[str, Any] | None:
        if self.index >= len(self.ops):
            return None
        return self.ops[self.index]

    def peek_type(self) -> str:
        op = self.peek()
        if op is None:
            return "retain"
        if "delete" in op:
            return "delete"
        if "insert" in op:
            return "insert"
        return "retain"

    def peek_length(self) -> int | float:
        op = self.peek()
        if op is None:
            return float("inf")
        return op_len(op) - self.offset

    def next(self, length: int | None = None) -> dict[str, Any]:
        if self.index >= len(self.ops):
            return {"retain": length or 0}

        op = self.ops[self.index]
        remaining = op_len(op) - self.offset
        take = remaining if length is None else min(length, remaining)

        if "insert" in op:
            value = op["insert"]
            if isinstance(value, str):
                part_value = value[self.offset : self.offset + take]
            else:
                part_value = value
            part = {"insert": part_value}
            if "attributes" in op:
                part["attributes"] = deepcopy(op["attributes"])
        elif "delete" in op:
            part = {"delete": take}
        else:
            part = {"retain": take}
            if "attributes" in op:
                part["attributes"] = deepcopy(op["attributes"])

        self.offset += take
        if self.offset >= op_len(op):
            self.index += 1
            self.offset = 0
        return part


def compact_ops(ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for op in ops:
        if not op:
            continue
        if op.get("retain") == 0 or op.get("delete") == 0 or op.get("insert") == "":
            continue
        if result:
            last = result[-1]
            if "insert" in last and "insert" in op and isinstance(last["insert"], str) and isinstance(op["insert"], str) and last.get("attributes") == op.get("attributes"):
                last["insert"] += op["insert"]
                continue
            if "delete" in last and "delete" in op:
                last["delete"] += op["delete"]
                continue
            if "retain" in last and "retain" in op and last.get("attributes") == op.get("attributes"):
                last["retain"] += op["retain"]
                continue
        result.append(op)
    while result and set(result[-1].keys()) == {"retain"}:
        result.pop()
    return result


def transform_delta(delta: dict[str, Any], against: dict[str, Any], priority: bool = False) -> dict[str, Any]:
    """Transform delta against a concurrent delta.

    This implements the core OT idea for Quill-like Delta ops: insert/retain/delete.
    It is intentionally compact for teaching and class-demo usage.
    """
    a = OpIterator(delta.get("ops", []))
    b = OpIterator(against.get("ops", []))
    out: list[dict[str, Any]] = []

    while a.has_next() or b.has_next():
        if a.peek_type() == "insert" and (priority or b.peek_type() != "insert"):
            out.append(a.next())
            continue

        if b.peek_type() == "insert":
            out.append({"retain": op_len(b.next())})
            continue

        length = int(min(a.peek_length(), b.peek_length()))
        a_op = a.next(length)
        b_op = b.next(length)

        if is_delete(a_op):
            if is_retain(b_op):
                out.append(a_op)
            # both delete same region: skip
            continue

        if is_retain(a_op):
            if is_delete(b_op):
                # region was removed by remote op, so this op no longer needs to retain it
                continue
            retain = {"retain": length}
            if "attributes" in a_op:
                retain["attributes"] = deepcopy(a_op["attributes"])
            out.append(retain)

    return {"ops": compact_ops(out)}


def _attrs_equal(a: dict | None, b: dict | None) -> bool:
    return (a or {}) == (b or {})


def delta_to_units(delta: dict[str, Any]) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for op in delta.get("ops", []):
        attrs = deepcopy(op.get("attributes", {}))
        if "insert" not in op:
            continue
        value = op["insert"]
        if isinstance(value, str):
            for ch in value:
                units.append({"value": ch, "attributes": attrs})
        else:
            units.append({"value": value, "attributes": attrs})
    return units


def units_to_delta(units: list[dict[str, Any]]) -> dict[str, Any]:
    if not units:
        return {"ops": [{"insert": "\n"}]}

    ops: list[dict[str, Any]] = []
    i = 0
    while i < len(units):
        unit = units[i]
        attrs = unit.get("attributes") or {}
        value = unit["value"]
        if isinstance(value, str):
            chars = [value]
            i += 1
            while i < len(units) and isinstance(units[i]["value"], str) and _attrs_equal(units[i].get("attributes"), attrs):
                chars.append(units[i]["value"])
                i += 1
            op = {"insert": "".join(chars)}
            if attrs:
                op["attributes"] = attrs
            ops.append(op)
        else:
            op = {"insert": value}
            if attrs:
                op["attributes"] = attrs
            ops.append(op)
            i += 1
    return {"ops": ops}


def apply_attributes(existing: dict | None, patch: dict | None) -> dict:
    attrs = deepcopy(existing or {})
    for key, value in (patch or {}).items():
        if value is None:
            attrs.pop(key, None)
        else:
            attrs[key] = value
    return attrs


def apply_delta(document_delta: dict[str, Any], change_delta: dict[str, Any]) -> dict[str, Any]:
    """Apply a Quill-like change Delta onto a document Delta.

    Supports inserts, deletes, retains and retain attributes, enough for rich-text toolbar edits.
    """
    source_units = delta_to_units(document_delta)
    result: list[dict[str, Any]] = []
    index = 0

    for op in change_delta.get("ops", []):
        if "insert" in op:
            attrs = deepcopy(op.get("attributes", {}))
            value = op["insert"]
            if isinstance(value, str):
                result.extend({"value": ch, "attributes": attrs} for ch in value)
            else:
                result.append({"value": value, "attributes": attrs})
        elif "retain" in op:
            count = int(op["retain"])
            attrs_patch = op.get("attributes")
            for _ in range(count):
                if index >= len(source_units):
                    break
                unit = deepcopy(source_units[index])
                if attrs_patch:
                    unit["attributes"] = apply_attributes(unit.get("attributes"), attrs_patch)
                result.append(unit)
                index += 1
        elif "delete" in op:
            index += int(op["delete"])

    result.extend(deepcopy(source_units[index:]))

    # Quill documents must end with a newline.
    if not result or result[-1]["value"] != "\n":
        result.append({"value": "\n", "attributes": {}})
    return units_to_delta(result)


def delta_to_plain_text(delta: dict[str, Any]) -> str:
    parts: list[str] = []
    for op in delta.get("ops", []):
        value = op.get("insert")
        if isinstance(value, str):
            parts.append(value)
    return "".join(parts).strip()
