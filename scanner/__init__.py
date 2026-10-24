"""NSE daily swing scanner.

Scans the Nifty 500 every morning for three swing setups:
  A  Trend Pullback        (shallow pullback to the 20 EMA in an uptrend)
  B  Consolidation Breakout (tight, contracting base after a strong advance)
  C  Oversold Bounce        (sharp dip in a stock still above a rising 200 DMA)

It reads monthly, weekly, daily and hourly charts, checks the market regime
first, and writes one JSON file (latest.json) that the report builder and
Claude read. It never places an order.
"""

__version__ = "1.0.0"
