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
git clone https://github.com/philip-bankiers-helper/riskradar.git
cd riskradar
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
```

### 2. Configure

1. Copy `.env.example` to `.env` and set only the environment values you use.
2. Edit `config/positions.yaml`; its committed holdings are clearly marked SAMPLE.

Never put API keys, bot tokens, or Telegram routing values in `settings.yaml`.

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

`python scripts/run_backtest.py` generated every calibration and benchmark number
below from 1,869 trading days (2019-04-01 through 2026-09-04). It computes the
same rolling factor regressions as the live path: factor HHI is nonzero on
1,869/1,869 rows, versus 0/1,749 in the invalid prior artifact. Thresholds are
the 50th, 75th, 90th, and 97th percentiles of the regenerated score distribution.

| Level | Range | % of Days | Action |
|-------|-------|-----------|--------|
| 🟢 Cool | < 0.4636 | 50.0% | Normal trading, full position sizes |
| 🟡 Warm | 0.4636–0.6732 | 25.0% | Alert; reduce new position sizes by 25% |
| 🟠 Hot | 0.6732–0.8029 | 15.0% | Reduce exposure 25%; no new correlated positions |
| 🔴 Critical | 0.8029–0.8781 | 7.0% | Reduce exposure 50%; hedge factor concentrations |
| ⛔ Emergency | ≥ 0.8781 | 3.0% | Halt new entries; actively reduce largest exposures |

### Historical Validation

The hot threshold detected 2 of 4 named crises. Warm-level timing and hot detection:

| Event | First warm vs peak | Peak heat | Hot detected? |
|-------|--------------------|-----------|---------------|
| COVID Crash | 27 days | 0.9775 | Yes |
| 2022 Bear Market | 163 days | 0.9757 | Yes |
| Aug 2024 Vol Spike | 5 days | 0.5845 | **No** |
| Jan 2026 Quant Blowup | 11 days | 0.5928 | **No** |

### Signal Benchmarking

| Signal | F1 | Precision | Recall | Lead days | False-positive rate | Sharpe |
|--------|----|-----------|--------|-----------|---------------------|--------|
| RiskRadar Heat | 0.1576 | 9.09% | **59.03%** | **12.0** | 49.28% | 1.3272 |
| VIX > 25 | 0.1755 | 12.43% | 29.86% | 10.1 | **17.57%** | 1.7993 |
| 50-day MA Cross | **0.2123** | **13.52%** | 49.31% | 11.0 | 26.32% | **2.1642** |

RiskRadar wins recall and lead time, but loses F1, precision, false-positive rate,
and Sharpe to the 50-day MA; it also loses F1, precision, false-positive rate,
and Sharpe to VIX. Treat it as an unproven ensemble input, not a superior standalone signal.

## Tests

```bash
# Blocking tests (219 pass; network and holdout tests excluded by default)
pytest -q

# Battle tests (49 stress scenarios)
python scripts/battle_test.py

# Reproduce the empirical README tables
python scripts/run_backtest.py
python scripts/render_readme_metrics.py
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
