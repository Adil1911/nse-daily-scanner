"""Reversal and trigger candles on the last completed daily bar."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    o: float
    h: float
    l: float
    c: float

    @property
    def rng(self) -> float:
        return self.h - self.l

    @property
    def body(self) -> float:
        return abs(self.c - self.o)

    @property
    def upper(self) -> float:
        return self.h - max(self.o, self.c)

    @property
    def lower(self) -> float:
        return min(self.o, self.c) - self.l

    @property
    def bullish(self) -> bool:
        return self.c > self.o

    @property
    def bearish(self) -> bool:
        return self.c < self.o

    @property
    def close_position(self) -> float:
        """Where the close sits in the day's range: 0 at the low, 1 at the high."""
        return (self.c - self.l) / self.rng if self.rng > 0 else 0.5


def hammer(b: Bar) -> bool:
    if b.rng <= 0:
        return False
    return b.lower >= 2.0 * b.body and b.lower >= 0.5 * b.rng and b.upper <= 0.25 * b.rng


def bullish_engulfing(prev: Bar, cur: Bar) -> bool:
    return prev.bearish and cur.bullish and cur.o <= prev.c and cur.c >= prev.o


def piercing(prev: Bar, cur: Bar) -> bool:
    if not (prev.bearish and cur.bullish) or prev.rng <= 0:
        return False
    if prev.body < 0.5 * prev.rng:
        return False
    mid = (prev.o + prev.c) / 2.0
    return cur.o < prev.c and mid < cur.c < prev.o


def inside_bar(prev: Bar, cur: Bar) -> bool:
    return cur.h < prev.h and cur.l > prev.l


def long_lower_wick(b: Bar) -> bool:
    return b.rng > 0 and b.lower >= 0.5 * b.rng and b.close_position >= 0.5


def upper_half_close(b: Bar) -> bool:
    return b.rng > 0 and b.close_position >= 0.5


LABELS = {
    "bullish_engulfing": "Bullish Engulfing",
    "hammer": "Hammer",
    "piercing": "Piercing",
    "inside_bar": "Inside Bar",
    "long_lower_wick": "Long lower wick",
    "upper_half_close": "Close in upper half",
}

# Strongest first: when several apply, the first one is reported.
TRIGGERS_A = ("bullish_engulfing", "hammer", "piercing", "inside_bar")
TRIGGERS_C = ("bullish_engulfing", "hammer", "long_lower_wick", "upper_half_close")


def detect(prev: Bar, cur: Bar) -> list[str]:
    found = []
    if bullish_engulfing(prev, cur):
        found.append("bullish_engulfing")
    if hammer(cur):
        found.append("hammer")
    if piercing(prev, cur):
        found.append("piercing")
    if inside_bar(prev, cur):
        found.append("inside_bar")
    if long_lower_wick(cur):
        found.append("long_lower_wick")
    if upper_half_close(cur):
        found.append("upper_half_close")
    return found


def first_trigger(found: list[str], allowed: tuple[str, ...]) -> str | None:
    for key in allowed:
        if key in found:
            return key
    return None
