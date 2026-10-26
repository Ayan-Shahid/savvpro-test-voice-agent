"""Static domain data: the fictional site, the three telescopes, the target catalogue and its aliases.

Coordinates are real public astronomy (J2000). People are never in here.
The seed tool writes these into Postgres; tests and offline runs use them directly.
"""

from __future__ import annotations

from .types import Site, Target, Telescope

SITE = Site(
    slug="meridian-ridge",
    name="Meridian Ridge University Observatory",
    lat_deg=30.0,
    lon_deg=75.0,
    elevation_m=2400,
    tz_name="Etc/GMT-5",
    tz_offset_min=300,
)

# Kinds that count as "bright" for the darkness rule (Sun <= -12 deg is enough).
BRIGHT_KINDS = frozenset({"moon", "planet", "star", "double_star"})
# Optical targets the Moon can wash out.
DEEP_SKY_KINDS = frozenset({"star_cluster", "galaxy", "nebula", "supernova_remnant"})

TELESCOPES: dict[str, Telescope] = {
    t.code: t
    for t in (
        Telescope(
            code="OPT-25",
            name="Optical Refractor",
            kind="optical_refractor",
            min_altitude_deg=20.0,
            sun_max_alt_bright=-12.0,
            sun_max_alt_faint=-18.0,
            sun_avoid_deg=45.0,
            moon_avoid_deg=0.0,
            max_cloud_pct=40,
            max_pwv_mm=None,
            max_rain_pct=20,
            max_wind_kmh=45,
            allowed_kinds=frozenset({"moon", "planet", "star", "double_star", "star_cluster", "galaxy", "nebula"}),
            max_magnitude_extended=7.0,
            display_order=1,
        ),
        Telescope(
            code="DSR-100",
            name="Deep-Space Reflector",
            kind="deep_space_reflector",
            min_altitude_deg=20.0,
            sun_max_alt_bright=-12.0,
            sun_max_alt_faint=-18.0,
            sun_avoid_deg=45.0,
            moon_avoid_deg=0.0,
            max_cloud_pct=25,
            max_pwv_mm=None,
            max_rain_pct=10,
            max_wind_kmh=40,
            allowed_kinds=frozenset(
                {"moon", "planet", "star", "double_star", "star_cluster", "galaxy", "nebula", "supernova_remnant"}
            ),
            max_magnitude_extended=None,
            display_order=2,
        ),
        Telescope(
            code="SMM-12",
            name="Sub-Millimeter Radio Dish",
            kind="submm_dish",
            min_altitude_deg=15.0,
            sun_max_alt_bright=None,
            sun_max_alt_faint=None,
            sun_avoid_deg=30.0,
            moon_avoid_deg=5.0,
            max_cloud_pct=None,
            max_pwv_mm=4.0,
            max_rain_pct=10,
            max_wind_kmh=36,
            allowed_kinds=frozenset(
                {"moon", "planet", "galaxy", "supernova_remnant", "radio_source", "molecular_cloud"}
            ),
            max_magnitude_extended=None,
            display_order=3,
        ),
    )
}


def _t(
    slug: str,
    name: str,
    kind: str,
    *,
    body: str | None = None,
    ra: float | None = None,
    dec: float | None = None,
    mag: float | None = None,
    ids: tuple[str, ...] = (),
    radio_ok: bool | None = None,
    spoken: str | None = None,
) -> Target:
    return Target(
        slug=slug,
        name=name,
        kind=kind,
        ephemeris_body=body,
        ra_hours=ra,
        dec_deg=dec,
        magnitude=mag,
        catalogue_ids=ids,
        radio_ok=radio_ok,
        spoken_name=spoken or name,
    )


def _hms(h: float, m: float, s: float = 0.0) -> float:
    return h + m / 60 + s / 3600


def _dms(d: float, m: float, s: float = 0.0) -> float:
    sign = -1.0 if d < 0 else 1.0
    return sign * (abs(d) + m / 60 + s / 3600)


