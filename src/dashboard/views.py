"""Dashboard routes — serves the Plotly/HTMX dashboard."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.dashboard.charts import (
    correlation_heatmap_config,
    factor_bars_config,
    heat_gauge_config,
    heat_history_config,
    hrp_comparison_config,
    mst_network_config,
)

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

dashboard_router = APIRouter(tags=["dashboard"])

# Injected from main.py
_app_state: dict = {}


def set_dashboard_state(state: dict) -> None:
    """Inject application state."""
    global _app_state
    _app_state = state


@dashboard_router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Serve the main dashboard page."""
    heat = _app_state.get("last_heat_score")
    regime = _app_state.get("regime_state", {})
    throttle = _app_state.get("throttle_state", {})
    cluster_state = _app_state.get("cluster_state")
    positions = _app_state.get("positions", [])

    # Build context
    ctx = {
        "request": request,
        "heat_score": heat.score if heat else 0,
        "heat_level": heat.level.value if heat else "cool",
        "heat_action": heat.action if heat else "Waiting for data...",
        "dominant_factor": heat.dominant_factor if heat else "—",
        "top_pair": heat.top_correlated_pair if heat else "—",
        "regime": regime.get("regime", "unknown") if isinstance(regime, dict) else "unknown",
        "regime_confidence": regime.get("confidence", 0) if isinstance(regime, dict) else 0,
        "transition_risk": regime.get("transition_risk", 0) if isinstance(regime, dict) else 0,
        "throttle": throttle if isinstance(throttle, dict) else {},
        "avg_correlation": _app_state.get("avg_correlation", 0),
        "dcc_stress": _app_state.get("dcc_stress", 0.5),
        "n_positions": len(positions),
        "data_source": (_app_state.get("data_quality") or {}).get("primary_source", "initializing"),
    }
    return templates.TemplateResponse("dashboard.html", ctx)


# --- HTMX Partial Endpoints ---


@dashboard_router.get("/dashboard/partial/heat-gauge", response_class=HTMLResponse)
async def partial_heat_gauge(request: Request):
    """HTMX partial: heat score gauge."""
    heat = _app_state.get("last_heat_score")
    score = heat.score if heat else 0
    level = heat.level.value if heat else "cool"
    action = heat.action if heat else "Waiting..."
    dominant = heat.dominant_factor if heat else "—"
    top_pair = heat.top_correlated_pair if heat else "—"

    chart_json = heat_gauge_config(score, level)
    return templates.TemplateResponse("partials/heat_gauge.html", {
        "request": request,
        "chart_json": chart_json,
        "score": score,
        "level": level,
        "action": action,
        "dominant_factor": dominant,
        "top_pair": top_pair,
    })


@dashboard_router.get("/dashboard/partial/heat-history", response_class=HTMLResponse)
async def partial_heat_history(request: Request):
    """HTMX partial: heat score time series."""
    storage = _app_state.get("storage")
    history = []
    if storage:
        df = storage.get_heat_history(limit=200)
        history = df.to_dict(orient="records") if not df.empty else []

    chart_json = heat_history_config(history)
    return templates.TemplateResponse("partials/heat_history.html", {
        "request": request,
        "chart_json": chart_json,
        "n_points": len(history),
    })


@dashboard_router.get("/dashboard/partial/correlation", response_class=HTMLResponse)
async def partial_correlation(request: Request):
    """HTMX partial: correlation heatmap."""
    corr = _app_state.get("correlation_matrix")
    matrix = corr.to_dict() if corr is not None else {}
    avg = _app_state.get("avg_correlation", 0)
    top_pair = _app_state.get("top_correlated_pair", {})

    chart_json = correlation_heatmap_config(matrix)
    return templates.TemplateResponse("partials/correlation_heatmap.html", {
        "request": request,
        "chart_json": chart_json,
        "avg_correlation": avg,
        "top_pair": top_pair.get("pair", "—"),
        "top_correlation": top_pair.get("correlation", 0),
    })


