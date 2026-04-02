# PumpPortal Multi-Bot Manager v11 — Release Notes

## Overview

A Python-based copy trading bot manager for pump.fun tokens on Solana. Manages up to 20 independent copy trading bots from a single web dashboard, using the PumpPortal API for trade execution and real-time WebSocket data.

---

## Features

### Multi-Bot Architecture
- **20 independent bots** (Bot 1–20), each with its own target wallet, settings, and P&L tracking
- **Single shared WebSocket** connection to PumpPortal — eliminates rate limiting from multiple connections
- All bots subscribe to trade events on one connection, messages are routed to the matching bot automatically

### Web Dashboard (http://localhost:8888)
- **Master System Switch** — ON/OFF toggle to start/stop the entire system
- **Master Mode Toggle** — SIMULATE (yellow) / LIVE (green), overrides all bots when set to Simulate
- **Max Connections Dropdown** — limit from 1 to 90, with live connection count
- **Total P&L Display** — real-time aggregate P&L across all bots, color-coded green/red
- **Simulated Wallet Balance** — starts at 1 SOL in simulate mode with Reset button
- **Bot Grid** — compact cards for all 20 bots with status lights (green=live, yellow=simulate, gray=off) and on/off toggle switches
- **Per-Bot Panel** — click any bot to expand full controls

### Per-Bot Controls
- **Simulate / Live** buttons — set each bot's mode independently
- **Enable / Disable / Sell All** buttons
- **Target Wallet** input — paste any Solana wallet address to copy
- **Max Concurrent** slider (0–3)

### Per-Bot Trading Settings (expandable)
- Buy Amount (SOL)
- Sell Percentage
- Slippage %
- Priority Fee (SOL)
- Pool (auto, pump, raydium, pump-amm, launchlab)
- Copy Delay (seconds)
- **Save** and **Apply to All Bots** buttons

### Per-Bot Protection Settings (expandable)
- Stop Loss %
- Auto-Sell Timer (seconds)
- Min Hold Time (seconds)
- Max Consecutive Losses
- Min Buy SOL Filter
- Max Daily Loss (SOL)
- Max Open Positions
- **Save** and **Apply to All Bots** buttons

### Credential Management
- **API Key** — paste to set, shows masked (••••••••) with Edit button after saving
- **Wallet Private Key** — password field, same mask/edit behavior
- Both fields disappear once set, replaced by status indicator + Edit button

### Mass Wallet Import
- **📋 Mass Import** button opens modal
- Paste up to 20 wallet addresses, one per line
- Line 1 → Bot 1, Line 2 → Bot 2, etc.
- **Auto-enable** checkbox to start bots immediately after import
- Live wallet count display

### Manual Trading
- **Buy box** on each bot — paste token contract address + set SOL amount
- **Sell buttons** on each open position
- **Sell All** per bot or globally

### Positions & History
- **Bot Positions** — open positions for selected bot with hold timer and sell buttons
- **Bot History** — trade-by-trade log with P&L % pills (green/red/blue)
- **All Positions** — combined view across all 20 bots, shows which bot owns each
- **All History** — merged timeline from all bots

### Activity Log
- Live scrolling log at bottom of dashboard
- Color-coded: blue=buy, red=sell, yellow=simulate, green=success
- **Clear** button to reset display
- **Export** button downloads `sim_log.txt` with full summary including:
  - Simulation balance
  - Total P&L and win rate
  - Per-bot breakdown
  - Complete activity log

### Smart Features
- **Burned Token Blacklist** — never re-buys a token you lost money on
- **Auto-Sell Timer** — sells tokens automatically after configurable timeout
- **Min Hold Time** — delays sell if target sells too early
- **Consecutive Loss Pause** — stops copying after N losses in a row
- **Daily Loss Limit** — halts trading if daily losses exceed threshold
- **Settings Edit Protection** — dashboard won't overwrite fields while you're typing

---

## Setup

### Requirements
- Python 3.8+
- `pip install websocket-client requests`

### Files
- `copytrade_bot.py` — main bot manager (run this)
- `dashboard.html` — web dashboard (served automatically)

### Running
```powershell
cd C:\bot
python copytrade_bot.py
```
Open http://localhost:8888 in your browser.

### Quick Start
1. Run the script
2. Open dashboard
3. Paste your PumpPortal API Key
4. Click **📋 Mass Import** and paste wallet addresses
5. Toggle **System → ON**
6. Set **Mode → SIMULATE** to test, or **LIVE** for real trades

---

## Companion Tools

### Token Scanner (`token_scanner.py`)
- Monitors new pump.fun tokens in real-time
- Filters by volume (>$4K) and ATH market cap (>$8.5K)
- Web dashboard at http://localhost:9999
- Copy button on each token to paste into bot's buy box

### Wallet Scanner (`wallet_scanner.py`)
- Finds profitable wallets to copy-trade
- Tracks win rate, P&L, average hold time
- Copyability score ranks wallets by how suitable they are for copy trading
- Filters out uncopyable scalp bots (<5s hold time)

---

## Architecture

```
┌─────────────────────────────┐
│   Dashboard (localhost:8888) │
│   HTML + JS, polls /api/state│
│   every 500ms                │
└──────────┬──────────────────┘
           │ HTTP API
┌──────────▼──────────────────┐
│   copytrade_bot.py           │
│   ┌────────────────────────┐ │
│   │  HTTP Server (8888)    │ │
│   │  /api/state, /api/bot/*│ │
│   └────────────────────────┘ │
│   ┌────────────────────────┐ │
│   │  Shared WebSocket      │ │
│   │  wss://pumpportal.fun  │ │
│   │  1 connection, 20 subs │ │
│   └────────────────────────┘ │
│   ┌────────────────────────┐ │
│   │  Bot 1-20 Instances    │ │
│   │  Each: settings, state,│ │
│   │  positions, history    │ │
│   └────────────────────────┘ │
└──────────┬──────────────────┘
           │ HTTPS (trades)
┌──────────▼──────────────────┐
│   PumpPortal Lightning API   │
│   Buy/Sell execution         │
└─────────────────────────────┘
```

---

## Version History

| Version | Changes |
|---------|---------|
| v1 | Basic PowerShell copy trade bot |
| v2 | Auto-reconnect, keepalive pings |
| v3 | TLS fix, connection diagnostics |
| v4 | Stop loss, auto-sell timer, win/loss tracking |
| v5 | Burned token blacklist, max daily loss, max positions |
| v6 | Live wallet switching via terminal, sellall/stop/pause commands |
| v7 | Multi-bot manager, web dashboard, 20 bots |
| v8 | Per-bot simulate/live, protection settings, connection limits |
| v9 | Sim wallet balance, activity log, all positions/history |
| v10 | Master on/off, bot grid at top, settings edit fix, log export |
| v11 | **Single shared WebSocket** — fixes rate limiting, all bots on one connection |

---

## Warnings

- **This bot trades real SOL on mainnet when in LIVE mode.** You can lose everything.
- **Never share your private keys or API keys publicly.**
- Start in **SIMULATE mode** to test before going live.
- PumpPortal charges a **0.5% fee** per trade.
- Copy trading meme tokens is **extremely high risk**.
- The P&L tracking is estimated based on target wallet performance — actual returns may differ due to slippage, timing, and fees.

---

## License

Use at your own risk. Not financial advice.