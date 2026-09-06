"""
market_maker.py

Two pieces:

1. NoiseTraderFlow: generates synthetic market/limit orders from "noise
   traders" so the book has realistic-ish activity for the market maker to
   quote against. Order arrivals follow a Poisson process; the underlying
   "true" price follows a simple random walk so the noise flow has a
   directional signal for the market maker to react to (and get run over
   by, if it's not managing inventory well).

2. AvellanedaStoikovMarketMaker: implements the classic Avellaneda-Stoikov
   (2008) quoting model. Given:
     - s: current mid/reference price
     - q: current inventory (signed; +ve = long, -ve = short)
     - sigma: volatility of the underlying
     - gamma: risk aversion
     - k: order book liquidity/decay parameter (how fast fill probability
          drops off as you quote further from mid)
     - T - t: time remaining in the trading horizon

   it computes:
     - reservation price r = s - q * gamma * sigma^2 * (T - t)
         (skews your "center" away from mid, away from your inventory:
          if you're long, r < s, meaning you want to sell more eagerly)
     - optimal spread  delta = gamma * sigma^2 * (T - t) + (2/gamma) * ln(1 + gamma/k)

   Quotes are then: bid = r - delta/2,  ask = r + delta/2
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from order_book import LimitOrderBook, Order, OrderType, Side


# Synthetic order flow

@dataclass
class NoiseTraderFlow:
    """
    Generates a "true" underlying price random walk plus noise-trader
    order submissions around it
    """
    start_price: float = 100.0
    price_vol: float = 0.02          # per-step std dev of the true price random walk
    order_arrival_rate: float = 3.0  # expected number of noise orders per time step
    market_order_prob: float = 0.35  # fraction of noise orders that are marketable
    limit_offset_std: float = 0.05   # how far from true price limit orders are placed
    order_size_mean: float = 1.0
    order_size_std: float = 0.4
    seed: Optional[int] = None

    def __post_init__(self):
        self._rng = random.Random(self.seed)
        self.true_price = self.start_price

    def step_true_price(self) -> float:
        self.true_price += self._rng.gauss(0, self.price_vol)
        self.true_price = max(self.true_price, 0.01)
        return self.true_price

    def _sample_size(self) -> float:
        return max(0.1, self._rng.gauss(self.order_size_mean, self.order_size_std))

    def generate_orders(self, t: float) -> List[Order]:
        """Generate this step's batch of noise-trader orders."""
        n_orders = self._rng_poisson(self.order_arrival_rate)
        orders = []
        for _ in range(n_orders):
            side = self._rng.choice([Side.BUY, Side.SELL])
            qty = self._sample_size()

            if self._rng.random() < self.market_order_prob:
                orders.append(Order(side=side, price=None, qty=qty,
                                     order_type=OrderType.MARKET,
                                     timestamp=t, owner="flow"))
            else:
                offset = abs(self._rng.gauss(0, self.limit_offset_std))
                if side == Side.BUY:
                    price = round(self.true_price - offset, 2)
                else:
                    price = round(self.true_price + offset, 2)
                price = max(price, 0.01)
                orders.append(Order(side=side, price=price, qty=qty,
                                     order_type=OrderType.LIMIT,
                                     timestamp=t, owner="flow"))
        return orders

    def _rng_poisson(self, lam: float) -> int:
        # Knuth's algorithm; avoids a numpy dependency for this one draw.
        L = math.exp(-lam)
        k = 0
        p = 1.0
        while True:
            k += 1
            p *= self._rng.random()
            if p <= L:
                return k - 1



# Avellaneda-Stoikov market maker

@dataclass
class AvellanedaStoikovMarketMaker:
    gamma: float = 0.1        # risk aversion
    k: float = 1.5            # book liquidity / fill-intensity decay parameter
    sigma: float = 0.02       # assumed volatility of the underlying (per step)
    horizon: float = 1.0      # total trading horizon T, in the same units as t
    quote_size: float = 1.0   # size posted on each side per quote refresh
    max_inventory: Optional[float] = None  # optional hard inventory cap

    inventory: float = field(default=0.0, init=False)
    cash: float = field(default=0.0, init=False)
    active_bid_id: Optional[int] = field(default=None, init=False)
    active_ask_id: Optional[int] = field(default=None, init=False)

    history: List[dict] = field(default_factory=list, init=False)

    def reservation_price(self, mid: float, t: float) -> float:
        time_left = max(self.horizon - t, 1e-6)
        return mid - self.inventory * self.gamma * (self.sigma ** 2) * time_left

    def optimal_spread(self, t: float) -> float:
        time_left = max(self.horizon - t, 1e-6)
        return (self.gamma * (self.sigma ** 2) * time_left
                + (2.0 / self.gamma) * math.log(1.0 + self.gamma / self.k))

    def desired_quotes(self, mid: float, t: float) -> Tuple[float, float]:
        r = self.reservation_price(mid, t)
        delta = self.optimal_spread(t)
        bid = r - delta / 2.0
        ask = r + delta / 2.0
        return round(bid, 2), round(ask, 2)

    def _quote_qty(self, side: Side) -> float:
        """Shrink (or zero out) the quote on a side if it would breach an inventory cap."""
        if self.max_inventory is None:
            return self.quote_size
        if side == Side.BUY and self.inventory + self.quote_size > self.max_inventory:
            return max(0.0, self.max_inventory - self.inventory)
        if side == Side.SELL and self.inventory - self.quote_size < -self.max_inventory:
            return max(0.0, self.inventory + self.max_inventory)
        return self.quote_size

    def refresh_quotes(self, book: LimitOrderBook, t: float) -> None:
        """Cancel stale quotes and post fresh bid/ask limit orders."""
        if self.active_bid_id is not None:
            book.cancel(self.active_bid_id)
            self.active_bid_id = None
        if self.active_ask_id is not None:
            book.cancel(self.active_ask_id)
            self.active_ask_id = None

        mid = book.mid_price()
        if mid is None:
            return  # can't quote without a reference price yet

        bid_price, ask_price = self.desired_quotes(mid, t)

        bid_qty = self._quote_qty(Side.BUY)
        ask_qty = self._quote_qty(Side.SELL)

        if bid_qty > 1e-9:
            bid_order = Order(side=Side.BUY, price=bid_price, qty=bid_qty,
                               order_type=OrderType.LIMIT, timestamp=t, owner="mm")
            book.submit(bid_order)
            self.active_bid_id = bid_order.order_id

        if ask_qty > 1e-9:
            ask_order = Order(side=Side.SELL, price=ask_price, qty=ask_qty,
                               order_type=OrderType.LIMIT, timestamp=t, owner="mm")
            book.submit(ask_order)
            self.active_ask_id = ask_order.order_id

    def apply_fills(self, fills) -> None:
        """Update inventory/cash from trades where the MM was the resting side."""
        for tr in fills:
            if tr.resting_owner != "mm":
                continue
            if tr.aggressor_side == Side.BUY:
                # aggressor bought, meaning MM's resting SELL got hit -> MM sold
                self.inventory -= tr.qty
                self.cash += tr.qty * tr.price
            else:
                # aggressor sold into MM's resting BUY -> MM bought
                self.inventory += tr.qty
                self.cash -= tr.qty * tr.price

    def mark_to_market(self, mid: float) -> float:
        return self.cash + self.inventory * mid

    def record(self, t: float, mid: float) -> None:
        self.history.append({
            "t": t,
            "mid": mid,
            "inventory": self.inventory,
            "cash": self.cash,
            "pnl": self.mark_to_market(mid),
        })
