from __future__ import annotations

from typing import Any

from poker.allin_ev import profit_allin_ev
from poker.metrics.base import Metric, register
from poker.models import HandDataset


@register
class ProfitCurveMetric(Metric):
    """Cumulative Hero profit by hand count — before/after fees and all-in EV."""

    id = "profit_curve"
    name = "盈利曲线"
    description = "按手数累计的费用前 / 抽水后 / 全压EV盈利"
    chart_type = "line"

    def compute(self, dataset: HandDataset, options: dict[str, Any] | None = None) -> dict[str, Any]:
        _ = options
        hands = dataset.sorted_hands()
        n = len(hands)

        hand_index: list[int] = []
        before: list[float] = []
        after: list[float] = []
        allin_ev: list[float] = []

        cum_before = 0.0
        cum_after = 0.0
        cum_allin = 0.0

        for i, hand in enumerate(hands, start=1):
            cum_before = round(cum_before + hand.profit_before_rake, 6)
            cum_after = round(cum_after + hand.profit_after_rake, 6)
            cum_allin = round(cum_allin + profit_allin_ev(hand), 6)
            hand_index.append(i)
            before.append(cum_before)
            after.append(cum_after)
            allin_ev.append(cum_allin)

        return {
            "metric_id": self.id,
            "name": self.name,
            "hand_count": n,
            "total_profit_before_rake": before[-1] if before else 0.0,
            "total_profit_after_rake": after[-1] if after else 0.0,
            "total_profit_allin_ev": allin_ev[-1] if allin_ev else 0.0,
            "total_rake_paid": round(sum(h.rake_share for h in hands), 6),
            "total_rake_only": round(sum(h.rake_only_share for h in hands), 6),
            "total_jackpot_share": round(sum(h.jackpot_share for h in hands), 6),
            "series": {
                "hand_index": hand_index,
                "profit_before_rake": before,
                "profit_after_rake": after,
                "profit_allin_ev": allin_ev,
            },
        }
