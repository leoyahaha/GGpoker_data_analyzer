from __future__ import annotations

from typing import Any

from poker.metrics.base import Metric, register
from poker.models import HandDataset


@register
class ProfitCurveMetric(Metric):
    """Cumulative Hero profit by hand count — before and after fees."""

    id = "profit_curve"
    name = "盈利曲线"
    description = "按手数累计的费用前 / 抽水后盈利"
    chart_type = "line"

    def compute(self, dataset: HandDataset, options: dict[str, Any] | None = None) -> dict[str, Any]:
        opts = options or {}
        max_points = int(opts.get("max_points") or 0)
        hands = dataset.sorted_hands()
        n = len(hands)

        hand_index: list[int] = []
        before: list[float] = []
        after: list[float] = []

        cum_before = 0.0
        cum_after = 0.0

        # Keep every point, or stride so series length <= max_points (always keep last).
        stride = 1
        if max_points > 1 and n > max_points:
            stride = max(1, (n + max_points - 2) // (max_points - 1))

        for i, hand in enumerate(hands, start=1):
            cum_before = round(cum_before + hand.profit_before_rake, 6)
            cum_after = round(cum_after + hand.profit_after_rake, 6)
            if i == n or i == 1 or stride == 1 or (i % stride == 0):
                hand_index.append(i)
                before.append(cum_before)
                after.append(cum_after)

        return {
            "metric_id": self.id,
            "name": self.name,
            "hand_count": n,
            "total_profit_before_rake": before[-1] if before else 0.0,
            "total_profit_after_rake": after[-1] if after else 0.0,
            "total_rake_paid": round(sum(h.rake_share for h in hands), 6),
            "total_rake_only": round(sum(h.rake_only_share for h in hands), 6),
            "total_jackpot_share": round(sum(h.jackpot_share for h in hands), 6),
            "series": {
                "hand_index": hand_index,
                "profit_before_rake": before,
                "profit_after_rake": after,
            },
            "series_downsampled": stride > 1,
            "series_stride": stride,
        }
