"""GG Smart HUD style overview stats for Hero (filtered sample)."""

from __future__ import annotations

from typing import Any

from poker.metrics.base import Metric, register
from poker.models import Action, Hand, HandDataset
from poker.positions import NINE_MAX_PROFILE, SIX_MAX_PROFILE

HERO = "Hero"
STEAL_POSITIONS = frozenset({"CO", "BTN", "SB"})
POSTFLOP = ("flop", "turn", "river")
VOLUNTARY = frozenset({"fold", "check", "call", "bet", "raise"})


def _pct(count: int, opp: int) -> float | None:
    if opp <= 0:
        return None
    return round(100.0 * count / opp, 1)


def _stat(count: int, opp: int) -> dict[str, Any]:
    return {"count": count, "opportunities": opp, "pct": _pct(count, opp)}


def _street_actions(hand: Hand, street: str) -> list[Action]:
    return [a for a in hand.actions if a.street == street and a.action in VOLUNTARY]


def _last_aggressor(actions: list[Action]) -> str | None:
    last: str | None = None
    for act in actions:
        if act.action in ("bet", "raise"):
            last = act.player
    return last


def _hero_folded_preflop(hand: Hand) -> bool:
    for act in hand.actions:
        if act.street != "preflop":
            break
        if act.is_hero and act.action == "fold":
            return True
    return False


def _hero_saw_flop(hand: Hand) -> bool:
    return hand.went_to_flop and not _hero_folded_preflop(hand)


def _hero_folded_after_flop(hand: Hand) -> bool:
    for act in hand.actions:
        if act.street in POSTFLOP and act.is_hero and act.action == "fold":
            return True
    return False


def _players_to_showdown(hand: Hand) -> set[str]:
    """Players who never folded (still in when betting ends)."""
    active = set(hand.seat_names.values())
    for act in hand.actions:
        if act.action == "fold":
            active.discard(act.player)
    return active


def _hero_went_to_showdown(hand: Hand) -> bool:
    if not _hero_saw_flop(hand) or _hero_folded_after_flop(hand):
        return False
    survivors = _players_to_showdown(hand)
    if HERO not in survivors:
        return False
    # Need at least one opponent at showdown (won without SD does not count).
    return len(survivors) >= 2 or HERO in hand.shown_cards


def _hero_pfr(hand: Hand) -> bool:
    for act in hand.actions:
        if act.street != "preflop":
            break
        if act.is_hero and act.action in ("raise", "bet"):
            return True
    return False


def _profile_for_hand(hand: Hand):
    if hand.max_players >= 9:
        return NINE_MAX_PROFILE
    return SIX_MAX_PROFILE


def _steal_spot(hand: Hand) -> tuple[bool, bool]:
    """(opportunity, attempt) for ATS from CO/BTN/SB when folded to."""
    pos = _profile_for_hand(hand).position_map(hand).get(HERO)
    if pos not in STEAL_POSITIONS:
        return False, False
    for act in hand.actions:
        if act.street != "preflop":
            break
        if act.action.startswith("posts") or act.action in ("show", "muck"):
            continue
        if act.is_hero and act.action in VOLUNTARY:
            return True, act.action in ("raise", "bet")
        if act.action in ("call", "raise", "bet"):
            return False, False
    return False, False


def _threebet_spot(hand: Hand) -> tuple[bool, bool]:
    """
    (opportunity, made): facing a raise, Hero re-raises.

    GG: 3BET includes 4-bet / 5-bet (any re-raise after a raise was made).
    Judged at Hero's first voluntary action while raise_count >= 1.
    """
    raises = 0
    for act in hand.actions:
        if act.street != "preflop":
            break
        if act.action.startswith("posts") or act.action in ("show", "muck"):
            continue
        if act.is_hero and act.action in VOLUNTARY:
            if raises < 1:
                return False, False
            return True, act.action in ("raise", "bet")
        if act.action in ("raise", "bet"):
            raises += 1
    return False, False


def _cbet_spot(street_actions: list[Action], aggressor: str | None) -> tuple[bool, bool]:
    """
    Continuation bet opportunity for ``aggressor`` on a street.

    Opportunity: aggressor acts while no bet/raise yet on the street.
    Hit: aggressor bets (open bet, not a raise vs donk).
    """
    if not aggressor:
        return False, False
    faced_bet = False
    for act in street_actions:
        if act.action in ("bet", "raise"):
            if act.player == aggressor and not faced_bet and act.action == "bet":
                return True, True
            if act.player == aggressor and not faced_bet and act.action == "raise":
                # Open-raise rare postflop; treat as aggression / CB hit.
                return True, True
            faced_bet = True
            continue
        if act.player == aggressor and not faced_bet:
            if act.action == "check":
                return True, False
            if act.action == "fold":
                return False, False
    return False, False


def _facing_cbet_response(
    street_actions: list[Action], cbetter: str
) -> tuple[bool, str | None]:
    """
    If Hero faces ``cbetter``'s open bet, return (True, fold|call|raise).

    Multiway: folds/calls before Hero still count; a raise before Hero does not.
    """
    saw_cb = False
    for act in street_actions:
        if not saw_cb:
            if act.player == cbetter and act.action == "bet":
                saw_cb = True
            elif act.action in ("bet", "raise"):
                return False, None
            continue
        if act.player == cbetter:
            continue
        if act.is_hero:
            if act.action == "fold":
                return True, "fold"
            if act.action == "call":
                return True, "call"
            if act.action in ("raise", "bet"):
                return True, "raise"
            return False, None
        if act.action == "raise":
            return False, None
    return False, None