@dashboard_router.get("/dashboard/partial/mst", response_class=HTMLResponse)
async def partial_mst(request: Request):
    """HTMX partial: MST network graph."""
    cluster_state = _app_state.get("cluster_state")
    positions = _app_state.get("positions", [])

    edges = []
    communities = []
    if cluster_state:
        edges = [e.model_dump() for e in cluster_state.mst_edges]
        communities = [c.model_dump() for c in cluster_state.communities]

    weights = {p.symbol: p.weight for p in positions}
    chart_json = mst_network_config(edges, communities, weights)

    return templates.TemplateResponse("partials/mst_graph.html", {
        "request": request,
        "chart_json": chart_json,
        "n_edges": len(edges),
        "n_communities": len(communities),
    })


@dashboard_router.get("/dashboard/partial/clusters", response_class=HTMLResponse)
async def partial_clusters(request: Request):
    """HTMX partial: cluster info + HRP comparison."""
    cluster_state = _app_state.get("cluster_state")
    positions = _app_state.get("positions", [])

    communities = []
    hrp_weights = {}
    migrations = []
    hrp_concentration = 0
    modularity = 0

    if cluster_state:
        communities = [c.model_dump() for c in cluster_state.communities]
        hrp_weights = cluster_state.hrp_weights
        migrations = [m.model_dump() for m in cluster_state.migrations]
        hrp_concentration = cluster_state.hrp_concentration
        modularity = cluster_state.modularity

    actual_weights = {p.symbol: p.weight for p in positions}
    chart_json = hrp_comparison_config(actual_weights, hrp_weights)

    return templates.TemplateResponse("partials/clusters.html", {
        "request": request,
        "chart_json": chart_json,
        "communities": communities,
        "migrations": migrations,
        "hrp_concentration": hrp_concentration,
        "modularity": modularity,
    })


@dashboard_router.get("/dashboard/partial/regime", response_class=HTMLResponse)
async def partial_regime(request: Request):
    """HTMX partial: regime & throttle panel."""
    regime = _app_state.get("regime_state", {})
    throttle = _app_state.get("throttle_state", {})
    dcc_stress = _app_state.get("dcc_stress", 0.5)

    if not isinstance(regime, dict):
        regime = {}
    if not isinstance(throttle, dict):
        throttle = {}

    return templates.TemplateResponse("partials/regime_panel.html", {
        "request": request,
        "regime": regime.get("regime", "unknown"),
        "confidence": regime.get("confidence", 0),
        "transition_risk": regime.get("transition_risk", 0),
        "probabilities": regime.get("probabilities", {}),
        "max_exposure": throttle.get("max_exposure", 1.0),
        "current_exposure": throttle.get("current_exposure", 1.0),
        "target_reduction": throttle.get("target_reduction", 0),
        "new_position_allowed": throttle.get("new_position_allowed", True),
        "heat_score": throttle.get("heat_score", 0),
        "dcc_stress": dcc_stress,
    })


@dashboard_router.get("/dashboard/partial/factors", response_class=HTMLResponse)
async def partial_factors(request: Request):
    """HTMX partial: factor exposure bars."""
    exposures = _app_state.get("factor_exposures", [])
    exposure_dicts = [e.model_dump() for e in exposures] if exposures else []

    chart_json = factor_bars_config(exposure_dicts)
    return templates.TemplateResponse("partials/factors.html", {
        "request": request,
        "chart_json": chart_json,
        "n_factors": len(exposure_dicts),
    })


@dashboard_router.get("/dashboard/partial/attribution", response_class=HTMLResponse)
async def partial_attribution(request: Request):
    """HTMX partial: position attribution & recommendations."""
    attribution = _app_state.get("attribution", [])
    recommendations = _app_state.get("recommendations")

    attribution_dicts = [a.model_dump() for a in attribution] if attribution else []
    actions = []
    hedges = []
    summary = "Waiting for data..."

    if recommendations:
        actions = [a.model_dump() for a in recommendations.actions]
        hedges = [h.model_dump() for h in recommendations.hedges]
        summary = recommendations.summary

    return templates.TemplateResponse("partials/attribution.html", {
        "request": request,
        "attribution": attribution_dicts,
        "actions": actions,
        "hedges": hedges,
        "summary": summary,
    })


