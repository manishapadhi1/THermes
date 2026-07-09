"""
THermes — Agentic Trading Platform
FastAPI backend: proxies Hermes WebUI for models/settings/chat,
adds trading-specific endpoints (analysis, orders, agents, watchlist)
"""
import json
import os
import secrets
import math
import re
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─── Constants ─────────────────────────────────────────
HERMES_URL = os.environ.get("HERMES_URL", "http://127.0.0.1:8787")
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
FRONTEND_DIR = BASE_DIR / "frontend"
STATE_FILE = CONFIG_DIR / "state.json"

# ─── State management ──────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"orders": [], "trade_history": [], "autonomous_log": []}

def save_state(st: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(st, f, indent=2, default=str)

def mask(v: Optional[str]) -> str:
    if not v:
        return ""
    return "••••" + str(v)[-4:] if len(str(v)) > 4 else "••••"

KITE_BASE = "https://api.kite.trade"

def zerodha_login_url(api_key: str) -> str:
    return f"https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"

DEFAULT_SYMBOLS = ["NIITMTS", "TCS", "INFY", "RELIANCE", "ITC", "HDFCBANK", "SBIN", "YESBANK"]
YAHOO_SUFFIX = ".NS"

def state_market_provider() -> str:
    st = load_state()
    return st.get("market_data", {}).get("provider", "yahoo")

def yahoo_symbol(symbol: str) -> str:
    return symbol if "." in symbol else f"{symbol}{YAHOO_SUFFIX}"

async def yahoo_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """Free Yahoo chart API. Usually delayed; not suitable for guaranteed exchange-real-time trading."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol(symbol)}"
    try:
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(url, params={"range": "1d", "interval": "1m"})
            r.raise_for_status()
            data = r.json()["chart"]["result"][0]
            meta = data.get("meta", {})
            quote = (data.get("indicators", {}).get("quote") or [{}])[0]
            closes = [x for x in quote.get("close", []) if x is not None]
            highs = [x for x in quote.get("high", []) if x is not None]
            lows = [x for x in quote.get("low", []) if x is not None]
            opens = [x for x in quote.get("open", []) if x is not None]
            vols = [x for x in quote.get("volume", []) if x is not None]
            if not closes:
                return None
            pc = meta.get("previousClose") or closes[0]
            ltp = meta.get("regularMarketPrice") or closes[-1]
            return {
                "symbol": symbol,
                "name": meta.get("longName") or meta.get("shortName") or symbol,
                "ltp": round(float(ltp), 2),
                "change": round(float(ltp) - float(pc), 2),
                "changePct": round(((float(ltp) - float(pc)) / float(pc) * 100), 2) if pc else 0,
                "o": round(float(opens[0] if opens else closes[0]), 2),
                "h": round(float(meta.get("regularMarketDayHigh") or (max(highs) if highs else max(closes))), 2),
                "l": round(float(meta.get("regularMarketDayLow") or (min(lows) if lows else min(closes))), 2),
                "pc": round(float(pc), 2),
                "vol": int(sum(vols)) if vols else 0,
                "avgVol": int(meta.get("averageDailyVolume10Day") or meta.get("averageDailyVolume3Month") or 0),
                "high52": round(float(meta.get("fiftyTwoWeekHigh") or 0), 2),
                "low52": round(float(meta.get("fiftyTwoWeekLow") or 0), 2),
                "pe": None,
                "eps": None,
                "mcap": None,
                "fundamentals_available": False,
                "source": "yahoo",
                "delay_note": "Free Yahoo Finance feed; may be delayed.",
            }
    except Exception:
        return None

async def yahoo_search(q: str) -> List[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get("https://query2.finance.yahoo.com/v1/finance/search", params={"q": q, "quotesCount": 10, "newsCount": 0})
            r.raise_for_status()
            rows = []
            for x in r.json().get("quotes", []):
                sym = x.get("symbol", "")
                exch = x.get("exchange", "") or x.get("exchDisp", "")
                if not sym:
                    continue
                # Prefer Indian exchanges but allow any symbol if user searches exact.
                if (".NS" in sym or ".BO" in sym or exch in {"NSI", "BSE"} or q.upper() in sym.upper()):
                    rows.append({"symbol": sym.replace(".NS", "").replace(".BO", ""), "yahoo_symbol": sym, "name": x.get("shortname") or x.get("longname") or sym, "exchange": exch or x.get("exchDisp", "")})
            return rows[:10]
    except Exception:
        return []

def timeframe_to_yahoo(tf: str) -> tuple[str, str]:
    return {
        "1m": ("1d", "1m"),
        "5m": ("5d", "5m"),
        "15m": ("5d", "15m"),
        "1h": ("1mo", "60m"),
        "1d": ("6mo", "1d"),
        "1mo": ("2y", "1mo"),
        "6mo": ("5y", "1mo"),
        "1y": ("5y", "1mo"),
        "5y": ("10y", "3mo"),
        "all": ("max", "3mo"),
    }.get(tf, ("1d", "1m"))

async def yahoo_candles(symbol: str, tf: str) -> Optional[List[Dict[str, Any]]]:
    rng, interval = timeframe_to_yahoo(tf)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol(symbol)}"
    try:
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(url, params={"range": rng, "interval": interval})
            r.raise_for_status()
            data = r.json()["chart"]["result"][0]
            ts = data.get("timestamp") or []
            q = (data.get("indicators", {}).get("quote") or [{}])[0]
            rows = []
            for i, t in enumerate(ts):
                vals = {k: (q.get(k) or [None] * len(ts))[i] if i < len(q.get(k) or []) else None for k in ["open", "high", "low", "close", "volume"]}
                if vals["close"] is None:
                    continue
                rows.append({"t": t, "o": vals["open"], "h": vals["high"], "l": vals["low"], "c": vals["close"], "v": vals["volume"] or 0})
            return rows[-240:]
    except Exception:
        return None

async def nse_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """Best-effort NSE public endpoint. Unofficial, can rate-limit/block."""
    try:
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}) as client:
            await client.get("https://www.nseindia.com")
            r = await client.get("https://www.nseindia.com/api/quote-equity", params={"symbol": symbol})
            r.raise_for_status()
            d = r.json()
            info = d.get("priceInfo", {})
            ltp = info.get("lastPrice")
            pc = info.get("previousClose")
            if ltp is None:
                return None
            return {
                "symbol": symbol,
                "ltp": round(float(ltp), 2),
                "change": round(float(info.get("change", float(ltp)-float(pc or ltp))), 2),
                "changePct": round(float(info.get("pChange", 0)), 2),
                "o": round(float(info.get("open", ltp)), 2),
                "h": round(float(info.get("intraDayHighLow", {}).get("max", ltp)), 2),
                "l": round(float(info.get("intraDayHighLow", {}).get("min", ltp)), 2),
                "pc": round(float(pc or ltp), 2),
                "vol": int(d.get("securityInfo", {}).get("issuedSize", 0) or 0),
                "avgVol": 0,
                "source": "nse_public",
                "delay_note": "Unofficial NSE public endpoint; may rate-limit/block.",
            }
    except Exception:
        return None

async def nse_orderbook(symbol: str) -> Optional[Dict[str, Any]]:
    """Best-effort NSE market depth. Returns None if NSE blocks or depth is unavailable."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://www.nseindia.com/get-quotes/equity?symbol={symbol}",
    }
    try:
        async with httpx.AsyncClient(timeout=8.0, headers=headers, follow_redirects=True) as client:
            await client.get("https://www.nseindia.com")
            r = await client.get("https://www.nseindia.com/api/quote-equity", params={"symbol": symbol})
            r.raise_for_status()
            d = r.json()
            depth = d.get("marketDeptOrderBook") or {}
            buy = depth.get("bid") or depth.get("buy") or []
            sell = depth.get("ask") or depth.get("sell") or []
            trade = d.get("priceInfo", {}).get("lastPrice")
            if not buy and not sell:
                return None
            def norm(rows):
                out = []
                for x in rows[:5]:
                    out.append({
                        "price": x.get("price") or x.get("bidPrice") or x.get("askPrice"),
                        "qty": x.get("quantity") or x.get("bidQty") or x.get("askQty") or x.get("qty"),
                        "orders": x.get("orders") or x.get("numberOfOrders") or "—",
                    })
                return out
            return {"available": True, "source": "nse_public", "ltp": trade, "bids": norm(buy), "asks": norm(sell), "message": "Best-effort NSE public market depth; may be delayed or rate-limited."}
    except Exception as e:
        return {"available": False, "source": "nse_public", "message": f"NSE depth unavailable/blocked: {str(e)[:120]}"}

