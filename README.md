# 🌡️ RiskRadar

**Portfolio-Level Latent Factor Risk Engine**

RiskRadar detects hidden correlation clustering across portfolio positions *before* risk explodes. It monitors factor exposure concentration, computes a composite Heat Score, and provides actionable trade recommendations when portfolio risk becomes dangerous.

> "Signals generate alpha. Clustering control protects survival."

## Features

- **Composite Heat Score** — 5-component risk metric with percentile-ranked history
- **Position Attribution** — decomposes risk to per-position contributions
- **Trade Recommendations** — concrete position sizing, hedge suggestions, HRP rebalance targets
- **Regime Detection** — HMM-based market regime identification (normal/volatile/crisis)
- **DCC-GARCH** — dynamic conditional correlation modeling
- **Causal DAG** — Pearl's SCM framework for interventional queries ("what if rates move 2σ?")
- **Crowding Alpha** — factor crowding detection as a predictive signal
- **Live Dashboard** — Plotly.js + HTMX, dark trading terminal theme
- **Tiered Alerts** — Telegram notifications with cooldowns (daily summary → emergency)
- **Signal Benchmarking** — compare heat score against VIX, 50d MA, and other baselines
- **Multi-Source Data** — Polygon.io REST/WebSocket with yfinance fallback

## Quick Start

### 1. Install

```bash
git clone https://github.com/yourusername/riskradar.git
cd riskradar
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

Edit `config/settings.yaml`:
- Set your portfolio positions and weights
- (Optional) Add API keys for Polygon.io, FRED, Telegram

### 3. Run

```bash
python -m src.main
```

Dashboard at `http://localhost:8000/dashboard` | API at `http://localhost:8000`

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | System health check |
| `/heat` | GET | Current heat score + all components |
| `/attribution` | GET | Per-position heat attribution |
| `/recommendations` | GET | Actionable trade recommendations |
| `/factors` | GET | Factor exposures |
| `/correlation` | GET | Correlation matrix |
| `/clusters` | GET | Louvain community detection state |
| `/clusters/mst` | GET | Minimum Spanning Tree |
| `/clusters/hrp` | GET | Hierarchical Risk Parity weights |
| `/regime` | GET | Market regime detection |
| `/throttle` | GET | Risk throttle / Kelly sizing |
| `/causal` | GET | Causal DAG state |
| `/causal/intervene` | POST | Causal intervention queries |
| `/crowding` | GET | Factor crowding state + alpha signal |
| `/benchmark` | GET | Signal quality vs benchmarks |
| `/signal-quality` | GET | Historical signal accuracy |
| `/data-quality` | GET | Data source health |
| `/positions` | GET/POST | Portfolio positions |
| `/simulate` | POST | Pre-trade impact simulation |
| `/kelly` | GET | Heat-adjusted Kelly sizing |
| `/ws/heat` | WebSocket | Real-time heat streaming |
| `/dashboard` | GET | Live dashboard UI |

## Heat Score

Combines 5 components into a single [0, 1] risk metric:

| Component | Weight | Source |
|-----------|--------|--------|
| Absorption Ratio | 25% | PCA variance concentration (Kritzman et al. 2010) |
| Turbulence Index | 20% | Mahalanobis distance |
| Diversification Ratio | 20% | Portfolio concentration (Choueifaty 2008) |
| Factor HHI | 15% | Factor exposure concentration |
| Avg Correlation | 20% | Pairwise correlation level |

### Calibrated Thresholds

Thresholds are calibrated from 2019–2026 backtest data (1,749 trading days):

| Level | Range | % of Days | Action |
|-------|-------|-----------|--------|
| 🟢 Cool | < 0.55 | ~51% | Normal trading, full position sizes |
| 🟡 Warm | 0.55–0.75 | ~23% | Alert; reduce new position sizes by 25% |
| 🟠 Hot | 0.75–0.85 | ~15% | Reduce exposure 25%; no new correlated positions |
| 🔴 Critical | 0.85–0.93 | ~7% | Reduce exposure 50%; hedge factor concentrations |
| ⛔ Emergency | > 0.93 | ~3% | Halt new entries; actively reduce largest exposures |

### Historical Validation

Detected all 4 major crises in backtest:
- **COVID Crash** (Feb–Apr 2020): 33 days early warning, heat peaked at 0.997
- **2022 Bear Market**: 164 days early warning, heat 0.79 average during
- **Aug 2024 Vol Spike**: 5 days early warning
- **Jan 2026 Quant Blowup**: 11 days early warning

### Signal Benchmarking

| Signal | F1 | Recall | Sharpe | Advantage |
|--------|-----|--------|--------|-----------|
| RiskRadar Heat | 0.166 | **60.7%** | 1.35 | Best recall, earliest warnings |
| VIX > 25 | 0.178 | 31.1% | 1.75 | Better precision |
| 50d MA Cross | **0.225** | 51.8% | **2.10** | Best risk-adjusted return |

RiskRadar catches the most drawdowns (60.7% recall) with 11.9 days average lead time, but has higher false positive rate than simpler alternatives. Best used as an ensemble with VIX for confirmation.

## Tests

```bash
# Unit tests (212)
pytest tests/ -v

# Battle tests (49 stress scenarios)
python scripts/battle_test.py
```

## Tech Stack

- **FastAPI** — REST API + WebSocket
- **DuckDB** — Time-series storage
- **Plotly.js + HTMX** — Dashboard (no build step)
- **scikit-learn** — PCA, HMM regime detection
- **arch** — DCC-GARCH conditional correlation
- **DoWhy** — Causal DAG / interventional queries
- **riskfolio-lib** — HRP, clustering
- **Polygon.io** — Real-time market data (optional, falls back to yfinance)
- **Redis** — Cache (optional, degrades gracefully)

## Architecture

```
src/
├── engine/           # Core computation
│   ├── heat_score.py     # Composite risk metric
│   ├── attribution.py    # Position-level decomposition
│   ├── recommendations.py # Trade action generation
│   ├── factor_model.py   # Factor regression
│   ├── correlation.py    # EWMA correlation
│   ├── dcc_garch.py      # Dynamic conditional correlation
│   ├── clustering.py     # Louvain + HRP + MST
│   ├── regime.py         # HMM regime detection
│   ├── throttle.py       # Kelly sizing + exposure limits
│   ├── causal_dag.py     # Pearl's SCM / DoWhy
│   ├── crowding.py       # Factor crowding alpha
│   ├── benchmark.py      # Signal quality comparison
│   └── backtest.py       # Historical crisis replay
├── data/
│   ├── market_data.py    # Data pipeline manager
│   ├── polygon_client.py # Polygon.io REST/WebSocket
│   ├── fred_client.py    # FRED macro data
│   └── storage.py        # DuckDB persistence
├── alerts/
│   ├── alert_manager.py  # Multi-tier alerting
│   └── telegram.py       # Telegram integration
├── dashboard/            # Plotly/HTMX UI
├── api/routes.py         # FastAPI endpoints
├── config.py             # Settings management
├── models.py             # Pydantic models
└── main.py               # Application entry point
```

## Research

See `RESEARCH_REPORT_v2.md` for full academic grounding, including:
- López de Prado (2024) Causal Factor Investing
- Kritzman (2010) Absorption Ratio
- MSCI Summer 2025 Quant Wobble analysis
- Jan 2026 quant crowding losses

## License

MIT
