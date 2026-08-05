"""Letter-kind classification for word-composition scale factors.

Each character is classified into one or more *kinds* that describe its
vertical extent relative to the baseline:

- ``CAPITAL``   — sits on the baseline, reaches cap height (ratio 1.0).
- ``ASCENDER``  — sits on the baseline, reaches above x-height (0.8).
- ``DESCENDER`` — sits at x-height, extends below baseline (depth 0.3).
- ``REGULAR``   — sits on the baseline, reaches x-height (0.6).
- ``SPECIAL``   — floats near cap height (degree sign, superscripts) (1.0).
- ``FLOATING``  — punctuation/diacritics above the baseline; excluded from
  the word scale factor (already handled by the baseline logic).
- ``UNKNOWN``   — unclassifiable; excluded from the scale factor.

A few glyphs span two kinds (e.g. Cyrillic ``ф`` is both an ascender and a
descender), so :func:`letter_kinds` returns a *set* of kinds.

A capital+ascender (uppercase letter in the ascenders set, e.g. Cyrillic
Ё, Й) yields ``{CAPITAL, ASCENDER}``; its top normalizes to a reference
line *above* the capitals line (see :data:`CAPITAL_ASCENDER_KEY`).

The per-word scale factor ``F`` (see ``Project.word_scale_factor``) is
derived from the union of kinds present in a word: it expresses the word's
vertical extent as a multiple of the cap height, so that dividing the
word bbox height by ``F`` recovers the (composition-independent) cap
height in pixels.
"""
from __future__ import annotations

import enum
from typing import Dict, Iterable, Optional, Set, Tuple


class Kind(str, enum.Enum):
    """Vertical-extent kind of a character, relative to the baseline."""

    CAPITAL = "capital"
    ASCENDER = "ascender"
    DESCENDER = "descender"
    REGULAR = "regular"
    SPECIAL = "special"
    FLOATING = "floating"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Explicit character sets (Cyrillic + Latin)
# ---------------------------------------------------------------------------

# Default ascenders: lowercase letters whose top rises above the x-height,
# plus uppercase capital+ascender letters (Ё, Й) whose top rises above the
# capitals line. Cyrillic defaults reflect a typical Russian handwriting
# font where б в д ё й ф rise above the x-height and Ё Й rise above cap
# height. These are overridable per-project (see ``Project.ascenders``).
DEFAULT_ASCENDERS = "бвдёйЙЁфbdfhkltßƒ"
# Default descenders: lowercase letters whose bottom drops below the
# baseline, plus uppercase capital+descender letters (Д, Ц, Щ). Cyrillic
# defaults: у р ц щ ф. Overridable per-project
# (see ``Project.descenders``).
DEFAULT_DESCENDERS = "ДЦЩурцщфgjpqyßƒþ"
# ф is both ascender and descender; it appears in both default sets above.

# Module-level sets used by the no-argument ``letter_kinds`` call. Kept in
# sync with the defaults; callers needing project-specific classification
# pass their own sets explicitly.
_ASCENDERS = set(DEFAULT_ASCENDERS)
_DESCENDERS = set(DEFAULT_DESCENDERS)

# Special: glyphs that float near cap height (degree, superscript digits,
# ordinal indicator). They occupy the cap-height band like capitals.
_SPECIAL = set("°") | set("⁰¹²³⁴⁵⁶⁷⁸⁹") | set("ªº")