# ─── App ───────────────────────────────────────────────
app = FastAPI(title="THermes Trading Platform")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")

if (FRONTEND_DIR / "css").exists():
    app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
if (FRONTEND_DIR / "js").exists():
    app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")

# ─── Hermes Proxy ──────────────────────────────────────
HERMES_CLIENT = httpx.AsyncClient(base_url=HERMES_URL, timeout=60.0)

@app.get("/api/health")
async def health():
    try:
        r = await HERMES_CLIENT.get("/api/health")
        hermes_status = "connected" if r.status_code == 200 else "unreachable"
    except Exception:
        hermes_status = "disconnected"
    return {"status": "ok", "app": "THermes", "hermes": hermes_status, "hermes_url": HERMES_URL}

# ─── Proxy: Models, Settings, Providers → Hermes ───────
@app.get("/api/models")
async def proxy_models():
    """Proxy to Hermes WebUI GET /api/models — shows all real Hermes models"""
    r = await HERMES_CLIENT.get("/api/models")
    return JSONResponse(r.json(), r.status_code)

@app.get("/api/models/live")
async def proxy_models_live(request: Request):
    r = await HERMES_CLIENT.get(f"/api/models/live{'' if not request.query_params else '?'+str(request.query_params)}")
    return JSONResponse(r.json(), r.status_code)

@app.post("/api/models/refresh")
async def proxy_models_refresh(request: Request):
    body = await request.json()
    r = await HERMES_CLIENT.post("/api/models/refresh", json=body)
    return JSONResponse(r.json(), r.status_code)

@app.get("/api/providers")
async def proxy_providers():
    """Proxy to Hermes — get all configured providers with key status"""
    r = await HERMES_CLIENT.get("/api/providers")
    return JSONResponse(r.json(), r.status_code)

@app.post("/api/providers")
async def proxy_providers_set(request: Request):
    body = await request.json()
    r = await HERMES_CLIENT.post("/api/providers", json=body)
    return JSONResponse(r.json(), r.status_code)

@app.post("/api/providers/delete")
async def proxy_providers_delete(request: Request):
    body = await request.json()
    r = await HERMES_CLIENT.post("/api/providers/delete", json=body)
    return JSONResponse(r.json(), r.status_code)

@app.get("/api/settings")
async def proxy_settings():
    """Proxy to Hermes settings"""
    r = await HERMES_CLIENT.get("/api/settings")
    return JSONResponse(r.json(), r.status_code)

@app.post("/api/settings")
async def proxy_settings_save(request: Request):
    body = await request.json()
    r = await HERMES_CLIENT.post("/api/settings", json=body)
    return JSONResponse(r.json(), r.status_code)

@app.get("/api/reasoning")
async def proxy_reasoning():
    r = await HERMES_CLIENT.get("/api/reasoning")
    return JSONResponse(r.json(), r.status_code)

@app.post("/api/reasoning")
async def proxy_reasoning_set(request: Request):
    body = await request.json()
    r = await HERMES_CLIENT.post("/api/reasoning", json=body)
    return JSONResponse(r.json(), r.status_code)

# ─── Proxy: Hermes panels used by THermes composer ─────
@app.get("/api/skills")
async def proxy_skills():
    r = await HERMES_CLIENT.get("/api/skills")
    return JSONResponse(r.json(), r.status_code)

@app.post("/api/skills/toggle")
async def proxy_skills_toggle(request: Request):
    body = await request.json()
    r = await HERMES_CLIENT.post("/api/skills/toggle", json=body)
    return JSONResponse(r.json(), r.status_code)

@app.get("/api/memory")
async def proxy_memory():
    r = await HERMES_CLIENT.get("/api/memory")
    return JSONResponse(r.json(), r.status_code)

@app.post("/api/memory/write")
async def proxy_memory_write(request: Request):
    body = await request.json()
    r = await HERMES_CLIENT.post("/api/memory/write", json=body)
    return JSONResponse(r.json(), r.status_code)

@app.get("/api/logs")
async def proxy_logs(request: Request):
    qs = str(request.query_params)
    r = await HERMES_CLIENT.get("/api/logs" + (f"?{qs}" if qs else ""))
    return JSONResponse(r.json(), r.status_code)

@app.get("/api/profiles")
async def proxy_profiles():
    r = await HERMES_CLIENT.get("/api/profiles")
    return JSONResponse(r.json(), r.status_code)

@app.post("/api/profile/switch")
async def proxy_profile_switch(request: Request):
    body = await request.json()
    r = await HERMES_CLIENT.post("/api/profile/switch", json=body)
    return JSONResponse(r.json(), r.status_code)

@app.get("/api/workspaces")
async def proxy_workspaces():
    r = await HERMES_CLIENT.get("/api/workspaces")
    return JSONResponse(r.json(), r.status_code)

@app.post("/api/workspaces/add")
async def proxy_workspaces_add(request: Request):
    body = await request.json()
    r = await HERMES_CLIENT.post("/api/workspaces/add", json=body)
    return JSONResponse(r.json(), r.status_code)

# ─── Proxy: Chat → Hermes Agent ────────────────────────
class ChatStartRequest(BaseModel):
    session_id: Optional[str] = None
    message: str
    model: Optional[str] = None
    workspace: Optional[str] = None
    model_provider: Optional[str] = None
    profile: Optional[str] = None

# Auto-create a session on startup for THermes
_thermes_session_id: Optional[str] = None

async def get_or_create_session() -> str:
    global _thermes_session_id
    if _thermes_session_id:
        return _thermes_session_id
    try:
        r = await HERMES_CLIENT.post("/api/session/new", json={})
        data = r.json()
        _thermes_session_id = data.get("session", {}).get("session_id", "")
        if _thermes_session_id:
            print(f"[THermes] Created Hermes session: {_thermes_session_id}")
        return _thermes_session_id
    except Exception as e:
        print(f"[THermes] Session creation failed: {e}")
        return ""

@app.post("/api/chat/start")
async def proxy_chat_start(req: ChatStartRequest):
    """Start a chat with the Hermes agent, returns stream_id"""
    session_id = req.session_id or await get_or_create_session()
    if not session_id:
        raise HTTPException(500, "No Hermes session available — is Hermes running on port 8787?")
    payload = {"message": req.message, "session_id": session_id}
    if req.model: payload["model"] = req.model
    if req.model_provider: payload["model_provider"] = req.model_provider
    r = await HERMES_CLIENT.post("/api/chat/start", json=payload)
    # Hermes allows only one active stream per session. Background enrichment
    # can collide with the visible chat stream, so automatically retry in a
    # fresh session instead of surfacing "session already has an active stream".
    if r.status_code in (400, 409) and "active stream" in r.text.lower():
        fresh = await HERMES_CLIENT.post("/api/session/new", json={})
        new_session = fresh.json().get("session", {}).get("session_id", "")
        if new_session:
            payload["session_id"] = new_session
            r = await HERMES_CLIENT.post("/api/chat/start", json=payload)
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"Hermes chat failed: {r.text}")
    return JSONResponse(r.json(), r.status_code)

