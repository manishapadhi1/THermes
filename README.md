# THermes — Agentic Trading Platform

 **Multi-Broker** | **Autonomous Agents**

A full-featured trading platform with an AI agent chat interface, inspired by Hermes WebUI. Connects to Zerodha Kite and Angel Broking for order execution, supports multiple LLM backends (DeepSeek, OpenAI, Claude, Gemini, Qwen), includes autonomous trading agents with three risk profiles, and integrates MCP servers and skills.

## Features

### 🤖 AI Trading Agent
- Chat-based interface — ask for analysis, get entry/exit recommendations
- Goal-driven workflows: breakout scanning, watchlist analysis, sentiment scanning, portfolio review, day trade setups
- Real-time stock analysis with **technical**, **fundamental**, and **sentiment** scores
- Prominent **BUY / SELL / HOLD / AVOID** verdict badge per stock
- **Entry / Stop Loss / Target** prices always visible

### 📊 Analysis
- **Chart**: Canvas intraday chart with volume shading
- **Order Book**: Bid/ask ladder with market depth
- **Technical**: Candlestick patterns (Doji, Hammer, Shooting Star, Marubozu), RSI, MACD, 50/200 DMA, Pivot Points, Fibonacci
- **Fundamental**: 100-point score, P/E vs sector, ROE/ROCE, revenue/profit growth, Debt/Equity, Promoter holding
- **Sentiment**: Bull/Neutral/Bear breakdown, news sentiment, analyst consensus

### 🔌 Multi-LLM Support
Configure and switch between:
- **DeepSeek** (deepseek-chat, deepseek-reasoner)
- **OpenAI** (GPT-4o, GPT-4o-mini, o1, o3-mini)
- **Anthropic Claude** (Sonnet, Opus, Haiku)
- **Google Gemini** (2.5 Pro, 2.5 Flash)
- **Qwen/Alibaba** (qwen-max, qwen-plus)

### 💰 Broker Integration
- **Zerodha Kite** — API key + secret
- **Angel Broking** — API key + client credentials
- Test connection button
- Order placement with confirmation

### 🤖 Autonomous Trading Agents
Three risk profiles:
| Agent | Risk | Max Position | Stop Loss | Min Conviction |
|---|---|---|---|---|
| **Careful** | Low | 5% | 3% | 80% |
| **Moderate** | Medium | 15% | 5% | 60% |
| **Risky** | High | 30% | 8% | 40% |

- Autonomous scan mode — agent runs periodic analysis and generates trade signals
- Configurable from Settings panel

## Quick Start

```bash
# Clone
git clone https://github.com/manishapadhi1/THermes.git
cd THermes

# Install
pip install -r requirements.txt

# Run
python run.py
# Or
uvicorn backend.server:app --host 127.0.0.1 --port 8788

# Open
open http://127.0.0.1:8788
```

## Configure LLM

1. Click ⚙ **Settings** in the top bar
2. Go to **LLM Models** tab
3. Enter API keys for your providers
4. Select default model and chat model from the top bar dropdown

## Configure Broker

1. Settings → **Broker APIs**
2. Enter Zerodha/Angel Broking credentials
3. Click **Test Connection**

## Project Structure

```
THermes/
├── backend/
│   └── server.py          # FastAPI server (config, LLMs, MCP, skills, agents, trading)
├── frontend/
│   └── index.html          # Full trading UI (Tegel design, mobile responsive)
├── config/
│   ├── default.json        # Default configuration
│   └── skills/             # Skill definitions
├── run.py                  # Entry point
├── requirements.txt
└── README.md
```

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/health` | Health check |
| `GET /api/config` | Full configuration |
| `GET /api/llm/providers` | LLM provider list |
| `PUT /api/llm/providers/{id}` | Update provider API key |
| `PUT /api/llm/model` | Set default/chat model |
| `GET/POST/DELETE /api/mcp/servers` | MCP management |
| `GET/PUT /api/skills/{id}` | Skills management |
| `GET/PUT/POST /api/brokers/{name}` | Broker config |
| `GET /api/agents` | Agent configuration |
| `PUT /api/agents/activate` | Activate risk profile |
| `PUT /api/agents/autonomous` | Toggle autonomous trading |
| `POST /api/agents/run` | Run autonomous scan cycle |
| `GET /api/market/watchlist` | Watchlist data |
| `GET /api/market/intraday/{symbol}` | Intraday candles |
| `GET /api/analysis/watchlist` | Full analysis with scores |
| `GET /api/analysis/stock/{symbol}` | Single stock analysis |
| `POST /api/trade/order` | Place order |
| `GET /api/trade/portfolio` | Portfolio holdings |
| `POST /api/chat` | Chat with LLM |

## License

MIT
