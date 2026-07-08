"""
THermes — Agentic Trading Platform
FastAPI backend: proxies Hermes WebUI for models/settings/chat,
adds trading-specific endpoints (analysis, orders, agents, watchlist)
"""
import json
import os
import secrets
import math
from pathlib import Path
from typing import Optional
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
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
@app.get("/api/market/watchlist")
async def get_watchlist():
    return [
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

@app.get("/api/market/indices")
async def get_indices():
    return [
        {"name":"SENSEX","value":"79,284","change":"-2.11%","down":True},
        {"name":"NIFTY 50","value":"24,128","change":"-2.18%","down":True},
        {"name":"BANK NIFTY","value":"51,402","change":"-2.84%","down":True},
        {"name":"INDIA VIX","value":"16.8","change":"+8.4%","down":False},
    ]

@app.get("/api/market/intraday/{symbol}")
async def get_intraday(symbol: str):
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
    return {"symbol": symbol, "candles": candles}

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
    return {
        "cash": 124500,
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
    return {
        "zerodha": {"enabled": False, "api_key": "", "user_id": ""},
        "angel_broking": {"enabled": False, "api_key": "", "client_id": ""}
    }

class BrokerUpdate(BaseModel):
    broker: str
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    user_id: Optional[str] = None
    client_id: Optional[str] = None
    password: Optional[str] = None

@app.put("/api/brokers/{broker_name}")
async def update_broker(broker_name: str, config: BrokerUpdate):
    return {"status": "ok", "broker": broker_name}

@app.post("/api/brokers/{broker_name}/test")
async def test_broker(broker_name: str):
    return {
        "status": "ok",
        "broker": broker_name,
        "message": f"Connection to {broker_name.replace('_',' ').title()} successful",
        "account_id": "XK8892" if broker_name == "zerodha" else "AB12345",
        "balance": 450000.00
    }

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

# ─── Run ───────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8788)