def _flop_pfr(hand: Hand) -> str | None:
    return _last_aggressor(_street_actions(hand, "preflop"))


def compute_overview(hands: list[Hand]) -> dict[str, Any]:
    vpip_n = pfr_n = 0
    ats_n = ats_opp = 0
    three_n = three_opp = 0

    flop_cb_n = flop_cb_opp = 0
    flop_fcb = flop_ccb = flop_rcb = flop_face = 0
    turn_cb_n = turn_cb_opp = 0
    turn_fcb = turn_ccb = turn_rcb = turn_face = 0

    saw_flop_n = wwsf_n = wtsd_n = wsd_n = 0
    agg_bet_raise = agg_call = agg_fold = 0

    total = len(hands)
    for hand in hands:
        if hand.hero_vpip:
            vpip_n += 1
        if _hero_pfr(hand):
            pfr_n += 1

        steal_opp, steal_hit = _steal_spot(hand)
        if steal_opp:
            ats_opp += 1
            if steal_hit:
                ats_n += 1

        three_opp_flag, three_hit = _threebet_spot(hand)
        if three_opp_flag:
            three_opp += 1
            if three_hit:
                three_n += 1

        pfr = _flop_pfr(hand)
        flop_acts = _street_actions(hand, "flop")
        turn_acts = _street_actions(hand, "turn")

        if _hero_saw_flop(hand):
            saw_flop_n += 1
            if hand.hero_collected > 0:
                wwsf_n += 1
            if _hero_went_to_showdown(hand):
                wtsd_n += 1
                if hand.hero_collected > 0:
                    wsd_n += 1

        # Flop / Turn CB: preflop aggressor bets the street (GG + street popup).
        if pfr == HERO and flop_acts:
            opp, hit = _cbet_spot(flop_acts, HERO)
            if opp:
                flop_cb_opp += 1
                if hit:
                    flop_cb_n += 1

        if pfr and pfr != HERO and flop_acts:
            faced, resp = _facing_cbet_response(flop_acts, pfr)
            if faced and resp:
                flop_face += 1
                if resp == "fold":
                    flop_fcb += 1
                elif resp == "call":
                    flop_ccb += 1
                elif resp == "raise":
                    flop_rcb += 1

        if pfr == HERO and turn_acts:
            opp, hit = _cbet_spot(turn_acts, HERO)
            if opp:
                turn_cb_opp += 1
                if hit:
                    turn_cb_n += 1

        if pfr and pfr != HERO and turn_acts:
            faced, resp = _facing_cbet_response(turn_acts, pfr)
            if faced and resp:
                turn_face += 1
                if resp == "fold":
                    turn_fcb += 1
                elif resp == "call":
                    turn_ccb += 1
                elif resp == "raise":
                    turn_rcb += 1

        # TAF = Aggression Frequency % (not Aggression Factor).
        # Industry AFq: (bet+raise) / (bet+raise+call+fold) * 100; checks excluded.
        # GoPoker's (bet+raise)/call is AF ratio — contradicts in-game % display.
        for act in hand.actions:
            if not act.is_hero:
                continue
            if act.action in ("bet", "raise"):
                agg_bet_raise += 1
            elif act.action == "call":
                agg_call += 1
            elif act.action == "fold":
                agg_fold += 1

    taf_den = agg_bet_raise + agg_call + agg_fold
    return {
        "hand_count": total,
        "preflop": {
            "VPIP": _stat(vpip_n, total),
            "PFR": _stat(pfr_n, total),
            "ATS": _stat(ats_n, ats_opp),
            "3BET": _stat(three_n, three_opp),
        },
        "flop": {
            "CB": _stat(flop_cb_n, flop_cb_opp),
            "FCB": _stat(flop_fcb, flop_face),
            "CCB": _stat(flop_ccb, flop_face),
            "RCB": _stat(flop_rcb, flop_face),
        },
        "turn": {
            "CB": _stat(turn_cb_n, turn_cb_opp),
            "FCB": _stat(turn_fcb, turn_face),
            "CCB": _stat(turn_ccb, turn_face),
            "RCB": _stat(turn_rcb, turn_face),
        },
        "river": {
            "WT": _stat(wtsd_n, saw_flop_n),
            "WSD": _stat(wsd_n, wtsd_n),
            "WWSF": _stat(wwsf_n, saw_flop_n),
            "TAF": _stat(agg_bet_raise, taf_den),
        },
    }


@register
class OverviewDashboardMetric(Metric):
    """GG-style combined HUD stats for the filtered Hero sample."""

    id = "overview_dashboard"
    name = "综合数据看板"
    description = "按 GG Smart HUD 口径统计 VPIP/PFR/ATS/3BET 与各街 CB 等"
    chart_type = "stats"

    def compute(self, dataset: HandDataset, options: dict[str, Any] | None = None) -> dict[str, Any]:
        _ = options
        hands = dataset.sorted_hands()
        payload = compute_overview(hands)
        payload["metric_id"] = self.id
        payload["name"] = self.name
        return payload
