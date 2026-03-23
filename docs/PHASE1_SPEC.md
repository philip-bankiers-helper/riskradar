# RISK RADAR — Phase 1: MVP Core
## Task Specification

**Task ID:** RISKRADAR-PHASE1
**Priority:** High
**Timeline:** 2-3 weeks
**Owner:** Developer Agent

---

## Overview

Build the MVP core of Risk Radar — a portfolio-level latent factor risk engine that detects hidden correlation clustering across positions before risk explodes.

**Design principle:** "Signals generate alpha. Clustering control protects survival."

**Full research report:** `~/TinkerLab/riskradar/RESEARCH_REPORT_v2.md`

---

## Deliverables

### 1. Data Ingestion Layer

**1a. Market Data (Polygon.io WebSocket)**
- Real-time price streaming for portfolio positions + factor ETFs
- Use `polygon-api-client>=1.14.0`
- WebSocket connection with auto-reconnect
- Buffer ticks in Redis, batch write to DuckDB every 5 seconds
- Support for configurable watchlist of symbols

**1b. Factor ETF Prices (Real-time proxies)**
Default factor ETFs to track:

| Factor | ETF | Purpose |
|--------|-----|---------|
| Rates | TLT | Duration / rate sensitivity |
| Credit | HYG | HY spread / deleveraging |
| Dollar | UUP | DXY / FX translation |
| AI/Tech | SMH | Semiconductor / AI beta |
| Quality | QUAL | Quality factor crowding |
| Momentum | MTUM | Momentum crowding |
| Market | SPY | Beta baseline |
| Low Vol | USMV | Defensive positioning |
| Value | VTV | Value factor |

**1c. Macro Data (FRED API)**
- Daily pull of key series: DGS10, DGS2, BAMLH0A0HYM2, SOFR, DTWEXBGS, VIXCLS, T10YIE
- Use `fredapi` library
- Store in DuckDB with timestamps
- Calculate derived: 2s10s spread (DGS10 - DGS2), HY-IG spread

**1d. Storage**
- DuckDB 1.5.0 for persistent storage (OHLCV, factor data, heat score history)
- Redis for real-time cache and pub/sub
- Parquet files for historical data archival

### 2. Factor Exposure Engine

**2a. Rolling Factor Regression**
- For each portfolio position, run rolling OLS regression against factor ETF returns:
  `R_position = α + β₁·R_TLT + β₂·R_HYG + β₃·R_UUP + β₄·R_SMH + β₅·R_SPY + ε`
- Rolling window: 60 trading days (configurable)
- Update daily (or intraday if data permits)
- Output: Factor loading matrix (N_positions × N_factors)
- Use `statsmodels` OLS or `sklearn.linear_model.LinearRegression`

**2b. Factor Exposure Summary**
- For each factor: total portfolio exposure = Σ(position_weight × factor_beta)
- Factor concentration HHI = Σ(factor_exposure_share²)
- Identify dominant factor(s)

### 3. Correlation Heatmap Engine

**3a. Rolling Correlation Matrix**
- Calculate pairwise correlation matrix of portfolio positions
- Use 60-day EWMA (exponentially weighted): `pandas.DataFrame.ewm(span=60).corr()`
- Update daily
- Store correlation matrix history in DuckDB

**3b. Correlation Metrics**
- Average pairwise correlation (excluding diagonal)
- Max pairwise correlation
- Eigenvalue concentration (top eigenvalue / sum of all eigenvalues)

### 4. Heat Score Calculator

**4a. Component Metrics**

1. **Absorption Ratio (AR)**
   - PCA on rolling 252-day returns
   - AR = variance explained by top N/5 components / total variance
   - Use `sklearn.decomposition.PCA`

2. **Average Pairwise Correlation**
   - From correlation engine above

3. **Factor HHI**
   - From factor exposure engine above

4. **Turbulence Index**
   - Mahalanobis distance of today's return vector from historical mean
   - `turb = (r - μ)ᵀ Σ⁻¹ (r - μ)`

5. **Diversification Ratio (inverse)**
   - DR = (weighted avg individual vol) / (portfolio vol)
   - Heat component = 1/DR (lower DR = more heat)

**4b. Composite Heat Score**
```python
heat = (
    0.25 * percentile_rank(AR) +
    0.20 * percentile_rank(turbulence) +
    0.20 * percentile_rank(1/DR) +
    0.15 * percentile_rank(factor_HHI) +
    0.20 * percentile_rank(avg_correlation)
)
```
- Normalize each component to [0, 1] via rolling 252-day percentile rank
- Overall heat score: 0 = cool, 1 = maximum danger

**4c. Threshold Levels**
- 🟢 Cool: 0.0 - 0.4
- 🟡 Warm: 0.4 - 0.6
- 🟠 Hot: 0.6 - 0.7
- 🔴 Critical: 0.7 - 0.85
- ⛔ Emergency: 0.85+

### 5. Alert System

