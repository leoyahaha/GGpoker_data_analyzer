from __future__ import annotations

from functools import lru_cache
from itertools import combinations
from random import Random
from typing import Iterable, Sequence

RANK_ORDER = "23456789TJQKA"
SUIT_ORDER = "cdhs"
UNKNOWN_COMBO = "未知"

# 5-high wheel uses rank index of 5 (3).
_STRAIGHT_HIGHS = tuple(range(12, 3, -1))
_WHEEL_MASK = (1 << 12) | 0b1111  # A,2,3,4,5


def parse_card(card: str) -> tuple[str, str]:
    text = card.strip()
    if len(text) < 2:
        raise ValueError(f"invalid card: {card!r}")
    rank = text[0].upper()
    if rank == "1" and len(text) >= 3 and text[1].upper() == "0":
        rank = "T"
        suit = text[-1].lower()
    else:
        suit = text[-1].lower()
    if rank == "10":
        rank = "T"
    if rank not in RANK_ORDER or suit not in SUIT_ORDER:
        raise ValueError(f"invalid card: {card!r}")
    return rank, suit


def card_to_int(card: str) -> int:
    rank, suit = parse_card(card)
    return RANK_ORDER.index(rank) * 4 + SUIT_ORDER.index(suit)


def _tokenize_cards(raw: str) -> list[str]:
    text = raw.strip().replace(",", " ")
    parts = [p for p in text.split() if p]
    if len(parts) == 1 and len(parts[0]) >= 4 and len(parts[0]) % 2 == 0:
        blob = parts[0]
        return [blob[i : i + 2] for i in range(0, len(blob), 2)]
    return parts


def parse_hole_cards(raw: str | Sequence[str] | None) -> tuple[int, int] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        tokens = _tokenize_cards(raw)
    else:
        tokens = [str(p) for p in raw]
    if len(tokens) < 2:
        return None
    try:
        a, b = card_to_int(tokens[0]), card_to_int(tokens[1])
    except ValueError:
        return None
    if a == b:
        return None
    return (a, b)


def hole_combo_label(raw: str | Sequence[str] | None) -> str:
    """AKs / AKo / AA. Suited and offsuit are distinct."""
    parsed = parse_hole_cards(raw)
    if parsed is None:
        return UNKNOWN_COMBO
    r1, r2 = parsed[0] // 4, parsed[1] // 4
    s1, s2 = parsed[0] % 4, parsed[1] % 4
    if r1 < r2:
        r1, r2, s1, s2 = r2, r1, s2, s1
    hi, lo = RANK_ORDER[r1], RANK_ORDER[r2]
    if r1 == r2:
        return f"{hi}{lo}"
    return f"{hi}{lo}{'s' if s1 == s2 else 'o'}"


def _best_straight(mask: int) -> int:
    """Highest rank index of a 5-card straight, or -1."""
    for high in _STRAIGHT_HIGHS:
        window = 0b11111 << (high - 4)
        if mask & window == window:
            return high
    if mask & _WHEEL_MASK == _WHEEL_MASK:
        return 3
    return -1


def _top_ranks(mask: int, n: int) -> list[int]:
    ranks: list[int] = []
    bit = 12
    while bit >= 0 and len(ranks) < n:
        if mask & (1 << bit):
            ranks.append(bit)
        bit -= 1
    return ranks


