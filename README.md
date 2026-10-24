# NSE Daily Scanner

Roz subah market khulne se pehle Nifty 500 ke har stock ko aapki teen swing strategies ke rules pe check karta hai, aur ek report banata hai: aaj kis stock pe entry ka setup hai, kaun se watchlist pe hain, aur kaun se abhi risky hain.

| Strategy | Setup | Size |
|---|---|---|
| **A · Trend Pullback** | Uptrend mein 20 EMA tak halka, kam-volume pullback + trigger candle | 1% risk |
| **B · Consolidation Breakout** | 30%+ tezi ke baad tight, contracting base; pivot ke upar volume ke saath | 1% risk |
| **C · Oversold Bounce** | Rising 200 DMA ke upar wala stock 3-5 din tezi se gira + reversal candle | 0.5% risk (half) |

## Roz kya hota hai

1. **06:47 IST (Mon–Fri)**: GitHub Action chalta hai (`.github/workflows/daily-scan.yml`).
2. NSE se Nifty 500 ki list, Yahoo Finance se har stock ka 5 saal ka daily data, aur top setups ka hourly data.
3. **Regime pehle**: Nifty 50 vs 50/200 DMA aur India VIX. Nifty 200 DMA ke neeche ho to koi naya long nahi.
4. Har stock: monthly, weekly, daily aur hourly chart; A, B, C ke saare rules; risk ke nishan (200 DMA toda, bhaari volume pe girawat, gap-down, fail breakout, results paas, F&O ban).
5. Result `data` branch pe: `latest.json`, `report.html`, `picks_log.csv` (har din ke picks ka record).
6. Claude (scheduled task) news aur results date check karke report publish karta hai.

## Report kaise padhein

- **Aaj ki entries**: saare rules poore. Entry ek *buy-stop* hai: price us level ke upar jaaye tabhi trade bane. Stop-loss aur Target 1 diye hain; quantity aapke capital pe nikalti hai.
- **M W D H chips**: monthly, weekly, daily, hourly trend. Jitne zyada ▲, utna achha.
- **Watchlist**: setup lagbhag taiyaar, trigger candle ya pivot ka intezaar.
- **Risky**: zyada trade hone wale stocks jinke chart pe taaza nuksaan ke nishan hain.

## Khud chalana ho to

GitHub pe **Actions → Daily NSE scan → Run workflow**. Sirf kuch stocks: `symbols` mein `RELIANCE,TCS` likhein.

Local:

```bash
pip install -r requirements.txt
python -m scanner run --out out            # poora scan
python report/build_report.py --data out/latest.json --out out/report.html
python -m unittest discover -s tests       # tests (offline)
```

## Settings

`config.json` mein saare numbers hain (liquidity ₹10 Cr, pullback 4-12%, base 15-40 session, stop ki limit 7/8/6%, wagaira). NSE holidays bhi wahin hain: **2027 ki list aate hi add karni hai.**

## Dhyan rahe

- Ye rules poore karne wale setups ki list hai, kharidne ki salah nahi. SEBI-registered adviser ki jagah nahi.
- Yahoo Finance ka data free aur unofficial hai: late ho sakta hai. Order se pehle broker pe price dekh lein.
- Stop-loss ek plan hai, guarantee nahi: gap mein price stop ke paar khul sakta hai.
- F&O ban list aur results calendar NSE se "best effort" aate hain; na milein to report mein likha hota hai.