**5a. Telegram Alerts**
- Send alerts when heat score crosses threshold boundaries
- Include: current heat score, dominant factor(s), top correlated pair(s)
- Alert format:
  ```
  🔴 RISK RADAR ALERT
  Heat Score: 0.73 (CRITICAL)
  Dominant Factor: Rates (TLT beta = 0.82)
  Top Correlation: NVDA↔AMD (0.91)
  Action: Reduce exposure by 50%
  ```
- Use OpenClaw's message tool or direct Telegram bot API
- Alert on: threshold crossings, significant heat changes (>0.1 in 1 day), cluster migrations

### 6. API Layer

**6a. FastAPI Backend**
- `GET /health` — system health check
- `GET /heat` — current heat score + all components
- `GET /factors` — current factor exposures for all positions
- `GET /correlation` — current correlation matrix
- `GET /positions` — portfolio positions and weights
- `POST /positions` — add/update positions
- `DELETE /positions/{symbol}` — remove position
- `GET /history/heat` — heat score history (with date range params)
- WebSocket `/ws/heat` — real-time heat score streaming

**6b. Configuration**
- `config/settings.yaml`:
  - Polygon API key
  - FRED API key
  - Portfolio positions + weights
  - Factor ETFs
  - Alert thresholds
  - Rolling window parameters
  - Telegram bot token + chat ID

---

## Technical Stack

| Component | Library | Version |
|-----------|---------|---------|
| Language | Python | 3.12+ |
| Web | FastAPI | ≥0.128.0 |
| ASGI | uvicorn | latest |
| DB | DuckDB | 1.5.0 |
| Cache | redis-py | ≥5.0.0 |
| Data Feed | polygon-api-client | ≥1.14.0 |
| Stats | statsmodels | latest |
| ML | scikit-learn | ≥1.4 |
| Numerics | numpy, pandas | latest |
| Macro Data | fredapi | ≥0.5 |
| Config | pyyaml + pydantic | latest |
| Testing | pytest + pytest-asyncio | latest |

---

## Project Structure

```
riskradar/
├── src/
│   ├── __init__.py
│   ├── main.py              # FastAPI app entry point
│   ├── config.py             # Settings/configuration
│   ├── models.py             # Pydantic models
│   ├── data/
│   │   ├── __init__.py
│   │   ├── polygon_feed.py   # Polygon WebSocket client
│   │   ├── fred_client.py    # FRED API client
│   │   ├── storage.py        # DuckDB operations
│   │   └── cache.py          # Redis operations
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── factor_model.py   # Factor regression engine
│   │   ├── correlation.py    # Correlation matrix engine
│   │   ├── heat_score.py     # Heat score calculator
│   │   └── metrics.py        # AR, turbulence, DR, HHI
│   ├── alerts/
│   │   ├── __init__.py
│   │   └── telegram.py       # Telegram alert sender
│   └── api/
│       ├── __init__.py
│       └── routes.py         # API route definitions
├── tests/
│   ├── test_factor_model.py
│   ├── test_correlation.py
│   ├── test_heat_score.py
│   └── test_api.py
├── config/
│   └── settings.yaml
├── data/                     # DuckDB files, Parquet archives
├── docs/
│   └── PHASE1_SPEC.md
├── requirements.txt
├── pyproject.toml
└── README.md
```

---

## Success Criteria

1. ✅ System ingests real-time prices for portfolio + factor ETFs via Polygon WebSocket
2. ✅ Factor regression produces accurate beta loadings for each position
3. ✅ Correlation heatmap updates daily with EWMA
4. ✅ Heat score calculated correctly from 5 components
5. ✅ Telegram alerts fire when thresholds are crossed
6. ✅ FastAPI serves all endpoints with <100ms response time
7. ✅ System runs stably on the development machine (RTX 3060, 94GB RAM)
8. ✅ Tests pass for all core engines
9. ✅ Can add/remove positions dynamically via API

---

## Testing Approach

**Unit tests for each engine:**
- Factor model: test with known synthetic data (verify betas match)
- Correlation: test with known correlation structures
- Heat score: test each component independently, then composite
- Metrics: test AR, turbulence, DR calculations against known values

**Integration test:**
- Load historical data (SPY, NVDA, AAPL, TLT, HYG) for 2024-2025
- Run full pipeline
- Verify heat score rises during known stress periods (Summer 2025 quant wobble)

**For initial testing without Polygon API key:**
- Use `yfinance` as fallback data source for historical data
- Implement a `MockFeed` class that replays historical data

---

## Constraints

- Must run locally on Linux x86_64 with RTX 3060 / 94GB RAM
- No cloud dependencies for core functionality
- Polygon.io Developer plan ($79/month) for real-time data
- FRED API key (free) for macro data
- Redis can run locally (no cloud instance needed)
- All data stays on-machine — no external data sharing

---

## Notes for Developer

- Research report at `~/TinkerLab/riskradar/RESEARCH_REPORT_v2.md` has full context
- Start with `yfinance` for historical data to get engines working, then add Polygon WebSocket
- The causal DAG (López de Prado framework) is Phase 5 — for now, standard OLS factor regression is fine
- DCC-GARCH is Phase 2 — for now, EWMA rolling correlation is sufficient
- Focus on correctness first, optimization second
- Use async throughout (asyncio + FastAPI) for WebSocket handling
