"""
SPY 6-Month Intraday Pattern Analysis
======================================
Analyzes SPY 5-min data to find reliable directional patterns
that could improve signal quality for 0DTE options trading.
"""
import csv
import math
from collections import defaultdict
from datetime import date, time, timedelta

DATA_PATH = "data/SPY_5min_6months.csv"

# Load bars
bars_by_day = defaultdict(list)
with open(DATA_PATH) as f:
    reader = csv.DictReader(f)
    for row in reader:
        d = date.fromisoformat(row["Date"])
        bars_by_day[d].append({
            "time": row["Time"],
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
            "volume": int(row["Volume"]),
        })

dates = sorted(bars_by_day.keys())
print(f"Data: {dates[0]} to {dates[-1]}, {len(dates)} trading days")
print()

# ================================================================
# 1. OPENING RANGE BREAKOUT ACCURACY
# ================================================================
print("=" * 100)
print("1. OPENING RANGE BREAKOUT (ORB) — Does direction of ORB candle predict the day?")
print("=" * 100)

for orb_minutes in [15, 30]:
    orb_bars_count = orb_minutes // 5
    correct = 0
    total = 0
    call_wins = 0
    call_total = 0
    put_wins = 0
    put_total = 0

    for d in dates:
        day_bars = bars_by_day[d]
        if len(day_bars) < orb_bars_count + 6:
            continue

        orb = day_bars[:orb_bars_count]
        orb_open = orb[0]["open"]
        orb_close = orb[-1]["close"]
        orb_high = max(b["high"] for b in orb)
        orb_low = min(b["low"] for b in orb)
        orb_range = orb_high - orb_low

        if orb_range < 0.30:  # skip tiny range days
            continue

        # Rest of day after ORB
        rest = day_bars[orb_bars_count:]
        rest_high = max(b["high"] for b in rest)
        rest_low = min(b["low"] for b in rest)
        day_close = day_bars[-1]["close"]

        orb_bullish = orb_close > orb_open
        if orb_bullish:
            call_total += 1
            # Win = price went higher by at least 1x ORB range
            if rest_high > orb_high + orb_range * 0.5:
                call_wins += 1
                correct += 1
        else:
            put_total += 1
            if rest_low < orb_low - orb_range * 0.5:
                put_wins += 1
                correct += 1
        total += 1

    print(f"\n  ORB {orb_minutes}min:")
    print(f"    Bullish ORB → price extends higher: {call_wins}/{call_total} ({call_wins/call_total*100:.0f}%)" if call_total else "")
    print(f"    Bearish ORB → price extends lower:  {put_wins}/{put_total} ({put_wins/put_total*100:.0f}%)" if put_total else "")
    print(f"    Overall ORB direction accuracy: {correct}/{total} ({correct/total*100:.0f}%)" if total else "")

# ================================================================
# 2. FIRST 30-MIN TREND CONTINUATION VS REVERSAL
# ================================================================
print("\n" + "=" * 100)
print("2. FIRST 30-MIN MOVE — Continuation vs Mean Reversion")
print("=" * 100)

for threshold in [0.3, 0.5, 0.8]:
    continuation = 0
    reversal = 0
    total = 0

    for d in dates:
        day_bars = bars_by_day[d]
        if len(day_bars) < 20:
            continue

        open_price = day_bars[0]["open"]
        price_30m = day_bars[5]["close"]  # 30 min = 6 bars (9:30-10:00)
        move_30m = price_30m - open_price

        if abs(move_30m) < threshold:
            continue

        # Where does price go in the next 60 minutes (10:00 - 11:00)?
        next_bars = day_bars[6:18]
        if not next_bars:
            continue

        best_continuation = 0
        if move_30m > 0:
            best_continuation = max(b["high"] for b in next_bars) - price_30m
        else:
            best_continuation = price_30m - min(b["low"] for b in next_bars)

        next_close = next_bars[-1]["close"]
        continued = (move_30m > 0 and next_close > price_30m) or (move_30m < 0 and next_close < price_30m)

        total += 1
        if continued:
            continuation += 1
        else:
            reversal += 1

    print(f"\n  After >{threshold:.1f}pt move in first 30min:")
    print(f"    Continues (10:00-11:00): {continuation}/{total} ({continuation/total*100:.0f}%)" if total else "")
    print(f"    Reverses:                {reversal}/{total} ({reversal/total*100:.0f}%)" if total else "")