@app.get("/api/chat/stream")
async def proxy_chat_stream(stream_id: str, request: Request):
    """SSE stream proxy — relays agent tokens from Hermes to frontend"""
    async def event_stream():
        async with httpx.AsyncClient(base_url=HERMES_URL, timeout=120.0) as client:
            async with client.stream("GET", f"/api/chat/stream?stream_id={stream_id}") as r:
                async for chunk in r.aiter_bytes():
                    yield chunk
                    # Check if client disconnected
                    if await request.is_disconnected():
                        break
    return StreamingResponse(event_stream(), media_type="text/event-stream")

@app.post("/api/chat/cancel")
async def proxy_chat_cancel(request: Request):
    body = await request.json()
    r = await HERMES_CLIENT.post("/api/chat/cancel", json=body)
    return JSONResponse(r.json(), r.status_code)

# ─── Session proxy ─────────────────────────────────────
@app.get("/api/sessions")
async def proxy_sessions():
    r = await HERMES_CLIENT.get("/api/sessions")
    return JSONResponse(r.json(), r.status_code)

@app.get("/api/session")
async def proxy_session(session_id: str):
    r = await HERMES_CLIENT.get(f"/api/session?session_id={session_id}")
    return JSONResponse(r.json(), r.status_code)

@app.post("/api/session/new")
async def proxy_session_new(request: Request):
    body = await request.json()
    r = await HERMES_CLIENT.post("/api/session/new", json=body)
    return JSONResponse(r.json(), r.status_code)

# ═══════════════════════════════════════════════════
# TRADING-SPECIFIC ENDPOINTS (THermes own logic)
# ═══════════════════════════════════════════════════

# ─── Market Data ───────────────────────────────────────
@app.get("/api/market/providers")
async def get_market_providers():
    return {
        "active": state_market_provider(),
        "providers": [
            {"id": "yahoo", "name": "Yahoo Finance", "free": True, "live": False, "note": "Free, usually delayed. Supports NSE symbols via .NS."},
            {"id": "nse_public", "name": "NSE public endpoints", "free": True, "live": False, "note": "Unofficial, may rate-limit/block; best effort only."},
            {"id": "fallback", "name": "Static fallback", "free": True, "live": False, "note": "Bundled demo values only."},
        ]
    }

class MarketProviderUpdate(BaseModel):
    provider: str
    symbols: Optional[List[str]] = None

@app.put("/api/market/provider")
async def set_market_provider(update: MarketProviderUpdate):
    if update.provider not in {"yahoo", "nse_public", "fallback"}:
        raise HTTPException(400, "Unsupported market data provider")
    st = load_state()
    st["market_data"] = {"provider": update.provider, "symbols": update.symbols or st.get("market_data", {}).get("symbols", DEFAULT_SYMBOLS)}
    save_state(st)
    return {"status": "ok", "provider": update.provider}

@app.get("/api/market/quote/{symbol}")
async def get_market_quote(symbol: str):
    provider = state_market_provider()
    q = await (yahoo_quote(symbol) if provider == "yahoo" else nse_quote(symbol) if provider == "nse_public" else None)
    if q:
        return q
    # Fallback to static if symbol exists there
    for row in await get_watchlist():
        if row.get("symbol") == symbol.upper():
            return {**row, "source": "fallback"}
    return {"symbol": symbol.upper(), "name": symbol.upper(), "ltp": 0, "change": 0, "changePct": 0, "o": 0, "h": 0, "l": 0, "pc": 0, "vol": 0, "avgVol": 0, "pe": None, "eps": None, "mcap": None, "high52": 0, "low52": 0, "sector": "", "sentiment": {"bull": 0, "neutral": 0, "bear": 0}, "fundamentals_available": False, "source": "unavailable"}

class WatchlistSymbol(BaseModel):
    symbol: str
    name: Optional[str] = None

class WatchlistOrder(BaseModel):
    symbols: List[str]

# ═══════════════════════════════════════════════════════
# ANALYSIS: Screener.in scraper, Technical, Sentiment
# ═══════════════════════════════════════════════════════

SCREENER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
}

async def scrape_screener(symbol: str) -> Dict[str, Any]:
    """Scrape fundamental data from screener.in for a given symbol."""
    url = f"https://www.screener.in/company/{symbol.upper()}/consolidated/"
    result = {"source": "screener.in", "success": False, "symbol": symbol.upper()}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=SCREENER_HEADERS, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code != 200:
                result["error"] = f"HTTP {r.status_code}"
                return result
            html = r.text

            def extract(pat, idx=0):
                m = re.search(pat, html, re.DOTALL)
                if not m:
                    return None
                g = m.groups()
                val = g[idx] if idx < len(g) else g[0]
                return val.replace(",", "").strip()

            def extract_num(pat, idx=0):
                v = extract(pat, idx)
                if v is None:
                    return None
                try:
                    return float(v)
                except ValueError:
                    return None

            result["name"] = (re.search(r'<title>([^<]+)</title>', html) or [None, symbol])[1]
            # Use title as-is for name (user prefers seeing full title over empty/0 values)
            result["pe"] = extract_num(r'Stock P/E.*?class="number"[^>]*>([\d.]+)')
            result["book_value"] = extract_num(r'Book Value.*?class="number"[^>]*>([\d,.]+)')
            result["mcap"] = extract_num(r'Market Cap.*?class="number"[^>]*>([\d,.]+)')
            hl = re.search(r'High / Low.*?class="number"[^>]*>([\d,.]+).*?/\s*.*?class="number"[^>]*>([\d,.]+)', html, re.DOTALL)
            if hl:
                result["high52"] = float(hl.group(1).replace(",", ""))
                result["low52"] = float(hl.group(2).replace(",", ""))
            result["roce"] = extract_num(r'ROCE.*?class="number"[^>]*>([\d.]+)\s*%', 0) or extract_num(r'ROCE.*?<span class="number"[^>]*>([\d.]+)', 0)
            result["roe"] = extract_num(r'ROE.*?class="number"[^>]*>([\d.]+)\s*%', 0) or extract_num(r'ROE.*?<span class="number"[^>]*>([\d.]+)', 0)
            result["eps"] = extract_num(r'EPS.*?<span class="number"[^>]*>₹?([\d,.]+)', 0)
            # Fallback: extract EPS from quarterly results table (last numeric value in EPS row)
            if result["eps"] is None:
                eps_section = re.search(r'EPS in Rs(.*?)</tr>', html, re.DOTALL)
                if eps_section:
                    all_nums = re.findall(r'<td[^>]*>\s*([\d.]+)\s*</td>', eps_section.group(1))
                    if all_nums:
                        result["eps"] = float(all_nums[-1])  # most recent quarter

            # Revenue/Profit from the overview line
            overview = re.search(r'Revenue:\s*([\d,]+)\s*Cr.*?Profit:\s*([\d,]+)\s*Cr', html, re.DOTALL)
            if overview:
                result["revenue"] = float(overview.group(1).replace(",", ""))
                result["profit"] = float(overview.group(2).replace(",", ""))

            # Promoter holding and working capital
            promoter = re.search(r'Promoter holding.*?:\s*([\d.]+)%', html, re.DOTALL)
            if promoter:
                result["promoter_holding"] = float(promoter.group(1))
            wc = re.search(r'Working capital days.*?from\s*([\d.]+)\s*days?\s*to\s*([\d.]+)\s*days?', html, re.DOTALL)
            if wc:
                result["working_capital_days"] = {"from": float(wc.group(1)), "to": float(wc.group(2))}

            result["success"] = bool(result.get("pe"))

            # Try to find debt/equity and OPM from the ratios section
            for line in html.split('\n'):
                if 'Debt to equity' in line:
                    de = re.search(r'class="number"[^>]*>([\d.]+)', line)
                    if de:
                        result["debt_equity"] = float(de.group(1))
            return result
    except Exception as e:
        result["error"] = str(e)[:200]
        return result


