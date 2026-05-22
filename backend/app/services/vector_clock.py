from __future__ import annotations

from enum import Enum


class ClockRelation(str, Enum):
    BEFORE = "before"
    AFTER = "after"
    EQUAL = "equal"
    CONCURRENT = "concurrent"


class VectorClock:
    """Educational vector clock implementation used to label every edit operation."""

    @staticmethod
    def normalize(clock: dict | None) -> dict[str, int]:
        if not clock:
            return {}
        return {str(k): int(v) for k, v in clock.items()}

    @staticmethod
    def tick(clock: dict | None, node_id: str) -> dict[str, int]:
        c = VectorClock.normalize(clock)
        c[node_id] = c.get(node_id, 0) + 1
        return c

    @staticmethod
    def merge(a: dict | None, b: dict | None) -> dict[str, int]:
        left = VectorClock.normalize(a)
        right = VectorClock.normalize(b)
        keys = set(left) | set(right)
        return {k: max(left.get(k, 0), right.get(k, 0)) for k in keys}

    @staticmethod
    def compare(a: dict | None, b: dict | None) -> ClockRelation:
        left = VectorClock.normalize(a)
        right = VectorClock.normalize(b)
        keys = set(left) | set(right)

        less = False
        greater = False
        for k in keys:
            av = left.get(k, 0)
            bv = right.get(k, 0)
            if av < bv:
                less = True
            if av > bv:
                greater = True

        if less and greater:
            return ClockRelation.CONCURRENT
        if less:
            return ClockRelation.BEFORE
        if greater:
            return ClockRelation.AFTER
        return ClockRelation.EQUAL