TARGETS: dict[str, Target] = {
    t.slug: t
    for t in (
        _t("moon", "Moon", "moon", body="moon", mag=-12.0, spoken="the Moon"),
        _t("saturn", "Saturn", "planet", body="saturn barycenter", mag=0.6),
        _t("jupiter", "Jupiter", "planet", body="jupiter barycenter", mag=-2.5),
        _t("mars", "Mars", "planet", body="mars", mag=1.0),
        _t("venus", "Venus", "planet", body="venus", mag=-4.5),
        _t("uranus", "Uranus", "planet", body="uranus barycenter", mag=5.7),
        _t(
            "m31",
            "Andromeda Galaxy",
            "galaxy",
            ra=_hms(0, 42, 44.3),
            dec=_dms(41, 16, 9),
            mag=3.4,
            ids=("M31", "NGC 224"),
            spoken="the Andromeda Galaxy",
        ),
        _t(
            "m33",
            "Triangulum Galaxy",
            "galaxy",
            ra=_hms(1, 33, 50.9),
            dec=_dms(30, 39, 37),
            mag=5.7,
            ids=("M33", "NGC 598"),
            spoken="the Triangulum Galaxy",
        ),
        _t(
            "m51",
            "Whirlpool Galaxy",
            "galaxy",
            ra=_hms(13, 29, 52.7),
            dec=_dms(47, 11, 43),
            mag=8.4,
            ids=("M51", "NGC 5194"),
            spoken="the Whirlpool Galaxy",
        ),
        _t(
            "m42",
            "Orion Nebula",
            "nebula",
            ra=_hms(5, 35, 17.3),
            dec=_dms(-5, 23, 28),
            mag=4.0,
            ids=("M42", "NGC 1976"),
            radio_ok=True,
            spoken="the Orion Nebula",
        ),
        _t(
            "m57",
            "Ring Nebula",
            "nebula",
            ra=_hms(18, 53, 35.1),
            dec=_dms(33, 1, 45),
            mag=8.8,
            ids=("M57", "NGC 6720"),
            radio_ok=False,
            spoken="the Ring Nebula",
        ),
        _t(
            "m1",
            "Crab Nebula",
            "supernova_remnant",
            ra=_hms(5, 34, 31.9),
            dec=_dms(22, 0, 52),
            mag=8.4,
            ids=("M1", "NGC 1952"),
            spoken="the Crab Nebula",
        ),
        _t(
            "m45",
            "Pleiades",
            "star_cluster",
            ra=_hms(3, 47, 24),
            dec=_dms(24, 7, 0),
            mag=1.6,
            ids=("M45",),
            spoken="the Pleiades",
        ),
        _t(
            "m13",
            "Hercules Cluster",
            "star_cluster",
            ra=_hms(16, 41, 41.2),
            dec=_dms(36, 27, 35),
            mag=5.8,
            ids=("M13", "NGC 6205"),
            spoken="the Hercules Cluster",
        ),
        _t(
            "sgr-a",
            "Sagittarius A*",
            "radio_source",
            ra=_hms(17, 45, 40.0),
            dec=_dms(-29, 0, 28),
            spoken="Sagittarius A star",
        ),
        _t("cas-a", "Cassiopeia A", "radio_source", ra=_hms(23, 23, 24), dec=_dms(58, 48, 54)),
        _t("cyg-a", "Cygnus A", "radio_source", ra=_hms(19, 59, 28.4), dec=_dms(40, 44, 2)),
        _t(
            "tmc-1",
            "Taurus Molecular Cloud",
            "molecular_cloud",
            ra=_hms(4, 41, 42),
            dec=_dms(25, 41, 27),
            ids=("TMC-1",),
            spoken="the Taurus Molecular Cloud",
        ),
        _t("betelgeuse", "Betelgeuse", "star", ra=_hms(5, 55, 10.3), dec=_dms(7, 24, 25), mag=0.5),
        _t("sirius", "Sirius", "star", ra=_hms(6, 45, 8.9), dec=_dms(-16, 42, 58), mag=-1.5),
        _t("polaris", "Polaris", "star", ra=_hms(2, 31, 49.1), dec=_dms(89, 15, 51), mag=2.0),
        _t(
            "omega-cen",
            "Omega Centauri",
            "star_cluster",
            ra=_hms(13, 26, 47.3),
            dec=_dms(-47, 28, 46),
            mag=3.9,
            ids=("NGC 5139",),
        ),
        _t(
            "lmc",
            "Large Magellanic Cloud",
            "galaxy",
            ra=_hms(5, 23, 34.5),
            dec=_dms(-69, 45, 22),
            mag=0.9,
            spoken="the Large Magellanic Cloud",
        ),
    )
}

# alias -> (target slug, kind). Lower-case, single spaces. Includes mis-hearings found by tools/probes.
TARGET_ALIASES: dict[str, tuple[str, str]] = {}


def _alias(slug: str, kind: str, *names: str) -> None:
    for n in names:
        TARGET_ALIASES[n] = (slug, kind)


for _slug, _target in TARGETS.items():
    _alias(_slug, "official", _target.name.lower())