def evaluate7(cards: Sequence[int]) -> int:
    """
    Comparable 7-card strength. Higher is better.

    Layout: category in the high bits, then kickers.
    8 SF, 7 quads, 6 boat, 5 flush, 4 straight, 3 trips, 2 two-pair, 1 pair, 0 high.
    """
    rank_mask = 0
    suit_masks = [0, 0, 0, 0]
    counts = [0] * 13
    for c in cards:
        rank = c >> 2
        suit = c & 3
        counts[rank] += 1
        rank_mask |= 1 << rank
        suit_masks[suit] |= 1 << rank

    flush_mask = 0
    for sm in suit_masks:
        if sm.bit_count() >= 5:
            flush_mask = sm
            break

    quads = trips = 0
    pairs: list[int] = []
    for rank in range(12, -1, -1):
        n = counts[rank]
        if n == 4:
            quads = rank
        elif n == 3 and trips == 0:
            trips = rank
        elif n == 3:
            pairs.append(rank)
        elif n == 2:
            pairs.append(rank)

    if flush_mask:
        sf = _best_straight(flush_mask)
        if sf >= 0:
            return (8 << 20) | sf

    if quads:
        kicker = next(r for r in range(12, -1, -1) if r != quads and counts[r])
        return (7 << 20) | (quads << 8) | kicker
    if trips and pairs:
        return (6 << 20) | (trips << 8) | pairs[0]
    lower_trips = next((r for r in range(12, -1, -1) if r != trips and counts[r] == 3), -1) if trips else -1
    if trips and lower_trips >= 0:
        return (6 << 20) | (trips << 8) | lower_trips

    if flush_mask:
        kick = _top_ranks(flush_mask, 5)
        value = 5 << 20
        shift = 16
        for k in kick:
            value |= k << shift
            shift -= 4
        return value

    straight = _best_straight(rank_mask)
    if straight >= 0:
        return (4 << 20) | straight
    def _pad(ranks: list[int], n: int) -> list[int]:
        return (ranks + [0] * n)[:n]

    if trips:
        kick = _pad([r for r in range(12, -1, -1) if r != trips and counts[r]], 2)
        return (3 << 20) | (trips << 8) | (kick[0] << 4) | kick[1]
    if len(pairs) >= 2:
        used = {pairs[0], pairs[1]}
        kicker = next((r for r in range(12, -1, -1) if r not in used and counts[r]), 0)
        return (2 << 20) | (pairs[0] << 8) | (pairs[1] << 4) | kicker
    if pairs:
        kick = _pad([r for r in range(12, -1, -1) if r != pairs[0] and counts[r]], 3)
        return (1 << 20) | (pairs[0] << 12) | (kick[0] << 8) | (kick[1] << 4) | kick[2]
    kick = _top_ranks(rank_mask, 5)
    value = 0
    shift = 16
    for k in kick:
        value |= k << shift
        shift -= 4
    return value


def _matchup_key(hero: tuple[int, int], villain: tuple[int, int]) -> tuple[int, ...]:
    h = tuple(sorted(hero))
    v = tuple(sorted(villain))
    if h <= v:
        return h + v
    return v + h


def _same_category_tie(hero: tuple[int, int], villain: tuple[int, int]) -> bool:
    """Identical rank pattern and suitedness → 50% by symmetry (AA vs AA, AKs vs AKs)."""
    hr = sorted((hero[0] >> 2, hero[1] >> 2))
    vr = sorted((villain[0] >> 2, villain[1] >> 2))
    if hr != vr:
        return False
    hs = (hero[0] & 3) == (hero[1] & 3)
    vs = (villain[0] & 3) == (villain[1] & 3)
    return hs == vs


def _enumerate_equity(a: int, b: int, c: int, d: int) -> float:
    used = {a, b, c, d}
    deck = [i for i in range(52) if i not in used]
    wins = ties = 0
    total = 0
    h7 = [a, b, 0, 0, 0, 0, 0]
    v7 = [c, d, 0, 0, 0, 0, 0]
    for board in combinations(deck, 5):
        h7[2], h7[3], h7[4], h7[5], h7[6] = board
        v7[2], v7[3], v7[4], v7[5], v7[6] = board
        eh = evaluate7(h7)
        ev = evaluate7(v7)
        if eh > ev:
            wins += 1
        elif eh == ev:
            ties += 1
        total += 1
    return (wins + 0.5 * ties) / total if total else 0.5


def _sample_equity(a: int, b: int, c: int, d: int, samples: int = 40000) -> float:
    used = {a, b, c, d}
    deck = [i for i in range(52) if i not in used]
    rng = Random(hash((a, b, c, d, samples)) & 0xFFFFFFFFFFFFFFFF)
    wins = ties = 0
    h7 = [a, b, 0, 0, 0, 0, 0]
    v7 = [c, d, 0, 0, 0, 0, 0]
    n = len(deck)
    for _ in range(samples):
        # Partial Fisher–Yates for 5 board cards.
        for i in range(5):
            j = rng.randrange(i, n)
            deck[i], deck[j] = deck[j], deck[i]
        board = deck[0], deck[1], deck[2], deck[3], deck[4]
        h7[2], h7[3], h7[4], h7[5], h7[6] = board
        v7[2], v7[3], v7[4], v7[5], v7[6] = board
        eh = evaluate7(h7)
        ev = evaluate7(v7)
        if eh > ev:
            wins += 1
        elif eh == ev:
            ties += 1
    return (wins + 0.5 * ties) / samples


@lru_cache(maxsize=4096)
def _equity_cached(a: int, b: int, c: int, d: int, swapped: bool) -> float:
    hero = (a, b)
    villain = (c, d)
    if _same_category_tie(hero, villain):
        return 0.5
    equity = _sample_equity(a, b, c, d)
    return 1.0 - equity if swapped else equity


