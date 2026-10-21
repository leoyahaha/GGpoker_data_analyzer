from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from poker.models import Action, Hand


def _sanitize_stakes_text(stakes: str) -> str:
    """Repair NUL corruption in stake digits (e.g. '$0\\x0025' → '$0.25')."""
    return stakes.replace("\x00", ".")


def _format_stakes(key: str, original: str) -> str:
    """Build '$sb/$bb' (keeping ante suffix from original when present)."""
    sb_s, bb_s = key.split("/", 1)
    ante = ""
    ante_m = re.search(r"(\([^()]*\))\s*$", original)
    if ante_m:
        ante = ante_m.group(1)
    return f"${sb_s}/${bb_s}{ante}"


def _repair_stakes(stakes: str, actions: list[Action], source_file: str = "") -> str:
    """Fix corrupt header stakes using filename, else posted blinds."""
    from poker.filters import normalize_stakes, stakes_key_from_filename

    stakes = _sanitize_stakes_text(stakes)
    header_key = normalize_stakes(stakes)
    file_key = stakes_key_from_filename(source_file) if source_file else None
    if file_key:
        if header_key == file_key:
            return stakes
        return _format_stakes(file_key, stakes)

    if header_key:
        return stakes

    posted_sb: float | None = None
    posted_bb: float | None = None
    for act in actions:
        if act.action == "posts small blind" and act.amount > 0:
            posted_sb = act.amount
        elif act.action == "posts big blind" and act.amount > 0:
            posted_bb = act.amount
    if posted_sb is None or posted_bb is None:
        return stakes
    posts_key = normalize_stakes(f"{posted_sb}/{posted_bb}")
    if not posts_key:
        return stakes
    return _format_stakes(posts_key, stakes)


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
    """Parse GG hand header; supports nested parentheses in stakes (9-max ante)."""
    prefix = re.match(r"^Poker Hand #(?P<hand_id>\S+):\s+", line)
    if not prefix:
        return None
    tail = line[prefix.end() :]
    dt_m = re.search(r" - (?P<dt>\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})$", tail)
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
    }

TABLE_RE = re.compile(
    r"^Table '(?P<table>[^']+)'\s+(?P<max>\d+)-max"
    r"(?:\s+Seat #(?P<button>\d+) is the button)?",
)

SEAT_RE = re.compile(
    r"^Seat (?P<seat>\d+):\s+(?P<name>\S+)\s+\(\$(?P<stack>[\d.]+) in chips\)",
)

DEALT_HERO_RE = re.compile(r"^Dealt to Hero \[(?P<cards>[^\]]+)\]")

# Money actions for any player; we filter Hero separately.
ACTION_RE = re.compile(
    r"^(?P<name>\S+):\s+"
    r"(?P<action>posts small blind|posts big blind|posts the ante|"
    r"posts small & big blinds|posts|"
    r"bets|calls|raises|checks|folds|shows|mucks)"
    r"(?P<rest>.*)$"
)

RAISES_RE = re.compile(r"raises \$(?P<by>[\d.]+) to \$(?P<to>[\d.]+)")
BETS_CALLS_RE = re.compile(r"(?:bets|calls) \$(?P<amount>[\d.]+)")
POSTS_RE = re.compile(r"posts(?: small blind| big blind| the ante| small & big blinds)? \$(?P<amount>[\d.]+)")
# Rare: "Hero: posts $0.05" style dead blinds / extras
POSTS_GENERIC_RE = re.compile(r"posts \$(?P<amount>[\d.]+)")

RETURNED_RE = re.compile(r"^Uncalled bet \(\$(?P<amount>[\d.]+)\) returned to (?P<name>\S+)")
COLLECTED_RE = re.compile(r"^(?P<name>\S+) collected \$(?P<amount>[\d.]+) from (?:pot|main pot|side pot(?:-\d+)?)")