# ================================================================
# 3. VWAP RELATIONSHIP — How predictive is VWAP position?
# ================================================================
print("\n" + "=" * 100)
print("3. VWAP AS DIRECTIONAL FILTER")
print("=" * 100)

for entry_time_str, entry_idx in [("10:00", 6), ("10:30", 12), ("11:00", 18)]:
    above_vwap_wins = 0
    above_vwap_total = 0
    below_vwap_wins = 0
    below_vwap_total = 0

    for d in dates:
        day_bars = bars_by_day[d]
        if len(day_bars) < entry_idx + 12:
            continue

        # Compute VWAP up to entry
        cum_vol = 0
        cum_pv = 0
        for b in day_bars[:entry_idx + 1]:
            typical = (b["high"] + b["low"] + b["close"]) / 3
            cum_pv += typical * b["volume"]
            cum_vol += b["volume"]
        vwap = cum_pv / cum_vol if cum_vol > 0 else 0

        entry_price = day_bars[entry_idx]["close"]
        above_vwap = entry_price > vwap

        # Look at next 30 min (6 bars)
        future = day_bars[entry_idx + 1:entry_idx + 7]
        if not future:
            continue

        future_max = max(b["high"] for b in future)
        future_min = min(b["low"] for b in future)

        if above_vwap:
            above_vwap_total += 1
            # CALL: did it go up 0.3+ in next 30 min?
            if future_max - entry_price > 0.30:
                above_vwap_wins += 1
        else:
            below_vwap_total += 1
            if entry_price - future_min > 0.30:
                below_vwap_wins += 1

    print(f"\n  At {entry_time_str}:")
    if above_vwap_total:
        print(f"    Above VWAP → CALL works: {above_vwap_wins}/{above_vwap_total} ({above_vwap_wins/above_vwap_total*100:.0f}%)")
    if below_vwap_total:
        print(f"    Below VWAP → PUT works:  {below_vwap_wins}/{below_vwap_total} ({below_vwap_wins/below_vwap_total*100:.0f}%)")

# ================================================================
# 4. VOLUME CONFIRMATION — Does high volume improve signals?
# ================================================================
print("\n" + "=" * 100)
print("4. VOLUME CONFIRMATION — High volume breakout vs low volume")
print("=" * 100)

for orb_minutes in [15]:
    orb_bars_count = orb_minutes // 5

    high_vol_wins = 0
    high_vol_total = 0
    low_vol_wins = 0
    low_vol_total = 0

    for d in dates:
        day_bars = bars_by_day[d]
        if len(day_bars) < orb_bars_count + 12:
            continue

        orb = day_bars[:orb_bars_count]
        orb_high = max(b["high"] for b in orb)
        orb_low = min(b["low"] for b in orb)
        orb_vol = sum(b["volume"] for b in orb) / len(orb)
        orb_range = orb_high - orb_low

        if orb_range < 0.30:
            continue

        # Look for breakout in next 12 bars (60 min)
        for i in range(orb_bars_count, min(orb_bars_count + 12, len(day_bars))):
            bar = day_bars[i]
            breakout_up = bar["close"] > orb_high
            breakout_down = bar["close"] < orb_low

            if breakout_up or breakout_down:
                # Check if breakout bar has high volume
                high_vol = bar["volume"] > orb_vol * 1.5

                # Does it continue in breakout direction for next 6 bars?
                future = day_bars[i + 1:i + 7]
                if not future:
                    break

                if breakout_up:
                    win = max(b["high"] for b in future) > bar["close"] + orb_range * 0.3
                else:
                    win = min(b["low"] for b in future) < bar["close"] - orb_range * 0.3

                if high_vol:
                    high_vol_total += 1
                    if win:
                        high_vol_wins += 1
                else:
                    low_vol_total += 1
                    if win:
                        low_vol_wins += 1
                break

    print(f"\n  ORB {orb_minutes}min breakout:")
    if high_vol_total:
        print(f"    High volume breakout (>1.5x avg): {high_vol_wins}/{high_vol_total} ({high_vol_wins/high_vol_total*100:.0f}%)")
    if low_vol_total:
        print(f"    Low volume breakout:              {low_vol_wins}/{low_vol_total} ({low_vol_wins/low_vol_total*100:.0f}%)")

