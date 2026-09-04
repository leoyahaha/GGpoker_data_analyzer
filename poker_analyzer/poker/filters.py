from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from poker.models import Hand, HandDataset

# Game types: regular cash (NLH*) vs Rush & Cash speed tables.
GAME_TYPE_NLH = "nlh"
GAME_TYPE_RUSH = "rush"
PRESET_GAME_TYPES: tuple[tuple[str, str], ...] = (
    (GAME_TYPE_NLH, "普通桌"),
    (GAME_TYPE_RUSH, "极速桌"),
)
_VALID_GAME_TYPES = {GAME_TYPE_NLH, GAME_TYPE_RUSH}

TABLE_FORMAT_6MAX = "6max"
TABLE_FORMAT_9MAX = "9max"
PRESET_TABLE_FORMATS: tuple[tuple[str, str], ...] = (
    (TABLE_FORMAT_6MAX, "6-max"),
    (TABLE_FORMAT_9MAX, "9-max"),
)
_VALID_TABLE_FORMATS = {TABLE_FORMAT_6MAX, TABLE_FORMAT_9MAX}

_STAKES_RE = re.compile(
    r"[$₮]?(?P<sb>\d+(?:\.\d+)?)\s*/\s*[$₮]?(?P<bb>\d+(?:\.\d+)?)",
)


def normalize_stakes(raw: str) -> str | None:
    """Normalize supported currency stakes to a plain ``small/big`` key."""
    if not raw:
        return None
    m = _STAKES_RE.search(raw.replace(" ", ""))
    if not m:
        return None
    sb = _fmt_level(float(m.group("sb")))
    bb = _fmt_level(float(m.group("bb")))
    return f"{sb}/{bb}"