def preflop_equity(hero_cards: Iterable[str] | str, villain_cards: Iterable[str] | str) -> float | None:
    """Hero equity vs villain on a random full runout (ties count as half)."""
    hero = parse_hole_cards(hero_cards if isinstance(hero_cards, str) else tuple(hero_cards))
    villain = parse_hole_cards(villain_cards if isinstance(villain_cards, str) else tuple(villain_cards))
    if hero is None or villain is None:
        return None
    if set(hero) & set(villain):
        return None
    key = _matchup_key(hero, villain)
    swapped = tuple(sorted(hero)) != key[:2]
    return _equity_cached(key[0], key[1], key[2], key[3], swapped)


# --- Range parsing & Monte Carlo equity ---

_RANK_IDX = {r: i for i, r in enumerate(RANK_ORDER)}


def _pair_combos(rank: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s1 in range(4):
        for s2 in range(s1 + 1, 4):
            out.append((rank * 4 + s1, rank * 4 + s2))
    return out


def _suited_combos(r_hi: int, r_lo: int) -> list[tuple[int, int]]:
    return [(r_hi * 4 + s, r_lo * 4 + s) for s in range(4)]


def _offsuit_combos(r_hi: int, r_lo: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s1 in range(4):
        for s2 in range(4):
            if s1 != s2:
                out.append((r_hi * 4 + s1, r_lo * 4 + s2))
    return out


def _add_combo(combos: dict[tuple[int, int], None], c1: int, c2: int) -> None:
    combos[tuple(sorted((c1, c2)))] = None


def _add_hand_label(combos: dict[tuple[int, int], None], hi: str, lo: str, suited: str | None) -> None:
    r_hi, r_lo = _RANK_IDX[hi], _RANK_IDX[lo]
    if r_hi == r_lo:
        for c in _pair_combos(r_hi):
            _add_combo(combos, c[0], c[1])
        return
    if r_hi < r_lo:
        r_hi, r_lo = r_lo, r_hi
    if suited in (None, "s"):
        for c in _suited_combos(r_hi, r_lo):
            _add_combo(combos, c[0], c[1])
    if suited in (None, "o"):
        for c in _offsuit_combos(r_hi, r_lo):
            _add_combo(combos, c[0], c[1])


def _parse_hand_token(token: str) -> tuple[str, str, str | None] | None:
    t = token.strip().upper()
    if len(t) < 2:
        return None
    if t.endswith(("S", "O")) and len(t) >= 3:
        hi, lo, suf = t[0], t[1], t[-1].lower()
        if hi in _RANK_IDX and lo in _RANK_IDX:
            return hi, lo, suf
    if len(t) == 2 and t[0] in _RANK_IDX and t[1] in _RANK_IDX:
        return t[0], t[1], None
    return None


def _expand_plus(hi: str, lo: str, suited: str | None) -> list[tuple[str, str, str | None]]:
    r_hi, r_lo = _RANK_IDX[hi], _RANK_IDX[lo]
    out: list[tuple[str, str, str | None]] = []
    if r_hi == r_lo:
        for r in range(r_lo, 13):
            out.append((RANK_ORDER[r], RANK_ORDER[r], None))
        return out
    if r_hi < r_lo:
        r_hi, r_lo = r_lo, r_hi
        hi, lo = RANK_ORDER[r_hi], RANK_ORDER[r_lo]
    for r in range(r_lo, r_hi):
        out.append((hi, RANK_ORDER[r], suited))
    return out


def _expand_dash(left: str, right: str) -> list[tuple[str, str, str | None]]:
    l = _parse_hand_token(left)
    r = _parse_hand_token(right)
    if l is None or r is None:
        raise ValueError(f"无效范围: {left}-{right}")
    hi1, lo1, s1 = l
    hi2, lo2, s2 = r
    if s1 != s2:
        raise ValueError(f"范围两端花色类型需一致: {left}-{right}")
    suited = s1
    if hi1 == lo1 and hi2 == lo2:
        a, b = _RANK_IDX[hi1], _RANK_IDX[hi2]
        if a > b:
            a, b = b, a
        return [(RANK_ORDER[i], RANK_ORDER[i], None) for i in range(a, b + 1)]
    if hi1 != hi2:
        raise ValueError(f"范围两端高牌需一致: {left}-{right}")
    a, b = _RANK_IDX[lo1], _RANK_IDX[lo2]
    if a > b:
        a, b = b, a
    return [(hi1, RANK_ORDER[i], suited) for i in range(a, b + 1)]


def parse_range(text: str) -> list[tuple[int, int]]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("范围不能为空")
    combos: dict[tuple[int, int], None] = {}
    for part in raw.replace(";", ",").split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token and not token.endswith("-"):
            left, right = token.split("-", 1)
            for hand in _expand_dash(left, right):
                _add_hand_label(combos, *hand)
            continue
        if token.endswith("+"):
            base = _parse_hand_token(token[:-1])
            if base is None:
                raise ValueError(f"无效范围标记: {token}")
            for hand in _expand_plus(*base):
                _add_hand_label(combos, *hand)
            continue
        base = _parse_hand_token(token)
        if base is None:
            raise ValueError(f"无效范围标记: {token}")
        _add_hand_label(combos, *base)
    if not combos:
        raise ValueError("范围未包含任何组合")
    return list(combos.keys())


def parse_board(raw: str | Sequence[str] | None) -> list[int]:
    if raw is None:
        return []
    if isinstance(raw, str):
        tokens = _tokenize_cards(raw)
    else:
        tokens = [str(p) for p in raw]
    if len(tokens) > 5:
        raise ValueError("公共牌最多 5 张")
    board: list[int] = []
    seen: set[int] = set()
    for tok in tokens:
        c = card_to_int(tok)
        if c in seen:
            raise ValueError(f"重复的公共牌: {tok}")
        seen.add(c)
        board.append(c)
    return board


def _is_specific_hand(text: str) -> bool:
    tokens = _tokenize_cards(text)
    if len(tokens) != 2:
        return False
    try:
        parse_card(tokens[0])
        parse_card(tokens[1])
        return True
    except ValueError:
        return False


def parse_player_input(text: str) -> list[tuple[int, int]]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("请输入手牌或范围")
    if _is_specific_hand(raw):
        hand = parse_hole_cards(raw)
        if hand is None:
            raise ValueError("无效手牌")
        return [hand]
    return parse_range(raw)


def minimum_defense_frequency(bet_pct: float) -> float:
    """bet_pct = bet size as % of pot; returns MDF in [0, 1]."""
    if bet_pct < 0:
        raise ValueError("下注百分比不能为负")
    frac = bet_pct / 100.0
    if frac == 0:
        return 1.0
    return 1.0 / (1.0 + frac)


def monte_carlo_equity(
    player1: str,
    player2: str,
    board: str | Sequence[str] | None = None,
    *,
    samples: int = 20000,
    seed: int = 0,
) -> dict[str, float | int | list[str]]:
    """Monte Carlo equity for hand/range vs hand/range, preflop or with partial board."""
    if samples < 200:
        raise ValueError("样本数至少 200")
    if samples > 100000:
        raise ValueError("单次样本数最多 100000")

    range1 = parse_player_input(player1)
    range2 = parse_player_input(player2)
    board_cards = parse_board(board)

    if len(board_cards) > 5:
        raise ValueError("公共牌最多 5 张")

    dead = set(board_cards)
    if seed:
        rng = Random(seed)
    else:
        rng = Random()

    wins1 = ties = 0
    need_board = 5 - len(board_cards)
    h7 = [0, 0, 0, 0, 0, 0, 0]
    v7 = [0, 0, 0, 0, 0, 0, 0]
    for bi, c in enumerate(board_cards):
        h7[2 + bi] = c
        v7[2 + bi] = c

    attempts = 0
    done = 0
    while done < samples:
        attempts += 1
        if attempts > samples * 50:
            raise ValueError("双方范围/手牌与公共牌冲突过多，请检查输入")
        c1 = rng.choice(range1)
        c2 = rng.choice(range2)
        used = dead | set(c1) | set(c2)
        if len(used) != len(dead) + 4:
            continue

        h7[0], h7[1] = c1
        v7[0], v7[1] = c2

        deck = [i for i in range(52) if i not in used]
        n = len(deck)
        if need_board > n:
            continue
        for i in range(need_board):
            j = rng.randrange(i, n)
            deck[i], deck[j] = deck[j], deck[i]
        for i in range(need_board):
            h7[2 + len(board_cards) + i] = deck[i]
            v7[2 + len(board_cards) + i] = deck[i]

        eh = evaluate7(h7)
        ev = evaluate7(v7)
        if eh > ev:
            wins1 += 1
        elif eh == ev:
            ties += 1
        done += 1

    eq1 = (wins1 + 0.5 * ties) / samples
    eq2 = 1.0 - eq1
    tie_pct = ties / samples
    board_labels = []
    for c in board_cards:
        r = RANK_ORDER[c // 4]
        s = SUIT_ORDER[c % 4]
        board_labels.append(f"{r}{s}")

    return {
        "player1_equity": round(eq1 * 100, 2),
        "player2_equity": round(eq2 * 100, 2),
        "tie_pct": round(tie_pct * 100, 2),
        "wins1": wins1,
        "ties": ties,
        "samples": samples,
        "player1_combos": len(range1),
        "player2_combos": len(range2),
        "board": board_labels,
    }