# ================================================================
# 5. PREVIOUS DAY CLOSE CONTEXT
# ================================================================
print("\n" + "=" * 100)
print("5. GAP ANALYSIS — Does gap direction predict intraday move?")
print("=" * 100)

gap_up_continue = 0
gap_up_fade = 0
gap_down_continue = 0
gap_down_fade = 0

for i in range(1, len(dates)):
    prev_d = dates[i - 1]
    curr_d = dates[i]

    prev_close = bars_by_day[prev_d][-1]["close"]
    curr_open = bars_by_day[curr_d][0]["open"]
    gap = curr_open - prev_close
    gap_pct = gap / prev_close * 100

    if abs(gap_pct) < 0.1:
        continue

    day_bars = bars_by_day[curr_d]
    if len(day_bars) < 20:
        continue

    # Where does SPY go in the first 2 hours?
    two_hr = day_bars[:24]
    mid_day_close = two_hr[-1]["close"]

    if gap > 0:  # gap up
        if mid_day_close > curr_open:
            gap_up_continue += 1
        else:
            gap_up_fade += 1
    else:  # gap down
        if mid_day_close < curr_open:
            gap_down_continue += 1
        else:
            gap_down_fade += 1

total_gaps = gap_up_continue + gap_up_fade + gap_down_continue + gap_down_fade
print(f"\n  Gap Up (>{0.1}%):")
g_up = gap_up_continue + gap_up_fade
if g_up:
    print(f"    Continues higher: {gap_up_continue}/{g_up} ({gap_up_continue/g_up*100:.0f}%)")
    print(f"    Fades/reverses:   {gap_up_fade}/{g_up} ({gap_up_fade/g_up*100:.0f}%)")

g_dn = gap_down_continue + gap_down_fade
if g_dn:
    print(f"  Gap Down:")
    print(f"    Continues lower: {gap_down_continue}/{g_dn} ({gap_down_continue/g_dn*100:.0f}%)")
    print(f"    Fades/reverses:  {gap_down_fade}/{g_dn} ({gap_down_fade/g_dn*100:.0f}%)")

# ================================================================
# 6. IDEAL ENTRY TIME — When do the best moves start?
# ================================================================
print("\n" + "=" * 100)
print("6. BEST ENTRY WINDOWS — Which time slots have biggest moves?")
print("=" * 100)

time_slots = [
    ("09:45-10:15", 3, 9),
    ("10:00-10:30", 6, 12),
    ("10:15-10:45", 9, 15),
    ("10:30-11:00", 12, 18),
    ("12:45-13:15", 39, 45),
    ("13:00-13:30", 42, 48),
    ("13:30-14:00", 48, 54),
    ("14:00-14:30", 54, 60),
]

print(f"  {'Window':15s} | {'Avg Move':>8s} | {'Avg Up':>8s} | {'Avg Down':>8s} | {'>$0.50 move':>11s} | {'Directional':>11s}")
print("  " + "-" * 80)

