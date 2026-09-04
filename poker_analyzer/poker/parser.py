from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from poker.models import Action, Hand


@dataclass(frozen=True)
class HandMetadata:
    """Header/table fields needed for lightweight filter discovery."""

    hand_id: str
    site: str
    game: str
    datetime: datetime
    stakes: str
    table_name: str
    max_players: int


_AMOUNT = r"\d+(?:\.\d+)?"
_CURRENCY = r"[$₮]"
_HAND_HEADER_RE = re.compile(
    r"^(?P<label>CoinPoker Hand|Poker Hand) #(?P<hand_id>[^:\s]+):\s+"
)


def _money_pattern(group: str) -> str:
    return rf"{_CURRENCY}\s*(?P<{group}>{_AMOUNT})"


def _extract_trailing_stakes(text: str) -> tuple[str, str] | None:
    """Split ``game (stakes...)`` where stakes may contain nested ``(...)``."""
    stripped = text.rstrip()
    if not stripped.endswith(")"):
        return None
    depth = 0
    start = -1
    for i in range(len(stripped) - 1, -1, -1):
        ch = stripped[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            depth -= 1
            if depth == 0:
                start = i
                break
    if start < 0:
        return None
    game = stripped[:start].strip()
    stakes = stripped[start + 1 : -1]
    return game, stakes


def _parse_hand_header(line: str) -> dict[str, str] | None:
    """Parse a supported site header, including nested GG ante stakes."""
    prefix = _HAND_HEADER_RE.match(line)
    if not prefix:
        return None
    tail = line[prefix.end() :]
    site = "coinpoker" if prefix.group("label") == "CoinPoker Hand" else "ggpoker"
    dt_prefix = r"\s+" if site == "coinpoker" else r"\s+-\s+"
    dt_m = re.search(
        dt_prefix
        + r"(?P<dt>\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})"
        + r"(?:\s+(?P<timezone>\S+))?\s*$",
        tail,
    )
    if not dt_m:
        return None
    parsed = _extract_trailing_stakes(tail[: dt_m.start()].strip())
    if not parsed:
        return None
    game, stakes = parsed
    return {
        "hand_id": prefix.group("hand_id"),
        "game": game,
        "stakes": stakes,
        "dt": dt_m.group("dt"),
        "site": site,
        "timezone": dt_m.group("timezone") or "",
    }

TABLE_RE = re.compile(
    r"^Table '(?P<table>[^']+)'\s+(?P<max>\d+)-max"
    r"(?:\s+Seat #(?P<button>\d+) is the button)?",
)

SEAT_RE = re.compile(
    rf"^Seat (?P<seat>\d+):\s+(?P<name>\S+)\s+"
    rf"\({_money_pattern('stack')} in chips\)",
)

DEALT_HERO_RE = re.compile(r"^Dealt to Hero \[(?P<cards>[^\]]+)\]")

# Money actions for any player; we filter Hero separately.
ACTION_RE = re.compile(
    r"^(?P<name>\S+):\s+"
    r"(?P<action>posts small blind|posts big blind|posts (?:the )?ante|"
    r"posts small & big blinds|posts|"
    r"bets|calls|raises|checks|folds|shows|mucks|ALLIN)"
    r"(?P<rest>.*)$"
)

RAISES_RE = re.compile(
    rf"raises\s+{_money_pattern('by')}\s+to\s+{_money_pattern('to')}"
)
BETS_CALLS_RE = re.compile(rf"(?:bets|calls)\s+{_money_pattern('amount')}")
POSTS_RE = re.compile(
    rf"posts(?: small blind| big blind| (?:the )?ante| small & big blinds)?\s+"
    rf"{_money_pattern('amount')}"
)
# Rare: "Hero: posts $0.05" style dead blinds / extras
POSTS_GENERIC_RE = re.compile(rf"posts\s+{_money_pattern('amount')}")
ALLIN_RE = re.compile(rf"ALLIN\s+{_money_pattern('amount')}")

RETURNED_RE = re.compile(
    rf"^Uncalled bet \({_money_pattern('amount')}\) returned to (?P<name>\S+)"
)
DIRECT_RETURN_RE = re.compile(
    rf"^(?P<name>\S+):\s+RETURN\s+{_money_pattern('amount')}"
)
COLLECTED_RE = re.compile(
    rf"^(?P<name>\S+) collected {_money_pattern('amount')} from "
    r"(?:pot|main pot|side pot(?:-\d+)?)"
)

SUMMARY_POT_RE = re.compile(
    rf"\bTotal pot\s+{_money_pattern('pot')}", re.IGNORECASE
)
SUMMARY_FEE_RES = {
    field: re.compile(rf"\b{label}\s+{_money_pattern(field)}", re.IGNORECASE)
    for field, label in (
        ("rake", "Rake"),
        ("jackpot", "Jackpot"),
        ("bingo", "Bingo"),
        ("fortune", "Fortune"),
        ("tax", "Tax"),
    )
}
SPLASH_FEE_RE = re.compile(
    rf"\bSplash Fee\s+{_money_pattern('splash_fee')}", re.IGNORECASE
)

_STREET_MARKERS = {
    "*** HOLE CARDS ***": "preflop",
    "*** FLOP ***": "flop",
    "*** TURN ***": "turn",
    "*** RIVER ***": "river",
}

SHOW_CARDS_RE = re.compile(
    r"\[(?P<cards>[2-9TJQKA][cdhs]\s+[2-9TJQKA][cdhs])\]",
    re.IGNORECASE,
)
SEAT_SHOWED_RE = re.compile(
    r"^Seat \d+:\s+(?P<name>\S+).*?\b(?:showed|mucked) \[(?P<cards>[^\]]+)\]",
    re.IGNORECASE,
)

FLOP_LINE_RE = re.compile(r"^\*\*\* (?:FIRST |SECOND )?FLOP \*\*\* \[(?P<cards>[^\]]+)\]")
TURN_LINE_RE = re.compile(r"^\*\*\* (?:FIRST |SECOND )?TURN \*\*\* \[(?P<prev>[^\]]+)\] \[(?P<card>[^\]]+)\]")
RIVER_LINE_RE = re.compile(r"^\*\*\* (?:FIRST |SECOND )?RIVER \*\*\* \[(?P<prev>[^\]]+)\] \[(?P<card>[^\]]+)\]")
BOARD_SUMMARY_RE = re.compile(r"^Board \[(?P<cards>[^\]]+)\]")


def _parse_card_tokens(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split() if part.strip())


def _money(value: str | None) -> float:
    if not value:
        return 0.0
    return float(value)


def _split_hands(text: str) -> list[str]:
    parts = re.split(r"(?=^(?:CoinPoker Hand|Poker Hand) #)", text, flags=re.MULTILINE)
    blocks: list[str] = []
    for part in parts:
        block = part.strip()
        if not _HAND_HEADER_RE.match(block):
            continue
        header_line = block.split("\n", 1)[0]
        if _parse_hand_header(header_line) is None:
            continue
        blocks.append(block)
    return blocks


def scan_hand_metadata(text: str) -> list[HandMetadata]:
    """Read header/table metadata for every supported hand without parsing actions."""
    metadata: list[HandMetadata] = []
    for block in _split_hands(text):
        lines = block.splitlines()
        header = _parse_hand_header(lines[0])
        if header is None:
            continue
        table_name = ""
        max_players = 0
        for line in lines[1:]:
            table = TABLE_RE.match(line)
            if table:
                table_name = table.group("table")
                max_players = int(table.group("max"))
                break
        metadata.append(
            HandMetadata(
                hand_id=header["hand_id"],
                site=header["site"],
                game=header["game"],
                datetime=datetime.strptime(header["dt"], "%Y/%m/%d %H:%M:%S"),
                stakes=header["stakes"],
                table_name=table_name,
                max_players=max_players,
            )
        )
    return metadata


def parse_hand(raw: str, source_file: str = "") -> Hand | None:
    """Parse a single hand history block into a Hand model."""
    non_empty = [ln.rstrip() for ln in raw.splitlines()]

    header = None
    for ln in non_empty:
        parsed_header = _parse_hand_header(ln)
        if parsed_header:
            header = parsed_header
            break
    if not header:
        return None

    hand_id = header["hand_id"]
    stakes = header["stakes"]
    dt = datetime.strptime(header["dt"], "%Y/%m/%d %H:%M:%S")

    table_name = ""
    max_players = 0
    button_seat: int | None = None
    hero_seat: int | None = None
    hero_cards: str | None = None
    seat_names: dict[int, str] = {}
    seat_stacks: dict[int, float] = {}

    hero_invested = 0.0
    hero_returned = 0.0
    hero_collected = 0.0
    total_collected = 0.0
    street_contrib: dict[str, float] = {}
    went_to_flop = False
    flop_cards: tuple[str, ...] = ()
    hero_vpip = False
    in_summary = False
    street = "preflop"
    pot = 0.0
    actions: list[Action] = []
    shown_cards: dict[str, tuple[str, ...]] = {}

    total_pot = splash_fee = 0.0
    rake = jackpot = bingo = fortune = tax = 0.0
    summary_lines: list[str] = []

    def reset_street_contrib() -> None:
        street_contrib.clear()

    for ln in non_empty:
        if not ln.strip():
            continue

        if ln.startswith("Table "):
            m = TABLE_RE.match(ln)
            if m:
                table_name = m.group("table")
                max_players = int(m.group("max"))
                if m.group("button"):
                    button_seat = int(m.group("button"))
            continue

        seat_m = SEAT_RE.match(ln)
        if seat_m:
            seat_no = int(seat_m.group("seat"))
            name = seat_m.group("name")
            seat_names[seat_no] = name
            seat_stacks[seat_no] = _money(seat_m.group("stack"))
            if name == "Hero":
                hero_seat = seat_no
            continue

        dealt = DEALT_HERO_RE.match(ln)
        if dealt:
            hero_cards = dealt.group("cards")
            continue

        flop_line = FLOP_LINE_RE.match(ln)
        if flop_line:
            parsed = _parse_card_tokens(flop_line.group("cards"))
            if not flop_cards:
                flop_cards = parsed
            went_to_flop = True
            reset_street_contrib()
            street = "flop"
            continue

        turn_line = TURN_LINE_RE.match(ln)
        if turn_line:
            reset_street_contrib()
            street = "turn"
            continue

        river_line = RIVER_LINE_RE.match(ln)
        if river_line:
            reset_street_contrib()
            street = "river"
            continue

        street_hit = False
        for marker, street_name in _STREET_MARKERS.items():
            if ln.startswith(marker):
                if street_name == "flop":
                    went_to_flop = True
                if street_name != "preflop":
                    # Blinds are posted before HOLE CARDS and count toward preflop;
                    # only reset contribution when entering a new postflop street.
                    reset_street_contrib()
                street = street_name
                street_hit = True
                break
        if street_hit:
            continue

        if ln.startswith("*** SHOWDOWN ***"):
            continue
        if ln.startswith("*** SUMMARY ***"):
            in_summary = True
            continue

        if in_summary:
            summary_lines.append(ln)
            board_m = BOARD_SUMMARY_RE.match(ln)
            if board_m and not flop_cards:
                parsed = _parse_card_tokens(board_m.group("cards"))
                if len(parsed) >= 3:
                    flop_cards = parsed[:3]
                    went_to_flop = True
            showed = SEAT_SHOWED_RE.match(ln)
            if showed:
                name = showed.group("name")
                cards = _parse_card_tokens(showed.group("cards"))
                if len(cards) >= 2 and name not in shown_cards:
                    shown_cards[name] = cards[:2]
            pot_m = SUMMARY_POT_RE.search(ln)
            if pot_m:
                total_pot = _money(pot_m.group("pot"))
            for field, field_re in SUMMARY_FEE_RES.items():
                field_m = field_re.search(ln)
                if not field_m:
                    continue
                value = _money(field_m.group(field))
                if field == "rake":
                    rake = value
                elif field == "jackpot":
                    jackpot = value
                elif field == "bingo":
                    bingo = value
                elif field == "fortune":
                    fortune = value
                else:
                    tax = value
            splash_m = SPLASH_FEE_RE.search(ln)
            if splash_m:
                splash_fee = _money(splash_m.group("splash_fee"))
            continue

        returned = RETURNED_RE.match(ln) or DIRECT_RETURN_RE.match(ln)
        if returned:
            name = returned.group("name")
            amt = _money(returned.group("amount"))
            pot = max(0.0, round(pot - amt, 6))
            street_contrib[name] = max(0.0, round(street_contrib.get(name, 0.0) - amt, 6))
            if name == "Hero":
                hero_returned += amt
            continue

        collected = COLLECTED_RE.match(ln)
        if collected:
            amt = _money(collected.group("amount"))
            total_collected += amt
            if collected.group("name") == "Hero":
                hero_collected += amt
            continue

        action = ACTION_RE.match(ln)
        if not action:
            continue

        name = action.group("name")
        act = action.group("action")
        rest = action.group("rest") or ""
        is_hero = name == "Hero"
        pot_before = pot

        # Posts (blinds/antes) — not VPIP
        if act.startswith("posts"):
            m = POSTS_RE.search(act + rest) or POSTS_GENERIC_RE.search(act + rest)
            if m:
                amt = _money(m.group("amount"))
                pot = round(pot + amt, 6)
                # Ante is dead money outside betting "to $X" totals (GG 9-max).
                if "ante" not in act:
                    street_contrib[name] = round(street_contrib.get(name, 0.0) + amt, 6)
                actions.append(
                    Action(
                        street=street,
                        player=name,
                        action=act,
                        amount=amt,
                        pot_before=pot_before,
                        is_hero=is_hero,
                    )
                )
                if is_hero:
                    hero_invested += amt
            continue

        if act == "raises":
            m = RAISES_RE.search(act + rest)
            if m:
                to_amt = _money(m.group("to"))
                prev = street_contrib.get(name, 0.0)
                add = round(to_amt - prev, 6)
                if add < 0:
                    add = _money(m.group("by"))
                    to_amt = round(prev + add, 6)
                pot = round(pot + add, 6)
                street_contrib[name] = to_amt
                actions.append(
                    Action(
                        street=street,
                        player=name,
                        action="raise",
                        amount=add,
                        to_amount=to_amt,
                        pot_before=pot_before,
                        is_hero=is_hero,
                    )
                )
                if is_hero:
                    hero_invested += add
                    hero_vpip = True
            continue

        if act in ("bets", "calls"):
            m = BETS_CALLS_RE.search(act + rest)
            if m:
                amt = _money(m.group("amount"))
                pot = round(pot + amt, 6)
                street_contrib[name] = round(street_contrib.get(name, 0.0) + amt, 6)
                kind = "bet" if act == "bets" else "call"
                actions.append(
                    Action(
                        street=street,
                        player=name,
                        action=kind,
                        amount=amt,
                        pot_before=pot_before,
                        is_hero=is_hero,
                    )
                )
                if is_hero:
                    hero_invested += amt
                    hero_vpip = True
            continue

        if act == "ALLIN":
            m = ALLIN_RE.search(act + rest)
            if m:
                amt = _money(m.group("amount"))
                prev = street_contrib.get(name, 0.0)
                highest = max(street_contrib.values(), default=0.0)
                to_amt = round(prev + amt, 6)
                if highest <= 0:
                    kind = "bet"
                elif to_amt <= highest:
                    kind = "call"
                else:
                    kind = "raise"
                pot = round(pot + amt, 6)
                street_contrib[name] = to_amt
                actions.append(
                    Action(
                        street=street,
                        player=name,
                        action=kind,
                        amount=amt,
                        to_amount=to_amt if kind == "raise" else 0.0,
                        pot_before=pot_before,
                        is_hero=is_hero,
                    )
                )
                if is_hero:
                    hero_invested += amt
                    hero_vpip = True
            continue

        if act in ("checks", "folds", "shows", "mucks"):
            kind = {
                "checks": "check",
                "folds": "fold",
                "shows": "show",
                "mucks": "muck",
            }[act]
            if kind in ("show", "muck"):
                cards_m = SHOW_CARDS_RE.search(rest)
                if cards_m:
                    cards = _parse_card_tokens(cards_m.group("cards"))
                    if len(cards) >= 2:
                        shown_cards[name] = cards[:2]
            actions.append(
                Action(
                    street=street,
                    player=name,
                    action=kind,
                    pot_before=pot_before,
                    is_hero=is_hero,
                )
            )
            continue

    extra = {"total_collected": round(total_collected, 6)}
    if header["site"] == "coinpoker":
        # Site-only metadata lives in ``extra`` so the normalized public model
        # and existing GG values stay unchanged.
        extra.update(
            {
                "site": header["site"],
                "game": header["game"],
                "currency": "₮",
                "timezone": header["timezone"],
                "seat_stacks": seat_stacks,
                "splash_fee": splash_fee,
                "additional_fees": splash_fee,
            }
        )

    return Hand(
        hand_id=hand_id,
        datetime=dt,
        table_name=table_name,
        stakes=stakes,
        max_players=max_players,
        hero_seat=hero_seat,
        hero_cards=hero_cards,
        hero_invested=round(hero_invested, 6),
        hero_collected=round(hero_collected, 6),
        hero_returned=round(hero_returned, 6),
        total_pot=total_pot,
        rake=rake,
        jackpot=jackpot,
        bingo=bingo,
        fortune=fortune,
        tax=tax,
        source_file=source_file,
        raw_summary="\n".join(summary_lines),
        went_to_flop=went_to_flop,
        flop_cards=flop_cards,
        hero_vpip=hero_vpip,
        button_seat=button_seat,
        seat_names=seat_names,
        actions=actions,
        shown_cards=shown_cards,
        extra=extra,
    )


def parse_text(text: str, source_file: str = "") -> list[Hand]:
    hands: list[Hand] = []
    for block in _split_hands(text):
        hand = parse_hand(block, source_file=source_file)
        if hand is not None:
            hands.append(hand)
    return hands


def parse_file(path: Path | str) -> list[Hand]:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_text(text, source_file=str(path.name))
