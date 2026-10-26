"""Deterministic utterance parser: self-repair, lexicon claims, time grammar, fuzzy fallback, intent cues.

Order matters and is fixed:
  1. normalise  2. split self-repairs  3. catalogue ids  4. telescope aliases  5. target aliases
  6. time grammar on unclaimed tokens  7. fuzzy target match on unclaimed noun spans  8. intent cues
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process, utils

from ..catalogue import FORBIDDEN_TARGETS, STOP_WORDS, TARGET_ALIASES, TELESCOPE_ALIASES
from .normalize import ORDINAL_WORDS, normalize
from .timegrammar import MONTHS, WEEKDAY_ABBR, WEEKDAYS, NightRef, TimeRef, parse_times

AUTO_ACCEPT = 92.0
CONFIRM_FLOOR = 80.0
LEAD = 10.0

REPAIR_MARKERS = (
    ("no", "wait"),
    ("wait", "no"),
    ("i", "mean"),
    ("or", "rather"),
    ("make", "that"),
    ("no", "sorry"),
    ("sorry", "i", "mean"),
)
_TELESCOPE_KEYS = sorted((tuple(k.split()) for k in TELESCOPE_ALIASES), key=len, reverse=True)
_TARGET_KEYS = sorted((tuple(k.split()) for k in TARGET_ALIASES), key=len, reverse=True)
_FORBIDDEN_KEYS = sorted((tuple(k.split()) for k in FORBIDDEN_TARGETS), key=len, reverse=True)
_FUZZY_CHOICES = {k: v[0] for k, v in TARGET_ALIASES.items() if v[1] in {"official", "common"}}
OTHER_OPTICAL = {"OPT-25": "DSR-100", "DSR-100": "OPT-25"}
OUT_OF_SCOPE_WORDS = frozenset(
    {
        "table",
        "restaurant",
        "flight",
        "hotel",
        "taxi",
        "pizza",
        "doctor",
        "dentist",
        "haircut",
        "appointment",
        "train",
        "cinema",
        "movie",
        "dinner",
        "lunch",
    }
)
_TIME_WORDS = (
    set(WEEKDAYS)
    | set(WEEKDAY_ABBR)
    | set(MONTHS)
    | {
        "am",
        "pm",
        "midnight",
        "noon",
        "half",
        "quarter",
        "past",
        "th",
        "st",
        "nd",
        "rd",
        "slash",
        "o",
        "clock",
        "hour",
        "hours",
        "minutes",
        "mins",
        "later",
        "earlier",
    }
)


@dataclass
class Mention:
    value: str
    start: int
    end: int
    via: str
    score: float = 100.0
    negated: bool = False


@dataclass
class ParseResult:
    text: str
    tokens: list[str]
    targets: list[Mention] = field(default_factory=list)
    telescopes: list[Mention] = field(default_factory=list)
    forbidden: str | None = None
    target_candidates: list[tuple[str, float]] = field(default_factory=list)  # needs "did you mean"
    unknown_target: str | None = None
    telescope_candidates: list[str] = field(default_factory=list)
    night: NightRef | None = None
    start: TimeRef | None = None
    end: TimeRef | None = None
    duration: int | None = None
    shift: int | None = None
    extend: int | None = None
    same_time_next: bool = False
    pick: int | None = None
    flags: set[str] = field(default_factory=set)
    go_back_steps: int = 1
    self_repair: bool = False

    @property
    def target(self) -> str | None:
        live = [m for m in self.targets if not m.negated]
        return live[-1].value if live else None

    @property
    def telescope(self) -> str | None:
        live = [m for m in self.telescopes if not m.negated]
        if not live:
            return None
        last = live[-1]
        if last.via == "other" and last.value in self.negated_telescopes:
            # "not the refractor, the other optical one"
            return OTHER_OPTICAL.get(last.value, last.value)
        return last.value

    @property
    def negated_telescopes(self) -> set[str]:
        return {m.value for m in self.telescopes if m.negated}

    @property
    def has_slots(self) -> bool:
        return any(
            (
                self.target,
                self.telescope,
                self.night,
                self.start,
                self.end,
                self.duration,
                self.shift,
                self.extend,
                self.same_time_next,
                self.pick is not None,
            )
        )


def _match_longest(
    tokens: list[str],
    claimed: list[bool],
    keys: list[tuple[str, ...]],
) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []
    i = 0
    while i < len(tokens):
        hit = None
        if not claimed[i]:
            for k in keys:
                n = len(k)
                if tuple(tokens[i : i + n]) == k and not any(claimed[i : i + n]):
                    hit = (i, i + n, " ".join(k))
                    break
        if hit:
            found.append(hit)
            for j in range(hit[0], hit[1]):
                claimed[j] = True
            i = hit[1]
        else:
            i += 1
    return found


def _negated(tokens: list[str], start: int) -> bool:
    window = tokens[max(0, start - 3) : start]
    return "not" in window or "except" in window or ("instead" in window and "of" in window)


def _split_repairs(tokens: list[str]) -> tuple[list[list[str]], bool]:
    segs: list[list[str]] = [[]]
    i = 0
    repaired = False
    while i < len(tokens):
        for m in REPAIR_MARKERS:
            if tuple(tokens[i : i + len(m)]) == m and segs[-1]:
                segs.append([])
                i += len(m)
                repaired = True
                break
        else:
            segs[-1].append(tokens[i])
            i += 1
    return [s for s in segs if s], repaired


def _parse_segment(tokens: list[str], res: ParseResult, expect_target: bool) -> None:
    claimed = [False] * len(tokens)
    # catalogue ids: "m 42", "messier 31", "ngc 224"
    for i in range(len(tokens) - 1):
        if tokens[i] in {"m", "messier", "ngc"} and tokens[i + 1].isdigit():
            key = ("m" if tokens[i] != "ngc" else "ngc ") + tokens[i + 1]
            key = key if key in TARGET_ALIASES else key.replace(" ", "")
            if key in TARGET_ALIASES:
                res.targets.append(Mention(TARGET_ALIASES[key][0], i, i + 2, "catalogue"))
                claimed[i] = claimed[i + 1] = True
    for _s, _e, k in _match_longest(tokens, claimed, _FORBIDDEN_KEYS):
        res.forbidden = FORBIDDEN_TARGETS[k]
    # "the other optical one" after a negated optical telescope
    for s, e, k in _match_longest(tokens, claimed, _TELESCOPE_KEYS):
        code = TELESCOPE_ALIASES[k]
        other = s > 0 and tokens[s - 1] == "other"
        res.telescopes.append(Mention(code, s, e, "other" if other else "alias", negated=_negated(tokens, s)))
    for s, e, k in _match_longest(tokens, claimed, _TARGET_KEYS):
        slug, kind = TARGET_ALIASES[k]
        res.targets.append(Mention(slug, s, e, kind, negated=_negated(tokens, s)))
    tp = parse_times(tokens, claimed)
    for i in tp.used:
        claimed[i] = True
    res.night = tp.night or res.night
    res.start = tp.start or res.start
    res.end = tp.end or res.end
    res.duration = tp.duration or res.duration
    res.shift = tp.shift if tp.shift is not None else res.shift
    res.extend = tp.extend or res.extend
    res.same_time_next = res.same_time_next or tp.same_time_next
    # ordinal picks from offered options: "the second one", "option 2", "the first"
    for i, t in enumerate(tokens):
        if claimed[i]:
            continue
        if t in ORDINAL_WORDS and (
            i + 1 >= len(tokens)
            or tokens[i + 1] in {"one", "option", "choice", "1"}
            or (i > 0 and tokens[i - 1] == "the")
        ):
            res.pick = ORDINAL_WORDS[t]
            claimed[i] = True
        elif t in {"option", "number"} and i + 1 < len(tokens) and tokens[i + 1].isdigit():
            res.pick = int(tokens[i + 1])
            claimed[i] = claimed[i + 1] = True
    # telescope near-misses ("refactor", "deflector") are never accepted, only asked about
    if not res.telescopes:
        near: list[tuple[float, str]] = []
        for i, t in enumerate(tokens):
            if claimed[i] or len(t) < 6:
                continue
            for word, code in (("refractor", "OPT-25"), ("reflector", "DSR-100")):
                score = fuzz.ratio(t, word)
                if score >= 70:
                    near.append((score, code))
                    claimed[i] = True
        if near:
            res.telescope_candidates = [c for _s, c in sorted(near, key=lambda x: (-x[0], x[1]))]
            for code in ("OPT-25", "DSR-100"):
                if code not in res.telescope_candidates:
                    res.telescope_candidates.append(code)
    if any(t in OUT_OF_SCOPE_WORDS for t in tokens):
        res.flags.add("oos")
    # fuzzy fallback on unclaimed content words
    if (
        not any(not m.negated for m in res.targets)
        and res.forbidden is None
        and res.pick is None
        and "oos" not in res.flags
    ):
        _fuzzy_targets(tokens, claimed, res, expect_target)


def _fuzzy_targets(tokens: list[str], claimed: list[bool], res: ParseResult, expect_target: bool) -> None:
    spans: list[list[str]] = []
    cur: list[str] = []
    for i, t in enumerate(tokens):
        content = (
            not claimed[i]
            and t not in STOP_WORDS
            and t not in _TIME_WORDS
            and not t.isdigit()
            and len(t) > 2
            and t not in ORDINAL_WORDS
        )
        if content:
            cur.append(t)
        elif cur:
            spans.append(cur)
            cur = []
    if cur:
        spans.append(cur)
    best: tuple[str, float, float, str] | None = None
    for span in spans:
        for n in range(min(3, len(span)), 0, -1):
            for i in range(len(span) - n + 1):
                phrase = " ".join(span[i : i + n])
                hits = process.extract(
                    phrase, list(_FUZZY_CHOICES), scorer=fuzz.WRatio, processor=utils.default_process, limit=8
                )
                by_slug: dict[str, float] = {}
                for alias, score, _ in hits:
                    slug = _FUZZY_CHOICES[alias]
                    by_slug[slug] = max(by_slug.get(slug, 0.0), float(score))
                ranked = sorted(by_slug.items(), key=lambda kv: (-kv[1], kv[0]))
                if not ranked:
                    continue
                top_slug, top = ranked[0]
                second = ranked[1][1] if len(ranked) > 1 else 0.0
                if best is None or top > best[1]:
                    best = (top_slug, top, second, phrase)
    if best is None:
        return
    slug, top, second, phrase = best
    if top >= AUTO_ACCEPT and top - second >= LEAD:
        res.targets.append(Mention(slug, -1, -1, "fuzzy", top))
    elif top >= CONFIRM_FLOOR:
        others = [] if top - second >= LEAD else [s for s, sc in _rank(phrase) if sc >= top - LEAD and s != slug][:1]
        res.target_candidates = [(slug, top)] + [(s, top) for s in others]
    elif expect_target or _looks_like_object(tokens, phrase):
        res.unknown_target = phrase


def _rank(phrase: str) -> list[tuple[str, float]]:
    hits = process.extract(phrase, list(_FUZZY_CHOICES), scorer=fuzz.WRatio, processor=utils.default_process, limit=8)
    best: dict[str, float] = {}
    for alias, score, _ in hits:
        s = _FUZZY_CHOICES[alias]
        best[s] = max(best.get(s, 0.0), float(score))
    return sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))


def _looks_like_object(tokens: list[str], phrase: str) -> bool:
    first = phrase.split()[0]
    if first not in tokens:
        return False
    i = tokens.index(first)
    prev = tokens[max(0, i - 2) : i]
    return any(w in prev for w in ("book", "observe", "see", "at", "for", "target", "view", "watch"))


# ---- intent cues ---------------------------------------------------------------------------------
_CUES: dict[str, tuple[str, ...]] = {
    "yes": (
        "yes",
        "yeah",
        "yep",
        "yup",
        "correct",
        "sure",
        "go ahead",
        "book it",
        "confirm",
        "sounds good",
        "that s right",
        "thats right",
        "perfect",
        "do it",
        "please do",
        "absolutely",
        "ok",
        "okay",
        "that works",
        "fine",
        "great",
        "right",
    ),
    "no": ("no", "nope", "not quite", "wrong", "incorrect", "nah"),
    "undo": ("undo", "go back", "scratch that", "take that back", "previous", "back up", "revert"),
    "cancel": ("cancel", "forget it", "never mind", "nevermind", "forget about it"),
    "booking_ref": ("booking", "reservation", "slot i booked", "my session"),
    "list": ("my bookings", "what have i booked", "what did i book", "list my", "what do i have booked"),
    "weather": (
        "weather",
        "cloud",
        "cloudy",
        "clear sky",
        "clear skies",
        "rain",
        "forecast",
        "humid",
        "water vapour",
        "water vapor",
    ),
    "visibility": (
        "is it up",
        "rise",
        "rises",
        "set ",
        "visible",
        "when does",
        "when can i see",
        "above the horizon",
        "is up",
    ),
    "availability": ("free", "available", "open slot", "open slots", "availability"),
    "waitlist": ("waitlist", "wait list", "waiting list"),
    "help": ("help", "what can you do", "how does this work"),
    "repeat": ("repeat", "say that again", "pardon", "come again", "what did you say"),
    "start_over": ("start over", "start again", "from the beginning", "reset"),
    "goodbye": (
        "bye",
        "goodbye",
        "that s all",
        "thats all",
        "that is all",
        "hang up",
        "nothing else",
        "that s it",
        "no thanks",
        "no thank you",
    ),
    "pause": ("hold on", "one moment", "just a sec", "just a second", "give me a second"),
    "book": (
        "book",
        "reserve",
        "schedule",
        "i d like",
        "i would like",
        "i want",
        "can i get",
        "observe",
        "look at",
        "point",
        "get time",
        "time on",
    ),
    "change": ("actually", "instead", "switch", "change", "make it", "move it", "rather", "swap"),
    "offer": ("waitlist offer", "got an offer", "got a waitlist"),
}


def _has(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase.strip())}(?![a-z0-9])", text) is not None


def parse(text: str, expect_target: bool = False) -> ParseResult:
    tokens = normalize(text)
    res = ParseResult(text=text, tokens=tokens)
    segments, repaired = _split_repairs(tokens)
    res.self_repair = repaired
    # later segments override earlier ones slot by slot
    for seg in segments:
        before_t = len([m for m in res.targets if not m.negated])
        _parse_segment(seg, res, expect_target)
        after = [m for m in res.targets if not m.negated]
        if repaired and len(after) > before_t > 0:
            for m in after[:before_t]:
                m.negated = True  # superseded by the repair
    joined = " ".join(tokens)
    for flag, cues in _CUES.items():
        if any(_has(joined, c) for c in cues):
            res.flags.add(flag)
    if joined.strip() in {"wait", "wait a moment", "wait a second"}:
        res.flags.add("pause")
    steps = re.search(r"go back (\d+|two|three) steps?", joined)
    if steps:
        word = steps.group(1)
        res.go_back_steps = {"two": 2, "three": 3}.get(word, int(word) if word.isdigit() else 1)
    if "no" in res.flags and tokens and tokens[0] != "no" and "no" not in tokens:
        res.flags.discard("no")
    if "yes" in res.flags and {"right"} & set(tokens) and len(tokens) > 3 and "that" not in tokens:
        res.flags.discard("yes")  # "right" inside a longer sentence is not an answer
    return res