for label, start_idx, end_idx in time_slots:
    moves = []
    up_moves = []
    down_moves = []
    big_moves = 0

    for d in dates:
        day_bars = bars_by_day[d]
        if len(day_bars) <= end_idx:
            continue

        entry = day_bars[start_idx]["close"]
        best_up = max(b["high"] for b in day_bars[start_idx:end_idx + 1]) - entry
        best_down = entry - min(b["low"] for b in day_bars[start_idx:end_idx + 1])
        net = day_bars[end_idx]["close"] - entry

        moves.append(abs(net))
        if net > 0:
            up_moves.append(net)
        else:
            down_moves.append(abs(net))

        if max(best_up, best_down) > 0.50:
            big_moves += 1

    if moves:
        avg_move = sum(moves) / len(moves)
        avg_up = sum(up_moves) / len(up_moves) if up_moves else 0
        avg_down = sum(down_moves) / len(down_moves) if down_moves else 0
        directional = max(len(up_moves), len(down_moves)) / len(moves) * 100
        print(f"  {label:15s} | ${avg_move:>7.2f} | ${avg_up:>7.2f} | ${avg_down:>7.2f} | {big_moves:>4d}/{len(moves):3d} ({big_moves/len(moves)*100:.0f}%) | {directional:>5.0f}%")

# ================================================================
# 7. SIMULATED SIGNAL STRATEGIES — Win rate comparison
# ================================================================
print("\n" + "=" * 100)
print("7. SIGNAL STRATEGY WIN RATES — 30-min forward test")
print("=" * 100)

def compute_ema(closes, period):
    ema = [None] * len(closes)
    if len(closes) < period:
        return ema
    sma = sum(closes[:period]) / period
    ema[period - 1] = sma
    mult = 2.0 / (period + 1)
    for i in range(period, len(closes)):
        ema[i] = closes[i] * mult + ema[i-1] * (1 - mult)
    return ema

def compute_rsi(closes, period=14):
    rsi = [None] * len(closes)
    if len(closes) < period + 1:
        return rsi
    gains = []
    losses = []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        rsi[period] = 100
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100 - (100 / (1 + rs))
    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i-1]
        avg_gain = (avg_gain * (period - 1) + max(diff, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-diff, 0)) / period
        if avg_loss == 0:
            rsi[i] = 100
        else:
            rsi[i] = 100 - (100 / (1 + avg_gain / avg_loss))
    return rsi

strategies = {}

