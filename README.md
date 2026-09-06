# Limit Order Book Simulator + Avellaneda-Stoikov Market Maker

A synthetic limit order book matching engine, filled with noise-traders, and an Avellaneda-Stoikov market-making agent on top of that.

## Files

- `order_book.py` - the matching engine. Price-time priority Accepts LIMIT and MARKET orders Records all trades-
- `market_maker.py` — two parts:
  - `NoiseTraderFlow`: generates a stochastic "true price" and Poisson-arrival noise-trader orders (a combination of market and limit orders) so the book has realistic two-sided activity.
  - `AvellanedaStoikovMarketMaker`: the quoting agent. Implements the classical Avellaneda-Stoikov (2008) model:
    - **Reservation price** `r = mid - q * gamma * sigma^2 * (T - t)` - skews your center price away from mid based on your current inventory `q`. If you are long, `r < mid`, so you quote more aggressively to sell.
    - **Optimal spread**: `delta = gamma * sigma^2 * (T - t) + (2/gamma) * ln(1 + gamma/k)`
    - Quotes bid = r - delta/2 ask = r + delta/2- `simulate.py` — runs the simulation loop and generates plots + a CSV of the full inventory/cash/P&L history. Execute this file directly.

## Running it

```bash
pip install matplotlib
python3 simulate.py --steps 2000
```

Outputs (by default written to `/mnt/user-data/outputs`, override with
`--outdir`):
- `mm_simulation_results.csv` — full time series (mid price, inventory,
  cash, mark-to-market P&L)
- `mm_simulation_plots.png` — four panels: mid price, MM inventory, P&L,
  and quoted spread

### Key parameters to experiment with

| Flag | Meaning | What happens if you increase it |
|---|---|---|
| `--gamma` | Risk aversion | Wider quotes, more aggressive inventory skewing (MM fights harder to stay flat, at the cost of narrower spread capture) | | `--k` | Book liquidity / fill-intensity decay | Higher = MM assumes it can quote wider without losing fills, so spreads widen |
| `--sigma` | Assumed volatility | Higher = wider quotes and more aggressive inventory skewing (MM is more scared of holding risk) | | `--max-inventory` | Hard inventory cap | Lower = MM stops quoting on a side sooner once it's built up a position, capping downside but also capping spread capture | | `--quote-size` | Size posted per quote | Larger = faster inventory swings, more P&L variance |

**A note on calibration**: the two terms in the spread formula (`gamma * sigma^2 * (T-t)` and `(2/gamma) * ln(1 + gamma/k)`) need to be on a similar *scale* to the book's natural spread (determined by the noise-trader flow's `limit_offset_std` in `market_maker.py`), otherwise the MM's quotes will either (a) never be competitive, so it trades very little, or (b) be so tight that it captures no edge. The included `simulate.py` main.py picks working defaults (`gamma=0.05`, `k=50`), but if you change `--steps` (which is a model's `T` since we don't normalize time at the moment), you'll need to re-tune `gamma`/`k` — see the docstring math in `market_maker.py` for the relationship.

## What this demonstrates (and its current limitations)

Running the default simulation shows the mark-to-market P&L trending up overall, but has visible volatility. The MM accumulates a large position during a sustained price move, and thus takes on real mark-to-market risk during that stretch. This is the central tension in market making: the spread you capture on every fill is small and steady, but holding inventory through a directional move can result in serious losses

Known simplifications worth improving as next steps:
1. **No adverse selection modeling.** The noise-trader flow doesn't react to the MM's own quotes or "know" anything, real markets have informed traders who pick off stale quotes. See how much this might hurt the naive AS model.
2. **No competing market makers.** Right now the MM is the only liquidity-providing agent besides resting noise-trader limit orders. Adding a second MM with different parameters would help us study spread competition.