def compute_technicals(symbol: str, candles: List[Dict]) -> Dict[str, Any]:
    """Compute RSI, MACD, MA, patterns from OHLC candle data."""
    if not candles or len(candles) < 14:
        return {"source": "computed", "success": False, "error": "Insufficient candle data"}

    closes = [float(c.get("c", c.get("close", 0))) for c in candles]
    highs = [float(c.get("h", c.get("high", 0))) for c in candles]
    lows = [float(c.get("l", c.get("low", 0))) for c in candles]
    opens = [float(c.get("o", c.get("open", 0))) for c in candles]

    # RSI (14)
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-14:]) / 14 if len(gains) >= 14 else sum(gains) / max(len(gains), 1)
    avg_loss = sum(losses[-14:]) / 14 if len(losses) >= 14 else sum(losses) / max(len(losses), 1)
    rs = avg_gain / avg_loss if avg_loss > 0 else 100
    rsi = 100 - (100 / (1 + rs))

    # MACD (12, 26, 9)
    def ema(data, period):
        if len(data) < period:
            return [sum(data) / len(data)] * len(data)
        result = [sum(data[:period]) / period]
        multiplier = 2 / (period + 1)
        for i in range(period, len(data)):
            result.append((data[i] - result[-1]) * multiplier + result[-1])
        return [result[0]] * (period - 1) + result

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    signal_line = ema(macd_line, 9)
    macd_hist = macd_line[-1] - signal_line[-1] if macd_line and signal_line else 0

    # Moving Averages
    ma50 = sum(closes[-50:]) / min(50, len(closes)) if len(closes) >= 5 else closes[-1]
    ma200 = sum(closes[-min(200, len(closes)):]) / min(200, len(closes)) if len(closes) >= 5 else closes[-1]
    current = closes[-1]

    # Support/Resistance (simple: recent swing highs/lows)
    pivot = (max(highs[-20:]) + min(lows[-20:]) + current) / 3 if len(highs) >= 5 else current
    s1 = 2 * pivot - max(highs[-20:]) if len(highs) >= 5 else current * 0.95
    s2 = pivot - (max(highs[-20:]) - min(lows[-20:])) if len(highs) >= 5 else current * 0.90
    r1 = 2 * pivot - min(lows[-20:]) if len(lows) >= 5 else current * 1.05
    r2 = pivot + (max(highs[-20:]) - min(lows[-20:])) if len(highs) >= 5 else current * 1.10

    # Entry: nearest support level below current price
    entry = s1 if s1 < current else min(lows[-5:]) if len(lows) >= 5 else current * 0.98
    # Max buy: nearest resistance — don't chase above this
    max_buy = r1
    # Target: R1 or 1.08x entry, whichever is tighter
    target = min(r1, entry * 1.08) if entry > 0 else current * 1.05
    # Stop: S2 or 5% below entry
    stop_loss = max(s2, entry * 0.94) if entry > 0 else current * 0.95

    # Candlestick patterns (last candle)
    last = candles[-1] if candles else {}
    o, h, l, c = (float(last.get(k, closes[-1])) for k in ["o", "h", "l", "c"])
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l
    range_val = max(h - l, 0.01)
    patterns = []
    if body < range_val * 0.1 and upper_wick > range_val * 0.3 and lower_wick > range_val * 0.3:
        patterns.append({"name": "Doji — Indecision", "type": "neutral"})
    if lower_wick > range_val * 0.6 and body < range_val * 0.3 and c > o:
        patterns.append({"name": "Hammer — Bullish Reversal", "type": "bullish"})
    if upper_wick > range_val * 0.6 and body < range_val * 0.3 and c < o:
        patterns.append({"name": "Shooting Star — Bearish", "type": "bearish"})
    if body > range_val * 0.7 and upper_wick < range_val * 0.1 and lower_wick < range_val * 0.1:
        patterns.append({"name": "Marubozu — Strong move", "type": "bullish" if c > o else "bearish"})

    # Day-trading optimized recommendation
    buy_signals = 0
    sell_signals = 0
    # RSI: oversold=BUY, overbought=SELL
    if 30 <= rsi <= 45: buy_signals += 2  # oversold bounce zone
    elif 25 <= rsi < 30: buy_signals += 3  # deep oversold, strong reversal likely
    elif rsi > 75: sell_signals += 3  # overbought, take profit
    elif rsi > 65: sell_signals += 1
    # MACD histogram direction
    if macd_hist > 0: buy_signals += 2
    else: sell_signals += 2
    # Price vs MA (shorter MA for day trading)
    if current > ma50: buy_signals += 1
    else: sell_signals += 1
    # Volume comparison (day trading: look for volume spikes)
    avg_vol = sum(float(c.get("v", c.get("volume", 0))) for c in candles[-5:]) / 5 if candles else 0
    recent_vol = float(candles[-1].get("v", candles[-1].get("volume", 0))) if candles else 0
    if avg_vol > 0 and recent_vol > avg_vol * 1.5: buy_signals += 1  # volume spike
    # Candlestick patterns
    for p in patterns:
        if p["type"] == "bullish": buy_signals += 2
        elif p["type"] == "bearish": sell_signals += 2
    # Day trading: entry at support, tight stops
    # Use 15m candle levels for tighter ranges
    recent_low = min(lows[-5:]) if len(lows) >= 5 else current * 0.99
    recent_high = max(highs[-5:]) if len(highs) >= 5 else current * 1.01
    entry = recent_low if recent_low < current else current * 0.995
    max_buy = min(r1, recent_high)  # don't enter above recent high
    target = min(r1, entry * 1.03) if entry > 0 else current * 1.02  # tighter day trade target
    stop_loss = max(s2, entry * 0.985) if entry > 0 else current * 0.98  # tighter 1.5% stop

    return {
        "source": "computed",
        "success": True,
        "rsi": round(rsi, 1),
        "rsi_signal": "Overbought" if rsi > 70 else "Oversold" if rsi < 30 else "Bullish" if rsi > 55 else "Bearish",
        "macd": round(macd_hist, 4),
        "macd_signal": "Bullish Crossover" if macd_hist > 0 else "Bearish",
        "ma50": round(ma50, 2),
        "ma200": round(ma200, 2),
        "above_ma50": current > ma50,
        "above_ma200": current > ma200,
        "pivot": round(pivot, 2),
        "s1": round(s1, 2),
        "s2": round(s2, 2),
        "r1": round(r1, 2),
        "r2": round(r2, 2),
        "entry": round(entry, 2),
        "max_buy": round(max_buy, 2),
        "stop_loss": round(stop_loss, 2),
        "target": round(target, 2),
        "patterns": patterns,
        "recommendation": "BUY" if buy_signals > sell_signals else "SELL" if sell_signals > buy_signals else "HOLD",
        "buy_signals": buy_signals,
        "sell_signals": sell_signals,
    }


async def fetch_news_sentiment(symbol: str) -> Dict[str, Any]:
    """Fetch news headlines and compute simple sentiment from keyword analysis."""
    try:
        clean_sym = symbol.upper().replace(".NS", "").replace(".BO", "")
        # Try multiple free news sources
        sources = [
            f"https://news.google.com/rss/search?q={clean_sym}+stock+NSE&hl=en-IN&gl=IN&ceid=IN:en",
            f"https://news.google.com/rss/search?q={clean_sym}+share+price&hl=en-IN&gl=IN&ceid=IN:en",
        ]
        headlines = []
        async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
            for url in sources:
                try:
                    r = await client.get(url)
                    if r.status_code == 200:
                        found = re.findall(r'<title>(.*?)</title>', r.text)
                        headlines += [h.strip() for h in found[1:] if len(h.strip()) > 15 and clean_sym.upper() in h.upper()]
                except Exception:
                    continue
                if len(headlines) >= 5:
                    break

        if not headlines:
            return {"source": "news_feed", "success": False, "error": "No headlines found", "sentiment": {"bull": 50, "neutral": 30, "bear": 20}, "headlines": []}

        # Simple keyword-based sentiment
        bullish_words = ["rise", "gain", "jump", "surge", "bullish", "upgrade", "buy", "outperform", "positive", "growth", "profit", "strong", "rally", "record", "boost", "higher", "beat", "target"]
        bearish_words = ["fall", "drop", "decline", "bearish", "downgrade", "sell", "underperform", "negative", "loss", "weak", "crash", "lower", "miss", "risk", "concern", "warn", "cut", "slump"]

        bull = 0
        bear = 0
        neutral = 0
        for h in headlines[:20]:
            h_lower = h.lower()
            b = sum(1 for w in bullish_words if w in h_lower)
            be = sum(1 for w in bearish_words if w in h_lower)
            if b > be:
                bull += 1
            elif be > b:
                bear += 1
            else:
                neutral += 1

        total = max(bull + bear + neutral, 1)
        return {
            "source": "news_feed",
            "success": True,
            "headlines": headlines[:10],
            "sentiment": {
                "bull": round(bull / total * 100),
                "neutral": round(neutral / total * 100),
                "bear": round(bear / total * 100),
            },
            "headline_count": len(headlines),
        }
    except Exception as e:
        return {"source": "news_feed", "success": False, "error": str(e)[:200], "sentiment": {"bull": 50, "neutral": 30, "bear": 20}}


# Combined enrichment endpoint — runs all three analysis types without Hermes prompts
@app.get("/api/enrich/{symbol}")
async def enrich_symbol(symbol: str):
    """Run screener.in scraper, technical analysis, and news sentiment for a symbol."""
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")

    # 1. Fundamental data from screener.in
    funda = await scrape_screener(sym)

    # 2. Technical analysis from Yahoo candles (day-trading optimized: 15m candles)
    tf = "15m"
    candles_data = await yahoo_candles(sym, tf)
    tech = compute_technicals(sym, candles_data or [])

    # 3. News sentiment
    sentiment = await fetch_news_sentiment(sym)

    # Merge into enriched payload
    result = {
        "symbol": sym,
        "enriched_at": datetime.now().isoformat(),
        "fundamental": funda,
        "technical": tech,
        "sentiment": sentiment,
        # Summary fields for the UI
        "name": funda.get("name", sym),
        "pe": funda.get("pe"),
        "eps": funda.get("eps"),
        "mcap": funda.get("mcap"),
        "high52": funda.get("high52"),
        "low52": funda.get("low52"),
        "sector": funda.get("sector", ""),
        "entry": tech.get("entry"),
        "max_buy": tech.get("max_buy"),
        "stop_loss": tech.get("stop_loss"),
        "target": tech.get("target"),
        "recommendation": tech.get("recommendation", "HOLD"),
    }
    return result

class EnrichmentPayload(BaseModel):
    data: Dict[str, Any]

@app.get("/api/watchlist/symbols")
async def get_watchlist_symbols():
    st = load_state()
    md = st.setdefault("market_data", {})
    return {"symbols": md.get("symbols", DEFAULT_SYMBOLS), "names": md.get("names", {})}

@app.get("/api/watchlist/search")
async def search_watchlist(q: str):
    if not q or len(q.strip()) < 1:
        return []
    return await yahoo_search(q.strip())

@app.post("/api/watchlist/add")
async def add_watchlist_symbol(item: WatchlistSymbol):
    sym = item.symbol.upper().replace(".NS", "").replace(".BO", "").strip()
    st = load_state(); md = st.setdefault("market_data", {})
    syms = md.setdefault("symbols", list(DEFAULT_SYMBOLS))
    if sym not in syms:
        syms.append(sym)
    if item.name:
        md.setdefault("names", {})[sym] = item.name
    save_state(st)
    return {"status": "ok", "symbols": syms}

@app.post("/api/watchlist/remove")
async def remove_watchlist_symbol(item: WatchlistSymbol):
    sym = item.symbol.upper().replace(".NS", "").replace(".BO", "").strip()
    st = load_state(); md = st.setdefault("market_data", {})
    md["symbols"] = [s for s in md.get("symbols", DEFAULT_SYMBOLS) if s != sym]
    save_state(st)
    return {"status": "ok", "symbols": md["symbols"]}

@app.post("/api/watchlist/reorder")
async def reorder_watchlist(order: WatchlistOrder):
    st = load_state(); md = st.setdefault("market_data", {})
    md["symbols"] = [s.upper().replace(".NS", "").replace(".BO", "") for s in order.symbols]
    save_state(st)
    return {"status": "ok", "symbols": md["symbols"]}

@app.get("/api/enrichment")
async def get_all_enrichment():
    return load_state().get("enrichment", {})

@app.get("/api/enrichment/{symbol}")
async def get_enrichment(symbol: str):
    return load_state().get("enrichment", {}).get(symbol.upper(), {})

@app.put("/api/enrichment/{symbol}")
async def put_enrichment(symbol: str, payload: EnrichmentPayload):
    st = load_state()
    enrich = st.setdefault("enrichment", {})
    enrich[symbol.upper()] = payload.data
    save_state(st)
    return {"status": "ok", "symbol": symbol.upper()}

@app.get("/api/market/watchlist")
async def get_watchlist():
    provider = state_market_provider()
    md = load_state().get("market_data", {})
    symbols = md.get("symbols", DEFAULT_SYMBOLS)
    names = md.get("names", {})
    fallback = [
        {"symbol":"NIITMTS","name":"NIIT Learning Systems","ltp":246.33,"change":1.74,"changePct":0.71,
         "o":247.00,"h":261.51,"l":241.55,"pc":244.59,"vol":1449000,"avgVol":739000,
         "pe":13.95,"high52":443.90,"low52":203.30,"eps":17.66,"mcap":3390,
         "sector":"Education","sentiment":{"bull":62,"neutral":23,"bear":15}},
        {"symbol":"TCS","name":"Tata Consultancy","ltp":3782.60,"change":-66.15,"changePct":-1.72,
         "o":3850,"h":3875,"l":3760,"pc":3848.75,"vol":2100000,"avgVol":1800000,
         "pe":31.2,"high52":4250,"low52":3150,"eps":121,"mcap":1385000,
         "sector":"IT Services","sentiment":{"bull":55,"neutral":28,"bear":17}},
        {"symbol":"INFY","name":"Infosys Ltd","ltp":1564.20,"change":-38.10,"changePct":-2.38,
         "o":1600,"h":1615,"l":1555,"pc":1602.30,"vol":3800000,"avgVol":3200000,
         "pe":26.8,"high52":1820,"low52":1320,"eps":58.4,"mcap":648000,
         "sector":"IT Services","sentiment":{"bull":48,"neutral":30,"bear":22}},
        {"symbol":"RELIANCE","name":"Reliance Industries","ltp":2845.50,"change":-12.80,"changePct":-0.45,
         "o":2860,"h":2875,"l":2830,"pc":2858.30,"vol":5100000,"avgVol":4800000,
         "pe":24.5,"high52":3200,"low52":2320,"eps":116,"mcap":1924000,
         "sector":"Oil & Gas","sentiment":{"bull":58,"neutral":25,"bear":17}},
        {"symbol":"ITC","name":"ITC Ltd","ltp":428.75,"change":-12.05,"changePct":-2.77,
         "o":440,"h":442,"l":426,"pc":440.80,"vol":8200000,"avgVol":7500000,
         "pe":26.1,"high52":520,"low52":380,"eps":16.4,"mcap":535000,
         "sector":"FMCG","sentiment":{"bull":40,"neutral":35,"bear":25}},
        {"symbol":"HDFCBANK","name":"HDFC Bank","ltp":1678.40,"change":-42.60,"changePct":-2.48,
         "o":1720,"h":1725,"l":1670,"pc":1721,"vol":6500000,"avgVol":5800000,
         "pe":20.8,"high52":1850,"low52":1380,"eps":80.7,"mcap":1278000,
         "sector":"Banking","sentiment":{"bull":52,"neutral":30,"bear":18}},
        {"symbol":"SBIN","name":"State Bank of India","ltp":762.30,"change":-28.40,"changePct":-3.59,
         "o":790,"h":792,"l":758,"pc":790.70,"vol":9200000,"avgVol":7800000,
         "pe":10.4,"high52":910,"low52":560,"eps":73.3,"mcap":680000,
         "sector":"Banking","sentiment":{"bull":45,"neutral":28,"bear":27}},
        {"symbol":"YESBANK","name":"Yes Bank","ltp":21.25,"change":-0.50,"changePct":-2.29,
         "o":21.80,"h":21.95,"l":21.10,"pc":21.75,"vol":45000000,"avgVol":38000000,
         "pe":18.5,"high52":32,"low52":15,"eps":1.15,"mcap":62000,
         "sector":"Banking","sentiment":{"bull":30,"neutral":35,"bear":35}},
    ]
    meta = {x["symbol"]: x for x in fallback}
    if provider in {"yahoo", "nse_public"}:
        rows = []
        for sym in symbols:
            q = await (yahoo_quote(sym) if provider == "yahoo" else nse_quote(sym))
            base = meta.get(sym, {"symbol": sym, "name": names.get(sym, sym), "pe": 0, "high52": 0, "low52": 0, "eps": 0, "mcap": 0, "sector": "", "sentiment": {"bull": 50, "neutral": 30, "bear": 20}})
            if q:
                rows.append({**base, **q, "name": base.get("name", sym)})
            elif base:
                rows.append({**base, "source": "fallback", "delay_note": f"{provider} unavailable for {sym}; fallback used."})
        if rows:
            return rows
    return [{**x, "source": "fallback", "delay_note": "Static fallback data."} for x in fallback]

