"""
simulate.py

Ties order_book.py and market_maker.py together:

  for t in range(n_steps):
      1. advance the "true" price random walk
      2. generate a batch of noise-trader orders and submit them to the book
      3. every `requote_every` steps, the market maker cancels its old
         quotes and posts new bid/ask quotes based on Avellaneda-Stoikov
      4. record inventory / cash / mark-to-market P&L

Run this file directly to execute a default simulation and save plots.
"""

from __future__ import annotations

import argparse
import csv
import os
import random
from typing import List

from order_book import LimitOrderBook, Order, OrderType, Side
from market_maker import AvellanedaStoikovMarketMaker, NoiseTraderFlow


def seed_book(book: LimitOrderBook, flow: NoiseTraderFlow, levels: int = 5, size: float = 2.0) -> None:
    """Seed the book with a few resting noise-trader limit orders so the
    market maker has a mid price to quote around on step 0."""
    mid = flow.true_price
    for i in range(1, levels + 1):
        book.submit(Order(side=Side.BUY, price=round(mid - 0.05 * i, 2), qty=size,
                           order_type=OrderType.LIMIT, timestamp=0.0, owner="flow"))
        book.submit(Order(side=Side.SELL, price=round(mid + 0.05 * i, 2), qty=size,
                           order_type=OrderType.LIMIT, timestamp=0.0, owner="flow"))


def run_simulation(
    n_steps: int = 2000,
    requote_every: int = 1,
    seed: int = 42,
    gamma: float = 0.1,
    k: float = 1.5,
    sigma: float = 0.02,
    max_inventory: float = 20.0,
    quote_size: float = 1.0,
):
    random.seed(seed)
    flow = NoiseTraderFlow(start_price=100.0, price_vol=sigma, seed=seed)
    book = LimitOrderBook(tick_size=0.01)
    seed_book(book, flow)

    mm = AvellanedaStoikovMarketMaker(
        gamma=gamma, k=k, sigma=sigma, horizon=float(n_steps),
        quote_size=quote_size, max_inventory=max_inventory,
    )

    spread_log: List[float] = []

    for step in range(n_steps):
        t = float(step)
        flow.step_true_price()

        # Noise traders submit orders (may trade against MM's resting quotes).
        for order in flow.generate_orders(t):
            fills = book.submit(order)
            mm.apply_fills(fills)

        # Market maker refreshes its quotes periodically.
        if step % requote_every == 0:
            mm.refresh_quotes(book, t)

        mid = book.mid_price()
        if mid is None:
            mid = flow.true_price
        mm.record(t, mid)
        sp = book.spread()
        spread_log.append(sp if sp is not None else float("nan"))

    return mm, book, flow, spread_log


def save_results_csv(mm: AvellanedaStoikovMarketMaker, path: str) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["t", "mid", "inventory", "cash", "pnl"])
        writer.writeheader()
        for row in mm.history:
            writer.writerow(row)


def plot_results(mm: AvellanedaStoikovMarketMaker, spread_log: List[float], out_path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    ts = [h["t"] for h in mm.history]
    mids = [h["mid"] for h in mm.history]
    inv = [h["inventory"] for h in mm.history]
    pnl = [h["pnl"] for h in mm.history]

    fig, axes = plt.subplots(4, 1, figsize=(10, 12), sharex=True)

    axes[0].plot(ts, mids, color="black", linewidth=0.8)
    axes[0].set_title("Mid price")
    axes[0].set_ylabel("Price")

    axes[1].plot(ts, inv, color="tab:blue", linewidth=0.8)
    axes[1].axhline(0, color="gray", linewidth=0.5, linestyle="--")
    axes[1].set_title("Market maker inventory")
    axes[1].set_ylabel("Inventory (units)")

    axes[2].plot(ts, pnl, color="tab:green", linewidth=0.8)
    axes[2].set_title("Mark-to-market P&L")
    axes[2].set_ylabel("P&L ($)")

    axes[3].plot(ts, spread_log, color="tab:red", linewidth=0.6)
    axes[3].set_title("Quoted book spread")
    axes[3].set_ylabel("Spread ($)")
    axes[3].set_xlabel("Time step")

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Limit order book + Avellaneda-Stoikov market maker simulation")
    parser.add_argument("--steps", type=int, default=2000)
    parser.add_argument("--requote-every", type=int, default=1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--gamma", type=float, default=0.05, help="risk aversion")
    parser.add_argument("--k", type=float, default=50.0, help="book liquidity / fill-decay parameter")
    parser.add_argument("--sigma", type=float, default=0.02, help="assumed price volatility per step")
    parser.add_argument("--max-inventory", type=float, default=20.0)
    parser.add_argument("--quote-size", type=float, default=1.0)
    parser.add_argument("--outdir", type=str, default="/mnt/user-data/outputs")
    args = parser.parse_args()

    mm, book, flow, spread_log = run_simulation(
        n_steps=args.steps,
        requote_every=args.requote_every,
        seed=args.seed,
        gamma=args.gamma,
        k=args.k,
        sigma=args.sigma,
        max_inventory=args.max_inventory,
        quote_size=args.quote_size,
    )

    os.makedirs(args.outdir, exist_ok=True)
    csv_path = os.path.join(args.outdir, "mm_simulation_results.csv")
    plot_path = os.path.join(args.outdir, "mm_simulation_plots.png")

    save_results_csv(mm, csv_path)
    plot_results(mm, spread_log, plot_path)

    final = mm.history[-1]
    n_trades_mm = sum(1 for tr in book.trades if tr.resting_owner == "mm")
    print(f"Steps simulated:        {args.steps}")
    print(f"Trades filled vs MM:    {n_trades_mm}")
    print(f"Final inventory:        {final['inventory']:.2f}")
    print(f"Final cash:             {final['cash']:.2f}")
    print(f"Final mark-to-market:   {final['pnl']:.2f}")
    print(f"Saved CSV to:           {csv_path}")
    print(f"Saved plots to:         {plot_path}")


if __name__ == "__main__":
    main()
