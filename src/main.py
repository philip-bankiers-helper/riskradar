"""Risk Radar — FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.api.routes import broadcast_heat, router, set_app_state
from src.dashboard.views import dashboard_router, set_dashboard_state
from src.alerts.alert_manager import AlertManager
from src.config import DEFAULT_FACTOR_ETFS, Settings
from src.data.cache import Cache
from src.data.market_data import DataPipelineManager, MarketDataClient
from src.data.storage import Storage
from src.engine.benchmark import SignalBenchmark
from src.engine.clustering import ClusteringEngine
from src.engine.correlation import CorrelationEngine
from src.engine.dcc_garch import DCCGarchEngine
from src.engine.factor_model import FactorModel
from src.engine.attribution import PositionAttributor
from src.engine.heat_score import HeatScoreCalculator
from src.engine.causal_dag import CausalDAGEngine
from src.engine.crowding import CrowdingEngine
from src.engine.recommendations import TradeRecommendationEngine
from src.engine.regime import RegimeDetector
from src.engine.throttle import PreTradeSimulator, RiskThrottle
from src.models import (
    ClusterInfo,
    ClusterMigration,
    ClusterState,
    HeatLevel,
    MSTEdge,
    Position,
    ThrottleState,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("riskradar")


# Application state
state: dict = {
    "positions": [],
    "last_heat_score": None,
    "factor_exposures": [],
    "correlation_matrix": None,
    "avg_correlation": 0.0,
    "top_correlated_pair": {},
    "cluster_state": None,
    "dcc_correlation": None,
    "dcc_volatility": None,
    "dcc_stress": 0.5,
    "regime_state": None,
    "throttle_state": None,
    "risk_throttle": None,
    "pre_trade_simulator": None,
    "all_returns": None,
    "factor_returns": None,
    "attribution": None,
    "recommendations": None,
    "data_quality": None,
    "alert_manager": None,
    "benchmark_report": None,
}


async def compute_heat_loop(settings: Settings) -> None:
    """Background task: periodically compute heat score."""
    factor_etfs = settings.factor_etfs or DEFAULT_FACTOR_ETFS

    # Use DataPipelineManager for multi-source data
    pipeline = DataPipelineManager(
        polygon_api_key=settings.polygon_api_key,
        polygon_tier=settings.polygon_tier,
        duckdb_path=settings.duckdb_path,
        cache_ttl_seconds=settings.data_cache_ttl_seconds,
    )
    state["data_pipeline"] = pipeline

    factor_model = FactorModel(factor_etfs=factor_etfs, rolling_window=settings.rolling_window)
    corr_engine = CorrelationEngine(ewma_span=settings.ewma_span)
    dcc_engine = DCCGarchEngine(min_obs=60)
    cluster_engine = ClusteringEngine(
        correlation_threshold=0.4,
        migration_lookback=20,
    )
    regime_detector = RegimeDetector(
        n_regimes=3,
        lookback=504,
        retrain_interval=20,
    )
    risk_throttle = RiskThrottle(
        kelly_fraction=0.5,
        heat_floor=0.4,
        heat_ceiling=0.85,
    )
    causal_engine = CausalDAGEngine(use_prior=True, min_observations=60)
    crowding_engine = CrowdingEngine(lookback=60)
    position_attributor = PositionAttributor()
    recommendation_engine = TradeRecommendationEngine()
    state["risk_throttle"] = risk_throttle
    state["causal_engine"] = causal_engine
    state["crowding_engine"] = crowding_engine

    # Track historical data for regime features
    avg_corr_history: list[float] = []
    heat_score_history: list[float] = []

    # Try to load pre-calibrated calculator, fall back to fresh
    calibration_path = Path(settings.duckdb_path).parent / "heat_score_history.json"
    heat_calc = HeatScoreCalculator.from_calibration_file(
        path=str(calibration_path),
        weight_ar=settings.heat_weight_absorption_ratio,
        weight_turb=settings.heat_weight_turbulence,
        weight_dr=settings.heat_weight_diversification,
        weight_hhi=settings.heat_weight_factor_hhi,
        weight_corr=settings.heat_weight_avg_correlation,
        threshold_warm=settings.heat_threshold_warm,
        threshold_hot=settings.heat_threshold_hot,
        threshold_critical=settings.heat_threshold_critical,
        threshold_emergency=settings.heat_threshold_emergency,
    )

    # Multi-tier alert manager
    alert_cooldowns = {
        "daily_summary": settings.alert_cooldown_daily_summary,
        "level_change": settings.alert_cooldown_level_change,
        "threshold_cross": settings.alert_cooldown_threshold_cross,
        "regime_shift": settings.alert_cooldown_regime_shift,
        "emergency": settings.alert_cooldown_emergency,
    }
    alert_manager = AlertManager(
        telegram_bot_token=settings.telegram_bot_token,
        telegram_chat_id=settings.telegram_chat_id,
        cooldown_minutes=alert_cooldowns,
    )
    state["alert_manager"] = alert_manager

    storage = state.get("storage")

    while True:
        try:
            positions: list[Position] = state.get("positions", [])
            if not positions:
                logger.info("No positions configured — waiting...")
                await asyncio.sleep(settings.heat_update_interval_seconds)
                continue

            position_symbols = [p.symbol for p in positions]
            factor_symbols = [f["symbol"] for f in factor_etfs]
            all_symbols = list(set(position_symbols + factor_symbols))

            # Fetch returns via pipeline (Polygon -> yfinance -> cache)
            logger.info("Computing heat score for %d positions...", len(positions))
            returns = await pipeline.get_returns(all_symbols, period_days=settings.ar_lookback)
            state["data_quality"] = pipeline.get_data_quality_report()

            position_returns = returns[[s for s in position_symbols if s in returns.columns]]
            factor_returns = returns[[s for s in factor_symbols if s in returns.columns]]

            if position_returns.empty:
                logger.warning("No position return data available")
                await asyncio.sleep(settings.heat_update_interval_seconds)
                continue

            # 1. Factor exposures
            weights_dict = {p.symbol: p.weight for p in positions}
            exposures = factor_model.estimate_exposures(position_returns, factor_returns)
            summaries = factor_model.aggregate_exposures(exposures, weights_dict)
            state["factor_exposures"] = summaries
            dominant = factor_model.get_dominant_factor(summaries)

            # 2. Correlation matrix (EWMA baseline)
            corr_matrix = corr_engine.compute_correlation_matrix(position_returns)
            state["correlation_matrix"] = corr_matrix

            avg_corr = corr_engine.compute_avg_correlation(corr_matrix)
            state["avg_correlation"] = avg_corr

            sym_a, sym_b, corr_val = corr_engine.get_top_correlated_pair(corr_matrix)
            top_pair_str = f"{sym_a}↔{sym_b} ({corr_val:.2f})" if sym_a else ""
            state["top_correlated_pair"] = {"pair": top_pair_str, "correlation": corr_val}

            # 2b. DCC-GARCH conditional correlation
            try:
                dcc_corr, dcc_vols = dcc_engine.compute_dcc(position_returns)
                state["dcc_correlation"] = dcc_corr
                state["dcc_volatility"] = dcc_vols
                state["dcc_stress"] = dcc_engine.get_correlation_regime_indicator()

                # Use DCC correlation for clustering if available
                active_corr = dcc_corr if not dcc_corr.empty else corr_matrix
                logger.info(
                    "DCC stress indicator: %.3f", state["dcc_stress"]
                )
            except Exception as e:
                logger.warning("DCC computation failed, using EWMA: %s", e)
                active_corr = corr_matrix
                state["dcc_stress"] = 0.5

            # 2c. Clustering (Louvain + HRP + MST)
            try:
                # Louvain community detection
                communities = cluster_engine.detect_communities(active_corr)
                community_summary = cluster_engine.get_community_summary(communities)
                modularity = cluster_engine.get_modularity(active_corr, communities)

                # Migration detection
                migrations_raw = cluster_engine.detect_migrations()
                migration_risk = cluster_engine.get_migration_risk_score()

                # MST
                mst_edges_raw = cluster_engine.compute_mst(active_corr)

                # HRP
                hrp_result = cluster_engine.compute_hrp(
                    position_returns,
                    covariance=None,  # Use returns-based covariance
                )
                hrp_weights = hrp_result["weights"]
                hrp_concentration = cluster_engine.compute_hrp_concentration(hrp_weights)

                cluster_state = ClusterState(
                    communities=[ClusterInfo(**c) for c in community_summary],
                    n_communities=cluster_engine.n_communities,
                    modularity=modularity,
                    migrations=[ClusterMigration(**m) for m in migrations_raw],
                    migration_risk=migration_risk,
                    mst_edges=[MSTEdge(**e) for e in mst_edges_raw],
                    hrp_weights=hrp_weights.to_dict() if not hrp_weights.empty else {},
                    hrp_concentration=hrp_concentration,
                    dcc_stress_indicator=state["dcc_stress"],
                )
                state["cluster_state"] = cluster_state

                logger.info(
                    "Clusters: %d communities | Modularity: %.3f | Migration risk: %.3f | HRP HHI: %.3f",
                    cluster_engine.n_communities,
                    modularity,
                    migration_risk,
                    hrp_concentration,
                )
            except Exception as e:
                logger.error("Clustering failed: %s", e, exc_info=True)

            # 3. Heat score
            portfolio_weights = np.array([p.weight for p in positions])
            factor_exp_dict = {s.factor_name: s.total_exposure for s in summaries}

            heat = heat_calc.compute(
                returns=position_returns,
                portfolio_weights=portfolio_weights,
                factor_exposures=factor_exp_dict,
                avg_correlation=avg_corr,
                dominant_factor=dominant,
                top_correlated_pair=top_pair_str,
            )

            state["last_heat_score"] = heat

            # Store returns for pre-trade simulation API
            state["all_returns"] = returns
            state["factor_returns"] = factor_returns

            # Initialize pre-trade simulator (once)
            if state.get("pre_trade_simulator") is None:
                state["pre_trade_simulator"] = PreTradeSimulator(
                    factor_model=factor_model,
                    heat_calculator=heat_calc,
                    corr_engine=corr_engine,
                    cluster_engine=cluster_engine,
                )

            # 3b. Regime detection
            try:
                # Use full historical portfolio returns (equal-weighted)
                port_ret = position_returns.mean(axis=1)
                avg_corr_history.append(avg_corr)
                heat_score_history.append(heat.score)

                # Build features from the full returns series
                regime_features = regime_detector.build_features(
                    portfolio_returns=port_ret,
                    avg_correlations=avg_corr_history,
                    heat_scores=heat_score_history,
                )

                if not regime_features.empty:
                    if not regime_detector.is_fitted:
                        regime_detector.fit(regime_features)
                    regime_result = regime_detector.predict(regime_features)
                else:
                    regime_result = regime_detector._default_prediction()

                state["regime_state"] = regime_result
                logger.info(
                    "Regime: %s (confidence=%.2f, transition_risk=%.3f)",
                    regime_result["regime"],
                    regime_result["confidence"],
                    regime_result["transition_risk"],
                )
            except Exception as e:
                logger.warning("Regime detection failed: %s", e)
                state["regime_state"] = regime_detector._default_prediction()

            # 3c. Risk throttle
            try:
                current_exposure = float(np.sum(np.abs(portfolio_weights)))
                regime_label = state.get("regime_state", {}).get("regime", "normal")
                throttle_result = risk_throttle.compute_exposure_limits(
                    heat_score=heat.score,
                    current_exposure=current_exposure,
                    regime=regime_label,
                )
                state["throttle_state"] = throttle_result
                logger.info(
                    "Throttle: max_exp=%.2f, reduction=%.0f%%, new_allowed=%s",
                    throttle_result["max_exposure"],
                    throttle_result["target_reduction"] * 100,
                    throttle_result["new_position_allowed"],
                )
            except Exception as e:
                logger.warning("Throttle computation failed: %s", e)

            # 3d. Causal DAG
            try:
                # Map factor returns to factor names for causal engine
                factor_name_map = {f["symbol"]: f["name"] for f in factor_etfs}
                named_factor_returns = factor_returns.rename(columns=factor_name_map)

                causal_state = causal_engine.build_dag(
                    factor_returns=named_factor_returns,
                    position_returns=position_returns,
                )
                causal_effects = causal_engine.get_causal_effects_on_portfolio()
                logger.info(
                    "Causal DAG: %d edges, %d significant effects",
                    causal_state.n_edges,
                    len(causal_effects),
                )
            except Exception as e:
                logger.warning("Causal DAG computation failed: %s", e)

            # 3e. Crowding detection
            try:
                factor_exp_dict_for_crowding = {
                    s.factor_name: s.total_exposure for s in summaries
                }
                crowding_state = crowding_engine.compute_and_store(
                    position_returns=position_returns,
                    factor_returns=factor_returns,
                    factor_exposures=factor_exp_dict_for_crowding,
                    correlation_matrix=corr_matrix,
                    avg_correlation=avg_corr,
                )
                state["crowding_state"] = crowding_state
                logger.info(
                    "Crowding: %.3f (%s) | Alpha signal: %.3f | Most crowded: %s",
                    crowding_state.composite_score,
                    crowding_state.level,
                    crowding_state.crowding_alpha_signal,
                    crowding_state.most_crowded_factor,
                )
            except Exception as e:
                logger.warning("Crowding computation failed: %s", e)

            # 3f. Position attribution
            try:
                # Build per-position factor exposures dict
                per_position_factors: dict[str, dict[str, float]] = {}
                for exp in exposures:
                    sym = exp.symbol
                    if sym not in per_position_factors:
                        per_position_factors[sym] = {}
                    per_position_factors[sym][exp.factor_name] = exp.beta

                attribution = position_attributor.compute_attribution(
                    positions=positions,
                    returns=position_returns,
                    factor_exposures=per_position_factors,
                    correlation_matrix=corr_matrix,
                    heat_score=heat.score,
                    avg_correlation=avg_corr,
                )
                state["attribution"] = attribution
                if attribution:
                    top = attribution[0]
                    logger.info(
                        "Attribution: top contributor %s (%.1f%% heat share, rec=%s)",
                        top.symbol, top.heat_share * 100, top.recommendation,
                    )
            except Exception as e:
                logger.warning("Attribution computation failed: %s", e)

            # 3g. Trade recommendations
            try:
                cluster_hrp = {}
                cs = state.get("cluster_state")
                if cs is not None:
                    cluster_hrp = cs.hrp_weights

                recommendations = recommendation_engine.generate_recommendations(
                    positions=positions,
                    attribution=state.get("attribution", []),
                    heat_score=heat.score,
                    regime_state=state.get("regime_state"),
                    throttle_state=state.get("throttle_state"),
                    cluster_state=cs,
                    crowding_state=state.get("crowding_state"),
                    hrp_weights=cluster_hrp,
                )
                state["recommendations"] = recommendations
                logger.info(
                    "Recommendations: %s", recommendations.summary,
                )
            except Exception as e:
                logger.warning("Recommendation computation failed: %s", e)

            # 4. Persist
            if storage:
                storage.save_heat_score({
                    "timestamp": heat.timestamp,
                    "score": heat.score,
                    "level": heat.level.value,
                    "absorption_ratio": heat.components.absorption_ratio,
                    "turbulence": heat.components.turbulence,
                    "diversification_ratio": heat.components.diversification_ratio,
                    "factor_hhi": heat.components.factor_hhi,
                    "avg_correlation": heat.components.avg_correlation,
                    "dominant_factor": heat.dominant_factor,
                    "top_correlated_pair": heat.top_correlated_pair,
                })

            # 5. Broadcast to WebSocket clients
            await broadcast_heat(heat)

            # 6. Multi-tier alerting
            previous_heat = state.get("_previous_heat_score")
            alerts_sent = await alert_manager.check_and_alert(
                heat_score=heat,
                previous_heat=previous_heat,
                regime_state=state.get("regime_state"),
                throttle_state=state.get("throttle_state"),
                attribution=state.get("attribution"),
                recommendations=state.get("recommendations"),
            )
            state["_previous_heat_score"] = heat
            if alerts_sent:
                logger.info("Alerts sent: %s", alerts_sent)

            logger.info(
                "Heat Score: %.2f (%s) | Dominant: %s | Top Pair: %s | DCC Stress: %.3f",
                heat.score, heat.level.value, dominant, top_pair_str,
                state.get("dcc_stress", 0.5),
            )

            # Clear market data cache for fresh data next cycle
            pipeline.clear_cache()

        except Exception as e:
            logger.error("Heat computation error: %s", e, exc_info=True)

        await asyncio.sleep(settings.heat_update_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    settings = Settings.from_yaml()

    # Initialize storage
    storage = Storage(settings.duckdb_path)
    state["storage"] = storage

    # Initialize cache (optional — degrades gracefully)
    cache = Cache(settings.redis_url)
    await cache.connect()
    state["cache"] = cache

    # Load positions from config
    if settings.portfolio_positions:
        state["positions"] = [
            Position(**p) for p in settings.portfolio_positions
        ]
        logger.info("Loaded %d positions from config", len(state["positions"]))

    # Inject state into routes and dashboard
    set_app_state(state)
    set_dashboard_state(state)

    # Start background heat computation
    heat_task = asyncio.create_task(compute_heat_loop(settings))

    # Start daily summary scheduler (16:30 ET)
    summary_task = asyncio.create_task(_daily_summary_scheduler(state))

    # Run benchmark on startup (cache results)
    benchmark_task = asyncio.create_task(_run_benchmark_on_startup(state))

    logger.info("🌡️  Risk Radar started on %s:%d", settings.host, settings.port)
    yield

    # Shutdown
    heat_task.cancel()
    summary_task.cancel()
    benchmark_task.cancel()
    for task in [heat_task, summary_task, benchmark_task]:
        try:
            await task
        except asyncio.CancelledError:
            pass
    # Stop streaming if active
    pipeline = state.get("data_pipeline")
    if pipeline:
        await pipeline.stop_streaming()
    storage.close()
    await cache.close()
    logger.info("Risk Radar shut down.")


async def _daily_summary_scheduler(app_state: dict) -> None:
    """Schedule daily summary at 16:30 ET (after market close)."""
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")

    while True:
        try:
            now = datetime.now(et)
            # Target: 16:30 ET today or tomorrow
            target = now.replace(hour=16, minute=30, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)

            wait_seconds = (target - now).total_seconds()
            logger.info("Daily summary scheduled in %.0f seconds (at %s ET)", wait_seconds, target.strftime("%H:%M"))
            await asyncio.sleep(wait_seconds)

            # Send daily summary
            alert_manager = app_state.get("alert_manager")
            heat = app_state.get("last_heat_score")
            if alert_manager and heat:
                await alert_manager.send_daily_summary(
                    heat_score=heat,
                    regime_state=app_state.get("regime_state"),
                    attribution=app_state.get("attribution"),
                    recommendations=app_state.get("recommendations"),
                    data_quality=app_state.get("data_quality"),
                )
                logger.info("Daily summary sent")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Daily summary scheduler error: %s", e)
            await asyncio.sleep(60)


async def _run_benchmark_on_startup(app_state: dict) -> None:
    """Run benchmark comparison on startup (in background thread)."""
    # Wait for initial data to be available
    await asyncio.sleep(30)

    try:
        benchmark = SignalBenchmark()
        heat_history, corr_history = SignalBenchmark.from_history_file("data/heat_score_history.json")

        if heat_history:
            loop = asyncio.get_event_loop()
            report = await loop.run_in_executor(
                None,
                lambda: benchmark.run_comparison(heat_history, correlation_history=corr_history),
            )
            app_state["benchmark_report"] = report
            logger.info("Benchmark complete: best signal = %s", report.best_signal)
        else:
            logger.info("No heat history file found — benchmark skipped")
    except Exception as e:
        logger.warning("Benchmark computation failed: %s", e)


# Create FastAPI app
app = FastAPI(
    title="Risk Radar",
    description="Portfolio-Level Latent Factor Risk Engine",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(dashboard_router)

# Mount static files for dashboard
_dashboard_static = Path(__file__).parent / "dashboard" / "static"
app.mount("/static", StaticFiles(directory=str(_dashboard_static)), name="static")


if __name__ == "__main__":
    import uvicorn
    settings = Settings.from_yaml()
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )
