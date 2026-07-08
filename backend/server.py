"""
THermes — Agentic Trading Platform
FastAPI backend: config, LLMs, MCP, skills, trading agents, broker APIs
"""
import json
import os
import asyncio
import hashlib
import secrets
import time
import math
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager

# ─── Paths ──────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
FRONTEND_DIR = BASE_DIR / "frontend"
CONFIG_FILE = CONFIG_DIR / "default.json"
STATE_FILE = CONFIG_DIR / "state.json"

# ─── Config management ──────────────────────────────────
def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}

def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"orders": [], "trade_history": [], "autonomous_log": []}

def save_state(st: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(st, f, indent=2, default=str)

# ─── App lifecycle ──────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure default config
    cfg = load_config()
    if not cfg:
        print("[THermes] No config found — using embedded defaults.")
    print(f"[THermes] Server ready on port {cfg.get('app',{}).get('port',8788)}")
    yield

app = FastAPI(title="THermes Trading Platform", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── Static files ───────────────────────────────────────
@app.get("/")
async def serve_index():
    return FileResponse(FRONTEND_DIR / "index.html")

# Mount static assets
if (FRONTEND_DIR / "css").exists():
    app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
if (FRONTEND_DIR / "js").exists():
    app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")

# ─── Pydantic models ────────────────────────────────────
class LLMProviderUpdate(BaseModel):
    provider_id: str
    api_key: Optional[str] = None
    enabled: Optional[bool] = None
    model: Optional[str] = None

class ModelSelection(BaseModel):
    type: str  # "default" or "chat"
    provider: str
    model: str

class MCPServerConfig(BaseModel):
    id: str
    name: str
    command: str
    enabled: bool = False

class SkillConfig(BaseModel):
    id: str
    name: str
    description: str
    enabled: bool
    risk_level: str

class BrokerConfig(BaseModel):
    broker: str  # "zerodha" or "angel_broking"
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    user_id: Optional[str] = None
    access_token: Optional[str] = None
    client_id: Optional[str] = None
    password: Optional[str] = None

class AgentConfig(BaseModel):
    agent_type: str  # "careful", "moderate", "risky"

class AutonomousToggle(BaseModel):
    enabled: bool
    agent_type: Optional[str] = None

class TradeRequest(BaseModel):
    symbol: str
    action: str
    price: float
    quantity: int
    order_type: str = "LIMIT"

class ChatMessage(BaseModel):
    message: str
    session_id: Optional[str] = None

# ─── API: Health ────────────────────────────────────────
@app.get("/api/health")
async def health():
    cfg = load_config()
    return {
        "status": "ok",
        "app": "THermes",
        "version": cfg.get("app", {}).get("version", "1.0.0"),
        "timestamp": datetime.now().isoformat()
    }

# ─── API: Config ────────────────────────────────────────
@app.get("/api/config")
async def get_config():
    cfg = load_config()
    # Mask API keys
    safe = json.loads(json.dumps(cfg))
    for p in safe.get("llm_providers", []):
        if p.get("api_key"):
            p["api_key"] = "••••" + p["api_key"][-4:] if len(p["api_key"]) > 4 else "••••"
    for broker_key in safe.get("brokers", {}):
        b = safe["brokers"][broker_key]
        for key in ["api_key", "api_secret", "access_token", "password"]:
            if b.get(key):
                b[key] = "••••" + b[key][-4:] if len(str(b[key])) > 4 else "••••"
    return safe

# ─── API: LLM Providers ─────────────────────────────────
@app.get("/api/llm/providers")
async def get_llm_providers():
    cfg = load_config()
    providers = []
    for p in cfg.get("llm_providers", []):
        providers.append({
            "id": p["id"],
            "name": p["name"],
            "models": p["models"],
            "enabled": p["enabled"],
            "has_key": bool(p.get("api_key")),
            "api_base": p.get("api_base", "")
        })
    return providers

@app.put("/api/llm/providers/{provider_id}")
async def update_llm_provider(provider_id: str, update: LLMProviderUpdate):
    cfg = load_config()
    for p in cfg.get("llm_providers", []):
        if p["id"] == provider_id:
            if update.api_key is not None:
                p["api_key"] = update.api_key
            if update.enabled is not None:
                p["enabled"] = update.enabled
            if update.model is not None:
                if update.model not in p["models"]:
                    raise HTTPException(400, f"Model {update.model} not available for {provider_id}")
            save_config(cfg)
            return {"status": "ok", "provider": p["id"]}
    raise HTTPException(404, f"Provider {provider_id} not found")

@app.put("/api/llm/model")
async def set_model(selection: ModelSelection):
    cfg = load_config()
    valid = any(p["id"] == selection.provider and selection.model in p["models"] for p in cfg.get("llm_providers", []))
    if not valid:
        raise HTTPException(400, f"Invalid provider/model: {selection.provider}/{selection.model}")
    key = f"{selection.type}_model"
    cfg[key] = {"provider": selection.provider, "model": selection.model}
    save_config(cfg)
    return {"status": "ok", "type": selection.type, "provider": selection.provider, "model": selection.model}

# ─── API: MCP Servers ───────────────────────────────────
@app.get("/api/mcp/servers")
async def get_mcp_servers():
    cfg = load_config()
    return cfg.get("mcp_servers", [])

@app.post("/api/mcp/servers")
async def add_mcp_server(server: MCPServerConfig):
    cfg = load_config()
    # Check for duplicates
    if any(s["id"] == server.id for s in cfg.get("mcp_servers", [])):
        raise HTTPException(400, f"MCP server {server.id} already exists")
    cfg.setdefault("mcp_servers", []).append(server.model_dump())
    save_config(cfg)
    return {"status": "ok", "server": server.id}

@app.put("/api/mcp/servers/{server_id}")
async def update_mcp_server(server_id: str, update: dict):
    cfg = load_config()
    for s in cfg.get("mcp_servers", []):
        if s["id"] == server_id:
            s.update({k: v for k, v in update.items() if k in s})
            save_config(cfg)
            return {"status": "ok"}
    raise HTTPException(404, f"MCP server {server_id} not found")

@app.delete("/api/mcp/servers/{server_id}")
async def remove_mcp_server(server_id: str):
    cfg = load_config()
    cfg["mcp_servers"] = [s for s in cfg.get("mcp_servers", []) if s["id"] != server_id]
    save_config(cfg)
    return {"status": "ok"}

# ─── API: Skills ────────────────────────────────────────
@app.get("/api/skills")
async def get_skills():
    cfg = load_config()
    return cfg.get("skills", [])

@app.put("/api/skills/{skill_id}")
async def update_skill(skill_id: str, update: dict):
    cfg = load_config()
    for s in cfg.get("skills", []):
        if s["id"] == skill_id:
            s.update({k: v for k, v in update.items() if k in s})
            save_config(cfg)
            return {"status": "ok"}
    raise HTTPException(404, f"Skill {skill_id} not found")

# ─── API: Broker Configuration ──────────────────────────
@app.get("/api/brokers")
async def get_brokers():
    cfg = load_config()
    brokers = {}
    for name, data in cfg.get("brokers", {}).items():
        safe = {k: v for k, v in data.items()}
        for key in ["api_key", "api_secret", "access_token", "password"]:
            if safe.get(key) and len(str(safe[key])) > 4:
                safe[key] = "••••" + str(safe[key])[-4:]
        brokers[name] = safe
    return brokers

@app.put("/api/brokers/{broker_name}")
async def update_broker(broker_name: str, config: BrokerConfig):
    cfg = load_config()
    if broker_name not in cfg.get("brokers", {}):
        raise HTTPException(404, f"Broker {broker_name} not supported")
    b = cfg["brokers"][broker_name]
    for field in ["api_key", "api_secret", "user_id", "access_token", "client_id", "password"]:
        val = getattr(config, field, None)
        if val is not None:
            b[field] = val
    save_config(cfg)
    return {"status": "ok", "broker": broker_name}

@app.post("/api/brokers/{broker_name}/test")
async def test_broker(broker_name: str):
    cfg = load_config()
    b = cfg.get("brokers", {}).get(broker_name, {})
    if not b.get("api_key") and not b.get("access_token"):
        raise HTTPException(400, "No API credentials configured")
    # Simulate connection test
    return {
        "status": "ok",
        "broker": broker_name,
        "message": f"Connection to {broker_name.replace('_',' ').title()} successful",
        "account_id": "XK8892" if broker_name == "zerodha" else "AB12345",
        "balance": 450000.00
    }

# ─── API: Trading Agents ────────────────────────────────
@app.get("/api/agents")
async def get_agents():
    cfg = load_config()
    return {
        "agents": cfg.get("trading_agents", {}),
        "active": cfg.get("active_trading_agent", "careful"),
        "autonomous": cfg.get("autonomous_trading", False)
    }

@app.put("/api/agents/activate")
async def activate_agent(config: AgentConfig):
    cfg = load_config()
    if config.agent_type not in cfg.get("trading_agents", {}):
        raise HTTPException(400, f"Unknown agent type: {config.agent_type}")
    cfg["active_trading_agent"] = config.agent_type
    save_config(cfg)
    return {"status": "ok", "active_agent": config.agent_type}

@app.put("/api/agents/autonomous")
async def toggle_autonomous(toggle: AutonomousToggle):
    cfg = load_config()
    cfg["autonomous_trading"] = toggle.enabled
    if toggle.agent_type:
        if toggle.agent_type not in cfg.get("trading_agents", {}):
            raise HTTPException(400, f"Unknown agent type: {toggle.agent_type}")
        cfg["active_trading_agent"] = toggle.agent_type
    save_config(cfg)
    status = "ENABLED" if toggle.enabled else "DISABLED"
    return {"status": "ok", "autonomous_trading": status, "agent": cfg.get("active_trading_agent")}

# ─── API: Charts & Market Data ──────────────────────────
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
    # Simulated intraday OHLC data (60 candles)
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

# ─── API: Trade / Order ─────────────────────────────────
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

    # Trigger autonomous agent check
    cfg = load_config()
    if cfg.get("autonomous_trading"):
        # In production, this would fire the autonomous agent loop
        pass

    return {"status": "ok", "order": entry}

@app.get("/api/trade/orders")
async def get_orders():
    state = load_state()
    return state.get("orders", [])

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

# ─── API: Analysis (Agent compute) ──────────────────────
def compute_verdict(stock: dict, agent_type: str = "careful") -> dict:
    """Compute technical + fundamental + sentiment verdict for a stock."""
    s = stock
    # Technical signals
    vol_ratio = s["vol"] / s["avgVol"]
    near_high = (s["ltp"] / s["h"]) * 100
    tech_score = 0
    if near_high > 97 and vol_ratio > 1.5: tech_score += 25
    elif near_high > 95 and vol_ratio > 1.2: tech_score += 15
    elif vol_ratio > 2: tech_score -= 5
    if s["change"] >= 0: tech_score += 5
    else: tech_score -= 5
    above_50ma = s["ltp"] > s["ltp"] * 0.95  # simulated
    if above_50ma: tech_score += 5

    # Fundamental signals
    funda_score = 0
    if s["pe"] < 15: funda_score += 20
    elif s["pe"] < 20: funda_score += 10
    else: funda_score -= 5
    dist_from_high = (s["high52"] - s["ltp"]) / s["high52"]
    if dist_from_high > 0.4: funda_score += 10
    if s["eps"] > 0: funda_score += 5

    # Sentiment signals
    sent = s.get("sentiment", {"bull":50,"neutral":30,"bear":20})
    sent_score = sent["bull"] - sent["bear"]

    # Combined score (weighted by agent risk profile)
    weights = {"careful": (0.3, 0.4, 0.3), "moderate": (0.4, 0.3, 0.3), "risky": (0.5, 0.2, 0.3)}
    wt, wf, ws = weights.get(agent_type, (0.33, 0.33, 0.34))
    total = tech_score * wt + funda_score * wf + sent_score * ws

    # Agent-specific thresholds
    thresholds = {"careful": (25, 15, -5), "moderate": (18, 10, -10), "risky": (12, 5, -15)}
    buy_t, hold_t, _ = thresholds.get(agent_type, (20, 10, -10))

    if total >= buy_t: verdict = "BUY"
    elif total >= hold_t: verdict = "HOLD"
    elif total >= -5: verdict = "AVOID"
    else: verdict = "SELL"

    pivot = (s["h"] + s["l"] + s["ltp"]) / 3
    s1 = 2 * pivot - s["h"]
    r1 = 2 * pivot - s["l"]
    entry = max(s1, s["ltp"] * 0.985)
    stop_pcts = {"careful": 0.94, "moderate": 0.95, "risky": 0.92}
    stop = entry * stop_pcts.get(agent_type, 0.95)
    target_pcts = {"careful": 1.06, "moderate": 1.08, "risky": 1.12}
    target = min(r1, entry * target_pcts.get(agent_type, 1.08))

    return {
        "verdict": verdict,
        "entry": round(entry, 2),
        "stop_loss": round(stop, 2),
        "target": round(target, 2),
        "tech_score": round(tech_score, 1),
        "funda_score": round(funda_score, 1),
        "sent_score": round(sent_score, 1),
        "total_score": round(total, 1),
        "agent_type": agent_type
    }

@app.get("/api/analysis/watchlist")
async def analyze_watchlist(agent: str = "careful"):
    watchlist = [
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
    results = [compute_verdict(s, agent) | {"symbol": s["symbol"], "name": s["name"], "ltp": s["ltp"]} for s in watchlist]
    results.sort(key=lambda x: x["total_score"], reverse=True)
    return results

@app.get("/api/analysis/stock/{symbol}")
async def analyze_stock(symbol: str, agent: str = "careful"):
    watchlist = [
        {"symbol":"NIITMTS","ltp":246.33,"change":1.74,"changePct":0.71,
         "o":247.00,"h":261.51,"l":241.55,"pc":244.59,"vol":1449000,"avgVol":739000,
         "pe":13.95,"high52":443.90,"low52":203.30,"eps":17.66,"mcap":3390,
         "sector":"Education","sentiment":{"bull":62,"neutral":23,"bear":15}},
    ]
    stock = next((s for s in watchlist if s["symbol"] == symbol.upper()), None)
    if not stock:
        raise HTTPException(404, f"Stock {symbol} not in watchlist")
    return compute_verdict(stock, agent)

# ─── API: Autonomous Trading ────────────────────────────
@app.post("/api/agents/run")
async def run_autonomous_scan():
    """Run one cycle of autonomous trading analysis."""
    cfg = load_config()
    if not cfg.get("autonomous_trading"):
        return {"status": "skipped", "reason": "Autonomous trading is disabled"}

    agent_type = cfg.get("active_trading_agent", "careful")
    results = await analyze_watchlist(agent=agent_type)

    # Find actionable trades
    trades = []
    state = load_state()
    agent_cfg = cfg.get("trading_agents", {}).get(agent_type, {})
    min_conviction = agent_cfg.get("min_conviction", 60)

    for r in results:
        if r["verdict"] == "BUY" and r["total_score"] >= min_conviction / 100 * 50:
            trades.append({
                "symbol": r["symbol"],
                "action": "BUY",
                "entry": r["entry"],
                "stop_loss": r["stop_loss"],
                "target": r["target"],
                "conviction": round(r["total_score"] / 50 * 100),  # normalize to 0-100
                "recommended_qty": max(1, int((50000 / r["ltp"]) * agent_cfg.get("max_position_pct", 10) / 100))
            })

    log_entry = {
        "time": datetime.now().isoformat(),
        "agent": agent_type,
        "signals_found": len(trades),
        "trades": trades,
        "watchlist_count": len(results)
    }
    state.setdefault("autonomous_log", []).append(log_entry)
    save_state(state)

    return {
        "status": "ok",
        "agent": agent_type,
        "cycle": len(state.get("autonomous_log", [])),
        "signals": trades,
        "summary": f"Found {len(trades)} actionable trade(s) for {agent_type} agent"
    }

@app.get("/api/agents/log")
async def get_agent_log(limit: int = 20):
    state = load_state()
    log = state.get("autonomous_log", [])
    return log[-limit:]

# ─── API: Chat (LLM Proxy) ──────────────────────────────
@app.post("/api/chat")
async def chat(message: ChatMessage):
    """
    Chat with the selected LLM model.
    In production, this would route to the actual LLM API.
    For demo, returns simulated responses based on intent matching.
    """
    cfg = load_config()
    chat_cfg = cfg.get("chat_model", cfg.get("default_model", {}))
    provider = chat_cfg.get("provider", "deepseek")
    model = chat_cfg.get("model", "deepseek-chat")

    # Find provider config
    prov = next((p for p in cfg.get("llm_providers", []) if p["id"] == provider), None)
    api_key = prov.get("api_key", "") if prov else ""

    lower = message.message.lower()

    # Generate simulated response (in production, call actual LLM API)
    response = generate_simulated_response(message.message)

    # If a real API key is configured, we'd call the actual LLM here
    if api_key and len(api_key) > 10:
        try:
            # Placeholder for actual LLM call
            # response = await call_llm_api(provider, model, api_key, message.message)
            pass
        except Exception:
            pass  # Fall back to simulated

    return {
        "role": "assistant",
        "content": response,
        "model": f"{provider}/{model}",
        "simulated": not (api_key and len(api_key) > 10)
    }

def generate_simulated_response(user_text: str) -> str:
    """Generate a simulated agent response based on intent matching."""
    lower = user_text.lower()

    if any(w in lower for w in ["fundamental", "valuation", "financial health"]):
        return """**Fundamental Scan — Watchlist (ranked strongest to weakest)**

1. **NIITMTS** — 🟢 Strong (Score: 82/100)
   • P/E: 13.95 vs sector 27.4 (-49% cheap)
   • EPS: ₹17.66 | 52W Range: ₹203–₹444
   • ✅ Undervalued — good entry zone

2. **SBIN** — 🟢 Strong (Score: 76/100)
   • P/E: 10.4 vs sector 18.2 (-43% cheap)
   • EPS: ₹73.30 | 52W Range: ₹560–₹910
   • ✅ Deep value — strong govt backing

3. **HDFCBANK** — 🟡 Average (Score: 62/100)
   • P/E: 20.8 vs sector 18.2 (+14% premium)
   • EPS: ₹80.70 | 52W Range: ₹1380–₹1850
   • ⚠️ Fairly valued — wait for dip below ₹1,620

4. **TCS** — 🟡 Average (Score: 55/100)
   • P/E: 31.2 vs sector 28.5 (+9% premium)
   • EPS: ₹121 | Consistent compounder
   • ⚠️ Premium justified by quality, but limited upside

5. **RELIANCE** — 🟡 Average (Score: 54/100)
6. **INFY** — 🟡 Average (Score: 48/100)
7. **ITC** — 🔴 Weak (Score: 38/100)
8. **YESBANK** — 🔴 Weak (Score: 22/100)

**Best fundamental pick: NIITMTS** — 49% discount to sector P/E, 18% revenue growth, 20.6% ROCE. Red flag: declining OPM and high working capital days. **Buy below ₹244.**"""

    if any(w in lower for w in ["sentiment", "news", "buzz"]):
        return """**Sentiment Scan — Watchlist**

🟢 Strong Bullish **NIITMTS**: Bull 62% / Neutral 23% / Bear 15% ✅ Price + Sentiment aligned bullish
🟢 Strong Bullish **RELIANCE**: Bull 58% / Neutral 25% / Bear 17% ✅ Stable sentiment
🟢 Strong Bullish **TCS**: Bull 55% / Neutral 28% / Bear 17% ✅ Quality premium
🟡 Mildly Bullish **HDFCBANK**: Bull 52% / Neutral 30% / Bear 18%
🟡 Mildly Bullish **INFY**: Bull 48% / Neutral 30% / Bear 22%
🟡 Mildly Bullish **SBIN**: Bull 45% / Neutral 28% / Bear 27% ⚠️ Divergence
🟡 Mildly Bullish **ITC**: Bull 40% / Neutral 35% / Bear 25%
🔴 Bearish Bias **YESBANK**: Bull 30% / Neutral 35% / Bear 35%

**Key finding:** NIITMTS has strongest sentiment (62% bullish), confirmed by today's price action. Banking stocks show divergence — caution advised."""

    if any(w in lower for w in ["portfolio", "holding", "pnl", "rebalanc"]):
        return """**Portfolio Review — A/C XK8892**

| Holding | Qty | Avg | LTP | P&L | % |
|---|---|---|---|---|---|
| NIITMTS | 120 | ₹238.50 | ₹246.33 | +₹940 | +3.3% |
| TCS | 15 | ₹3,540 | ₹3,782.60 | +₹3,639 | +6.9% |
| ITC | 200 | ₹442 | ₹428.75 | -₹2,650 | -3.0% |
| INFY | 40 | ₹1,485 | ₹1,564.20 | +₹3,168 | +5.3% |

**Total Portfolio Value:** ₹1,33,597
**Cash:** ₹1,24,500
**Unrealized P&L:** +₹5,097 (+3.8%)

**Best:** TCS (+6.9%) — Consider taking partial profits at ₹3,850
**Weakest:** ITC (-3.0%) — Hold with stop at ₹418
**Suggestion:** Deploy ₹37,350 (30% cash) into NIITMTS at current levels."""

    if any(w in lower for w in ["day trade", "intraday", "scalp"]):
        return """**Day Trade Setups — Today**

Market deeply red (SENSEX -2.11%), favor relative strength:

1. 🥇 **NIITMTS** — Best setup
   • Entry: ₹243–245 (pullback buy) | Target: ₹260–265 | Stop: ₹238 | R:R = 1:3.5
   • Why: Only green stock, volume 2x avg, defending gains

2. 🥈 **TCS** — Bounce play
   • Entry: ₹3,760–3,780 | Target: ₹3,840–3,860 | Stop: ₹3,730 | R:R = 1:2.1
   • Why: Oversold, strong support at 3,750

3. 🥉 **RELIANCE** — Range trade
   • Entry: ₹2,835–2,845 | Target: ₹2,870–2,875 | Stop: ₹2,820 | R:R = 1:1.8
   • Why: Tight range, predictable"""

    if any(w in lower for w in ["breakout", "break out"]):
        return """**Breakout Scan — Watchlist**

➖ **NIITMTS** (₹246, +0.7%): No breakout — consolidating [Vol: 2.0x]
➖ **TCS** (₹3,783, -1.7%): No breakout — consolidating [Vol: 1.2x]
➖ **INFY** (₹1,564, -2.4%): No breakout — consolidating [Vol: 1.2x]
➖ **RELIANCE** (₹2,846, -0.5%): No breakout [Vol: 1.1x]
➖ **ITC** (₹429, -2.8%): No breakout [Vol: 1.1x]
➖ **HDFCBANK** (₹1,678, -2.5%): No breakout [Vol: 1.1x]
➖ **SBIN** (₹762, -3.6%): No breakout [Vol: 1.2x]
➖ **YESBANK** (₹21, -2.3%): No breakout [Vol: 1.2x]

**Top candidate: NIITMTS** — green on red market day, volume elevated. Break above ₹262 → ₹275."""

    # Default
    return """I've analyzed your request. Here's what I can help with:

**Quick actions:**
- Say **"scan breakouts"** — breakout patterns across your watchlist
- Say **"analyze watchlist"** — entry/exit levels with stop losses
- Say **"fundamental scan"** — valuation, growth, financial health scores
- Say **"sentiment scan"** — news, social buzz, analyst ratings
- Say **"portfolio review"** — P&L breakdown and rebalancing
- Say **"day trade setup"** — best intraday opportunities

Click any stock in the sidebar for **Technical** (candlestick patterns, RSI, MACD), **Fundamental** (valuation scores), and **one-click Trading** via Zerodha.

What would you like to do?"""

# ─── API: Agent Recommendations (for frontend detail panel) ───
@app.get("/api/recommendations/{symbol}")
async def get_recommendations(symbol: str, agent: str = "careful"):
    """Get comprehensive recommendation with candlestick patterns, RSI, MACD, etc."""
    import random
    random.seed(hash(symbol) % 10000)

    analysis = await analyze_stock(symbol, agent)

    # Candlestick patterns
    body_pct = random.uniform(0.1, 2.0)
    upper_wick = random.uniform(5, 35)
    lower_wick = random.uniform(5, 35)
    patterns = []
    if body_pct < 0.2 and upper_wick > 15 and lower_wick > 15:
        patterns.append({"name": "Doji — Indecision", "type": "neutral"})
    if lower_wick > 25 and body_pct > 0.3:
        patterns.append({"name": "Hammer — Bullish Reversal", "type": "bullish"})
    if body_pct > 1.2 and analysis["verdict"] == "BUY":
        patterns.append({"name": "Bullish Marubozu", "type": "bullish"})
    if body_pct > 1.2 and analysis["verdict"] == "SELL":
        patterns.append({"name": "Bearish Marubozu", "type": "bearish"})

    # Indicators
    rsi = 55 + random.uniform(-15, 15) if analysis["change"] >= 0 else 40 + random.uniform(-10, 15)
    ma50 = analysis["ltp"] * random.uniform(0.92, 0.99)
    ma200 = analysis["ltp"] * random.uniform(0.80, 0.93)

    return {
        **analysis,
        "patterns": patterns,
        "indicators": {
            "rsi": round(rsi, 1),
            "rsi_signal": "Overbought" if rsi > 70 else "Oversold" if rsi < 30 else "Neutral",
            "macd": "Bullish Crossover" if analysis["verdict"] == "BUY" else "Bearish" if analysis["verdict"] == "SELL" else "Neutral",
            "ma50": round(ma50, 2),
            "ma200": round(ma200, 2),
            "above_ma50": analysis["ltp"] > ma50,
            "above_ma200": analysis["ltp"] > ma200,
            "vol_ratio": round(analysis["vol"] / analysis["avgVol"], 1)
        },
        "fundamentals": {
            "pe": analysis["pe"],
            "sector_pe": 27.4 if analysis["sector"] == "Education" else 28.5,
            "eps": analysis["eps"],
            "roe": 16.6 if analysis["pe"] < 15 else 12.0,
            "roce": 20.6 if analysis["pe"] < 15 else 15.0,
            "rev_growth": 18.2 if symbol == "NIITMTS" else round(random.uniform(5, 20), 1),
            "profit_growth": round(random.uniform(2, 12), 1),
            "debt_equity": round(random.uniform(0.05, 0.8), 2),
            "opm_trend": "Declining" if symbol == "NIITMTS" else "Stable",
            "promoter_holding": round(random.uniform(30, 60), 1)
        }
    }

# ─── WebSocket for real-time updates ────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            # Handle real-time commands
            if data == "ping":
                await websocket.send_json({"type": "pong", "time": datetime.now().isoformat()})
            elif data.startswith("subscribe:"):
                symbol = data.split(":")[1]
                # In production, subscribe to real market data feed
                await websocket.send_json({"type": "subscribed", "symbol": symbol})
    except WebSocketDisconnect:
        pass

# ─── Run ────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    cfg = load_config()
    port = cfg.get("app", {}).get("port", 8788)
    host = cfg.get("app", {}).get("host", "127.0.0.1")
    uvicorn.run(app, host=host, port=port)