SUMMARY_POT_RE = re.compile(
    r"Total pot \$(?P<pot>[\d.]+)\s*\|\s*Rake \$(?P<rake>[\d.]+)"
    r"(?:\s*\|\s*Jackpot \$(?P<jackpot>[\d.]+))?"
    r"(?:\s*\|\s*Bingo \$(?P<bingo>[\d.]+))?"
    r"(?:\s*\|\s*Fortune \$(?P<fortune>[\d.]+))?"
    r"(?:\s*\|\s*Tax \$(?P<tax>[\d.]+))?"
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
    parts = re.split(r"(?=^Poker Hand #)", text, flags=re.MULTILINE)
    blocks: list[str] = []
    for part in parts:
        block = part.strip()
        if not block.startswith("Poker Hand #"):
            continue
        header_line = block.split("\n", 1)[0]
        if _parse_hand_header(header_line) is None:
            continue
        blocks.append(block)
    return blocks


def parse_hand(raw: str, source_file: str = "") -> Hand | None:
    """Parse a single hand history block into a Hand model."""
    non_empty = [ln.rstrip() for ln in raw.splitlines()]

    header = None
    for ln in non_empty:
        if ln.startswith("Poker Hand #"):
            header = _parse_hand_header(ln)
            break
    if not header:
        return None

    hand_id = header["hand_id"]
    stakes = _sanitize_stakes_text(header["stakes"])
    dt = datetime.strptime(header["dt"], "%Y/%m/%d %H:%M:%S")

    table_name = ""
    max_players = 0
    button_seat: int | None = None
    hero_seat: int | None = None
    hero_cards: str | None = None
    seat_names: dict[int, str] = {}

    hero_invested = 0.0
    hero_returned = 0.0
    hero_collected = 0.0
    total_collected = 0.0
    street_contrib: dict[str, float] = {}
    went_to_flop = False
    flop_cards: tuple[str, ...] = ()
    board_cards: list[str] = []
    hero_vpip = False
    in_summary = False
    street = "preflop"
    pot = 0.0
    actions: list[Action] = []
    shown_cards: dict[str, tuple[str, ...]] = {}

    total_pot = 0.0
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
            if not board_cards and len(parsed) >= 3:
                board_cards = list(parsed[:3])
            went_to_flop = True
            reset_street_contrib()
            street = "flop"
            continue

        turn_line = TURN_LINE_RE.match(ln)
        if turn_line:
            card = _parse_card_tokens(turn_line.group("card"))
            if len(board_cards) == 3 and card:
                board_cards.append(card[0])
            reset_street_contrib()
            street = "turn"
            continue

        river_line = RIVER_LINE_RE.match(ln)
        if river_line:
            card = _parse_card_tokens(river_line.group("card"))
            if len(board_cards) == 4 and card:
                board_cards.append(card[0])
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
            if board_m:
                parsed = _parse_card_tokens(board_m.group("cards"))
                if len(parsed) >= 3:
                    if not flop_cards:
                        flop_cards = parsed[:3]
                        went_to_flop = True
                    if len(board_cards) < len(parsed):
                        board_cards = list(parsed)
            showed = SEAT_SHOWED_RE.match(ln)
            if showed:
                name = showed.group("name")
                cards = _parse_card_tokens(showed.group("cards"))
                if len(cards) >= 2 and name not in shown_cards:
                    shown_cards[name] = cards[:2]
            pot_m = SUMMARY_POT_RE.search(ln)
            if pot_m:
                total_pot = _money(pot_m.group("pot"))
                rake = _money(pot_m.group("rake"))
                jackpot = _money(pot_m.group("jackpot"))
                bingo = _money(pot_m.group("bingo"))
                fortune = _money(pot_m.group("fortune"))
                tax = _money(pot_m.group("tax"))
            continue

        returned = RETURNED_RE.match(ln)
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
        is_all_in = "and is all-in" in rest
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
                        is_all_in=is_all_in,
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
                        is_all_in=is_all_in,
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
                        is_all_in=is_all_in,
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

    stakes = _repair_stakes(stakes, actions, source_file=source_file)

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
        board_cards=tuple(board_cards),
        hero_vpip=hero_vpip,
        button_seat=button_seat,
        seat_names=seat_names,
        actions=actions,
        shown_cards=shown_cards,
        extra={"total_collected": round(total_collected, 6)},
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