# Floating punctuation/diacritics — mirrors models._FLOATING_CHARS. Kept in
# sync here so classification is self-contained. These are excluded from
# the word scale factor (they do not define the word's vertical extent).
_FLOATING = set('"“”‘’´`ˆ¨˜˘˙˚¸˝„‟′″‴‵‶‷‹›«»')


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def letter_kinds(
    char: str,
    ascenders: Optional[Set[str]] = None,
    descenders: Optional[Set[str]] = None,
) -> Set[Kind]:
    """Classify a single character into a set of :class:`Kind` values.

    Most characters yield a single kind; a few (e.g. Cyrillic ``ф``) yield
    two (ascender + descender). An empty/whitespace char returns an empty
    set.

    ``ascenders`` / ``descenders`` override the module-default sets, allowing
    per-project classification (see ``Project.ascenders`` /
    ``Project.descenders``). When omitted, the defaults
    (:data:`DEFAULT_ASCENDERS` / :data:`DEFAULT_DESCENDERS`) are used.

    An uppercase letter (Unicode ``Lu``) in the ascenders set yields
    ``{CAPITAL, ASCENDER}`` — a capital+ascender whose top normalizes
    above the capitals line (see :data:`CAPITAL_ASCENDER_KEY`). An
    uppercase letter in the descenders set yields ``{CAPITAL, DESCENDER}``
    (e.g. Cyrillic Ц, Щ, Д).
    """
    if not char:
        return set()

    asc = ascenders if ascenders is not None else _ASCENDERS
    desc = descenders if descenders is not None else _DESCENDERS

    kinds: Set[Kind] = set()

    if char in _FLOATING:
        kinds.add(Kind.FLOATING)
        return kinds

    if char in _SPECIAL:
        kinds.add(Kind.SPECIAL)
        return kinds

    if char in asc:
        kinds.add(Kind.ASCENDER)
    if char in desc:
        kinds.add(Kind.DESCENDER)

    # Unicode-category fallback. Always run it so that uppercase letters
    # that are also descenders (e.g. Cyrillic Ц, Щ, Д) get CAPITAL added on
    # top of DESCENDER — they are capital+descender — and uppercase letters
    # that are also ascenders (e.g. Cyrillic Ё, Й) get CAPITAL added on top
    # of ASCENDER — they are capital+ascender. For lowercase letters
    # already classified via the explicit sets (ascender/descender), do NOT
    # add REGULAR (they are not regular). REGULAR is only assigned to
    # lowercase letters not in either explicit set.
    cat = _unicode_category(char)
    if cat == "Lu":
        kinds.add(Kind.CAPITAL)
    elif cat == "Nd":
        # Digits occupy the cap-height band.
        kinds.add(Kind.CAPITAL)
    elif cat == "Lt":
        # Titlecase (e.g. ǅ) — treat as capital.
        kinds.add(Kind.CAPITAL)
    elif cat == "Lm":
        kinds.add(Kind.CAPITAL)
    elif cat == "Ll":
        # Only assign REGULAR if the letter wasn't already classified as
        # ascender/descender via the explicit sets.
        if not kinds:
            kinds.add(Kind.REGULAR)
    else:
        # Non-letter (punctuation, symbol, …) not in the explicit sets.
        if not kinds:
            kinds.add(Kind.UNKNOWN)

    return kinds


def _unicode_category(char: str) -> str:
    """Return the major Unicode category for ``char`` (e.g. ``Lu``, ``Nd``)."""
    import unicodedata

    try:
        return unicodedata.category(char)
    except ValueError:
        return "Cn"  # unassigned


# ---------------------------------------------------------------------------
# Per-word aggregation
# ---------------------------------------------------------------------------

# Kinds that contribute to the word's vertical extent (i.e. are excluded
# from the scale factor). FLOATING and UNKNOWN do not anchor the extent.
_EXTENT_KINDS = {
    Kind.CAPITAL,
    Kind.ASCENDER,
    Kind.DESCENDER,
    Kind.REGULAR,
    Kind.SPECIAL,
}

# Synthetic key for the capital+ascender combination (uppercase letters in
# the ascenders set, e.g. Cyrillic Ё, Й). Not a ``Kind`` enum member — it
# is a string ratio key stored alongside the kind-based ratios and emitted
# into the ``word_kinds`` tuple so the word scale factor picks up the
# higher-than-capitals top extent.
CAPITAL_ASCENDER_KEY = "capital_ascender"

# Default per-kind vertical extent ratios, cap-relative (cap = 1.0).
# ``top`` is the height above the baseline; ``bottom`` is the depth below
# it. A word's scale factor F = max(top) + max(bottom) over the kinds
# present. These defaults are overridden by ``Project.calibrate_kind_ratios``
# once enough instances have been committed.
DEFAULT_KIND_RATIOS: Dict[str, float] = {
    Kind.CAPITAL.value: 1.0,    # top
    Kind.ASCENDER.value: 0.8,   # top
    Kind.REGULAR.value: 0.6,    # top
    Kind.SPECIAL.value: 1.0,    # top (degree, superscripts)
    Kind.DESCENDER.value: 0.3,  # bottom (depth)
    CAPITAL_ASCENDER_KEY: 1.1,  # top, above the capitals line (Ё, Й, …)
}

# Which ratios represent a *top* extent vs a *bottom* extent.
_TOP_KINDS = {Kind.CAPITAL, Kind.ASCENDER, Kind.REGULAR, Kind.SPECIAL}
_BOTTOM_KINDS = {Kind.DESCENDER}
# String ratio keys (not Kind enum values) that represent a *top* extent.
_TOP_RATIO_KEYS = {CAPITAL_ASCENDER_KEY}


