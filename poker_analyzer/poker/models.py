from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Action:
    """One betting action (any player) on a street."""

    street: str  # preflop | flop | turn | river
    player: str
    action: str  # fold | check | call | bet | raise | posts*
    amount: float = 0.0  # chips added by this action
    to_amount: float = 0.0  # street total after a raise ("raises to $X")
    pot_before: float = 0.0
    is_hero: bool = False


@dataclass
class Hand:
    """Normalized representation of a single poker hand."""

    hand_id: str
    datetime: datetime
    table_name: str
    stakes: str
    max_players: int
    hero_seat: int | None
    hero_cards: str | None
    hero_invested: float
    hero_collected: float
    hero_returned: float
    total_pot: float
    rake: float
    jackpot: float
    bingo: float
    fortune: float
    tax: float
    source_file: str
    raw_summary: str = ""
    went_to_flop: bool = False
    flop_cards: tuple[str, ...] = ()
    hero_vpip: bool = False
    button_seat: int | None = None
    seat_names: dict[int, str] = field(default_factory=dict)
    actions: list[Action] = field(default_factory=list)
    shown_cards: dict[str, tuple[str, ...]] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def fees(self) -> float:
        """All known pot deductions, including site-specific fees in ``extra``."""
        additional = self.extra.get("additional_fees", 0.0)
        if not isinstance(additional, (int, float)):
            additional = 0.0
        return self.rake + self.jackpot + self.bingo + self.fortune + self.tax + additional

    @property
    def net_invested(self) -> float:
        """Chips Hero put into the pot after uncalled returns."""
        return round(self.hero_invested - self.hero_returned, 6)

    @property
    def profit_after_rake(self) -> float:
        """Real chip delta for Hero (after rake)."""
        return round(self.hero_collected - self.net_invested, 6)

    def _collector_share(self, amount: float) -> float:
        if self.hero_collected <= 0 or amount <= 0:
            return 0.0
        total_collected = self.extra.get("total_collected")
        if not total_collected or total_collected <= 0:
            total_collected = max(self.total_pot - self.fees, self.hero_collected)
        return round(amount * (self.hero_collected / total_collected), 6)

    @property
    def rake_share(self) -> float:
        """
        Pot fees attributed to Hero for pre-fee profit (rake + jackpot + ...).

        Winner-pays: proportional to collected amount; 0 if Hero did not collect.
        Matches site 'before fees' totals by adding back all known deductions.
        """
        return self._collector_share(self.fees)

    @property
    def rake_only_share(self) -> float:
        return self._collector_share(self.rake)

    @property
    def jackpot_share(self) -> float:
        return self._collector_share(self.jackpot)

    @property
    def profit_before_rake(self) -> float:
        """Chip delta as if Hero's share of pot fees (rake+jackpot+...) were returned."""
        return round(self.profit_after_rake + self.rake_share, 6)

    @property
    def stakes_key(self) -> str | None:
        """Normalized blinds key, e.g. '0.05/0.1'."""
        from poker.filters import normalize_stakes

        return normalize_stakes(self.stakes)


@dataclass
class HandDataset:
    """In-memory collection of parsed hands, chronological."""

    hands: list[Hand] = field(default_factory=list)
    source_label: str = "local"
    load_stats: dict[str, int] = field(default_factory=dict)

    def sorted_hands(self) -> list[Hand]:
        return sorted(self.hands, key=lambda h: (h.datetime, h.hand_id))