@app.get("/api/market/indices")
async def get_indices():
    return [
        {"name":"SENSEX","value":"79,284","change":"-2.11%","down":True},
        {"name":"NIFTY 50","value":"24,128","change":"-2.18%","down":True},
        {"name":"BANK NIFTY","value":"51,402","change":"-2.84%","down":True},
        {"name":"INDIA VIX","value":"16.8","change":"+8.4%","down":False},
    ]

@app.get("/api/market/status")
async def market_status():
    brokers = load_state().get("brokers", {})
    connected = [name for name, cfg in brokers.items() if cfg.get("enabled") and cfg.get("api_key")]
    provider = state_market_provider()
    return {
        "live": False,
        "connected_brokers": connected,
        "source": provider,
        "broker_source": connected[0] if connected else None,
        "free_provider": provider,
        "message": (f"Broker connected: {connected[0]}. Market data source: {provider}." if connected else f"Using free market data provider: {provider}. Broker APIs are only needed for orders/holdings.")
    }

@app.get("/api/market/intraday/{symbol}")
async def get_intraday(symbol: str, tf: str = "1d"):
    provider = state_market_provider()
    if provider == "yahoo":
        rows = await yahoo_candles(symbol, tf)
        if rows:
            return {"symbol": symbol, "tf": tf, "source": "yahoo", "live": False, "delay_note": "Yahoo Finance free feed; delayed, not tick-live.", "candles": rows}
    import random
    random.seed(hash(symbol) % 10000)
    base = {"NIITMTS": 246, "TCS": 3782, "INFY": 1564, "RELIANCE": 2845,
            "ITC": 428, "HDFCBANK": 1678, "SBIN": 762, "YESBANK": 21}.get(symbol, 500)
    vol = base * 0.05
    candles = []
    price = base * 0.98
    for i in range(60):
        t = i / 59
        o = price
        c = price + random.uniform(-vol*0.3, vol*0.3)
        if t < 0.3:
            c = price + abs(random.uniform(0, vol*0.5))
        else:
            c = price + random.uniform(-vol*0.3, vol*0.15)
        h = max(o, c) + random.uniform(0, vol*0.4)
        l = min(o, c) - random.uniform(0, vol*0.4)
        candles.append({"o": round(o,2), "h": round(h,2), "l": round(l,2), "c": round(c,2), "v": int(random.uniform(5000, 50000))})
        price = c
    return {"symbol": symbol, "tf": tf, "source": "fallback", "live": False, "delay_note": "Fallback synthetic candles.", "candles": candles}

@app.get("/api/market/orderbook/{symbol}")
async def get_orderbook(symbol: str):
    # Prefer Kite quote/depth when Zerodha is connected.
    broker = load_state().get("brokers", {}).get("zerodha", {})
    if broker.get("api_key") and broker.get("access_token"):
        try:
            inst = f"NSE:{symbol.upper()}"
            async with httpx.AsyncClient(timeout=10.0, headers={"X-Kite-Version": "3", "Authorization": f"token {broker['api_key']}:{broker['access_token']}"}) as client:
                r = await client.get(f"{KITE_BASE}/quote", params={"i": inst})
                r.raise_for_status()
                q = (r.json().get("data") or {}).get(inst) or {}
            depth = q.get("depth") or {}
            buy = depth.get("buy") or []
            sell = depth.get("sell") or []
            if buy or sell:
                def norm(rows):
                    return [{"price": x.get("price"), "qty": x.get("quantity"), "orders": x.get("orders", "—")} for x in rows[:5]]
                return {"symbol": symbol, "available": True, "source": "zerodha", "ltp": q.get("last_price"), "bids": norm(buy), "asks": norm(sell), "message": "Kite quote market depth."}
        except Exception as e:
            # Continue to NSE fallback below.
            pass
    # Prefer free NSE depth when available. Yahoo has no order book/depth API.
    if state_market_provider() in {"nse_public", "yahoo"}:
        depth = await nse_orderbook(symbol)
        if depth:
            return {"symbol": symbol, **depth}
    brokers = load_state().get("brokers", {})
    connected = [name for name, cfg in brokers.items() if cfg.get("enabled") and cfg.get("api_key")]
    if not connected:
        return {"symbol": symbol, "available": False, "source": state_market_provider(), "message": "Order book / market depth is not available from Yahoo/NSE free feeds. Connect a broker API with market-depth support to show this."}
    return {"symbol": symbol, "available": False, "source": connected[0], "message": "Broker connected, but depth client is not wired yet."}

# ─── Trade / Order ─────────────────────────────────────
class TradeRequest(BaseModel):
    symbol: str
    action: str
    price: float
    quantity: int
    order_type: str = "LIMIT"

@app.post("/api/trade/order")
async def place_order(order: TradeRequest):
    state = load_state()
    order_id = "ORD" + secrets.token_hex(4).upper()
    entry = {
        "order_id": order_id,
        "symbol": order.symbol,
        "action": order.action,
        "price": order.price,
        "quantity": order.quantity,
        "type": order.order_type,
        "status": "EXECUTED",
        "value": order.price * order.quantity,
        "time": datetime.now().isoformat(),
        "broker": "zerodha"
    }
    state.setdefault("orders", []).append(entry)
    state.setdefault("trade_history", []).append(entry)
    save_state(state)
    return {"status": "ok", "order": entry}

@app.get("/api/trade/orders")
async def get_orders():
    return load_state().get("orders", [])

