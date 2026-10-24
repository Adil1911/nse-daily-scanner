"""Indian-style number formatting for text the user reads (₹1,23,456.50, 5.4 lakh)."""

from __future__ import annotations


def indian(n: float, nd: int = 2) -> str:
    neg = n < 0
    whole, _, frac = f"{abs(n):.{nd}f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return ("−" if neg else "") + whole + (f".{frac}" if nd else "")


def inr(n: float, nd: int = 2) -> str:
    return "₹" + indian(n, nd)


def shares(n: float) -> str:
    if n >= 1e7:
        return f"{n / 1e7:.1f} crore"
    if n >= 1e5:
        return f"{n / 1e5:.1f} lakh"
    return indian(n, 0)