for d in dates:
    day_bars = bars_by_day[d]
    if len(day_bars) < 40:
        continue

    closes = [b["close"] for b in day_bars]
    ema8 = compute_ema(closes, 8)
    ema21 = compute_ema(closes, 21)
    rsi14 = compute_rsi(closes, 14)

    # VWAP
    cum_vol = 0
    cum_pv = 0
    vwap = []
    for b in day_bars:
        typical = (b["high"] + b["low"] + b["close"]) / 3
        cum_pv += typical * b["volume"]
        cum_vol += b["volume"]
        vwap.append(cum_pv / cum_vol if cum_vol > 0 else 0)

    # ORB 15min
    orb_bars = day_bars[:3]
    orb_high = max(b["high"] for b in orb_bars)
    orb_low = min(b["low"] for b in orb_bars)
    orb_open = orb_bars[0]["open"]
    orb_close_price = orb_bars[-1]["close"]
    orb_range = orb_high - orb_low
    orb_bullish = orb_close_price > orb_open

    for i in range(3, len(day_bars) - 6):  # need 6 bars forward
        t = day_bars[i]["time"]
        h, m = int(t[:2]), int(t[3:5])
        bar_minutes = h * 60 + m

        # Only trade 9:45 - 11:15
        if bar_minutes < 585 or bar_minutes > 675:
            continue

        price = day_bars[i]["close"]
        prev_price = day_bars[i-1]["close"]

        # Forward: best upside and downside in next 30 min
        future = day_bars[i+1:i+7]
        future_max = max(b["high"] for b in future)
        future_min = min(b["low"] for b in future)

        # Strategy 1: ORB breakout
        if orb_range >= 0.30 and i >= 3:
            if prev_price <= orb_high and price > orb_high:
                call_win = future_max - price > price * 0.005  # 0.5% move
                strat = "ORB breakout (CALL)"
                strategies.setdefault(strat, {"wins": 0, "total": 0, "pnls": []})
                strategies[strat]["total"] += 1
                if call_win:
                    strategies[strat]["wins"] += 1
                strategies[strat]["pnls"].append(future_max - price)

            if prev_price >= orb_low and price < orb_low:
                put_win = price - future_min > price * 0.005
                strat = "ORB breakdown (PUT)"
                strategies.setdefault(strat, {"wins": 0, "total": 0, "pnls": []})
                strategies[strat]["total"] += 1
                if put_win:
                    strategies[strat]["wins"] += 1
                strategies[strat]["pnls"].append(price - future_min)

        # Strategy 2: ORB + direction filter
        if orb_range >= 0.30 and i >= 3:
            if orb_bullish and prev_price <= orb_high and price > orb_high:
                call_win = future_max - price > price * 0.005
                strat = "ORB+Dir breakout (CALL)"
                strategies.setdefault(strat, {"wins": 0, "total": 0, "pnls": []})
                strategies[strat]["total"] += 1
                if call_win:
                    strategies[strat]["wins"] += 1
                strategies[strat]["pnls"].append(future_max - price)

            if not orb_bullish and prev_price >= orb_low and price < orb_low:
                put_win = price - future_min > price * 0.005
                strat = "ORB+Dir breakdown (PUT)"
                strategies.setdefault(strat, {"wins": 0, "total": 0, "pnls": []})
                strategies[strat]["total"] += 1
                if put_win:
                    strategies[strat]["wins"] += 1
                strategies[strat]["pnls"].append(price - future_min)

        # Strategy 3: EMA cross
        if ema8[i] and ema21[i] and ema8[i-1] and ema21[i-1]:
            if ema8[i-1] <= ema21[i-1] and ema8[i] > ema21[i]:
                win = future_max - price > price * 0.005
                strat = "EMA 8/21 cross (CALL)"
                strategies.setdefault(strat, {"wins": 0, "total": 0, "pnls": []})
                strategies[strat]["total"] += 1
                if win:
                    strategies[strat]["wins"] += 1
                strategies[strat]["pnls"].append(future_max - price)

            if ema8[i-1] >= ema21[i-1] and ema8[i] < ema21[i]:
                win = price - future_min > price * 0.005
                strat = "EMA 8/21 cross (PUT)"
                strategies.setdefault(strat, {"wins": 0, "total": 0, "pnls": []})
                strategies[strat]["total"] += 1
                if win:
                    strategies[strat]["wins"] += 1
                strategies[strat]["pnls"].append(price - future_min)

        # Strategy 4: VWAP + EMA alignment
        if ema8[i] and ema21[i]:
            above_vwap = price > vwap[i]
            ema_bullish = ema8[i] > ema21[i]

            if above_vwap and ema_bullish and prev_price <= vwap[i-1]:
                # VWAP reclaim with EMA confirmation
                win = future_max - price > price * 0.005
                strat = "VWAP reclaim + EMA (CALL)"
                strategies.setdefault(strat, {"wins": 0, "total": 0, "pnls": []})
                strategies[strat]["total"] += 1
                if win:
                    strategies[strat]["wins"] += 1
                strategies[strat]["pnls"].append(future_max - price)

            if not above_vwap and not ema_bullish and prev_price >= vwap[i-1]:
                win = price - future_min > price * 0.005
                strat = "VWAP break + EMA (PUT)"
                strategies.setdefault(strat, {"wins": 0, "total": 0, "pnls": []})
                strategies[strat]["total"] += 1
                if win:
                    strategies[strat]["wins"] += 1
                strategies[strat]["pnls"].append(price - future_min)

        # Strategy 5: RSI + VWAP
        if rsi14[i] is not None:
            if price > vwap[i] and rsi14[i] < 40:
                win = future_max - price > price * 0.005
                strat = "VWAP above + RSI<40 (CALL)"
                strategies.setdefault(strat, {"wins": 0, "total": 0, "pnls": []})
                strategies[strat]["total"] += 1
                if win:
                    strategies[strat]["wins"] += 1
                strategies[strat]["pnls"].append(future_max - price)

            if price < vwap[i] and rsi14[i] > 60:
                win = price - future_min > price * 0.005
                strat = "VWAP below + RSI>60 (PUT)"
                strategies.setdefault(strat, {"wins": 0, "total": 0, "pnls": []})
                strategies[strat]["total"] += 1
                if win:
                    strategies[strat]["wins"] += 1
                strategies[strat]["pnls"].append(price - future_min)

        # Strategy 6: ORB + VWAP + Volume
        if orb_range >= 0.30 and i >= 3:
            bar_vol = day_bars[i]["volume"]
            avg_vol = sum(b["volume"] for b in day_bars[:i]) / i if i > 0 else 1
            high_vol = bar_vol > avg_vol * 1.3

            if high_vol and price > vwap[i]:
                if orb_bullish and prev_price <= orb_high and price > orb_high:
                    win = future_max - price > price * 0.005
                    strat = "ORB+Dir+VWAP+Vol (CALL)"
                    strategies.setdefault(strat, {"wins": 0, "total": 0, "pnls": []})
                    strategies[strat]["total"] += 1
                    if win:
                        strategies[strat]["wins"] += 1
                    strategies[strat]["pnls"].append(future_max - price)

            if high_vol and price < vwap[i]:
                if not orb_bullish and prev_price >= orb_low and price < orb_low:
                    win = price - future_min > price * 0.005
                    strat = "ORB+Dir+VWAP+Vol (PUT)"
                    strategies.setdefault(strat, {"wins": 0, "total": 0, "pnls": []})
                    strategies[strat]["total"] += 1
                    if win:
                        strategies[strat]["wins"] += 1
                    strategies[strat]["pnls"].append(price - future_min)

