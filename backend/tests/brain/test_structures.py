"""Property and unit tests for the data structures: interval tree, LIFO stack, waitlist heap."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from astro_brain import stack as stk
from astro_brain.interval_tree import IntervalTree
from astro_brain.types import CallState, Draft
from astro_brain.waitlist import Entry, WaitlistHeap

intervals = st.lists(
    st.tuples(st.integers(0, 500), st.integers(1, 120)).map(lambda t: (t[0], t[0] + t[1])), max_size=200
)


@settings(max_examples=300, deadline=None)
@given(intervals, st.integers(0, 600), st.integers(1, 120))
def test_tree_matches_brute_force(items: list[tuple[int, int]], qs: int, ql: int) -> None:
    tree = IntervalTree()
    for i, (s, e) in enumerate(items):
        tree.insert(s, e, i)
    qe = qs + ql
    expected = sorted(i for i, (s, e) in enumerate(items) if s < qe and qs < e)
    got = sorted(p for _s, _e, p in tree.overlaps(qs, qe))
    assert got == expected
    assert tree.any_overlap(qs, qe) == bool(expected)


@settings(max_examples=150, deadline=None)
@given(intervals)
def test_tree_stays_balanced_and_ordered(items: list[tuple[int, int]]) -> None:
    tree = IntervalTree()
    seqs = [tree.insert(s, e, (s, e)) for s, e in items]
    keys = [(s, e) for s, e, _p in tree]
    assert keys == sorted(keys)
    n = max(1, len(items))
    assert tree.height() <= 1.45 * (n.bit_length() + 1) + 1
    for (s, e), q in zip(items[::2], seqs[::2], strict=False):
        assert tree.remove(s, e, q)
    assert len(tree) == len(items) - len(items[::2])


def test_half_open_boundaries_do_not_collide() -> None:
    tree = IntervalTree()
    tree.insert(120, 240, "02:00-04:00")
    assert not tree.any_overlap(0, 120)  # [23:00, 02:00) touches but does not overlap
    assert tree.any_overlap(119, 121)


def test_free_gaps() -> None:
    tree = IntervalTree()
    tree.insert(60, 120)
    tree.insert(100, 180)
    tree.insert(300, 330)
    assert tree.free_gaps(0, 400, 30) == [(0, 60), (180, 300), (330, 400)]


def test_stack_undo_restores_and_compensates() -> None:
    s0 = CallState(draft=Draft(target="saturn"), hold_id=None)
    s1 = stk.push(s0, "Saturn").with_(draft=Draft(target="m42"), hold_id="h1")
    res = stk.pop(s1)
    assert res.state.draft.target == "saturn"
    assert [e.kind for e in res.compensations] == ["release"]
    assert stk.pop(res.state).frame is None


def test_stack_barrier_blocks_undo_and_depth_is_capped() -> None:
    s = CallState()
    for i in range(60):
        s = stk.push(s, f"f{i}")
    assert len(s.stack) == stk.MAX_DEPTH
    s = stk.push_barrier(s, "booked")
    assert stk.pop(s).blocked_by_barrier


def test_pop_to_named_frame() -> None:
    s = CallState(draft=Draft(target="m31"))
    s = stk.push(s, "a").with_(draft=Draft(target="saturn"))
    s = stk.push(s, "b").with_(draft=Draft(target="saturn", telescope="OPT-25"))
    res = stk.pop_to(s, lambda f: f.draft.target == "m31")
    assert res.state.draft == Draft(target="m31")
    assert res.state.stack == ()


def _e(i: str, grade: str, joined: int, seq: int, start: int = 0, end: int = 120, flex: int = 0) -> Entry:
    return Entry(i, f"u{i}", grade, joined, seq, start, end, flex)


def test_waitlist_priority_grade_then_fifo() -> None:
    h = WaitlistHeap([_e("b-early", "B", 10, 1), _e("a-late", "A", 50, 2), _e("a-early", "A", 20, 3)])
    assert [e.entry_id for e in h.ordered()] == ["a-early", "a-late", "b-early"]
    h.cancel("a-early")
    assert h.pop() is not None and h.pop() is not None
    assert len(h) == 0


def test_waitlist_pop_until_fit_keeps_skipped_entries() -> None:
    h = WaitlistHeap([_e("a-long", "A", 1, 1, 0, 240), _e("b-fits", "B", 2, 2, 30, 90, flex=30)])
    found = h.pop_until_fit(0, 120)
    assert found is not None and found[0].entry_id == "b-fits"
    assert found[1] == (30, 90)
    assert [e.entry_id for e in h.ordered()] == ["a-long"]


@given(st.lists(st.tuples(st.sampled_from("ABC"), st.integers(0, 1000)), min_size=1, max_size=60))
def test_heap_order_equals_sorted_order(raw: list[tuple[str, int]]) -> None:
    entries = [_e(str(i), g, j, i) for i, (g, j) in enumerate(raw)]
    h = WaitlistHeap(list(reversed(entries)))
    popped = [h.pop() for _ in entries]
    assert [e.key for e in popped if e] == sorted(e.key for e in entries)
