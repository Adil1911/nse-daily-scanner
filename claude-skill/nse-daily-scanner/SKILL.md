---
name: nse-daily-scanner
description: Daily NSE pre-market brief for swing trading. Reads the Nifty 500 scan GitHub runs every weekday at 06:47 IST (monthly, weekly, daily and hourly charts against Strategy A pullback, B breakout and C bounce rules), checks news and results dates for the picks, and publishes the report with entry, stop-loss, target, quantity and charts. Use for "aaj kaunse stock lun", "aaj ka report", pre-market brief, daily scan, "market kaisa hai aaj", risky stocks, watchlist, or a Nifty 500 stock's setup today.
---

# NSE Daily Scanner

A GitHub Action in **Adil1911/nse-daily-scanner** scans the Nifty 500 every weekday at 06:47 IST and force-pushes its output to the repo's `data` branch:

| file | what |
|---|---|
| `latest.json` | `regime`, `entries`, `watchlist`, `risky`, `sectors`, `stocks` (every scanned stock, short keys), `charts`, `warnings` |
| `report.html` | the plain page, without the news check |
| `picks_log.csv` | every day's entries and watchlist |

This skill turns that output into the user's brief. The user swing-trades Indian equities and writes in Hinglish, so reply in Hinglish.

**Report page:** https://claude.ai/artifact/JwHHQhPwFdhV2Jbu1rVCG4 (always update this artifact; never create a new one for a daily report).

## 1. Get the data

```bash
D=<scratchpad>/nse; rm -rf "$D"; mkdir -p "$D"
git clone -q --depth 1 --branch data https://github.com/Adil1911/nse-daily-scanner "$D/data"
git clone -q --depth 1 https://github.com/Adil1911/nse-daily-scanner "$D/code"
```

The repo is public, so the clone needs no login. If it fails, say so in one line and stop.

**Freshness.** `as_of` is the session the data closes on; `next_session` is the day the orders are for. Work out the last NSE session before now in India time (weekends and the `nse_holidays` in `$D/code/config.json` are closed; today counts only after 16:00). If `as_of` is older than that, today's run failed or has not finished: say so and offer to start it. To start it, call `add_repo(owner="Adil1911", repo="nse-daily-scanner", access="push")`, then run
`gh api -X POST repos/Adil1911/nse-daily-scanner/actions/workflows/daily-scan.yml/dispatches -f ref=main`.
A run takes about 10 minutes; `gh api "repos/Adil1911/nse-daily-scanner/actions/runs?per_page=1"` shows its status. `gh workflow run` fails here because GraphQL is blocked, so always use `gh api`.

## 2. The daily report (scheduled run, or "aaj ka report")

1. Read `latest.json` with a short Python script; it is large, so never print it whole. Note `regime`, `entries`, `watchlist`, `risky` and `warnings`.
2. **Check every entry** (at most 10; Strategy C first, because it depends most on news):
   - News: search `<company name> share news` for the last 7-10 days. Company-specific bad news is a **concern**: a results miss, a downgrade, a fraud or governance issue, regulatory action, a large promoter sale or pledge. Market-wide or sector-wide moves are not.
   - Results date: when `results.status` is `unverified`, search `<company name> board meeting results date <month year>`; NSE's corporate announcements are the best source.
   - Verdict **avoid** when results fall within 5 sessions of `next_session` (A, B) or 3 sessions (C), when a C stock has any concern, or when any stock has a fraud, governance or regulatory concern. Otherwise **keep**.
3. Write `$D/notes.json`:

   ```json
   {"summary": "2-3 short Hinglish lines: regime, how many entries survived the checks, the one thing to watch",
    "checked_at": "<now, ISO 8601 with +05:30>",
    "stocks": {"SYMBOL": {"news": "ok | concern | unverified",
                          "note": "one Hinglish line, with the source",
                          "results": "YYYY-MM-DD, or \"none\" when checked and nothing is due soon, or null when unchecked",
                          "verdict": "keep | avoid"}}}
   ```

4. Build the page:
   `python3 -I $D/code/report/build_report.py --data $D/data/latest.json --notes $D/notes.json --out $D/report.html`
   Add `--capital N` only if the user told you their capital in this conversation; the page has its own capital box that remembers what they type.
5. Load the `artifact-design` skill (page contract). Read the report page once with the Artifact tool (`action: "read"`, `url` above), then publish `$D/report.html` with `url` set to the report page. The page is regenerated every day, so today's file replaces yesterday's in full.
6. Reply in 3-6 lines: the regime in plain words, each entry (symbol, strategy, entry / stop-loss / target 1), anything removed and why, and that the page is updated. In a scheduled run nobody is watching, so send this with SendUserMessage.

## 3. Questions during the day

Answer from `latest.json` and say the data is as of the `as_of` close.

- "Aaj kaunse stock?": the entries with entry, stop-loss, target 1 and R:R, then the watchlist triggers.
- "Market kaisa hai?": `regime.headline`, Nifty against its 50 and 200 DMA, India VIX, breadth.
- "<SYMBOL> kaisa hai?": `stocks[SYMBOL]`. Keys: `n` name, `s` sector, `cap`, `c` close, `ch` change %, `r14` RSI(14), `a50`/`a200` above the 50/200 DMA, `e20` % from the 20 EMA, `r3m` 3-month return %, `rs` 3-month return vs Nifty in points, `atv` daily turnover in ₹ crore, `d52` % from the 52-week high, `atr` ATR %, `A`/`B`/`C` strategy state, `fl` risk flags. If it is in `entries` or `watchlist`, give its plan too. Stocks outside the Nifty 500 are not in the data: say so.
- "Kitne shares?": quantity = floor(capital × risk% ÷ (entry − stop)), capped at capital × max_position_pct ÷ entry. Both numbers are in each item's `sizing`; Strategy C is half size, and the HALF and CAUTION regimes halve Strategy A.
- For a full trade plan or a backtest of one setup, the user's own skills (trend-pullback-swing, consolidation-breakout-swing, oversold-bounce-swing) hold the detailed rules.

## Rules

- Regime first. When `regime.state` is NO_LONGS, say plainly that there are no new longs today, and give no buy list.
- These are setups that meet the user's own rules, never buy recommendations. Zero entries is a normal answer; never loosen a rule to fill a list.
- Every number comes from `latest.json` or a source you name. Never estimate a price.
- Entries are buy-stop orders: the trade exists only if price trades above the entry. A stop-loss is a plan, not a guarantee; a gap can open beyond it.
- Yahoo Finance data is free and can lag, so the user should confirm prices on their broker before ordering.
- Never place an order or act on the user's broker account.