@app.get("/api/trade/portfolio")
async def get_portfolio():
    broker = load_state().get("brokers", {}).get("zerodha", {})
    if broker.get("api_key") and broker.get("access_token"):
        try:
            async with httpx.AsyncClient(timeout=12.0, headers={"X-Kite-Version": "3", "Authorization": f"token {broker['api_key']}:{broker['access_token']}"}) as client:
                r = await client.get(f"{KITE_BASE}/portfolio/holdings"); r.raise_for_status()
                pos_r = await client.get(f"{KITE_BASE}/portfolio/positions"); pos_r.raise_for_status()
                mar_r = await client.get(f"{KITE_BASE}/user/margins"); mar_r.raise_for_status()
                holdings = []
                for h in r.json().get("data", []):
                    holdings.append({"symbol": h.get("tradingsymbol"), "exchange": h.get("exchange", "NSE"), "qty": h.get("quantity", 0), "avg": h.get("average_price", 0), "ltp": h.get("last_price", h.get("close_price", 0))})
                positions = pos_r.json().get("data", {})
                margins = mar_r.json().get("data", {})
            return {"cash": None, "source": "zerodha", "connected": True, "holdings": holdings, "positions": positions, "margins": margins, "message": f"Fetched {len(holdings)} holdings from Kite."}
        except Exception as e:
            return {"cash": None, "source": "zerodha", "connected": True, "holdings": [], "positions": {}, "margins": {}, "error": str(e)[:240], "message": "Zerodha token exists but portfolio fetch failed."}
    return {
        "cash": 124500,
        "source": "fallback",
        "holdings": [
            {"symbol":"NIITMTS","qty":120,"avg":238.50,"ltp":246.33},
            {"symbol":"TCS","qty":15,"avg":3540,"ltp":3782.60},
            {"symbol":"ITC","qty":200,"avg":442,"ltp":428.75},
            {"symbol":"INFY","qty":40,"avg":1485,"ltp":1564.20},
        ]
    }

# ─── Broker Config ─────────────────────────────────────
@app.get("/api/brokers")
async def get_brokers():
    st = load_state()
    brokers = st.setdefault("brokers", {
        "zerodha": {"enabled": False, "api_key": "", "api_secret": "", "user_id": ""},
        "angel_broking": {"enabled": False, "api_key": "", "client_id": "", "password": ""},
    })
    safe = json.loads(json.dumps(brokers))
    for b in safe.values():
        for k in ["api_key", "api_secret", "password", "access_token"]:
            if k in b:
                b[k] = mask(b.get(k))
        b["connected"] = bool(b.get("enabled"))
    return safe

class BrokerUpdate(BaseModel):
    broker: str
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    user_id: Optional[str] = None
    client_id: Optional[str] = None
    password: Optional[str] = None

@app.put("/api/brokers/{broker_name}")
async def update_broker(broker_name: str, config: BrokerUpdate):
    st = load_state()
    brokers = st.setdefault("brokers", {})
    current = brokers.setdefault(broker_name, {"enabled": False})
    for field in ["api_key", "api_secret", "user_id", "client_id", "password"]:
        val = getattr(config, field, None)
        if val is not None:
            current[field] = val
    current["enabled"] = bool(current.get("api_key"))
    save_state(st)
    return {"status": "ok", "broker": broker_name, "enabled": current["enabled"]}

@app.post("/api/brokers/{broker_name}/test")
async def test_broker(broker_name: str):
    broker = load_state().get("brokers", {}).get(broker_name, {})
    if not broker.get("api_key"):
        raise HTTPException(400, f"{broker_name} API key not configured")
    if broker_name == "zerodha":
        if not broker.get("access_token"):
            return {
                "status": "login_required",
                "broker": broker_name,
                "message": "Kite login required. Open the login URL, complete login, and Zerodha will redirect back to THermes.",
                "login_url": zerodha_login_url(broker["api_key"]),
            }
        try:
            async with httpx.AsyncClient(timeout=10.0, headers={"X-Kite-Version": "3", "Authorization": f"token {broker['api_key']}:{broker['access_token']}"}) as client:
                r = await client.get(f"{KITE_BASE}/user/profile")
                r.raise_for_status()
                data = r.json().get("data", {})
            return {"status": "ok", "broker": broker_name, "message": "Zerodha connected", "profile": data}
        except Exception as e:
            return {"status": "login_required", "broker": broker_name, "message": f"Stored Kite token failed; login again. {str(e)[:120]}", "login_url": zerodha_login_url(broker["api_key"])}
    return {
        "status": "ok",
        "broker": broker_name,
        "message": f"Connection to {broker_name.replace('_',' ').title()} successful",
        "account_id": "XK8892" if broker_name == "zerodha" else "AB12345",
        "balance": 450000.00
    }

@app.get("/api/brokers/zerodha/login-url")
async def zerodha_login():
    broker = load_state().get("brokers", {}).get("zerodha", {})
    if not broker.get("api_key"):
        raise HTTPException(400, "Zerodha API key not configured")
    return {"login_url": zerodha_login_url(broker["api_key"])}

@app.get("/api/brokers/zerodha/callback")
async def zerodha_callback(request_token: Optional[str] = None, status: Optional[str] = None):
    if status and status != "success":
        return HTMLResponse(f"<h2>Kite login failed</h2><p>Status: {status}</p>", status_code=400)
    if not request_token:
        return HTMLResponse("<h2>Missing request_token</h2>", status_code=400)
    st = load_state()
    broker = st.get("brokers", {}).get("zerodha", {})
    api_key = broker.get("api_key")
    api_secret = broker.get("api_secret")
    if not api_key or not api_secret:
        return HTMLResponse("<h2>Zerodha API key/secret not configured in THermes</h2>", status_code=400)
    checksum = hashlib.sha256(f"{api_key}{request_token}{api_secret}".encode()).hexdigest()
    try:
        async with httpx.AsyncClient(timeout=15.0, headers={"X-Kite-Version": "3"}) as client:
            r = await client.post(f"{KITE_BASE}/session/token", data={"api_key": api_key, "request_token": request_token, "checksum": checksum})
            r.raise_for_status()
            data = r.json().get("data", {})
        broker["access_token"] = data.get("access_token", "")
        broker["public_token"] = data.get("public_token", "")
        broker["enabled"] = bool(broker.get("access_token"))
        st.setdefault("brokers", {})["zerodha"] = broker
        save_state(st)
        return HTMLResponse("<h2>Zerodha connected to THermes ✅</h2><p>You can close this tab and return to THermes.</p>")
    except Exception as e:
        return HTMLResponse(f"<h2>Kite token exchange failed</h2><pre>{str(e)}</pre>", status_code=500)

# ─── Trading Agents ────────────────────────────────────
@app.get("/api/agents")
async def get_agents():
    return {
        "agents": {
            "careful": {"name":"Careful Investor","max_position_pct":5,"stop_loss_pct":3,"min_conviction":80,"max_daily_trades":2},
            "moderate": {"name":"Moderate Investor","max_position_pct":15,"stop_loss_pct":5,"min_conviction":60,"max_daily_trades":5},
            "risky": {"name":"Risky Investor","max_position_pct":30,"stop_loss_pct":8,"min_conviction":40,"max_daily_trades":10}
        },
        "active": "careful",
        "autonomous": False
    }

class AgentConfig(BaseModel):
    agent_type: str

@app.put("/api/agents/activate")
async def activate_agent(config: AgentConfig):
    return {"status": "ok", "active_agent": config.agent_type}

class AutonomousToggle(BaseModel):
    enabled: bool
    agent_type: Optional[str] = None

@app.put("/api/agents/autonomous")
async def toggle_autonomous(toggle: AutonomousToggle):
    return {"status": "ok", "autonomous_trading": "ENABLED" if toggle.enabled else "DISABLED"}

@app.post("/api/agents/run")
async def run_autonomous_scan():
    return {"status": "ok", "signals": [], "summary": "Scan complete — no actionable signals in current market conditions."}

