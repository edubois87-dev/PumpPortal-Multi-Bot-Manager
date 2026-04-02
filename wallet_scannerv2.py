"""
PumpPortal Wallet Scanner v2

IMPROVEMENTS:
  - Tracks average hold time per wallet (filters out uncopyable scalp bots)
  - "Copyability" score: high win rate + reasonable hold time = better
  - Lower thresholds: 3 trades minimum (was 5)
  - Shows avg SOL per trade
  - Faster leaderboard: every 30s
  - Sorted by copyability score, not just raw PnL

SETUP:
  pip install websocket-client requests
  python wallet_scanner.py
"""

import json
import time
import threading
import websocket
from datetime import datetime
from collections import defaultdict

# ========================= CONFIGURATION =========================

MIN_TRADES = 3                    # Min completed trades to qualify
MIN_WIN_RATE = 0.45               # 45% win rate minimum
MIN_PROFIT_SOL = 0.05             # Lower threshold to catch more wallets
LEADERBOARD_INTERVAL = 30         # Update every 30s (was 60)
MAX_TRACKED_WALLETS = 15000
TOP_N = 25

# Hold time filters for "copyability"
MIN_AVG_HOLD_SECONDS = 5          # Skip bots that hold < 5s avg (uncopyable)
IDEAL_HOLD_SECONDS = 30           # Wallets holding ~30s+ are best to copy

LOG_FILE = "scanner_log.txt"
RESULTS_FILE = "top_wallets.txt"

# ========================= END CONFIG ============================

wallet_stats = {}
token_buys = {}         # (wallet, mint) -> {sol_spent, buy_time}
token_creation = {}     # mint -> creation_time
total_trades_seen = 0
total_tokens_seen = 0
scan_start_time = datetime.now()
previous_leaderboard = set()

COLORS = {
    "INFO":    "\033[37m",
    "WARN":    "\033[93m",
    "SUCCESS": "\033[92m",
    "TRADE":   "\033[96m",
    "RANK":    "\033[95m",
    "HEADER":  "\033[93;1m",
    "TOP":     "\033[92;1m",
    "ERROR":   "\033[91m",
    "DIM":     "\033[90m",
    "RESET":   "\033[0m",
}

