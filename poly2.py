"""
╔══════════════════════════════════════════════════════════════════╗
║   POLYMARKET BOT — REAL/DEMO + LIVE/6h/12h/20h WINDOW           ║
║   Filter 40–60% | Auto Buy | Auto Claim | Laporan Harian        ║
║   Tombol: MULAI, STOP, STATUS, PNL, RIWAYAT, EXPORT, RESET      ║
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import csv
import io
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import httpx
from dotenv import load_dotenv

load_dotenv()

# ═══════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════

TELEGRAM_BOT_TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID          = "1311484460"
GROQ_API_KEY              = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL                = "qwen/qwen3.8-27b"
POLYMARKET_WALLET_KEY     = os.getenv("POLYMARKET_WALLET_KEY", "")
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")

# ── Gambar robot trading ───────────────────────────────────────
BOT_IMAGE_URL = "https://i.ibb.co/KpS4ZD3R/file-0000000096888211b59a5bc34420ef0c.png"

# ── Filter market (window jam) ─────────────────────────────────
LIVE_HOURS       = 2
HOURS_TO_CLOSE   = 6
FALLBACK_1_HOURS = 12
FALLBACK_2_HOURS = 20
TOP_MARKETS      = 10

# ── Keywords deteksi market sports ────────────────────────────
SPORTS_KEYWORDS = [
    "win", "beat", "defeat", "match", "game", "final", "championship",
    "league", "cup", "tournament", "nfl", "nba", "nhl", "mlb", "mls",
    "soccer", "football", "basketball", "baseball", "hockey", "tennis",
    "ufc", "mma", "boxing", "golf", "f1", "formula", "race", "grand prix",
    "premier league", "la liga", "serie a", "bundesliga", "champions league",
    "world cup", "playoffs", "superbowl", "super bowl", "nascar",
    "score", "goal", "point", "quarter", "half", "overtime",
    "leading", "halftime", "both teams", "first half", "second half",
]

# ── Filter harga ───────────────────────────────────────────────
MIN_ODDS = 0.40
MAX_ODDS = 0.60

# ── Setting trading ────────────────────────────────────────────
MIN_CONFIDENCE      = 60
MAX_BET_PER_MARKET  = 1.0
EXIT_UPPER          = 0.99
SCAN_INTERVAL_MENIT = 5
MIN_USDC_BALANCE    = 5.0

# ── Risk management ────────────────────────────────────────────
MAX_OPEN_POSITIONS     = 3
ENABLE_SELL            = True
MAX_DAILY_LOSSES       = 3
ALERT_BALANCE_DROP_PCT = 20

# ── Saldo fiktif ───────────────────────────────────────────────
VIRTUAL_START_BALANCE = 10.0

# ── File / API ─────────────────────────────────────────────────
POSITIONS_FILE       = "positions.json"
DAILY_STATS_FILE     = "daily_stats.json"
VIRTUAL_BALANCE_FILE = "virtual_balance.json"
BOT_STATE_FILE       = "bot_state.json"
LAST_REPORT_FILE     = "last_report.json"
HISTORY_FILE         = "history.json"
GAMMA_API            = "https://gamma-api.polymarket.com"
CLOB_API             = "https://clob.polymarket.com"
POLYGONSCAN_TX       = "https://polygonscan.com/tx/"
WIB                  = timezone(timedelta(hours=7))

# ═══════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════

class ImportantOnlyFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        if "HTTP Request:" in msg and "HTTP/1.1 200" in msg:
            return False
        if "HTTP Request:" in msg and "HTTP/2 200" in msg:
            return False
        return True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)
logger = logging.getLogger(__name__)
for handler in logging.getLogger().handlers:
    handler.addFilter(ImportantOnlyFilter())

# ═══════════════════════════════════════════════════════════════
#  STATE & STORAGE
# ═══════════════════════════════════════════════════════════════

def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def load_state() -> dict:
    return _load_json(BOT_STATE_FILE, {"mode": None, "active": False})


def save_state(state: dict):
    _save_json(BOT_STATE_FILE, state)


def load_virtual_balance() -> float:
    data = _load_json(VIRTUAL_BALANCE_FILE, {"balance": VIRTUAL_START_BALANCE,
                                             "start": VIRTUAL_START_BALANCE})
    return float(data.get("balance", VIRTUAL_START_BALANCE))


def save_virtual_balance(balance: float, start: float = None):
    data = _load_json(VIRTUAL_BALANCE_FILE, {"balance": VIRTUAL_START_BALANCE,
                                             "start": VIRTUAL_START_BALANCE})
    if start is not None:
        data["start"] = start
    data["balance"] = round(balance, 4)
    _save_json(VIRTUAL_BALANCE_FILE, data)


def reset_virtual_balance():
    _save_json(VIRTUAL_BALANCE_FILE, {"balance": VIRTUAL_START_BALANCE,
                                      "start": VIRTUAL_START_BALANCE})


def load_positions() -> dict:
    return _load_json(POSITIONS_FILE, {})


def save_positions(data: dict):
    _save_json(POSITIONS_FILE, data)


def already_held(market_id: str) -> bool:
    return market_id in load_positions()


def _extract_team_keys(question: str) -> list[str]:
    SKIP_WORDS = {
        "the", "vs", "vs.", "at", "in", "on", "by", "to", "of", "a", "an",
        "and", "or", "for", "with", "draw", "win", "both", "teams", "score",
        "halftime", "half", "first", "second", "spread", "leading", "will",
        "1h", "2h", "ld", "ft", "ml", "over", "under", "point", "points",
    }
    words = question.lower().replace("?", "").replace(":", "").replace("-", " ").split()
    return [w for w in words if len(w) >= 3 and w not in SKIP_WORDS]


def team_already_held(question: str) -> bool:
    positions = load_positions()
    new_keys  = set(_extract_team_keys(question))
    if not new_keys:
        return False
    for pos in positions.values():
        existing_keys = set(_extract_team_keys(pos.get("question", "")))
        if len(new_keys & existing_keys) >= 2:
            logger.info(f"⏭️ Skip duplikat tim: '{question[:50]}'")
            return True
    return False


def record_buy(market: dict, outcome: str, token_id: str, amount: float, fill_price: float):
    pos = load_positions()
    pos[market["id"]] = {
        "question":    market["question"],
        "outcome":     outcome,
        "token_id":    token_id,
        "entry_price": fill_price,
        "shares":      round(amount / fill_price, 4) if fill_price > 0 else 0,
        "cost_usd":    amount,
        "opened_at":   datetime.now(timezone.utc).isoformat(),
        "end_date":    market.get("end_date", ""),
    }
    save_positions(pos)


def load_history() -> list:
    return _load_json(HISTORY_FILE, [])


def record_history(pos: dict, exit_price: float, pnl: float, reason: str):
    history = load_history()
    now_wib = datetime.now(WIB)
    cost    = float(pos.get("cost_usd", 0))
    pct     = (pnl / cost * 100) if cost > 0 else 0
    history.append({
        "tanggal":     now_wib.strftime("%Y-%m-%d"),
        "bulan":       now_wib.strftime("%B"),
        "waktu_buka":  pos.get("opened_at", ""),
        "waktu_tutup": datetime.now(timezone.utc).isoformat(),
        "question":    pos.get("question", ""),
        "outcome":     pos.get("outcome", ""),
        "entry_price": float(pos.get("entry_price", 0)),
        "exit_price":  round(exit_price, 4),
        "cost_usd":    cost,
        "pnl_usd":     round(pnl, 4),
        "pnl_pct":     round(pct, 2),
        "hasil":       reason,
    })
    _save_json(HISTORY_FILE, history)


def close_position(market_id: str, realized_pnl: float = 0.0,
                   exit_price: float = 0.0, reason: str = "CLOSE"):
    pos  = load_positions()
    data = pos.pop(market_id, None)
    save_positions(pos)
    update_daily_stats(realized_pnl, is_loss=(reason == "KALAH"))
    if data:
        record_history(data, exit_price, realized_pnl, reason)


def load_daily_stats() -> dict:
    today = datetime.now(WIB).strftime("%Y-%m-%d")
    data  = _load_json(DAILY_STATS_FILE, {
        "date": today, "realized_pnl": 0.0, "trades": 0,
        "losses": 0, "start_balance_real": None,
    })
    if data.get("date") != today:
        return {
            "date": today, "realized_pnl": 0.0, "trades": 0,
            "losses": 0, "start_balance_real": None,
        }
    return data


def update_daily_stats(pnl_delta: float, is_loss: bool = False):
    stats = load_daily_stats()
    stats["realized_pnl"] = stats.get("realized_pnl", 0.0) + pnl_delta
    stats["trades"]        = stats.get("trades", 0) + 1
    if is_loss:
        stats["losses"] = stats.get("losses", 0) + 1
    _save_json(DAILY_STATS_FILE, stats)


def daily_loss_exceeded() -> bool:
    stats = load_daily_stats()
    return stats.get("losses", 0) >= MAX_DAILY_LOSSES


def can_open_new_position() -> bool:
    return len(load_positions()) < MAX_OPEN_POSITIONS


def is_demo() -> bool:
    return load_state().get("mode") == "DEMO"


# ═══════════════════════════════════════════════════════════════
#  TELEGRAM
# ═══════════════════════════════════════════════════════════════

async def tg_api(method: str, payload: dict) -> dict:
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN.startswith("ISI_"):
        return {}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}",
                json=payload,
            )
            return r.json()
    except Exception as e:
        logger.error(f"❌ Telegram {method} error: {e}")
        return {}


async def tg_send(text: str, keyboard: list = None) -> bool:
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text,
               "parse_mode": "HTML", "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    r = await tg_api("sendMessage", payload)
    return bool(r.get("ok"))


async def tg_send_photo(caption: str, keyboard: list = None) -> bool:
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "photo":      BOT_IMAGE_URL,
        "caption":    caption,
        "parse_mode": "HTML",
    }
    if keyboard:
        payload["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    r = await tg_api("sendPhoto", payload)
    if not r.get("ok"):
        return await tg_send(caption, keyboard)
    return True


async def tg_edit(message_id: int, text: str, keyboard: list = None):
    """Edit pesan teks biasa — skip kalau gagal (pesan sudah expired/foto)."""
    payload = {"chat_id": TELEGRAM_CHAT_ID, "message_id": message_id,
               "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    r = await tg_api("editMessageText", payload)
    # FIX: kalau edit gagal (pesan foto/expired), kirim pesan baru
    if not r.get("ok"):
        logger.warning(f"⚠️ editMessageText gagal (msg_id={message_id}), kirim baru")
        await tg_send(text, keyboard)


async def tg_answer_callback(cb_id: str, text: str = ""):
    await tg_api("answerCallbackQuery", {"callback_query_id": cb_id, "text": text})


async def tg_send_document(filename: str, content: bytes, caption: str = ""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            await c.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument",
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption},
                files={"document": (filename, content, "text/csv")},
            )
    except Exception as e:
        logger.error(f"❌ tg_send_document error: {e}")


# ── Keyboard builders ─────────────────────────────────────────

def kb_mode_select():
    return [
        [{"text": "💵 DANA REAL", "callback_data": "mode_REAL"},
         {"text": "🎮 DANA DEMO", "callback_data": "mode_DEMO"}],
    ]


def kb_main():
    return [
        [{"text": "▶️ MULAI TES",  "callback_data": "start"},
         {"text": "⏸️ STOP",       "callback_data": "stop"}],
        [{"text": "📊 STATUS",     "callback_data": "status"},
         {"text": "📈 PNL",        "callback_data": "pnl"}],
        [{"text": "📜 RIWAYAT",    "callback_data": "history"},
         {"text": "📥 EXPORT CSV", "callback_data": "export"}],
        [{"text": "🔄 RESET",      "callback_data": "reset"},
         {"text": "🔀 GANTI MODE", "callback_data": "change_mode"}],
    ]


# ═══════════════════════════════════════════════════════════════
#  TELEGRAM CALLBACK HANDLER
# ═══════════════════════════════════════════════════════════════

async def handle_callback(data: str, cb_id: str, message_id: int):
    state = load_state()

    if data == "change_mode":
        save_state({"mode": None, "active": False})
        await tg_answer_callback(cb_id, "Pilih mode")
        # FIX: kirim pesan baru daripada edit (avoid 400 pada pesan foto)
        await tg_send("🔀 <b>GANTI MODE</b>\n\nPilih mode dana:", kb_mode_select())
        return

    if data.startswith("mode_"):
        mode = data.split("_", 1)[1]
        if mode == "REAL":
            if not POLYMARKET_WALLET_KEY or POLYMARKET_WALLET_KEY.startswith("ISI_") \
               or not POLYMARKET_FUNDER_ADDRESS or POLYMARKET_FUNDER_ADDRESS.startswith("ISI_"):
                await tg_answer_callback(cb_id, "⚠️ Wallet belum diisi")
                await tg_send("⚠️ Mode REAL butuh <code>POLYMARKET_WALLET_KEY</code> "
                              "dan <code>POLYMARKET_FUNDER_ADDRESS</code>.\nPakai DEMO dulu.")
                return
            save_state({"mode": "REAL", "active": False})
            await tg_answer_callback(cb_id, "Mode REAL dipilih")
            # FIX: kirim pesan baru + keyboard (tidak edit pesan foto)
            await tg_send(
                "✅ Mode: 💵 <b>DANA REAL</b>\n\n"
                "Klik ▶️ MULAI TES untuk mulai.", kb_main())
        else:
            save_state({"mode": "DEMO", "active": False})
            bal = load_virtual_balance()
            await tg_answer_callback(cb_id, "Mode DEMO dipilih")
            await tg_send(
                f"✅ Mode: 🎮 <b>DANA DEMO</b>\n"
                f"Saldo fiktif: ${bal:.2f}\n\n"
                "Klik ▶️ MULAI TES untuk mulai.", kb_main())
        return

    if data == "start":
        if not state.get("mode"):
            await tg_answer_callback(cb_id, "Pilih mode dulu")
            await tg_send("⚠️ Pilih mode dulu:", kb_mode_select())
            return
        if state.get("mode") == "REAL":
            ok, msg = await validate_wallet()
            if not ok:
                await tg_answer_callback(cb_id, "❌ Wallet error")
                await tg_send(f"❌ <b>Gagal koneksi wallet:</b>\n{msg}")
                return
        state["active"] = True
        save_state(state)
        await tg_answer_callback(cb_id, "Bot dimulai")
        await tg_send_photo(
            "🤖 <b>BOT DIMULAI</b>\n"
            "Scan & auto buy aktif.\n"
            f"Mode: <b>{state['mode']}</b> | Min confidence: {MIN_CONFIDENCE}%"
        )
        return

    if data == "stop":
        state["active"] = False
        save_state(state)
        await tg_answer_callback(cb_id, "Bot dihentikan")
        await tg_send("⏸️ <b>BOT DIHENTIKAN</b>\nPosisi tetap tersimpan.")
        return

    if data == "status":
        await tg_answer_callback(cb_id, "Status")
        await tg_send_photo(await build_status_text())
        return

    if data == "pnl":
        await tg_answer_callback(cb_id, "Laporan PnL")
        await tg_send_photo(await build_pnl_text())
        return

    if data == "history":
        await tg_answer_callback(cb_id, "Riwayat posisi")
        await tg_send_photo(build_history_text())
        return

    if data == "export":
        await tg_answer_callback(cb_id, "Membuat CSV...")
        await export_csv()
        return

    if data == "reset":
        if state.get("mode") == "DEMO":
            reset_virtual_balance()
            save_positions({})
            _save_json(HISTORY_FILE, [])
            _save_json(DAILY_STATS_FILE, {
                "date": datetime.now(WIB).strftime("%Y-%m-%d"),
                "realized_pnl": 0.0, "trades": 0, "losses": 0,
                "start_balance_real": None,
            })
            await tg_answer_callback(cb_id, "Reset selesai")
            await tg_send(f"🔄 <b>RESET DEMO</b>\nSaldo kembali ${VIRTUAL_START_BALANCE:.2f}, "
                          "semua posisi & riwayat dihapus.")
        else:
            await tg_answer_callback(cb_id, "Reset hanya untuk DEMO")
            await tg_send("⚠️ Reset hanya tersedia di mode DEMO.")
        return


# ── Build teks laporan ─────────────────────────────────────────

async def build_status_text() -> str:
    state     = load_state()
    mode      = state.get("mode") or "-"
    active    = "🟢 AKTIF" if state.get("active") else "🔴 STOP"
    positions = load_positions()
    history   = load_history()
    daily     = load_daily_stats()

    lines = [
        "📊 <b>STATUS BOT</b>",
        f"Mode: <b>{mode}</b> | {active}",
        f"Posisi terbuka: {len(positions)}/{MAX_OPEN_POSITIONS}",
        f"Total trade: {len(history)}",
        f"Kalah hari ini: {daily.get('losses', 0)}/{MAX_DAILY_LOSSES}",
    ]
    if mode == "DEMO":
        bal = load_virtual_balance()
        lines.append(f"Saldo fiktif: <b>${bal:.2f}</b>")
    elif mode == "REAL":
        bal = get_usdc_balance()
        lines.append(f"Saldo pUSD: <b>${bal:.2f}</b>")

    for mid, p in positions.items():
        end_dt = p.get("end_date", "")
        sisa   = ""
        if end_dt:
            try:
                end_utc = datetime.fromisoformat(end_dt.replace("Z", "+00:00"))
                delta   = end_utc - datetime.now(timezone.utc)
                if delta.total_seconds() > 0:
                    h, m = divmod(int(delta.total_seconds() / 60), 60)
                    sisa = f" ⏳{h}j{m}m"
            except Exception:
                pass
        lines.append(f"• {p['question'][:50]} — {p['outcome']} @ {p['entry_price']:.2f}{sisa}")
    return "\n".join(lines)


async def build_pnl_text() -> str:
    state     = load_state()
    mode      = state.get("mode") or "-"
    positions = load_positions()
    daily     = load_daily_stats()
    lines = [
        "📈 <b>LAPORAN PNL</b>",
        datetime.now(WIB).strftime("%d %b %Y, %H:%M WIB"),
        f"Mode: <b>{mode}</b>", "",
    ]
    if mode == "DEMO":
        bal   = load_virtual_balance()
        start = _load_json(VIRTUAL_BALANCE_FILE, {}).get("start", VIRTUAL_START_BALANCE)
        pnl   = bal - start
        pct   = (pnl / start * 100) if start > 0 else 0
        status = ("TERPUTAR ✅" if pnl > 0
                  else "HABIS ❌" if bal < 1
                  else "MENURUN ⚠️" if pnl < 0
                  else "STAGNAN")
        lines += [
            f"💰 Modal awal : ${start:.2f}",
            f"💵 Saldo skrg : <b>${bal:.2f}</b>",
            f"📊 PnL total  : <b>{pnl:+.2f} ({pct:+.1f}%)</b>",
            f"📉 Status     : {status}", "",
        ]
    elif mode == "REAL":
        bal       = get_usdc_balance()
        start_bal = daily.get("start_balance_real") or bal
        pnl_today = bal - start_bal
        pct_today = (pnl_today / start_bal * 100) if start_bal > 0 else 0
        lines += [
            f"💵 Saldo pUSD : <b>${bal:.2f}</b>",
            f"📊 PnL hari ini: <b>{pnl_today:+.2f} ({pct_today:+.1f}%)</b>", "",
        ]

    if positions:
        lines.append("🔓 <b>Posisi terbuka:</b>")
        for p in positions.values():
            end_dt = p.get("end_date", "")
            sisa   = ""
            if end_dt:
                try:
                    end_utc = datetime.fromisoformat(end_dt.replace("Z", "+00:00"))
                    delta   = end_utc - datetime.now(timezone.utc)
                    if delta.total_seconds() > 0:
                        h, m = divmod(int(delta.total_seconds() / 60), 60)
                        sisa = f" ⏳{h}j{m}m"
                except Exception:
                    pass
            lines.append(f"• {p['question'][:50]} — {p['outcome']} @ {p['entry_price']:.2f}{sisa}")

    lines += [
        "",
        f"📅 PnL hari ini  : ${daily.get('realized_pnl', 0):+.2f}",
        f"🔢 Trade hari ini: {daily.get('trades', 0)}",
        f"❌ Kalah hari ini: {daily.get('losses', 0)}/{MAX_DAILY_LOSSES}",
    ]
    return "\n".join(lines)


def build_history_text(limit: int = 10) -> str:
    history = load_history()
    if not history:
        return "📜 <b>RIWAYAT POSISI</b>\n\nBelum ada riwayat trade."

    total     = len(history)
    menang    = sum(1 for h in history if h.get("hasil") == "MENANG")
    kalah     = sum(1 for h in history if h.get("hasil") == "KALAH")
    exit_     = sum(1 for h in history if h.get("hasil") == "EXIT")
    wr        = (menang / total * 100) if total > 0 else 0
    total_pnl = sum(h.get("pnl_usd", 0) for h in history)

    lines = [
        "📜 <b>RIWAYAT POSISI</b>",
        f"Total: {total} trade | Win Rate: <b>{wr:.1f}%</b>",
        f"✅ {menang} menang | ❌ {kalah} kalah | 🔄 {exit_} exit",
        f"PnL keseluruhan: <b>${total_pnl:+.2f}</b>",
        "",
        "— 10 Terakhir —",
    ]
    for h in history[-limit:][::-1]:
        emoji = "✅" if h["hasil"] == "MENANG" else "❌" if h["hasil"] == "KALAH" else "🔄"
        lines.append(
            f"{emoji} {h['tanggal']} | {h['outcome']} | "
            f"${h['pnl_usd']:+.2f} ({h['pnl_pct']:+.1f}%)\n"
            f"   {h['question'][:50]}"
        )
    return "\n".join(lines)


async def export_csv():
    history = load_history()
    if not history:
        await tg_send("📥 Belum ada riwayat untuk di-export.")
        return
    buf    = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Tanggal", "Bulan", "Pertanyaan", "Outcome",
        "Entry Price", "Exit Price", "Modal ($)",
        "PnL ($)", "PnL (%)", "Hasil",
        "Waktu Buka (UTC)", "Waktu Tutup (UTC)",
    ])
    for h in history:
        writer.writerow([
            h.get("tanggal", ""), h.get("bulan", ""),
            h.get("question", ""), h.get("outcome", ""),
            h.get("entry_price", ""), h.get("exit_price", ""),
            h.get("cost_usd", ""), h.get("pnl_usd", ""),
            h.get("pnl_pct", ""), h.get("hasil", ""),
            h.get("waktu_buka", ""), h.get("waktu_tutup", ""),
        ])
    total_pnl = sum(h.get("pnl_usd", 0) for h in history)
    filename  = f"polymarket_history_{datetime.now(WIB).strftime('%Y%m%d_%H%M')}.csv"
    content   = buf.getvalue().encode("utf-8-sig")
    caption   = f"📥 <b>Export Riwayat</b>\n{len(history)} trade | PnL total: ${total_pnl:+.2f}"
    await tg_send_document(filename, content, caption)


# ═══════════════════════════════════════════════════════════════
#  TELEGRAM POLL LOOP
# ═══════════════════════════════════════════════════════════════

async def telegram_poll_loop():
    # Drop pending updates
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                params={"offset": -1, "timeout": 0}
            )
        logger.info("✅ Drop pending updates selesai")
    except Exception as e:
        logger.warning(f"⚠️ Drop updates error: {e}")

    offset      = 0
    retry_delay = 1  # backoff untuk 429

    while True:
        try:
            if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN.startswith("ISI_"):
                await asyncio.sleep(30)
                continue
            async with httpx.AsyncClient(timeout=35) as c:
                r = await c.get(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                    params={"offset": offset, "timeout": 25,
                            "allowed_updates": json.dumps(["callback_query", "message"])},
                )

            # FIX: Handle 429 Too Many Requests dengan backoff
            if r.status_code == 429:
                retry_after = int(r.headers.get("Retry-After", retry_delay))
                logger.warning(f"⚠️ TG 429 rate limit — tunggu {retry_after}s")
                await asyncio.sleep(retry_after)
                retry_delay = min(retry_delay * 2, 60)
                continue
            retry_delay = 1  # reset backoff kalau sukses

            # FIX: Handle 502 Bad Gateway
            if r.status_code in (502, 503, 504):
                logger.warning(f"⚠️ TG gateway error {r.status_code} — tunggu 5s")
                await asyncio.sleep(5)
                continue

            data = r.json()

            if not data.get("ok") and data.get("error_code") == 409:
                logger.warning("⚠️ 409 Conflict: ada instance lain, tunggu 10 detik...")
                await asyncio.sleep(10)
                continue

            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                if "callback_query" in upd:
                    cb = upd["callback_query"]
                    await handle_callback(cb["data"], cb["id"],
                                          cb["message"]["message_id"])
                elif "message" in upd:
                    txt = (upd["message"].get("text") or "").strip()
                    if txt == "/start":
                        await tg_send_photo(
                            "🤖 <b>BOT POLYMARKET</b>\n\nPilih mode dana:",
                            kb_mode_select()
                        )
                    elif txt == "/menu":
                        await tg_send("🎛️ Menu utama:", kb_main())

        except Exception as e:
            logger.warning(f"⚠️ TG poll error: {e}")
            await asyncio.sleep(5)
        await asyncio.sleep(1)


# ═══════════════════════════════════════════════════════════════
#  POLYMARKET CLIENT
# ═══════════════════════════════════════════════════════════════

def _get_clob_client():
    from py_clob_client.client import ClobClient
    from py_clob_client.constants import POLYGON
    client = ClobClient(
        host=CLOB_API,
        key=POLYMARKET_WALLET_KEY,
        chain_id=POLYGON,
        signature_type=1,
        funder=POLYMARKET_FUNDER_ADDRESS or None,
    )
    client.set_api_creds(client.create_or_derive_api_creds())
    return client


async def validate_wallet() -> tuple[bool, str]:
    if not POLYMARKET_WALLET_KEY or not POLYMARKET_FUNDER_ADDRESS:
        return False, "POLYMARKET_WALLET_KEY atau FUNDER_ADDRESS kosong"
    try:
        bal = get_usdc_balance()
        if bal >= 0:
            return True, f"Saldo pUSD: ${bal:.2f}"
        return False, "Gagal ambil saldo"
    except Exception as e:
        return False, str(e)


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_sports_market(question: str) -> bool:
    q = question.lower()
    return any(kw in q for kw in SPORTS_KEYWORDS)


async def _fetch_markets_in_window(hours: float, sports_only: bool = False) -> list[dict]:
    now   = datetime.now(timezone.utc)
    limit = min(TOP_MARKETS * 10, 200) if sports_only else min(TOP_MARKETS * 3, 100)
    params = {
        "active":       "true",
        "closed":       "false",
        "end_date_min": _iso_z(now),
        "end_date_max": _iso_z(now + timedelta(hours=hours)),
        "limit":        limit,
        "_sort":        "volume24hr",
        "_order":       "desc",
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{GAMMA_API}/markets", params=params)
        r.raise_for_status()
        markets = r.json()

    if sports_only:
        markets = [m for m in markets if _is_sports_market(m.get("question", ""))]
        logger.info(f"🔍 Filter sports: {len(markets)} market lolos")

    return markets


def _parse_market(m: dict) -> dict | None:
    try:
        raw    = m.get("outcomePrices", "[]")
        prices = json.loads(raw) if isinstance(raw, str) else raw
        if not prices or len(prices) < 2:
            return None
        yes = float(prices[0])
        if not (MIN_ODDS <= yes <= MAX_ODDS):
            return None
        tokens = m.get("clobTokenIds", "[]")
        if isinstance(tokens, str):
            tokens = json.loads(tokens)
        if not tokens or len(tokens) < 2:
            return None
        return {
            "id":        str(m.get("id")),
            "question":  m.get("question", ""),
            "category":  m.get("category", ""),
            "yes_price": yes,
            "no_price":  float(prices[1]),
            "volume":    float(m.get("volume", 0) or 0),
            "end_date":  m.get("endDate", ""),
            "token_yes": tokens[0],
            "token_no":  tokens[1],
        }
    except (ValueError, IndexError, TypeError, json.JSONDecodeError):
        return None


async def get_top_markets() -> tuple[list[dict], str]:
    try:
        for hours, label in [
            (LIVE_HOURS,       f"⚡ Live ≤ {LIVE_HOURS} jam"),
            (HOURS_TO_CLOSE,   f"🏆 Olahraga ≤ {HOURS_TO_CLOSE} jam"),
            (FALLBACK_1_HOURS, f"🏆 Olahraga ≤ {FALLBACK_1_HOURS} jam"),
            (FALLBACK_2_HOURS, f"🏆 Olahraga ≤ {FALLBACK_2_HOURS} jam"),
        ]:
            markets = await _fetch_markets_in_window(hours, sports_only=True)
            result  = [p for p in (_parse_market(m) for m in markets) if p][:TOP_MARKETS]
            if result:
                logger.info(f"✅ {len(result)} market ditemukan ({label})")
                return result, label

        markets = await _fetch_markets_in_window(FALLBACK_2_HOURS, sports_only=False)
        result  = [p for p in (_parse_market(m) for m in markets) if p][:TOP_MARKETS]
        if result:
            label = f"🌐 Semua kategori ≤ {FALLBACK_2_HOURS} jam"
            logger.info(f"⚠️ Fallback: {len(result)} market")
            return result, label

        return [], "0 market"
    except Exception as e:
        logger.error(f"❌ Gagal ambil market: {e}")
        return [], "error"


async def get_market_status(market_id: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{GAMMA_API}/markets/{market_id}")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning(f"⚠️ Market status error {market_id}: {e}")
        return None


# ── Helper wallet ──────────────────────────────────────────────

def get_usdc_balance() -> float:
    try:
        PUSD_CONTRACT = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"
        padded = POLYMARKET_FUNDER_ADDRESS.lower().replace("0x", "").zfill(64)
        data   = "0x70a08231" + padded
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "eth_call",
            "params": [{"to": PUSD_CONTRACT, "data": data}, "latest"],
        }
        import httpx as _h
        rpc_list = [
            "https://rpc.ankr.com/polygon",
            "https://polygon-bor-rpc.publicnode.com",
            "https://1rpc.io/matic",
        ]
        for rpc in rpc_list:
            try:
                r   = _h.post(rpc, json=payload, timeout=10)
                raw = r.json().get("result")
                if raw:
                    return round(int(raw, 16) / 1e6, 4)
            except Exception:
                continue
        return 0.0
    except Exception as e:
        logger.error(f"❌ Balance error: {e}")
        return 0.0


def get_best_bid(token_id: str) -> float | None:
    try:
        import httpx as _h
        r = _h.get(f"{CLOB_API}/price",
                   params={"token_id": token_id, "side": "SELL"}, timeout=10)
        return float(r.json().get("price", 0))
    except Exception as e:
        logger.error(f"❌ get_best_bid error: {e}")
        return None


def get_best_ask(token_id: str) -> float | None:
    try:
        import httpx as _h
        r = _h.get(f"{CLOB_API}/price",
                   params={"token_id": token_id, "side": "BUY"}, timeout=10)
        return float(r.json().get("price", 0))
    except Exception as e:
        logger.error(f"❌ get_best_ask error: {e}")
        return None


# ══════════════════════════════════════════════════════════════
#  FIX UTAMA: _post_order_direct — pakai client.post_order bawaan
# ══════════════════════════════════════════════════════════════

def _post_order_direct(signed_order, order_type: str = "FOK") -> dict:
    """
    FIX v0.34.6:
    - Pakai client.post_order() langsung (sudah handle auth internally)
    - Tidak perlu rebuild payload manual lagi
    - Fallback ke HTTP manual kalau post_order tidak ada
    """
    try:
        client = _get_clob_client()

        # Cara 1: pakai method bawaan client (paling aman di v0.34.6)
        if hasattr(client, "post_order"):
            resp = client.post_order(signed_order, order_type)
            logger.info(f"📋 post_order response: {resp}")
            if isinstance(resp, dict):
                err = resp.get("errorMsg") or resp.get("error")
                if err:
                    return {"success": False, "error": err}
                return {
                    "success":  True,
                    "order":    resp,
                    "tx_hash":  resp.get("transactionHash", ""),
                }
            return {"success": True, "order": resp, "tx_hash": ""}

        # Cara 2: fallback HTTP manual dengan payload yang benar
        vals = signed_order.order.dict()

        # FIX: side harus integer (0=BUY, 1=SELL), bukan string
        side_int = vals.get("side", 0)
        if isinstance(side_int, str):
            side_int = 0 if side_int.upper() == "BUY" else 1

        order_payload = {
            "salt":          str(vals["salt"]),
            "maker":         vals["maker"],
            "signer":        vals["signer"],
            "taker":         vals["taker"],
            "tokenId":       str(vals["tokenId"]),
            "makerAmount":   str(vals["makerAmount"]),
            "takerAmount":   str(vals["takerAmount"]),
            "expiration":    str(vals["expiration"]),
            "nonce":         str(vals["nonce"]),
            "feeRateBps":    str(vals["feeRateBps"]),
            "side":          side_int,           # FIX: integer bukan string
            "signatureType": vals["signatureType"],
        }
        body_dict = {
            "order":     order_payload,
            "signature": signed_order.signature,
            "orderType": order_type,
            # FIX: owner = maker address, bukan funder address
            "owner":     vals["maker"],
        }
        body = json.dumps(body_dict, separators=(',', ':'))

        # Build auth headers pakai client internal signer
        from py_clob_client.signing.hmac import create_level_2_headers
        headers = create_level_2_headers(
            signer=client.signer,
            creds=client.creds,
            method="POST",
            request_path="/order",
            body=body,
        )
        headers["Content-Type"] = "application/json"

        import httpx as _h
        r    = _h.post(f"{CLOB_API}/order", content=body.encode(), headers=headers, timeout=15)
        data = r.json()
        logger.info(f"📋 Order response ({r.status_code}): {data}")

        if r.status_code != 200 or data.get("errorMsg") or data.get("error"):
            err = data.get("errorMsg") or data.get("error") or str(data)
            return {"success": False, "error": err}

        return {"success": True, "order": data, "tx_hash": data.get("transactionHash", "")}

    except Exception as e:
        logger.error(f"❌ _post_order_direct error: {e}")
        return {"success": False, "error": str(e)}


def buy_outcome(token_id: str, amount_usd: float) -> dict:
    if is_demo():
        return {"success": True, "dry_run": True,
                "order": {"note": f"DEMO: beli ${amount_usd:.2f}"}}
    try:
        from py_clob_client.clob_types import MarketOrderArgs
        from py_clob_client.order_builder.constants import BUY
        amount_usd = round(amount_usd, 2)
        if amount_usd < 1.0:
            return {"success": False, "error": "Minimal $1 per order"}
        client     = _get_clob_client()
        order_args = MarketOrderArgs(token_id=token_id, amount=amount_usd, side=BUY)
        signed     = client.create_market_order(order_args)
        return _post_order_direct(signed, "FOK")
    except Exception as e:
        logger.error(f"❌ buy_outcome error: {e}")
        return {"success": False, "error": str(e)}


def sell_outcome(token_id: str, shares: float) -> dict:
    if is_demo():
        return {"success": True, "dry_run": True,
                "order": {"note": f"DEMO: jual {shares:.4f} share"}}
    try:
        from py_clob_client.clob_types import MarketOrderArgs
        from py_clob_client.order_builder.constants import SELL
        if shares < 1.0:
            return {"success": False, "error": "Share terlalu kecil"}
        client     = _get_clob_client()
        order_args = MarketOrderArgs(token_id=token_id, amount=round(shares, 4), side=SELL)
        signed     = client.create_market_order(order_args)
        return _post_order_direct(signed, "FOK")
    except Exception as e:
        logger.error(f"❌ sell_outcome error: {e}")
        return {"success": False, "error": str(e)}


def redeem_position(market_id: str, condition_id: str) -> dict:
    if is_demo():
        return {"success": True, "dry_run": True, "note": "DEMO: claim disimulasikan"}
    try:
        client = _get_clob_client()
        if hasattr(client, "redeem"):
            resp    = client.redeem(condition_id=condition_id)
            tx_hash = resp.get("transactionHash", "") if isinstance(resp, dict) else ""
            return {"success": True, "resp": resp, "tx_hash": tx_hash}
        return {"success": False, "error": "client.redeem tidak tersedia"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _polygonscan_link(tx_hash: str) -> str:
    if tx_hash:
        return f"\n🔗 <a href='{POLYGONSCAN_TX}{tx_hash}'>Lihat di Polygonscan</a>"
    return ""


# ═══════════════════════════════════════════════════════════════
#  NEWS + GROQ ANALYZER
# ═══════════════════════════════════════════════════════════════

async def get_news(query: str, max_items: int = 4) -> str:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get("https://news.google.com/rss/search",
                            params={"q": query[:100], "hl": "en-US",
                                    "gl": "US", "ceid": "US:en"})
            r.raise_for_status()
            root = ET.fromstring(r.text)
        items = root.findall(".//item")[:max_items]
        if not items:
            return "Tidak ada berita terkait"
        return "\n".join(f"- {(it.findtext('title') or '').strip()}" for it in items)
    except Exception as e:
        logger.warning(f"⚠️ News error: {e}")
        return "Berita tidak tersedia"


async def analyze_market(market: dict) -> dict:
    news   = await get_news(market["question"])
    prompt = f"""Kamu adalah analis prediction market profesional.

