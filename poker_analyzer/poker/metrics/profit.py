from __future__ import annotations

from typing import Any

from poker.allin_ev import profit_allin_ev
from poker.metrics.base import Metric, register
from poker.models import HandDataset

# Chart does not need one point per hand; keep curve shape with far fewer points.
_MAX_SERIES_POINTS = 2500
# Profit-curve all-in EV: fewer MC samples is enough for a cumulative chart.
_CURVE_EQUITY_SAMPLES = 2500


def _series_indices(n: int, max_points: int) -> list[int]:
    if n <= max_points or max_points < 2:
        return list(range(n))
    out: list[int] = []
    last = -1
    for k in range(max_points):
        i = round(k * (n - 1) / (max_points - 1))
        if i != last:
            out.append(i)
            last = i
    return out


@register
class ProfitCurveMetric(Metric):
    """Cumulative Hero profit by hand count — before/after fees and all-in EV."""

    id = "profit_curve"
    name = "盈利曲线"
    description = "按手数累计的费用前 / 抽水后 / 全压EV盈利"
    chart_type = "line"

    def compute(self, dataset: HandDataset, options: dict[str, Any] | None = None) -> dict[str, Any]:
        _ = options
        # Load/filter already keep chronological order; avoid re-sorting 100k+ hands.
        hands = dataset.hands
        n = len(hands)
        keep = _series_indices(n, _MAX_SERIES_POINTS)
        keep_i = 0
        next_keep = keep[0] if keep else -1

        hand_index: list[int] = []
        before: list[float] = []
        after: list[float] = []
        allin_ev: list[float] = []
        showdown: list[float] = []
        non_showdown: list[float] = []

        cum_before = 0.0
        cum_after = 0.0
        cum_allin = 0.0
        cum_showdown = 0.0
        cum_non_showdown = 0.0
        total_rake = 0.0
        total_rake_only = 0.0
        total_jackpot = 0.0

        for i, hand in enumerate(hands):
            profit = hand.profit_before_rake
            cum_before += profit
            cum_after += hand.profit_after_rake
            cum_allin += profit_allin_ev(hand, samples=_CURVE_EQUITY_SAMPLES)
            if hand.went_to_showdown:
                cum_showdown += profit
            else:
                cum_non_showdown += profit
            total_rake += hand.rake_share
            total_rake_only += hand.rake_only_share
            total_jackpot += hand.jackpot_share

            if i == next_keep:
                hand_index.append(i + 1)
                before.append(round(cum_before, 6))
                after.append(round(cum_after, 6))
                allin_ev.append(round(cum_allin, 6))
                showdown.append(round(cum_showdown, 6))
                non_showdown.append(round(cum_non_showdown, 6))
                keep_i += 1
                next_keep = keep[keep_i] if keep_i < len(keep) else -1

        return {
            "metric_id": self.id,
            "name": self.name,
            "hand_count": n,
            "total_profit_before_rake": round(cum_before, 6),
            "total_profit_after_rake": round(cum_after, 6),
            "total_profit_allin_ev": round(cum_allin, 6),
            "total_rake_paid": round(total_rake, 6),
            "total_rake_only": round(total_rake_only, 6),
            "total_jackpot_share": round(total_jackpot, 6),
            "total_showdown_winnings": round(cum_showdown, 6),
            "total_non_showdown_winnings": round(cum_non_showdown, 6),
            "series": {
                "hand_index": hand_index,
                "profit_before_rake": before,
                "profit_after_rake": after,
                "profit_allin_ev": allin_ev,
                "showdown_winnings": showdown,
                "non_showdown_winnings": non_showdown,
            },
        }