def log(message, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    color = COLORS.get(level, COLORS["INFO"])
    reset = COLORS["RESET"]
    print(f"{color}[{ts}] {message}{reset}")


def get_wallet_entry(wallet):
    if wallet not in wallet_stats:
        if len(wallet_stats) >= MAX_TRACKED_WALLETS:
            oldest = min(wallet_stats, key=lambda w: wallet_stats[w]["last_seen"])
            del wallet_stats[oldest]

        wallet_stats[wallet] = {
            "buys": 0,
            "sells": 0,
            "wins": 0,
            "losses": 0,
            "pnl": 0.0,
            "total_sol_spent": 0.0,
            "total_sol_received": 0.0,
            "tokens_traded": set(),
            "last_seen": datetime.now(),
            "first_seen": datetime.now(),
            "hold_times": [],       # list of hold durations in seconds
        }
    return wallet_stats[wallet]


def calc_copyability(stats):
    """Score 0-100 for how good a wallet is to copy trade.
    Factors: win rate, avg hold time, profit, consistency."""
    total_closed = stats["wins"] + stats["losses"]
    if total_closed < MIN_TRADES:
        return 0

    win_rate = stats["wins"] / total_closed
    if win_rate < MIN_WIN_RATE:
        return 0

    # Hold time score: 0-30 points
    # < 5s = 0 (uncopyable), 5-15s = 10, 15-60s = 20, 60s+ = 30
    avg_hold = sum(stats["hold_times"]) / len(stats["hold_times"]) if stats["hold_times"] else 0
    if avg_hold < MIN_AVG_HOLD_SECONDS:
        return 0  # Too fast to copy

    if avg_hold >= 60:
        hold_score = 30
    elif avg_hold >= 15:
        hold_score = 20
    elif avg_hold >= 5:
        hold_score = 10
    else:
        hold_score = 0

    # Win rate score: 0-40 points
    wr_score = min(40, win_rate * 50)

    # Profit score: 0-20 points
    profit_score = min(20, stats["pnl"] * 20)

    # Volume score: 0-10 points (more trades = more reliable)
    vol_score = min(10, total_closed * 1.5)

    return round(hold_score + wr_score + profit_score + vol_score, 1)


def print_leaderboard():
    global previous_leaderboard

    qualified = []
    for wallet, stats in wallet_stats.items():
        total_closed = stats["wins"] + stats["losses"]
        if total_closed < MIN_TRADES:
            continue

        win_rate = stats["wins"] / total_closed if total_closed > 0 else 0
        if win_rate < MIN_WIN_RATE:
            continue

        if stats["pnl"] < MIN_PROFIT_SOL:
            continue

        avg_hold = sum(stats["hold_times"]) / len(stats["hold_times"]) if stats["hold_times"] else 0
        if avg_hold < MIN_AVG_HOLD_SECONDS:
            continue

        avg_sol = stats["total_sol_spent"] / stats["buys"] if stats["buys"] > 0 else 0

        score = calc_copyability(stats)
        if score <= 0:
            continue

        qualified.append({
            "wallet": wallet,
            "win_rate": win_rate,
            "wins": stats["wins"],
            "losses": stats["losses"],
            "pnl": stats["pnl"],
            "buys": stats["buys"],
            "sells": stats["sells"],
            "total_sol": stats["total_sol_spent"],
            "avg_sol": avg_sol,
            "tokens": len(stats["tokens_traded"]),
            "avg_hold": avg_hold,
            "score": score,
        })

    # Sort by copyability score
    qualified.sort(key=lambda x: x["score"], reverse=True)
    top = qualified[:TOP_N]

    elapsed = datetime.now() - scan_start_time
    elapsed_str = str(elapsed).split(".")[0]

    tps = total_trades_seen / max(elapsed.total_seconds(), 1)

    print()
    print(f"{COLORS['HEADER']}{'='*120}")
    print(f"  PUMP.FUN WALLET SCANNER v2 | {total_trades_seen} trades ({tps:.0f}/s) | {len(wallet_stats)} wallets | {total_tokens_seen} tokens | {elapsed_str}")
    print(f"  Filters: {MIN_TRADES}+ trades, {MIN_WIN_RATE*100:.0f}%+ win rate, {MIN_PROFIT_SOL}+ SOL profit, {MIN_AVG_HOLD_SECONDS}s+ avg hold")
    print(f"{'='*120}{COLORS['RESET']}")

    if not top:
        print(f"{COLORS['DIM']}  No wallets qualify yet. Keep scanning — need wallets to buy AND sell while running...{COLORS['RESET']}")
        print()
        return

    # Header
    print(f"{COLORS['DIM']}  {'#':<4} {'Wallet':<46} {'Score':>6} {'Win%':>6} {'W/L':>7} {'PnL':>9} {'AvgHold':>8} {'AvgSOL':>8}{COLORS['RESET']}")
    print(f"{COLORS['DIM']}  {'-'*4} {'-'*46} {'-'*6} {'-'*6} {'-'*7} {'-'*9} {'-'*8} {'-'*8}{COLORS['RESET']}")

    current_leaderboard = set()
    new_count = 0

    for i, w in enumerate(top):
        rank = i + 1
        wallet = w["wallet"]
        current_leaderboard.add(wallet)

        is_new = wallet not in previous_leaderboard and len(previous_leaderboard) > 0
        if is_new:
            new_count += 1

        color = COLORS["TOP"] if rank <= 3 else COLORS["RANK"] if rank <= 10 else COLORS["INFO"]
        medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "  "
        new_tag = " 🆕" if is_new else ""

        pnl_str = f"+{w['pnl']:.3f}" if w['pnl'] >= 0 else f"{w['pnl']:.3f}"
        wl_str = f"{w['wins']}/{w['losses']}"

        # Format hold time
        ah = w['avg_hold']
        if ah >= 60:
            hold_str = f"{ah/60:.1f}m"
        else:
            hold_str = f"{ah:.0f}s"

        # Color hold time
        if ah >= IDEAL_HOLD_SECONDS:
            hold_str = f"\033[92m{hold_str}\033[0m"  # green
        elif ah >= 10:
            hold_str = f"\033[93m{hold_str}\033[0m"  # yellow
        else:
            hold_str = f"\033[91m{hold_str}\033[0m"  # red

        score_str = f"{w['score']:.0f}"
        avg_sol_str = f"{w['avg_sol']:.2f}"

        print(f"{color}{medal}{rank:<3} {w['wallet']:<46} {score_str:>6} {w['win_rate']*100:>5.0f}% {wl_str:>7} {pnl_str:>9} {hold_str:>18} {avg_sol_str:>8}{new_tag}{COLORS['RESET']}")

    if new_count > 0:
        print(f"\n{COLORS['SUCCESS']}  >>> {new_count} new wallet(s) on leaderboard{COLORS['RESET']}")

    previous_leaderboard.clear()
    previous_leaderboard.update(current_leaderboard)

    print()

    # Legend
    print(f"{COLORS['DIM']}  Score = copyability (hold time + win rate + profit + volume) | AvgHold: \033[92mgreen\033[90m=good \033[93myellow\033[90m=ok \033[91mred\033[90m=too fast{COLORS['RESET']}")
    print()

    # Save to file
    try:
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:
            f.write(f"Pump.fun Top Wallets - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Trades: {total_trades_seen} | Wallets: {len(wallet_stats)} | Uptime: {elapsed_str}\n")
            f.write(f"{'='*100}\n\n")

            for i, w in enumerate(top):
                ah = w['avg_hold']
                hold_str = f"{ah/60:.1f}m" if ah >= 60 else f"{ah:.0f}s"
                f.write(f"#{i+1} {w['wallet']}\n")
                f.write(f"   Score: {w['score']:.0f} | Win: {w['win_rate']*100:.0f}% | W/L: {w['wins']}/{w['losses']} | PnL: {w['pnl']:+.4f} SOL\n")
                f.write(f"   Avg hold: {hold_str} | Avg buy: {w['avg_sol']:.3f} SOL\n\n")

        log(f"Saved to {RESULTS_FILE}", "SUCCESS")
    except Exception as e:
        log(f"Save error: {e}", "ERROR")


def leaderboard_timer():
    while True:
        time.sleep(LEADERBOARD_INTERVAL)
        try:
            print_leaderboard()
        except Exception as e:
            log(f"Leaderboard error: {e}", "ERROR")


# ---- WebSocket Handlers ----

def on_message(ws, message):
    global total_trades_seen

    try:
        data = json.loads(message)
    except json.JSONDecodeError:
        return

    if "mint" in data and data.get("txType") == "create":
        token_creation[data["mint"]] = datetime.now()
        return

    if "mint" not in data or "traderPublicKey" not in data:
        return

    tx_type = data.get("txType", "")
    if tx_type not in ("buy", "sell"):
        return

    mint = data["mint"]
    trader = data["traderPublicKey"]

    try:
        sol_amount = float(data.get("solAmount", 0))
    except (ValueError, TypeError):
        return

    if sol_amount <= 0:
        return

    total_trades_seen += 1
    stats = get_wallet_entry(trader)
    stats["last_seen"] = datetime.now()
    stats["tokens_traded"].add(mint)

    key = (trader, mint)

    if tx_type == "buy":
        stats["buys"] += 1
        stats["total_sol_spent"] += sol_amount

        if key not in token_buys:
            token_buys[key] = {
                "sol_spent": sol_amount,
                "buy_time": datetime.now(),
            }
        else:
            token_buys[key]["sol_spent"] += sol_amount

    elif tx_type == "sell":
        stats["sells"] += 1
        stats["total_sol_received"] += sol_amount

        if key in token_buys:
            spent = token_buys[key]["sol_spent"]
            buy_time = token_buys[key]["buy_time"]
            profit = sol_amount - spent

            # Track hold time
            hold_secs = (datetime.now() - buy_time).total_seconds()
            stats["hold_times"].append(hold_secs)
            # Keep last 50 hold times to save memory
            if len(stats["hold_times"]) > 50:
                stats["hold_times"] = stats["hold_times"][-50:]

            stats["pnl"] += profit

            if profit >= 0:
                stats["wins"] += 1
            else:
                stats["losses"] += 1

            del token_buys[key]


def on_open(ws):
    log("Connected to PumpPortal", "SUCCESS")
    ws.send(json.dumps({"method": "subscribeNewToken"}))
    log("Subscribed to new token creation events", "SUCCESS")
    log("Monitoring all pump.fun trades...", "INFO")
    log(f"Leaderboard every {LEADERBOARD_INTERVAL}s | Results saved to {RESULTS_FILE}", "INFO")
    print()


def on_error(ws, error):
    if error:
        log(f"WebSocket error: {error}", "ERROR")


def on_close(ws, code, msg):
    log(f"WebSocket closed (code: {code})", "WARN")


def on_ping(ws, data):
    pass


def on_pong(ws, data):
    pass


class TokenSubscriber:
    def __init__(self):
        self.ws = None
        self.subscribed_tokens = set()

    def set_ws(self, ws):
        self.ws = ws

    def subscribe_token(self, mint):
        if mint in self.subscribed_tokens:
            return
        if self.ws:
            try:
                self.ws.send(json.dumps({
                    "method": "subscribeTokenTrade",
                    "keys": [mint],
                }))
                self.subscribed_tokens.add(mint)
            except Exception:
                pass


token_subscriber = TokenSubscriber()


def on_message_wrapper(ws, message):
    global total_tokens_seen
    try:
        data = json.loads(message)
        if "mint" in data and data.get("txType") == "create":
            token_creation[data["mint"]] = datetime.now()
            token_subscriber.subscribe_token(data["mint"])
            total_tokens_seen += 1
    except (json.JSONDecodeError, KeyError):
        pass

    on_message(ws, message)


def on_open_wrapper(ws):
    token_subscriber.set_ws(ws)
    on_open(ws)


# ---- Main ----

def main():
    print()
    print(f"\033[95m{'='*60}\033[0m")
    print(f"\033[95m   Pump.fun Wallet Scanner v2                              \033[0m")
    print(f"\033[95m   Find copyable, profitable wallets                       \033[0m")
    print(f"\033[95m{'='*60}\033[0m")
    print()

    log(f"Min trades         : {MIN_TRADES}")
    log(f"Min win rate       : {MIN_WIN_RATE*100:.0f}%")
    log(f"Min profit         : {MIN_PROFIT_SOL} SOL")
    log(f"Min avg hold       : {MIN_AVG_HOLD_SECONDS}s")
    log(f"Ideal hold time    : {IDEAL_HOLD_SECONDS}s+")
    log(f"Leaderboard every  : {LEADERBOARD_INTERVAL}s")
    log(f"Max tracked wallets: {MAX_TRACKED_WALLETS}")
    print()
    log("Scanner needs to see BOTH a buy and sell on the same token", "WARN")
    log("to record a win/loss. Let it run 15-30+ minutes for good data.", "WARN")
    print()

    lb_thread = threading.Thread(target=leaderboard_timer, daemon=True)
    lb_thread.start()

    reconnect_count = 0

    while reconnect_count < 50:
        try:
            ws = websocket.WebSocketApp(
                "wss://pumpportal.fun/api/data",
                on_open=on_open_wrapper,
                on_message=on_message_wrapper,
                on_error=on_error,
                on_close=on_close,
                on_ping=on_ping,
                on_pong=on_pong,
            )

            log("Connecting...", "INFO")
            ws.run_forever(ping_interval=20, ping_timeout=10, reconnect=0)

            reconnect_count += 1
            wait = min(5 * reconnect_count, 30)
            log(f"Reconnecting in {wait}s... ({reconnect_count}/50)", "WARN")
            time.sleep(wait)

        except KeyboardInterrupt:
            log("Scanner stopped.", "INFO")
            break
        except Exception as e:
            reconnect_count += 1
            log(f"Error: {e}", "ERROR")
            time.sleep(5)

    print()
    log("=== FINAL RESULTS ===", "HEADER")
    print_leaderboard()
    log("Scanner stopped.", "INFO")


if __name__ == "__main__":
    main()