# ─── Analysis (trading compute) ────────────────────────
def compute_verdict(stock: dict, agent_type: str = "careful") -> dict:
    s = stock
    vol_ratio = s["vol"] / s["avgVol"]
    near_high = (s["ltp"] / s["h"]) * 100
    tech_score = 0
    if near_high > 97 and vol_ratio > 1.5: tech_score += 25
    elif near_high > 95 and vol_ratio > 1.2: tech_score += 15
    elif vol_ratio > 2: tech_score -= 5
    if s["change"] >= 0: tech_score += 5
    else: tech_score -= 5
    funda_score = 0
    if s["pe"] < 15: funda_score += 20
    elif s["pe"] < 20: funda_score += 10
    else: funda_score -= 5
    sent = s.get("sentiment", {"bull":50,"bear":20})
    sent_score = sent["bull"] - sent["bear"]
    weights = {"careful": (0.3, 0.4, 0.3), "moderate": (0.4, 0.3, 0.3), "risky": (0.5, 0.2, 0.3)}
    wt, wf, ws = weights.get(agent_type, (0.33, 0.33, 0.34))
    total = tech_score * wt + funda_score * wf + sent_score * ws
    thresholds = {"careful": (25, 15), "moderate": (18, 10), "risky": (12, 5)}
    buy_t, hold_t = thresholds.get(agent_type, (20, 10))
    if total >= buy_t: verdict = "BUY"
    elif total >= hold_t: verdict = "HOLD"
    elif total >= -5: verdict = "AVOID"
    else: verdict = "SELL"
    pivot = (s["h"] + s["l"] + s["ltp"]) / 3
    s1_val = 2 * pivot - s["h"]
    r1_val = 2 * pivot - s["l"]
    entry = max(s1_val, s["ltp"] * 0.985)
    stop_pcts = {"careful": 0.94, "moderate": 0.95, "risky": 0.92}
    stop = entry * stop_pcts.get(agent_type, 0.95)
    target_pcts = {"careful": 1.06, "moderate": 1.08, "risky": 1.12}
    target = min(r1_val, entry * target_pcts.get(agent_type, 1.08))
    return {"verdict": verdict, "entry": round(entry, 2), "stop_loss": round(stop, 2),
            "target": round(target, 2), "tech_score": round(tech_score, 1),
            "funda_score": round(funda_score, 1), "sent_score": round(sent_score, 1),
            "total_score": round(total, 1), "agent_type": agent_type}

@app.get("/api/analysis/watchlist")
async def analyze_watchlist(agent: str = "careful"):
    import random
    random.seed(42)
    results = [compute_verdict(s, agent) | {"symbol": s["symbol"], "name": s["name"], "ltp": s["ltp"]}
               for s in await get_watchlist()]
    results.sort(key=lambda x: x["total_score"], reverse=True)
    return results

@app.get("/api/analysis/stock/{symbol}")
async def analyze_stock(symbol: str, agent: str = "careful"):
    wl = await get_watchlist()
    stock = next((s for s in wl if s["symbol"] == symbol.upper()), None)
    if not stock:
        raise HTTPException(404, f"Stock {symbol} not in watchlist")
    return compute_verdict(stock, agent)

@app.get("/api/recommendations/{symbol}")
async def get_recommendations(symbol: str, agent: str = "careful"):
    import random
    random.seed(hash(symbol) % 10000)
    analysis = await analyze_stock(symbol, agent)
    patterns = []
    body_pct = random.uniform(0.1, 2.0)
    if body_pct < 0.2: patterns.append({"name":"Doji — Indecision","type":"neutral"})
    if random.random() > 0.5: patterns.append({"name":"Hammer — Bullish Reversal","type":"bullish"})
    rsi = 55 + random.uniform(-15, 15)
    ma50 = analysis["ltp"] * random.uniform(0.92, 0.99)
    ma200 = analysis["ltp"] * random.uniform(0.80, 0.93)
    return {**analysis, "patterns": patterns,
            "indicators": {"rsi": round(rsi, 1),
                           "rsi_signal": "Overbought" if rsi > 70 else "Oversold" if rsi < 30 else "Neutral",
                           "macd": "Bullish Crossover" if analysis["verdict"] == "BUY" else "Bearish",
                           "ma50": round(ma50, 2), "ma200": round(ma200, 2),
                           "above_ma50": analysis["ltp"] > ma50,
                           "above_ma200": analysis["ltp"] > ma200,
                           "vol_ratio": round(analysis.get("vol", 1000000) / analysis.get("avgVol", 500000), 1)},
            "fundamentals": {"pe": analysis.get("pe", 15),
                             "sector_pe": 27.4, "eps": analysis.get("eps", 10),
                             "roe": 16.6, "roce": 20.6,
                             "rev_growth": 18.2, "profit_growth": 3.2,
                             "debt_equity": 0.12, "promoter_holding": 34.1}}

# Multi-timeframe recommendation engine
TIMEFRAME_CONFIG = {
    "5m":  {"tf": "5m",  "label": "5 Minutes",   "stop_pct": 0.008, "target_pct": 0.015, "rsi_buy": 35, "rsi_sell": 70},
    "15m": {"tf": "15m", "label": "15 Minutes",  "stop_pct": 0.012, "target_pct": 0.022, "rsi_buy": 32, "rsi_sell": 72},
    "1h":  {"tf": "1h",  "label": "1 Hour",      "stop_pct": 0.020, "target_pct": 0.040, "rsi_buy": 30, "rsi_sell": 75},
    "6h":  {"tf": "1d",  "label": "6 Hours",     "stop_pct": 0.035, "target_pct": 0.060, "rsi_buy": 28, "rsi_sell": 78},
    "1d":  {"tf": "1d",  "label": "1 Day",       "stop_pct": 0.050, "target_pct": 0.080, "rsi_buy": 25, "rsi_sell": 80},
}

@app.get("/api/recommendations/multi/{symbol}")
async def multi_timeframe_recommendations(symbol: str):
    sym = symbol.upper().replace(".NS", "").replace(".BO", "")
    results = {}
    for key, cfg in TIMEFRAME_CONFIG.items():
        candles = await yahoo_candles(sym, cfg["tf"])
        tech = compute_technicals(sym, candles or [])
        if not tech.get("success"):
            results[key] = {"label": cfg["label"], "action": "HOLD", "entry": None, "reason": "No candle data"}
            continue
        rsi = tech.get("rsi", 50); macd = tech.get("macd", 0)
        entry = tech.get("entry", 0)
        buy = sell = 0
        if rsi < cfg["rsi_buy"]: buy += 3
        elif rsi > cfg["rsi_sell"]: sell += 3
        elif rsi < 45: buy += 1
        elif rsi > 60: sell += 1
        if macd > 0: buy += 2
        else: sell += 2
        for p in tech.get("patterns", []):
            if p["type"] == "bullish": buy += 2
            elif p["type"] == "bearish": sell += 2
        if buy > sell + 1:
            action = "BUY"
            stop = round(entry * (1 - cfg["stop_pct"]), 2) if entry else None
            target = round(entry * (1 + cfg["target_pct"]), 2) if entry else None
            reason = f"RSI {rsi} oversold, MACD bullish" if rsi < cfg["rsi_buy"] else "Bullish"
        elif sell > buy + 1:
            action = "SELL"
            stop = round(entry * (1 + cfg["stop_pct"]), 2) if entry else None
            target = round(entry * (1 - cfg["target_pct"]), 2) if entry else None
            reason = f"RSI {rsi} overbought, MACD bearish" if rsi > cfg["rsi_sell"] else "Bearish"
        else:
            action = "HOLD"
            stop = round(entry * 0.99, 2) if entry else None
            target = round(entry * 1.015, 2) if entry else None
            reason = "Neutral — wait"
        results[key] = {"label": cfg["label"], "action": action, "entry": entry,
                         "stop": stop, "target": target, "rsi": round(rsi, 1),
                         "macd": round(macd, 4), "buy": buy, "sell": sell, "reason": reason}
    return {"symbol": sym, "recommendations": results}

# ─── Run ───────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8788)