def word_scale_factor(
    kinds: Tuple, ratios: Dict[str, float]
) -> float:
    """Compute the per-word scale factor ``F`` from present kinds + ratios.

    ``F`` expresses the word's vertical extent as a multiple of the cap
    height: ``F = max(top_ratio) + max(bottom_ratio)`` over the kinds
    present in the word. Dividing the word bbox height by ``F`` recovers
    the (composition-independent) cap height in pixels.

    ``kinds`` may contain :class:`Kind` enum values and/or synthetic
    string keys (e.g. :data:`CAPITAL_ASCENDER_KEY`) emitted by
    :func:`word_kinds_from_chars` for capital+ascender glyphs.

    Returns 1.0 if no extent kinds are present (e.g. a floating-only word)
    so the scale degrades gracefully to the raw word-height mapping.
    """
    if not kinds:
        return 1.0
    # Normalize each entry to a string key. Entries may be Kind enum
    # values (from word_kinds_from_chars) or plain strings (from a
    # deserialized DB word_kinds tuple, since Kind is a str enum and JSON
    # stores the string value). Synthetic keys like capital_ascender are
    # already strings.
    top_keys = {k.value for k in _TOP_KINDS}
    bot_keys = {k.value for k in _BOTTOM_KINDS}
    tops = []
    bots = []
    for k in kinds:
        key = k.value if isinstance(k, Kind) else str(k)
        if key in top_keys:
            tops.append(ratios.get(key, DEFAULT_KIND_RATIOS.get(key, 0.0)))
        elif key in bot_keys:
            bots.append(ratios.get(key, DEFAULT_KIND_RATIOS.get(key, 0.0)))
        elif key in _TOP_RATIO_KEYS:
            # Synthetic string keys (e.g. capital_ascender) — top extents
            # above the plain kind lines.
            tops.append(ratios.get(key, DEFAULT_KIND_RATIOS.get(key, 0.0)))
    top = max(tops) if tops else 0.0
    bot = max(bots) if bots else 0.0
    f = top + bot
    return f if f > 0 else 1.0


def word_kinds_from_chars(
    chars: Iterable[str],
    ascenders: Optional[Set[str]] = None,
    descenders: Optional[Set[str]] = None,
) -> Tuple:
    """Compute the sorted set of extent kinds present in a word.

    Floating and unknown characters are excluded (they do not define the
    word's vertical extent). The result is a stable, hashable tuple sorted
    by the :class:`Kind` enum order, suitable for storing on a
    ``GlyphInstance``. When any character is a capital+ascender (uppercase
    letter in the ascenders set, e.g. Ё, Й), the synthetic
    :data:`CAPITAL_ASCENDER_KEY` string is appended after the ``Kind``
    values so the word scale factor picks up the higher top extent.

    ``ascenders`` / ``descenders`` override the module-default sets for
    per-project classification.
    """
    present: Set[Kind] = set()
    has_capital_ascender = False
    for ch in chars:
        ch_kinds = letter_kinds(ch, ascenders, descenders)
        # Detect the capital+ascender combination (uppercase letter in the
        # ascenders set, e.g. Ё, Й): both CAPITAL and ASCENDER present for
        # the same character.
        if Kind.CAPITAL in ch_kinds and Kind.ASCENDER in ch_kinds:
            has_capital_ascender = True
        for k in ch_kinds:
            if k in _EXTENT_KINDS:
                present.add(k)
    # Sort Kind enum values by their enum order; append synthetic string
    # keys after, so the tuple stays stable and hashable.
    result = tuple(sorted(present, key=lambda k: k.value))
    if has_capital_ascender:
        result = result + (CAPITAL_ASCENDER_KEY,)
    return result


def word_kinds_labels(kinds: Iterable) -> str:
    """Human-readable comma-separated labels for a word-kinds tuple.

    Accepts either :class:`Kind` values or their string values (the
    serialized form stored on ``GlyphInstance.word_kinds``), including
    synthetic string keys like :data:`CAPITAL_ASCENDER_KEY`.
    """
    if not kinds:
        return "—"
    labels = []
    for k in kinds:
        labels.append(k.value if isinstance(k, Kind) else str(k))
    return ", ".join(labels)


# ---------------------------------------------------------------------------
# Committability — does a word have a reliable baseline anchor?
# ---------------------------------------------------------------------------

# Kinds whose bbox bottom coincides with the baseline (non-descenders).
# A word must contain at least one such letter for the baseline to be
# detectable; descender-only or floating-only words cannot anchor it.
# SPECIAL (degree sign, superscripts) is excluded: those are symbols, not
# letters, so they do not by themselves make a word committable.
_NON_DESCENDER_KINDS = {
    Kind.CAPITAL,
    Kind.ASCENDER,
    Kind.REGULAR,
}


def word_is_committable(
    chars: Iterable[str],
    ascenders: Optional[Set[str]] = None,
    descenders: Optional[Set[str]] = None,
) -> bool:
    """True if *chars* contain at least one non-descender letter.

    Descender-only words (e.g. ``уру``), floating/punctuation-only words,
    and special-symbol-only words (e.g. ``°``) cannot anchor a baseline:
    every glyph bottom sits below (or above) the true baseline, so the
    per-word baseline estimate would be wrong. Such words are *disallowed*
    for committing until the user edits in a non-descender letter.

    ``ascenders`` / ``descenders`` override the module-default sets for
    per-project classification.
    """
    for ch in chars:
        kinds = letter_kinds(ch, ascenders, descenders)
        if kinds & _NON_DESCENDER_KINDS:
            return True
    return False