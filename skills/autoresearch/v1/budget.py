"""
C1 autoresearch — budget enforcement.
Hard caps: per-round and overall. Never exceed total even if a round under-spent.
"""

from __future__ import annotations


class BudgetExceeded(Exception):
    pass


class Budget:
    """Hard budget enforcer for autoresearch loops."""

    def __init__(self, rounds: int, usd: float, tokens: int) -> None:
        self.max_rounds = rounds
        self.max_usd = usd
        self.max_tokens = tokens

        self.spent_usd = 0.0
        self.spent_tokens = 0
        self.rounds_used = 0

    def check_round(self, usd: float, tokens: int) -> None:
        """
        Record usage for one round. Raises BudgetExceeded if any cap is hit.
        Enforcement is strict: even partial overrun raises.
        """
        if self.rounds_used >= self.max_rounds:
            raise BudgetExceeded(
                f"Round cap reached: {self.rounds_used}/{self.max_rounds}"
            )

        new_usd = self.spent_usd + usd
        new_tokens = self.spent_tokens + tokens

        if new_usd > self.max_usd:
            raise BudgetExceeded(
                f"USD cap exceeded: would spend ${new_usd:.4f} > ${self.max_usd:.4f}"
            )
        if new_tokens > self.max_tokens:
            raise BudgetExceeded(
                f"Token cap exceeded: {new_tokens} > {self.max_tokens}"
            )

        self.spent_usd = round(new_usd, 6)
        self.spent_tokens = new_tokens
        self.rounds_used += 1

    @property
    def remaining_usd(self) -> float:
        return round(self.max_usd - self.spent_usd, 6)

    @property
    def remaining_tokens(self) -> int:
        return self.max_tokens - self.spent_tokens

    def summary(self) -> dict:
        return {
            "rounds_used": self.rounds_used,
            "max_rounds": self.max_rounds,
            "spent_usd": self.spent_usd,
            "max_usd": self.max_usd,
            "spent_tokens": self.spent_tokens,
            "max_tokens": self.max_tokens,
        }