print(f"\n  {'Strategy':35s} | {'Signals':>7s} | {'Win%':>6s} | {'Avg Move':>8s} | {'Med Move':>8s}")
print("  " + "-" * 80)

for strat in sorted(strategies.keys()):
    s = strategies[strat]
    if s["total"] < 5:
        continue
    wr = s["wins"] / s["total"] * 100
    avg = sum(s["pnls"]) / len(s["pnls"])
    med = sorted(s["pnls"])[len(s["pnls"]) // 2]
    print(f"  {strat:35s} | {s['total']:>7d} | {wr:>5.1f}% | ${avg:>7.2f} | ${med:>7.2f}")

# ================================================================
# 8. CONSECUTIVE SAME-DIRECTION SIGNALS — Does repeating work?
# ================================================================
print("\n" + "=" * 100)
print("8. SIGNAL FREQUENCY — Choppy signals (whipsaw) vs clean trends")
print("=" * 100)

for d in dates[:5]:
    day_bars = bars_by_day[d]
    if len(day_bars) < 30:
        continue
    closes = [b["close"] for b in day_bars]
    ema8 = compute_ema(closes, 8)
    ema21 = compute_ema(closes, 21)

    signals = []
    for i in range(1, len(day_bars)):
        if ema8[i] and ema21[i] and ema8[i-1] and ema21[i-1]:
            if ema8[i-1] <= ema21[i-1] and ema8[i] > ema21[i]:
                signals.append((day_bars[i]["time"], "CALL"))
            elif ema8[i-1] >= ema21[i-1] and ema8[i] < ema21[i]:
                signals.append((day_bars[i]["time"], "PUT"))

    print(f"  {d}: {len(signals)} EMA signals → {signals[:8]}")
