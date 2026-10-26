"""Text normalisation: lower case, number words to digits, punctuation, fillers.

Hand-written instead of text2num so behaviour is explicit (text2num leaves "one"/"two" as words unless
its threshold is 0, and it rewrites ordinals we need to keep for "the second one").
"""

from __future__ import annotations

import re

UNITS = {
    "zero": 0,
    "oh": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50}
ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "1st": 1, "2nd": 2, "3rd": 3, "last": -1}
FILLERS = frozenset({"uh", "um", "umm", "er", "erm", "hmm", "uhh", "ah"})
# Number words that stay words when they mean something else ("the second one", "seven sisters").
_KEEP_PHRASES = (
    "seven sisters",
    "the second one",
    "the first one",
    "the third one",
    "the last one",
    "no one",
    "any one",
    "anyone",
    "tmc one",
)


def _protect(text: str) -> tuple[str, dict[str, str]]:
    saved: dict[str, str] = {}
    for i, phrase in enumerate(_KEEP_PHRASES):
        if phrase in text:
            key = f"keepphrase{i}x"
            saved[key] = phrase
            text = text.replace(phrase, key)
    return text, saved


def words_to_digits(tokens: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in TENS:
            value = TENS[t]
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            if nxt in UNITS and 0 < UNITS[nxt] < 10:
                value += UNITS[nxt]
                i += 1
            out.append(str(value))
        elif t in UNITS and t != "oh":
            out.append(str(UNITS[t]))
        elif t == "oh" and out and out[-1].isdigit():  # "ten oh five" -> 10 05
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            if nxt in UNITS:
                out.append(f"0{UNITS[nxt]}")
                i += 1
            else:
                out.append(t)
        else:
            out.append(t)
        i += 1
    return out


def normalize(text: str) -> list[str]:
    s = text.lower().strip()
    s = s.replace("’", "'").replace("‘", "'")
    s = re.sub(r"\ba\.\s?m\.?", " am ", s)
    s = re.sub(r"\bp\.\s?m\.?", " pm ", s)
    s = re.sub(r"o'clock|o clock", " ", s)
    s = re.sub(r"(\d)\s*-\s*(\d)", r"\1 to \2", s)  # "11-2" -> "11 to 2"
    s = re.sub(r"(\d)(am|pm)\b", r"\1 \2", s)
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1 \2", s)  # "30th" -> "30 th"
    s = re.sub(r"(\d{1,2})/(\d{1,2})", r"\1 slash \2", s)
    s = re.sub(r"[^a-z0-9:'*\s]", " ", s)
    s = s.replace("'s ", " ").replace("'", " ")
    s = s.replace("*", " star ")
    s, saved = _protect(" ".join(s.split()))
    tokens = [t for t in s.split() if t not in FILLERS]
    tokens = words_to_digits(tokens)
    out: list[str] = []
    for t in tokens:
        if t in saved:
            out.extend(saved[t].split())
        else:
            out.append(t)
    return out