@dashboard_router.get("/dashboard/partial/simulate", response_class=HTMLResponse)
async def partial_simulate_result(
    request: Request,
    candidate_symbol: str = "",
    candidate_weight: float = 0.05,
):
    """HTMX partial: pre-trade simulation result."""
    if not candidate_symbol:
        return templates.TemplateResponse("partials/simulate_result.html", {
            "request": request,
            "result": None,
            "error": None,
        })

    simulator = _app_state.get("pre_trade_simulator")
    if simulator is None:
        return templates.TemplateResponse("partials/simulate_result.html", {
            "request": request,
            "result": None,
            "error": "Simulator not initialized yet.",
        })

    positions = _app_state.get("positions", [])
    returns = _app_state.get("all_returns")
    factor_returns = _app_state.get("factor_returns")
    current_heat = _app_state.get("last_heat_score")

    if returns is None:
        return templates.TemplateResponse("partials/simulate_result.html", {
            "request": request,
            "result": None,
            "error": "Market data not loaded yet.",
        })

    try:
        # Fetch candidate if needed
        import pandas as pd
        if candidate_symbol.upper() not in returns.columns:
            from src.data.market_data import MarketDataClient
            mdc = MarketDataClient()
            new_returns = mdc.get_returns([candidate_symbol.upper()], period_days=252)
            if not new_returns.empty:
                common_idx = returns.index.intersection(new_returns.index)
                merged = returns.loc[common_idx].join(
                    new_returns.loc[common_idx], how="inner", rsuffix="_new"
                )
            else:
                merged = returns
        else:
            merged = returns

        result = simulator.simulate_impact(
            candidate_symbol=candidate_symbol.upper(),
            candidate_weight=candidate_weight,
            current_positions=positions,
            returns=merged,
            factor_returns=factor_returns,
            current_heat=current_heat,
        )

        # Sanitize numpy types
        import numpy as np

        def _sanitize(obj):
            if isinstance(obj, dict):
                return {k: _sanitize(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [_sanitize(v) for v in obj]
            elif isinstance(obj, (np.integer,)):
                return int(obj)
            elif isinstance(obj, (np.floating,)):
                return float(obj)
            elif isinstance(obj, (np.bool_,)):
                return bool(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            return obj

        result = _sanitize(result)

        return templates.TemplateResponse("partials/simulate_result.html", {
            "request": request,
            "result": result,
            "error": None,
        })
    except Exception as e:
        return templates.TemplateResponse("partials/simulate_result.html", {
            "request": request,
            "result": None,
            "error": str(e),
        })


@dashboard_router.get("/dashboard/partial/benchmark", response_class=HTMLResponse)
async def partial_benchmark(request: Request):
    """HTMX partial: signal benchmark comparison table."""
    report = _app_state.get("benchmark_report")
    results = []
    best_signal = "N/A"
    summary = "Benchmark not yet computed..."

    if report:
        results = [r.model_dump() for r in report.results]
        best_signal = report.best_signal
        summary = report.heat_vs_vix_summary

    return templates.TemplateResponse("partials/benchmark.html", {
        "request": request,
        "results": results,
        "best_signal": best_signal,
        "summary": summary,
    })


@dashboard_router.get("/dashboard/partial/alerts", response_class=HTMLResponse)
async def partial_alerts(request: Request):
    """HTMX partial: recent alert history."""
    alert_manager = _app_state.get("alert_manager")
    alerts = []
    cooldowns = {}

    if alert_manager:
        alerts = alert_manager.get_alert_history()[-10:]  # Last 10
        cooldowns = alert_manager.get_cooldown_status()

    return templates.TemplateResponse("partials/alerts.html", {
        "request": request,
        "alerts": alerts,
        "cooldowns": cooldowns,
    })