def _fmt_level(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _stakes_sort_key(stakes: str) -> tuple[float, float]:
    parts = stakes.split("/", 1)
    if len(parts) != 2:
        return (0.0, 0.0)
    try:
        return (float(parts[1]), float(parts[0]))
    except ValueError:
        return (0.0, 0.0)


def sort_stakes(stakes: Iterable[str]) -> list[str]:
    return sorted({s for s in stakes if s}, key=_stakes_sort_key)


def _stakes_preset_items(stakes: Iterable[str]) -> list[dict[str, Any]]:
    return [
        {"id": s, "label": s.replace("/", "-"), "has_data": True}
        for s in sort_stakes(stakes)
    ]


@dataclass
class FilterSpec:
    """Analysis filter. table_format is required for analysis (6max or 9max)."""

    date_from: date | None = None
    date_to: date | None = None
    stakes: list[str] = field(default_factory=list)
    game_types: list[str] = field(default_factory=list)
    table_format: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> FilterSpec:
        if not payload:
            return cls()

        date_from = _parse_date(payload.get("date_from"))
        date_to = _parse_date(payload.get("date_to"))

        raw_stakes = payload.get("stakes") or []
        stakes: list[str] = []
        for item in raw_stakes:
            key = normalize_stakes(str(item))
            if key and key not in stakes:
                stakes.append(key)

        raw_game_types = payload.get("game_types") or []
        game_types: list[str] = []
        for item in raw_game_types:
            key = str(item).strip().lower()
            if key in _VALID_GAME_TYPES and key not in game_types:
                game_types.append(key)

        raw_table_format = str(payload.get("table_format") or "").strip().lower()
        table_format = raw_table_format if raw_table_format in _VALID_TABLE_FORMATS else None

        return cls(
            date_from=date_from,
            date_to=date_to,
            stakes=stakes,
            game_types=game_types,
            table_format=table_format,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "stakes": list(self.stakes),
            "game_types": list(self.game_types),
            "table_format": self.table_format,
        }


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    # Accept YYYY-MM-DD (HTML date input) or YYYY/MM/DD
    text = text.replace("/", "-")[:10]
    return date.fromisoformat(text)


_FILENAME_DATE_RE = re.compile(r"^GG(?P<y>\d{4})(?P<m>\d{2})(?P<d>\d{2})")
_FILENAME_STAKES_FMT_RE = re.compile(
    r" -\s*(?P<sb>\d+(?:\.\d+)?)\s*-\s*(?P<bb>\d+(?:\.\d+)?)\s*-\s*(?P<fmt>6max|9max)\.txt$",
    re.IGNORECASE,
)


def file_date_from_name(name: str) -> date | None:
    """Parse session date from GG export filename, e.g. GG20260822-....txt."""
    m = _FILENAME_DATE_RE.match(name)
    if not m:
        return None
    return date(int(m.group("y")), int(m.group("m")), int(m.group("d")))


def hand_file_date(hand: Hand) -> date | None:
    file_day = file_date_from_name(hand.source_file)
    if file_day is not None:
        return file_day
    # A recognized legacy filename stays filename-driven. Arbitrary CoinPoker
    # and renamed GG files fall back to their parsed header datetime.
    if _FILENAME_STAKES_FMT_RE.search(hand.source_file):
        return None
    return hand.datetime.date()


def hand_stakes_key(hand: Hand) -> str | None:
    return normalize_stakes(hand.stakes)


def hand_game_type(hand: Hand) -> str:
    """
    Classify table type from HH metadata.

    Rush & Cash (极速桌): table / filename contains RushAndCash.
    Otherwise treat as regular cash NLH (普通桌).
    """
    haystack = f"{hand.table_name} {hand.source_file}".lower()
    if "rushandcash" in haystack:
        return GAME_TYPE_RUSH
    return GAME_TYPE_NLH


def hand_table_format(hand: Hand) -> str:
    """Classify 6-max vs 9-max from parsed table size."""
    if hand.max_players >= 9:
        return TABLE_FORMAT_9MAX
    return TABLE_FORMAT_6MAX


def apply_filter(dataset: HandDataset, spec: FilterSpec | None) -> HandDataset:
    """Return a new dataset containing only hands matching the filter."""
    if spec is None:
        return HandDataset(hands=list(dataset.hands), source_label=dataset.source_label)

    stakes_set = {normalize_stakes(s) for s in spec.stakes if normalize_stakes(s)}
    game_type_set = {g for g in spec.game_types if g in _VALID_GAME_TYPES}

    filtered: list[Hand] = []
    for hand in dataset.hands:
        if spec.date_from or spec.date_to:
            file_day = hand_file_date(hand)
            if file_day is None:
                continue
            if spec.date_from and file_day < spec.date_from:
                continue
            if spec.date_to and file_day > spec.date_to:
                continue
        if stakes_set:
            key = hand_stakes_key(hand)
            if key not in stakes_set:
                continue
        if game_type_set:
            if hand_game_type(hand) not in game_type_set:
                continue
        if spec.table_format:
            if hand_table_format(hand) != spec.table_format:
                continue
        filtered.append(hand)

    return HandDataset(hands=filtered, source_label=dataset.source_label)


def available_stakes(hands: Iterable[Hand]) -> list[str]:
    found = {hand_stakes_key(h) for h in hands}
    found.discard(None)
    return sort_stakes(found)  # type: ignore[arg-type]


def available_game_types(hands: Iterable[Hand]) -> list[str]:
    found = {hand_game_type(h) for h in hands}
    return [gid for gid, _ in PRESET_GAME_TYPES if gid in found]


def available_table_formats(hands: Iterable[Hand]) -> list[str]:
    found = {hand_table_format(h) for h in hands}
    return [fid for fid, _ in PRESET_TABLE_FORMATS if fid in found]


def available_stakes_for_format(hands: Iterable[Hand], table_format: str) -> list[str]:
    subset = [h for h in hands if hand_table_format(h) == table_format]
    return available_stakes(subset)


def directory_filter_hints(directory: Path) -> dict[str, Any]:
    """Discover filters from known filenames, with per-hand content fallback."""
    formats: set[str] = set()
    stakes_by_format: dict[str, set[str]] = {TABLE_FORMAT_6MAX: set(), TABLE_FORMAT_9MAX: set()}
    if not directory.is_dir():
        return {"table_formats": formats, "stakes_by_format": stakes_by_format}

    file_dates: list[date] = []
    content_game_types: set[str] = set()
    used_filename_metadata = False
    for path in directory.glob("*.txt"):
        if not path.is_file():
            continue
        name = path.name
        day = file_date_from_name(name)
        if day:
            file_dates.append(day)

        # Keep the established GG filename path fast and unchanged.
        m = _FILENAME_STAKES_FMT_RE.search(name)
        if m:
            used_filename_metadata = True
            fmt = m.group("fmt").lower()
            formats.add(fmt)
            key = normalize_stakes(f"{m.group('sb')}/{m.group('bb')}")
            if key:
                stakes_by_format[fmt].add(key)
            continue

        from poker.parser import scan_hand_metadata

        try:
            hand_metadata = scan_hand_metadata(
                path.read_text(encoding="utf-8", errors="replace")
            )
        except OSError:
            hand_metadata = []

        found_content_format = False
        for metadata in hand_metadata:
            if day is None:
                file_dates.append(metadata.datetime.date())
            haystack = f"{metadata.table_name} {name}".lower()
            content_game_types.add(
                GAME_TYPE_RUSH if "rushandcash" in haystack else GAME_TYPE_NLH
            )
            if metadata.max_players <= 0:
                continue
            fmt = (
                TABLE_FORMAT_9MAX
                if metadata.max_players >= 9
                else TABLE_FORMAT_6MAX
            )
            found_content_format = True
            formats.add(fmt)
            key = normalize_stakes(metadata.stakes)
            if key:
                stakes_by_format[fmt].add(key)

        if found_content_format:
            continue

        # Retain partial legacy filename hints only when content has no table.
        lower = name.lower()
        if "9max" in lower:
            formats.add(TABLE_FORMAT_9MAX)
        elif "6max" in lower:
            formats.add(TABLE_FORMAT_6MAX)

    file_dates.sort()
    return {
        "table_formats": formats,
        "stakes_by_format": {k: sort_stakes(v) for k, v in stakes_by_format.items()},
        "file_dates": file_dates,
        "content_game_types": content_game_types,
        "used_filename_metadata": used_filename_metadata,
    }


def filter_options_from_directory(directory: Path) -> dict[str, Any]:
    """Filter UI options from filename scan (before hands are parsed)."""
    hints = directory_filter_hints(directory)
    present_formats = hints["table_formats"]
    stakes_by_format = hints["stakes_by_format"]
    file_dates = hints.get("file_dates") or []
    all_stakes: set[str] = set()
    for stakes in stakes_by_format.values():
        all_stakes.update(stakes)
    sorted_stakes = sort_stakes(all_stakes)
    content_game_types = set(hints.get("content_game_types") or [])
    if hints.get("used_filename_metadata") or not content_game_types:
        present_game_types = {gid for gid, _ in PRESET_GAME_TYPES}
    else:
        present_game_types = content_game_types

    return {
        "date_from": file_dates[0].isoformat() if file_dates else None,
        "date_to": file_dates[-1].isoformat() if file_dates else None,
        "stakes_presets": _stakes_preset_items(sorted_stakes),
        "stakes_in_data": sorted_stakes,
        "game_types_presets": [
            {"id": gid, "label": label, "has_data": gid in present_game_types}
            for gid, label in PRESET_GAME_TYPES
        ],
        "game_types_in_data": [
            gid for gid, _ in PRESET_GAME_TYPES if gid in present_game_types
        ],
        "table_formats_presets": [
            {
                "id": fid,
                "label": label,
                "has_data": fid in present_formats,
            }
            for fid, label in PRESET_TABLE_FORMATS
        ],
        "table_formats_in_data": [fid for fid, _ in PRESET_TABLE_FORMATS if fid in present_formats],
        "stakes_by_format": stakes_by_format,
    }


def empty_filter_options() -> dict[str, Any]:
    """Preset filter UI before any hand histories are loaded."""
    return {
        "date_from": None,
        "date_to": None,
        "stakes_presets": [],
        "stakes_in_data": [],
        "game_types_presets": [
            {"id": gid, "label": label, "has_data": False}
            for gid, label in PRESET_GAME_TYPES
        ],
        "game_types_in_data": [],
        "table_formats_presets": [
            {"id": fid, "label": label, "has_data": False}
            for fid, label in PRESET_TABLE_FORMATS
        ],
        "table_formats_in_data": [],
        "stakes_by_format": {fid: [] for fid, _ in PRESET_TABLE_FORMATS},
    }


def filter_options(dataset: HandDataset) -> dict[str, Any]:
    hands = dataset.sorted_hands()
    present = available_stakes(hands)
    present_game_types = set(available_game_types(hands))
    present_table_formats = set(available_table_formats(hands))
    stakes_by_format = {
        fid: available_stakes_for_format(hands, fid) for fid, _ in PRESET_TABLE_FORMATS
    }
    file_dates = sorted({d for h in hands if (d := hand_file_date(h))})
    return {
        "date_from": file_dates[0].isoformat() if file_dates else None,
        "date_to": file_dates[-1].isoformat() if file_dates else None,
        "stakes_presets": _stakes_preset_items(present),
        "stakes_in_data": present,
        "game_types_presets": [
            {
                "id": gid,
                "label": label,
                "has_data": gid in present_game_types,
            }
            for gid, label in PRESET_GAME_TYPES
        ],
        "game_types_in_data": available_game_types(hands),
        "table_formats_presets": [
            {
                "id": fid,
                "label": label,
                "has_data": fid in present_table_formats,
            }
            for fid, label in PRESET_TABLE_FORMATS
        ],
        "table_formats_in_data": available_table_formats(hands),
        "stakes_by_format": stakes_by_format,
    }
