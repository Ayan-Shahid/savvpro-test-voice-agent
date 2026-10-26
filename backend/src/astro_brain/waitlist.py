"""Waitlist priority queue on `heapq`.

Order is the total key (grade rank, joined_at minute, sequence): grade A before B before C, then first
come, then an identity sequence that can never tie. Cancelled entries are tombstoned (lazy deletion).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

from .types import Minute

GRADE_RANK = {"A": 0, "B": 1, "C": 2}


@dataclass(frozen=True, slots=True)
class Entry:
    entry_id: str
    user_id: str
    grade: str
    joined_at: Minute
    seq: int
    want_start: Minute
    want_end: Minute
    flex: int = 0  # minutes the window may shift either way

    @property
    def key(self) -> tuple[int, int, int, str]:
        return (GRADE_RANK[self.grade], self.joined_at, self.seq, self.entry_id)

    def fit(self, free_start: Minute, free_end: Minute) -> tuple[Minute, Minute] | None:
        """The wanted window, shifted by at most `flex`, placed inside [free_start, free_end)."""
        length = self.want_end - self.want_start
        if free_end - free_start < length:
            return None
        lo = max(free_start, self.want_start - self.flex)
        hi = min(free_end - length, self.want_start + self.flex)
        if lo > hi:
            return None
        start = min(max(self.want_start, lo), hi)
        return (start, start + length)


class WaitlistHeap:
    def __init__(self, entries: list[Entry] | None = None) -> None:
        self._heap: list[tuple[tuple[int, int, int, str], Entry]] = [(e.key, e) for e in entries or []]
        heapq.heapify(self._heap)
        self._dead: set[str] = set()

    def __len__(self) -> int:
        return sum(1 for _k, e in self._heap if e.entry_id not in self._dead)

    def push(self, e: Entry) -> None:
        heapq.heappush(self._heap, (e.key, e))

    def cancel(self, entry_id: str) -> None:
        self._dead.add(entry_id)

    def pop(self) -> Entry | None:
        while self._heap:
            _k, e = heapq.heappop(self._heap)
            if e.entry_id not in self._dead:
                return e
        return None

    def pop_until_fit(self, free_start: Minute, free_end: Minute) -> tuple[Entry, tuple[Minute, Minute]] | None:
        """Highest-priority entry whose window fits; entries that don't fit are pushed back unchanged."""
        skipped: list[Entry] = []
        found: tuple[Entry, tuple[Minute, Minute]] | None = None
        while (e := self.pop()) is not None:
            placed = e.fit(free_start, free_end)
            if placed is not None:
                found = (e, placed)
                break
            skipped.append(e)
        for e in skipped:
            self.push(e)
        return found

    def ordered(self) -> list[Entry]:
        return [e for _k, e in sorted(self._heap) if e.entry_id not in self._dead]