_alias("moon", "common", "the moon", "luna")
_alias("m31", "common", "andromeda", "andromeda galaxy", "the andromeda galaxy", "and romeda", "andromeda nebula")
_alias("m33", "common", "triangulum", "pinwheel galaxy")
_alias("m51", "common", "whirlpool")
_alias("m42", "common", "orion", "orion nebula", "great orion nebula", "orion kl")
_alias("m42", "mishearing", "o ryan nebula", "o'ryan nebula", "oh ryan nebula")
_alias("m57", "common", "ring nebula", "the ring")
_alias("m1", "common", "crab", "the crab", "crab nebula")
_alias("m45", "common", "pleiades", "seven sisters", "the seven sisters", "subaru")
_alias("m45", "mishearing", "pleiadies", "plea dees", "pleiadees")
_alias("m13", "common", "hercules cluster", "great hercules cluster")
_alias(
    "sgr-a",
    "common",
    "sagittarius a star",
    "sagittarius a",
    "sag a star",
    "sgr a star",
    "sgr a",
    "galactic center",
    "galactic centre",
    "the galactic center",
    "the galactic centre",
)
_alias("cas-a", "common", "cassiopeia a", "cas a")
_alias("cyg-a", "common", "cygnus a")
_alias("tmc-1", "common", "taurus molecular cloud", "tmc 1", "tmc one")
_alias("betelgeuse", "mishearing", "beetle juice", "beetlejuice", "betel geuse")
_alias("uranus", "mishearing", "your anus", "you're anus")
_alias("omega-cen", "common", "omega centauri", "omega cen")
_alias("lmc", "common", "large magellanic cloud", "magellanic cloud")
for _slug, _target in TARGETS.items():
    for _cid in _target.catalogue_ids:
        _alias(_slug, "catalogue", _cid.lower(), _cid.lower().replace(" ", ""))

# Telescope aliases: exact phrases only; telescopes are never fuzzy-accepted.
TELESCOPE_ALIASES: dict[str, str] = {
    "optical refractor": "OPT-25",
    "refractor": "OPT-25",
    "optical": "OPT-25",
    "optical telescope": "OPT-25",
    "the refractor": "OPT-25",
    "lens telescope": "OPT-25",
    "small telescope": "OPT-25",
    "deep space reflector": "DSR-100",
    "deep-space reflector": "DSR-100",
    "reflector": "DSR-100",
    "deep space": "DSR-100",
    "mirror": "DSR-100",
    "big mirror": "DSR-100",
    "big telescope": "DSR-100",
    "the reflector": "DSR-100",
    "deep space telescope": "DSR-100",
    "sub millimeter radio dish": "SMM-12",
    "sub-millimeter radio dish": "SMM-12",
    "submillimeter radio dish": "SMM-12",
    "sub millimetre radio dish": "SMM-12",
    "radio dish": "SMM-12",
    "dish": "SMM-12",
    "radio": "SMM-12",
    "radio telescope": "SMM-12",
    "sub mm dish": "SMM-12",
    "submillimeter": "SMM-12",
    "sub millimeter": "SMM-12",
    "the dish": "SMM-12",
    "sub-mm dish": "SMM-12",
}

# Words that must never be fuzzy-matched to a target (they score high against real names).
STOP_WORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "please",
        "thanks",
        "thank",
        "you",
        "yes",
        "yeah",
        "yep",
        "no",
        "nope",
        "ok",
        "okay",
        "i",
        "me",
        "my",
        "we",
        "us",
        "it",
        "its",
        "is",
        "are",
        "was",
        "be",
        "am",
        "pm",
        "at",
        "on",
        "in",
        "for",
        "to",
        "from",
        "of",
        "by",
        "with",
        "until",
        "till",
        "tonight",
        "tomorrow",
        "today",
        "night",
        "evening",
        "morning",
        "book",
        "booking",
        "reserve",
        "want",
        "would",
        "like",
        "could",
        "can",
        "see",
        "look",
        "observe",
        "watch",
        "view",
        "point",
        "serious",
        "seriously",
        "just",
        "actually",
        "instead",
        "switch",
        "make",
        "change",
        "it's",
        "that",
        "this",
        "next",
        "sure",
        "right",
        "um",
        "uh",
        "hmm",
        "well",
        "so",
        "then",
        "also",
        "maybe",
        "time",
        "hour",
        "hours",
        "minutes",
        "later",
        "earlier",
        "telescope",
        "target",
        "cancel",
        "undo",
        "go",
        "back",
    ]
)

FORBIDDEN_TARGETS = {"sun": "the Sun", "the sun": "the Sun"}