PERTANYAAN: {market['question']}
BERAKHIR: {market.get('end_date') or '?'}
HARGA YES: {market['yes_price']:.2%} | HARGA NO: {market['no_price']:.2%}

BERITA:
{news}

Jawab HANYA dengan JSON valid satu baris tanpa teks lain, tanpa markdown, tanpa backtick:
{{"signal":"BUY_YES","confidence":80,"recommended_outcome":"YES","reasoning":"alasan singkat"}}"""
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post("https://api.groq.com/openai/v1/chat/completions",
                             headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                                      "Content-Type": "application/json"},
                             json={"model": GROQ_MODEL,
                                   "messages": [{"role": "user", "content": prompt}],
                                   "temperature": 0.1,
                                   "max_tokens":  200})
            r.raise_for_status()
            raw = r.json()["choices"][0]["message"]["content"].strip()

        # FIX: regex lebih robust — handle newline dan thinking tags
        # Hapus thinking tags dulu kalau ada
        raw = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
        # Cari JSON dengan regex yang handle multiline
        json_match = re.search(r'\{.*?\}', raw, re.DOTALL)
        if not json_match:
            logger.warning(f"⚠️ Groq no JSON: {raw[:100]}")
            return {"signal": "SKIP", "confidence": 0,
                    "recommended_outcome": "SKIP", "recommended_token": None}

        result  = json.loads(json_match.group())
        outcome = result.get("recommended_outcome", "SKIP")
        conf    = int(result.get("confidence", 0) or 0)
        logger.info(f"🧠 Groq: {outcome} | conf={conf}% | {result.get('reasoning','')[:60]}")

        result["recommended_token"] = (
            market["token_yes"] if outcome == "YES"
            else market["token_no"] if outcome == "NO" else None)
        return result
    except Exception as e:
        logger.error(f"❌ Groq error: {e}")
        return {"signal": "SKIP", "confidence": 0,
                "recommended_outcome": "SKIP", "recommended_token": None}


# ═══════════════════════════════════════════════════════════════
#  POSITION MANAGEMENT
# ═══════════════════════════════════════════════════════════════

async def manage_open_positions() -> int:
    if not ENABLE_SELL:
        return 0
    positions = load_positions()
    if not positions:
        return 0
    sold = 0
    demo = is_demo()

    for market_id, pos in list(positions.items()):
        token_id = pos["token_id"]

        status          = await get_market_status(market_id)
        resolved        = False
        winning_outcome = None
        if status:
            closed = status.get("closed") or status.get("umaResolutionStatus") == "resolved"
            if closed:
                raw_prices = status.get("outcomePrices", "[]")
                prices     = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
                try:
                    final_yes = float(prices[0])
                except Exception:
                    final_yes = 0.5
                if final_yes >= 0.99:
                    winning_outcome = "YES"
                elif final_yes <= 0.01:
                    winning_outcome = "NO"
                resolved = True

        if resolved:
            won    = (winning_outcome == pos["outcome"])
            shares = float(pos["shares"])
            if won:
                if demo:
                    gain = shares * 1.0
                    bal  = load_virtual_balance() + gain
                    save_virtual_balance(bal)
                    close_position(market_id, realized_pnl=gain - pos["cost_usd"],
                                   exit_price=1.0, reason="MENANG")
                    sold += 1
                    await tg_send_photo(
                        f"🎉 <b>MENANG (DEMO)</b>\n{pos['question'][:80]}\n"
                        f"+${gain:.2f} → saldo ${bal:.2f}"
                    )
                else:
                    res = redeem_position(market_id, status.get("conditionId", ""))
                    if res.get("success"):
                        gain    = shares * 1.0
                        tx_link = _polygonscan_link(res.get("tx_hash", ""))
                        close_position(market_id, realized_pnl=gain - pos["cost_usd"],
                                       exit_price=1.0, reason="MENANG")
                        sold += 1
                        await tg_send_photo(
                            f"🎉 <b>CLAIM BERHASIL</b>\n"
                            f"{pos['question'][:80]}\n+${gain:.2f}{tx_link}"
                        )
                    else:
                        await tg_send(f"❌ Claim gagal: {res.get('error')}\n"
                                      f"Claim manual: {pos['question'][:80]}")
            else:
                close_position(market_id, realized_pnl=-pos["cost_usd"],
                               exit_price=0.0, reason="KALAH")
                await tg_send_photo(
                    f"❌ <b>KALAH</b>\n{pos['question'][:80]}\n"
                    f"-${pos['cost_usd']:.2f}"
                )
            continue

        # Exit atas 0.99
        bid = get_best_bid(token_id)
        if bid is not None and bid >= EXIT_UPPER:
            shares = float(pos["shares"])
            if demo:
                gain = shares * bid
                bal  = load_virtual_balance() + gain
                save_virtual_balance(bal)
                close_position(market_id, realized_pnl=gain - pos["cost_usd"],
                               exit_price=bid, reason="EXIT")
                sold += 1
                await tg_send_photo(
                    f"✅ <b>EXIT 0.99 (DEMO)</b>\n{pos['question'][:80]}\n"
                    f"+${gain - pos['cost_usd']:+.2f} → saldo ${bal:.2f}"
                )
            else:
                res = sell_outcome(token_id, shares)
                if res.get("success"):
                    gain    = shares * bid
                    tx_link = _polygonscan_link(res.get("tx_hash", ""))
                    close_position(market_id, realized_pnl=gain - pos["cost_usd"],
                                   exit_price=bid, reason="EXIT")
                    sold += 1
                    await tg_send_photo(
                        f"✅ <b>JUAL 0.99</b>\n{pos['question'][:80]}\n"
                        f"+${gain - pos['cost_usd']:+.2f}{tx_link}"
                    )
    return sold


# ═══════════════════════════════════════════════════════════════
#  LAPORAN & ALERT
# ═══════════════════════════════════════════════════════════════

async def maybe_send_hourly_report():
    now = datetime.now(WIB)
    if now.minute > 5:
        return
    key  = now.strftime("%Y-%m-%d-%H")
    last = _load_json(LAST_REPORT_FILE, {"last": "", "midnight": ""})
    if last.get("last") == key:
        return
    last["last"] = key
    _save_json(LAST_REPORT_FILE, last)
    await tg_send_photo(await build_pnl_text())


async def maybe_send_midnight_report():
    now = datetime.now(WIB)
    if now.hour != 0 or now.minute > 5:
        return
    key  = now.strftime("%Y-%m-%d-midnight")
    last = _load_json(LAST_REPORT_FILE, {"last": "", "midnight": ""})
    if last.get("midnight") == key:
        return
    last["midnight"] = key
    _save_json(LAST_REPORT_FILE, last)

    history   = load_history()
    today_str = now.strftime("%Y-%m-%d")
    today_h   = [h for h in history if h.get("tanggal") == today_str]
    if not today_h:
        return
    menang    = sum(1 for h in today_h if h.get("hasil") == "MENANG")
    kalah     = sum(1 for h in today_h if h.get("hasil") == "KALAH")
    pnl_today = sum(h.get("pnl_usd", 0) for h in today_h)
    wr        = (menang / len(today_h) * 100) if today_h else 0
    await tg_send_photo(
        f"🌙 <b>LAPORAN HARIAN — {today_str}</b>\n"
        f"Trade: {len(today_h)} | ✅ {menang} menang | ❌ {kalah} kalah\n"
        f"Win Rate: {wr:.1f}% | PnL: <b>${pnl_today:+.2f}</b>"
    )


async def check_real_balance_alert():
    if is_demo():
        return
    stats = load_daily_stats()
    bal   = get_usdc_balance()

    if not stats.get("start_balance_real"):
        stats["start_balance_real"] = bal
        _save_json(DAILY_STATS_FILE, stats)
        return

    start    = stats["start_balance_real"]
    drop_pct = ((start - bal) / start * 100) if start > 0 else 0
    if drop_pct >= ALERT_BALANCE_DROP_PCT:
        await tg_send(
            f"🚨 <b>ALERT SALDO TURUN {drop_pct:.1f}%</b>\n"
            f"Awal: ${start:.2f} → Sekarang: ${bal:.2f}"
        )
    if bal < MIN_USDC_BALANCE:
        await tg_send(f"⚠️ <b>SALDO RENDAH: ${bal:.2f}</b>")


# ═══════════════════════════════════════════════════════════════
#  CYCLE
# ═══════════════════════════════════════════════════════════════

async def run_cycle():
    state = load_state()
    if not state.get("active"):
        return

    if daily_loss_exceeded():
        await tg_send(
            f"🛑 <b>DAILY LOSS LIMIT</b>\n"
            f"Sudah kalah {MAX_DAILY_LOSSES}x hari ini. Bot stop otomatis."
        )
        state["active"] = False
        save_state(state)
        return

    await check_real_balance_alert()
    await manage_open_positions()
    markets, label = await get_top_markets()
    if not markets:
        logger.info("💤 Tidak ada market di window.")
        return

    demo = is_demo()
    for m in markets:
        if already_held(m["id"]):
            continue
        if team_already_held(m["question"]):
            continue
        if not can_open_new_position():
            break

        analysis = await analyze_market(m)
        sig      = analysis.get("signal", "SKIP")
        conf     = int(analysis.get("confidence", 0) or 0)
        if sig == "SKIP" or conf < MIN_CONFIDENCE:
            continue
        token = analysis.get("recommended_token")
        if not token:
            continue

        if demo:
            bal = load_virtual_balance()
            if bal < MAX_BET_PER_MARKET:
                await tg_send(f"⚠️ Saldo DEMO habis (${bal:.2f}). Reset via tombol.")
                return
            amount = MAX_BET_PER_MARKET
            ask    = m["yes_price"] if analysis["recommended_outcome"] == "YES" else m["no_price"]
        else:
            bal = get_usdc_balance()
            if bal < MAX_BET_PER_MARKET:
                await tg_send(f"⚠️ pUSD tidak cukup: ${bal:.2f}")
                return
            amount = MAX_BET_PER_MARKET
            ask    = get_best_ask(token)
            if ask is None:
                continue

        res = buy_outcome(token, amount)
        if res.get("success"):
            if demo:
                save_virtual_balance(load_virtual_balance() - amount)
            record_buy(m, analysis["recommended_outcome"], token, amount, ask)
            tx_link = _polygonscan_link(res.get("tx_hash", "")) if not demo else ""
            await tg_send_photo(
                f"🛒 <b>BELI {'DEMO' if demo else 'REAL'}</b> [{label}]\n"
                f"📌 {m['question'][:90]}\n"
                f"🎯 {analysis['recommended_outcome']} @ {ask:.2f} — ${amount:.2f}\n"
                f"📊 Confidence: {conf}%\n"
                f"🧠 {analysis.get('reasoning', '-')}{tx_link}"
            )
        else:
            logger.error(f"❌ Beli gagal: {res.get('error')}")
        await asyncio.sleep(2)


# ═══════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════

async def main():
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN.startswith("ISI_"):
        raise RuntimeError("TELEGRAM_BOT_TOKEN belum diisi")
    if not GROQ_API_KEY or GROQ_API_KEY.startswith("ISI_"):
        raise RuntimeError("GROQ_API_KEY belum diisi")

    state = load_state()
    state["active"] = False
    state["mode"]   = None
    save_state(state)
    logger.info("🔄 State direset — pilih mode dulu")

    asyncio.create_task(telegram_poll_loop())

    await tg_send_photo(
        "🤖 <b>BOT POLYMARKET</b>\n\nPilih mode dana:",
        kb_mode_select()
    )

    while True:
        try:
            await run_cycle()
            await maybe_send_hourly_report()
            await maybe_send_midnight_report()
        except Exception as e:
            logger.exception("❌ Cycle error")
            await tg_send(f"⚠️ Bot error: {e}")
        await asyncio.sleep(SCAN_INTERVAL_MENIT * 60)


if __name__ == "__main__":
    asyncio.run(main())
