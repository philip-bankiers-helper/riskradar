"""FastAPI route definitions for Risk Radar."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException

from src.models import (
    ClusterMigration,
    ClusterState,
    HeatScore,
    MSTEdge,
    Position,
    PortfolioState,
    PreTradeResult,
    RegimeState,
    ThrottleState,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# These will be injected by main.py on startup
_app_state: dict = {}


def set_app_state(state: dict) -> None:
    """Inject application state (engines, storage, etc.)."""
    global _app_state
    _app_state = state


@router.get("/health")
async def health_check():
    """System health check."""
    data_quality = _app_state.get("data_quality")
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "0.2.0",
        "data_source": data_quality.get("primary_source") if data_quality else "initializing",
    }


@router.get("/heat", response_model=None)
async def get_heat_score():
    """Get current heat score and all components."""
    heat = _app_state.get("last_heat_score")
    if heat is None:
        raise HTTPException(status_code=503, detail="Heat score not yet computed. Waiting for data.")
    return heat.model_dump()


@router.get("/factors")
async def get_factor_exposures():
    """Get current factor exposures for all positions."""
    exposures = _app_state.get("factor_exposures", [])
    return {"factor_exposures": [e.model_dump() for e in exposures]}


@router.get("/correlation")
async def get_correlation_matrix():
    """Get current correlation matrix."""
    corr = _app_state.get("correlation_matrix")
    if corr is None:
        raise HTTPException(status_code=503, detail="Correlation not yet computed.")

    return {
        "matrix": corr.to_dict(),
        "avg_correlation": _app_state.get("avg_correlation", 0.0),
        "top_pair": _app_state.get("top_correlated_pair", {}),
    }


@router.get("/positions")
async def get_positions():
    """Get current portfolio positions."""
    positions = _app_state.get("positions", [])
    return {"positions": [p.model_dump() if hasattr(p, "model_dump") else p for p in positions]}


@router.post("/positions")
async def update_positions(positions: list[Position]):
    """Add or update portfolio positions."""
    storage = _app_state.get("storage")
    if storage:
        storage.save_positions([p.model_dump() for p in positions])

    _app_state["positions"] = positions
    logger.info("Updated %d positions", len(positions))
    return {"status": "ok", "count": len(positions)}


@router.delete("/positions/{symbol}")
async def delete_position(symbol: str):
    """Remove a position from the portfolio."""
    storage = _app_state.get("storage")
    if storage:
        storage.delete_position(symbol.upper())

    positions = _app_state.get("positions", [])
    _app_state["positions"] = [p for p in positions if p.symbol != symbol.upper()]
    return {"status": "ok", "removed": symbol.upper()}


@router.get("/history/heat")
async def get_heat_history(
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
):
    """Get heat score history."""
    storage = _app_state.get("storage")
    if not storage:
        raise HTTPException(status_code=503, detail="Storage not initialized.")

    start_dt = datetime.fromisoformat(start) if start else None
    end_dt = datetime.fromisoformat(end) if end else None

    df = storage.get_heat_history(start=start_dt, end=end_dt, limit=limit)
    return {"history": df.to_dict(orient="records")}


@router.get("/clusters")
async def get_clusters():
    """Get current cluster/community state."""
    cluster_state = _app_state.get("cluster_state")
    if cluster_state is None:
        raise HTTPException(status_code=503, detail="Clustering not yet computed.")
    return cluster_state.model_dump()


@router.get("/clusters/migrations")
async def get_cluster_migrations():
    """Get recent cluster migration events."""
    cluster_state = _app_state.get("cluster_state")
    if cluster_state is None:
        raise HTTPException(status_code=503, detail="Clustering not yet computed.")
    return {
        "migrations": [m.model_dump() for m in cluster_state.migrations],
        "migration_risk": cluster_state.migration_risk,
    }


@router.get("/clusters/mst")
async def get_mst():
    """Get Minimum Spanning Tree of correlation network."""
    cluster_state = _app_state.get("cluster_state")
    if cluster_state is None:
        raise HTTPException(status_code=503, detail="Clustering not yet computed.")
    return {"edges": [e.model_dump() for e in cluster_state.mst_edges]}


@router.get("/clusters/hrp")
async def get_hrp_weights():
    """Get HRP optimal weights (diversification benchmark)."""
    cluster_state = _app_state.get("cluster_state")
    if cluster_state is None:
        raise HTTPException(status_code=503, detail="Clustering not yet computed.")
    return {
        "weights": cluster_state.hrp_weights,
        "concentration_hhi": cluster_state.hrp_concentration,
    }


@router.get("/regime")
async def get_regime():
    """Get current market regime detection state."""
    regime = _app_state.get("regime_state")
    if regime is None:
        return {"regime": "unknown", "is_fitted": False}
    return regime


@router.get("/throttle")
async def get_throttle():
    """Get current risk throttle status."""
    throttle = _app_state.get("throttle_state")
    if throttle is None:
        raise HTTPException(status_code=503, detail="Throttle not yet computed.")
    return throttle


@router.post("/simulate")
async def simulate_pre_trade(candidate_symbol: str, candidate_weight: float = 0.05):
    """
    Simulate adding a position and see impact on Heat Score.

    Query params:
        candidate_symbol: Ticker symbol to simulate adding.
        candidate_weight: Portfolio weight for the new position (default 5%).
    """
    simulator = _app_state.get("pre_trade_simulator")
    if simulator is None:
        raise HTTPException(status_code=503, detail="Simulator not initialized.")

    positions = _app_state.get("positions", [])
    returns = _app_state.get("all_returns")
    factor_returns = _app_state.get("factor_returns")

    if returns is None or factor_returns is None:
        raise HTTPException(status_code=503, detail="Market data not yet loaded.")

    current_heat = _app_state.get("last_heat_score")

    # Fetch candidate data if not already in returns
    if candidate_symbol.upper() not in returns.columns:
        try:
            from src.data.market_data import MarketDataClient
            mdc = MarketDataClient()
            new_returns = mdc.get_returns([candidate_symbol.upper()], period_days=252)
            if not new_returns.empty:
                # Merge with existing returns
                common_idx = returns.index.intersection(new_returns.index)
                returns = returns.loc[common_idx].join(
                    new_returns.loc[common_idx], how="inner", rsuffix="_new"
                )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not fetch data for {candidate_symbol}: {e}")

    result = simulator.simulate_impact(
        candidate_symbol=candidate_symbol.upper(),
        candidate_weight=candidate_weight,
        current_positions=positions,
        returns=returns,
        factor_returns=factor_returns,
        current_heat=current_heat,
    )

    return _sanitize_numpy(result)


def _sanitize_numpy(obj):
    """Recursively convert numpy types to Python native for JSON serialization."""
    import numpy as np
    if isinstance(obj, dict):
        return {k: _sanitize_numpy(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_numpy(v) for v in obj]
    elif isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating,)):
        return float(obj)
    elif isinstance(obj, (np.bool_,)):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


@router.get("/kelly")
async def get_kelly_sizing(
    expected_return: float = 0.001,
    variance: float = 0.0004,
):
    """
    Compute heat-adjusted Kelly sizing for a hypothetical trade.

    Query params:
        expected_return: Expected daily return.
        variance: Variance of daily returns.
    """
    throttle = _app_state.get("risk_throttle")
    if throttle is None:
        raise HTTPException(status_code=503, detail="Throttle not initialized.")

    heat = _app_state.get("last_heat_score")
    heat_score = heat.score if heat else 0.5
    regime = _app_state.get("regime_state", {})
    regime_label = regime.get("regime", "normal") if isinstance(regime, dict) else "normal"

    result = throttle.adjusted_kelly(
        expected_return=expected_return,
        variance=variance,
        heat_score=heat_score,
        regime=regime_label,
    )
    return result


@router.get("/dcc")
async def get_dcc_state():
    """Get DCC-GARCH conditional correlation state."""
    dcc_corr = _app_state.get("dcc_correlation")
    dcc_vols = _app_state.get("dcc_volatility")
    stress = _app_state.get("dcc_stress", 0.5)

    if dcc_corr is None:
        raise HTTPException(status_code=503, detail="DCC not yet computed.")

    return {
        "conditional_correlation": dcc_corr.to_dict() if dcc_corr is not None else {},
        "stress_indicator": stress,
    }


@router.get("/causal")
async def get_causal_dag():
    """Get current causal DAG state and factor effects."""
    causal_engine = _app_state.get("causal_engine")
    if causal_engine is None:
        raise HTTPException(status_code=503, detail="Causal engine not initialized.")
    return causal_engine.to_dict()


@router.post("/causal/intervene")
async def causal_intervention(factor: str, shock_std: float = 1.0):
    """Simulate a causal intervention: 'What if factor moves by N std devs?'

    Query params:
        factor: Factor name (rates, credit, dollar, market, etc.)
        shock_std: Shock size in standard deviations.
    """
    causal_engine = _app_state.get("causal_engine")
    returns = _app_state.get("all_returns")
    if causal_engine is None:
        raise HTTPException(status_code=503, detail="Causal engine not initialized.")
    if returns is None:
        raise HTTPException(status_code=503, detail="No market data yet.")

    result = causal_engine.interventional_query(returns, factor, shock_std)
    return _sanitize_numpy(result)


@router.get("/crowding")
async def get_crowding():
    """Get current factor crowding state and alpha signal."""
    crowding_engine = _app_state.get("crowding_engine")
    if crowding_engine is None:
        raise HTTPException(status_code=503, detail="Crowding engine not initialized.")
    return crowding_engine.to_dict()


@router.get("/attribution")
async def get_attribution():
    """Get position-level heat attribution."""
    attribution = _app_state.get("attribution")
    if attribution is None:
        raise HTTPException(status_code=503, detail="Attribution not yet computed.")
    return {"attribution": [_sanitize_numpy(a.model_dump()) for a in attribution]}


@router.get("/recommendations")
async def get_recommendations():
    """Get actionable trade recommendations."""
    recommendations = _app_state.get("recommendations")
    if recommendations is None:
        raise HTTPException(status_code=503, detail="Recommendations not yet computed.")
    return _sanitize_numpy(recommendations.model_dump())


@router.get("/signal-quality")
async def get_signal_quality():
    """Get signal accuracy metrics from backtest results."""
    import json
    from pathlib import Path

    backtest_path = Path("data/backtest_results.json")
    if not backtest_path.exists():
        raise HTTPException(status_code=404, detail="No backtest results found.")

    with open(backtest_path) as f:
        data = json.load(f)

    # Extract signal quality metrics
    stats = data.get("heat_score_stats", {})
    crises = data.get("crisis_analysis", [])

    detected = sum(1 for c in crises if c.get("strong_signal", False))
    total = len(crises)

    # Forward returns by level
    fwd_returns = data.get("forward_returns_by_level", {})

    return {
        "detection_rate": detected / total if total > 0 else 0,
        "crises_detected": detected,
        "crises_total": total,
        "heat_score_stats": stats,
        "forward_returns_by_level": fwd_returns,
        "crisis_details": crises,
    }


@router.post("/backtest")
async def run_backtest():
    """Run historical backtest against known crises.

    This fetches historical data and replays the heat score computation.
    May take 30-60 seconds.
    """
    backtest_result = _app_state.get("backtest_result")
    if backtest_result is not None:
        # Return cached result
        from src.engine.backtest import BacktestEngine
        engine = BacktestEngine()
        return engine.to_dict(backtest_result)

    # Run fresh backtest
    import asyncio
    from src.engine.backtest import BacktestEngine

    engine = BacktestEngine()

    # Run in thread to avoid blocking
    def _run():
        engine.fetch_historical_data()
        return engine.run_full_backtest()

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _run)
    _app_state["backtest_result"] = result

    return engine.to_dict(result)


# WebSocket connections for live streaming
_ws_clients: set[WebSocket] = set()


@router.websocket("/ws/heat")
async def heat_websocket(websocket: WebSocket):
    """Real-time heat score streaming via WebSocket."""
    await websocket.accept()
    _ws_clients.add(websocket)
    logger.info("WebSocket client connected. Total: %d", len(_ws_clients))

    try:
        while True:
            # Keep connection alive, send heat updates
            await websocket.receive_text()  # Wait for client pings
    except WebSocketDisconnect:
        _ws_clients.discard(websocket)
        logger.info("WebSocket client disconnected. Total: %d", len(_ws_clients))


@router.get("/benchmark")
async def get_benchmark():
    """Get signal benchmark comparison results."""
    report = _app_state.get("benchmark_report")
    if report is None:
        raise HTTPException(status_code=503, detail="Benchmark not yet computed. It runs on startup — try again shortly.")
    return _sanitize_numpy(report.model_dump())


@router.get("/data-quality")
async def get_data_quality():
    """Get current data source and quality information."""
    quality = _app_state.get("data_quality")
    if quality is None:
        return {"primary_source": "initializing", "data_age_seconds": None}
    return quality


@router.get("/alerts/history")
async def get_alert_history():
    """Get recent alert history."""
    alert_manager = _app_state.get("alert_manager")
    if alert_manager is None:
        return {"alerts": [], "cooldowns": {}}
    return {
        "alerts": alert_manager.get_alert_history(),
        "cooldowns": alert_manager.get_cooldown_status(),
    }


async def broadcast_heat(heat: HeatScore) -> None:
    """Push heat score update to all connected WebSocket clients."""
    global _ws_clients
    if not _ws_clients:
        return

    message = json.dumps(heat.model_dump(), default=str)
    disconnected = set()

    for ws in _ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            disconnected.add(ws)

    _ws_clients -= disconnected
