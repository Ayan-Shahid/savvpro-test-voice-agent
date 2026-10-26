"""Parser corpus: the collisions measured in the stress test, pinned as regression cases."""

from __future__ import annotations

import pytest

from astro_brain.nlp.normalize import normalize
from astro_brain.nlp.parser import parse

TARGETS = [
    ("Book Saturn please", "saturn"),
    ("I'm serious about Jupiter", "jupiter"),
    ("seven sisters tonight", "m45"),
    ("beetle juice", "betelgeuse"),
    ("o ryan nebula on the reflector", "m42"),
    ("M forty two", "m42"),
    ("messier 31", "m31"),
    ("pleiadies", "m45"),
    ("sag a star on the dish", "sgr-a"),
    ("jupiter's moons", "jupiter"),
    ("Saturn on Saturday", "saturn"),
    ("the andromeda galaxy", "m31"),
]


@pytest.mark.parametrize(("text", "slug"), TARGETS)
def test_targets(text: str, slug: str) -> None:
    assert parse(text).target == slug


@pytest.mark.parametrize("text", ["please", "Saturday night", "I'm serious", "march", "sunday", "the second one"])
def test_no_invented_targets(text: str) -> None:
    r = parse(text)
    assert r.target is None
    assert not r.target_candidates


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("the refractor", "OPT-25"),
        ("big mirror", "DSR-100"),
        ("the dish", "SMM-12"),
        ("radio", "SMM-12"),
        ("deep space", "DSR-100"),
        ("not the refractor, the other optical one", "DSR-100"),
    ],
)
def test_telescopes(text: str, code: str) -> None:
    assert parse(text).telescope == code


@pytest.mark.parametrize("text", ["the refactor", "the deflector"])
def test_telescope_near_miss_is_asked(text: str) -> None:
    r = parse(text)
    assert r.telescope is None
    assert set(r.telescope_candidates) == {"OPT-25", "DSR-100"}


TIMES = [
    ("from 11 pm to 2 am", (11, 0, "pm"), (2, 0, "am")),
    ("half past ten till quarter to one", (10, 30, None), (12, 45, None)),
    ("at 9 pm", (9, 0, "pm"), None),
    ("quarter to midnight", (23, 45, "pm"), None),
    ("eleven thirty pm", (11, 30, "pm"), None),
    ("between 22:00 and 23:30", (22, 0, None), (23, 30, None)),
]


@pytest.mark.parametrize(("text", "start", "end"), TIMES)
def test_times(text: str, start: tuple[int, int, str | None], end: tuple[int, int, str | None] | None) -> None:
    r = parse(text)
    assert r.start is not None
    assert (r.start.hour, r.start.minute, r.start.meridiem) == start
    if end is None:
        assert r.end is None
    else:
        assert r.end is not None
        assert (r.end.hour, r.end.minute, r.end.meridiem) == end


def test_durations_and_relative_edits() -> None:
    assert parse("for two hours starting at 10").duration == 120
    assert parse("for an hour and a half").duration == 90
    assert parse("make it an hour later").shift == 60
    assert parse("30 minutes earlier").shift == -30
    assert parse("extend it by half an hour").extend == 30
    assert parse("same time tomorrow").same_time_next


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("tonight", "offset"),
        ("tomorrow night", "offset"),
        ("next friday", "weekday"),
        ("sat night", "weekday"),
        ("october 30th", "date"),
        ("30/10", "date"),
        ("the 30th", "date"),
        ("last night", "past"),
    ],
)
def test_nights(text: str, kind: str) -> None:
    r = parse(text)
    assert r.night is not None and r.night.kind == kind


def test_abbreviations_are_not_dates_without_a_frame() -> None:
    assert parse("the sun").night is None
    assert parse("the sun").forbidden == "the Sun"
    assert parse("mars").night is None


def test_self_repair_keeps_the_last_value() -> None:
    r = parse("Book Saturn, no wait, Jupiter")
    assert r.target == "jupiter"
    assert r.self_repair


def test_normalize_numbers() -> None:
    assert normalize("eleven thirty p.m.") == ["11", "30", "pm"]
    assert normalize("m forty-two") == ["m", "42"]
    assert normalize("the second one") == ["the", "second", "one"]
