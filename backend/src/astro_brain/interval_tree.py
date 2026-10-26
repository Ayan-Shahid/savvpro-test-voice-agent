"""Augmented AVL interval tree over half-open integer intervals [start, end).

Each node stores the maximum `end` in its subtree, so "does anything overlap [a, b)?" is answered in
O(log n) and "list everything that overlaps" in O(log n + k). Keys are (start, end, seq) so equal
intervals with different payloads coexist and iteration order is total and deterministic.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class _Node:
    start: int
    end: int
    seq: int
    payload: Any
    max_end: int
    height: int = 1
    left: _Node | None = None
    right: _Node | None = None

    @property
    def key(self) -> tuple[int, int, int]:
        return (self.start, self.end, self.seq)


def _h(n: _Node | None) -> int:
    return n.height if n else 0


def _m(n: _Node | None) -> int:
    return n.max_end if n else -(1 << 62)


def _fix(n: _Node) -> _Node:
    n.height = 1 + max(_h(n.left), _h(n.right))
    n.max_end = max(n.end, _m(n.left), _m(n.right))
    return n


def _rot_right(y: _Node) -> _Node:
    x = y.left
    assert x is not None
    y.left = x.right
    x.right = y
    _fix(y)
    return _fix(x)


def _rot_left(x: _Node) -> _Node:
    y = x.right
    assert y is not None
    x.right = y.left
    y.left = x
    _fix(x)
    return _fix(y)


def _balance(n: _Node) -> _Node:
    _fix(n)
    bf = _h(n.left) - _h(n.right)
    if bf > 1:
        assert n.left is not None
        if _h(n.left.left) < _h(n.left.right):
            n.left = _rot_left(n.left)
        return _rot_right(n)
    if bf < -1:
        assert n.right is not None
        if _h(n.right.right) < _h(n.right.left):
            n.right = _rot_right(n.right)
        return _rot_left(n)
    return n


class IntervalTree:
    def __init__(self) -> None:
        self._root: _Node | None = None
        self._seq = 0
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def insert(self, start: int, end: int, payload: Any = None) -> int:
        if end <= start:
            raise ValueError("interval must be non-empty and half-open: start < end")
        self._seq += 1
        node = _Node(start, end, self._seq, payload, end)
        self._root = self._insert(self._root, node)
        self._size += 1
        return self._seq

    def _insert(self, n: _Node | None, new: _Node) -> _Node:
        if n is None:
            return new
        if new.key < n.key:
            n.left = self._insert(n.left, new)
        else:
            n.right = self._insert(n.right, new)
        return _balance(n)

    def remove(self, start: int, end: int, seq: int) -> bool:
        before = self._size
        self._root = self._remove(self._root, (start, end, seq))
        return self._size < before

    def _remove(self, n: _Node | None, key: tuple[int, int, int]) -> _Node | None:
        if n is None:
            return None
        if key < n.key:
            n.left = self._remove(n.left, key)
        elif key > n.key:
            n.right = self._remove(n.right, key)
        else:
            self._size -= 1
            if n.left is None:
                return n.right
            if n.right is None:
                return n.left
            succ = n.right
            while succ.left is not None:
                succ = succ.left
            n.start, n.end, n.seq, n.payload = succ.start, succ.end, succ.seq, succ.payload
            self._size += 1  # the recursive removal below decrements again
            n.right = self._remove(n.right, succ.key)
        return _balance(n)

    def any_overlap(self, start: int, end: int) -> bool:
        """True if some stored [s, e) intersects [start, end). O(log n)."""
        n = self._root
        while n is not None:
            if n.start < end and start < n.end:
                return True
            n = n.left if n.left is not None and n.left.max_end > start else n.right
        return False

    def overlaps(self, start: int, end: int) -> list[tuple[int, int, Any]]:
        """All stored intervals intersecting [start, end), in key order. O(log n + k)."""
        out: list[tuple[int, int, Any]] = []
        self._collect(self._root, start, end, out)
        return out

    def _collect(self, n: _Node | None, start: int, end: int, out: list[tuple[int, int, Any]]) -> None:
        if n is None or n.max_end <= start:
            return
        self._collect(n.left, start, end, out)
        if n.start < end and start < n.end:
            out.append((n.start, n.end, n.payload))
        if n.start < end:
            self._collect(n.right, start, end, out)

    def __iter__(self) -> Iterator[tuple[int, int, Any]]:
        stack: list[_Node] = []
        n = self._root
        while stack or n is not None:
            while n is not None:
                stack.append(n)
                n = n.left
            n = stack.pop()
            yield (n.start, n.end, n.payload)
            n = n.right

    def free_gaps(self, lo: int, hi: int, min_len: int) -> list[tuple[int, int]]:
        """Maximal sub-intervals of [lo, hi) not covered by any stored interval, at least `min_len` long."""
        gaps: list[tuple[int, int]] = []
        cursor = lo
        for s, e, _p in self.overlaps(lo, hi):
            if s > cursor and s - cursor >= min_len:
                gaps.append((cursor, min(s, hi)))
            cursor = max(cursor, e)
            if cursor >= hi:
                break
        if hi - cursor >= min_len:
            gaps.append((cursor, hi))
        return gaps

    def height(self) -> int:
        return _h(self._root)
