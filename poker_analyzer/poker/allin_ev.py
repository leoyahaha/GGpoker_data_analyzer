from __future__ import annotations

from itertools import combinations

from poker.equity import evaluate7, parse_board, parse_hole_cards, preflop_equity
from poker.models import Hand

_STREET_BOARD_N = {"preflop": 0, "flop": 3, "turn": 4, "river": 5}
_EARLY_STREETS = ("preflop", "flop", "turn")


def _hero_hole(hand: Hand) -> str | None:
    shown = hand.shown_cards.get("Hero")
    if shown and len(shown) >= 2:
        return f"{shown[0]} {shown[1]}"
    return hand.hero_cards


def _equity_known(hero: str, villain: str, board: list[str]) -> float | None:
    if not board:
        return preflop_equity(hero, villain)
    h = parse_hole_cards(hero)
    v = parse_hole_cards(villain)
    b = parse_board(board)
    if h is None or v is None:
        return None
    used = set(h) | set(v) | set(b)
    if len(used) != 4 + len(b):
        return None
    need = 5 - len(b)
    if need < 0 or need > 5:
        return None
    deck = [i for i in range(52) if i not in used]
    wins = ties = total = 0
    h7 = [h[0], h[1], *b, *([0] * need)]
    v7 = [v[0], v[1], *b, *([0] * need)]
    fill_at = 2 + len(b)
    for rem in combinations(deck, need):
        h7[fill_at : fill_at + need] = rem
        v7[fill_at : fill_at + need] = rem
        eh = evaluate7(h7)
        ev = evaluate7(v7)
        if eh > ev:
            wins += 1
        elif eh == ev:
            ties += 1
        total += 1
    if not total:
        return None
    return (wins + 0.5 * ties) / total


def early_allin_ev_profit(hand: Hand) -> float | None:
    """
    Theoretical before-fee profit for HU all-in before river showdown.

    Returns None when this hand should keep actual profit_before_rake
    (no early all-in, multiway, missing cards, etc.).
    """
    allin_acts = [
        a
        for a in hand.actions
        if a.is_all_in and a.street in _EARLY_STREETS and a.action in ("bet", "call", "raise")
    ]
    if not allin_acts:
        return None
    if any(a.is_hero and a.action == "fold" for a in hand.actions):
        return None

    hero = _hero_hole(hand)
    if not hero:
        return None
    villains = [n for n in hand.shown_cards if n != "Hero"]
    if len(villains) != 1:
        return None
    vill_cards = hand.shown_cards[villains[0]]
    if len(vill_cards) < 2:
        return None
    villain = f"{vill_cards[0]} {vill_cards[1]}"

    # Hero must be in the all-in pot (went to showdown with the other player).
    if hand.net_invested <= 0 or hand.total_pot <= 0:
        return None

    allin_street = allin_acts[-1].street
    board_n = _STREET_BOARD_N[allin_street]
    board = list(hand.board_cards[:board_n])
    if len(board) < board_n:
        return None

    eq = _equity_known(hero, villain, board)
    if eq is None:
        return None
    return round(eq * hand.total_pot - hand.net_invested, 6)


def profit_allin_ev(hand: Hand) -> float:
    """Per-hand profit for the all-in EV curve (falls back to actual before-fee)."""
    ev = early_allin_ev_profit(hand)
    if ev is None:
        return hand.profit_before_rake
    return ev
