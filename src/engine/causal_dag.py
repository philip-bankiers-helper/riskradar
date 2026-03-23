"""Causal DAG engine for factor-to-return causal inference.

Implements Pearl's structural causal model (SCM) framework using DoWhy.
Based on López de Prado & Zoonekynd (2025) — "Causality and Factor Investing".

Key principles:
- Include confounders (variables affecting both factor and returns)
- Exclude colliders (variables affected by both factor and returns)
- Test interventional predictions, not just associational
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class CausalEdge:
    """A directed causal edge in the DAG."""
    source: str
    target: str
    effect: float = 0.0
    p_value: float = 1.0
    confidence_interval: tuple[float, float] = (0.0, 0.0)
    method: str = "linear_regression"


@dataclass
class CausalDAGState:
    """Current state of the causal DAG."""
    edges: list[CausalEdge] = field(default_factory=list)
    confounders: list[str] = field(default_factory=list)
    colliders: list[str] = field(default_factory=list)
    n_nodes: int = 0
    n_edges: int = 0
    last_updated: str = ""
    discovery_method: str = "prior"  # "prior" or "pc_algorithm"


# Predefined causal structure from research report
# Fed Policy → Rates → Credit → Funding Liquidity → Dollar → Factor Crowding
PRIOR_DAG_EDGES = [
    # Macro causal chain
    ("rates", "credit"),        # Rate changes drive credit spreads
    ("credit", "dollar"),       # Credit stress affects dollar
    ("rates", "market"),        # Rates affect market beta

    # Factor → portfolio effects
    ("rates", "position_returns"),
    ("credit", "position_returns"),
    ("dollar", "position_returns"),
    ("market", "position_returns"),
    ("ai_tech", "position_returns"),
    ("momentum", "position_returns"),
    ("quality", "position_returns"),
    ("value", "position_returns"),
    ("low_vol", "position_returns"),

    # Factor interactions (causal, not just correlated)
    ("market", "momentum"),     # Market drives momentum factor
    ("market", "ai_tech"),      # Market drives tech beta
    ("rates", "value"),         # Rates affect value vs growth
    ("credit", "quality"),      # Credit stress drives quality premium
]

# Confounders: affect both factor and returns — MUST include
CONFOUNDERS = ["rates", "credit", "market"]

# Colliders: affected by both factor and returns — MUST exclude from conditioning
# VIX is the classic collider: it's affected by both factor moves and return moves
COLLIDERS = ["vix"]


class CausalDAGEngine:
    """Causal factor model using Structural Causal Models (SCMs).

    Two modes:
    1. Prior-based: Use the predefined DAG from domain knowledge
    2. Data-driven: Use PC algorithm from causal-learn for discovery

    The prior DAG is preferred for risk management (stability > discovery).
    """

    def __init__(
        self,
        use_prior: bool = True,
        significance_level: float = 0.05,
        min_observations: int = 60,
    ):
        self.use_prior = use_prior
        self.significance_level = significance_level
        self.min_observations = min_observations
        self._causal_effects: dict[str, CausalEdge] = {}
        self._dag_state: CausalDAGState | None = None
        self._last_graph = None

    def build_dag(
        self,
        factor_returns: pd.DataFrame,
        position_returns: pd.Series | pd.DataFrame,
    ) -> CausalDAGState:
        """Build or update the causal DAG.

        Args:
            factor_returns: DataFrame with factor ETF returns (columns = factor names).
            position_returns: Portfolio-level returns (Series) or per-position (DataFrame).
        """
        if isinstance(position_returns, pd.DataFrame):
            # Aggregate to portfolio level (equal-weighted mean)
            port_ret = position_returns.mean(axis=1)
        else:
            port_ret = position_returns

        # Build combined data
        data = factor_returns.copy()
        data["position_returns"] = port_ret

        # Align indices
        data = data.dropna()

        if len(data) < self.min_observations:
            logger.warning(
                "Only %d observations (need %d) — returning empty DAG",
                len(data), self.min_observations,
            )
            self._dag_state = CausalDAGState()
            return self._dag_state

        if self.use_prior:
            edges = self._estimate_prior_dag_effects(data)
        else:
            edges = self._discover_dag(data)

        self._dag_state = CausalDAGState(
            edges=edges,
            confounders=CONFOUNDERS,
            colliders=COLLIDERS,
            n_nodes=len(data.columns),
            n_edges=len(edges),
            last_updated=pd.Timestamp.now().isoformat(),
            discovery_method="prior" if self.use_prior else "pc_algorithm",
        )
        return self._dag_state

    def _estimate_prior_dag_effects(self, data: pd.DataFrame) -> list[CausalEdge]:
        """Estimate causal effects using the prior DAG structure with DoWhy."""
        edges = []

        for source, target in PRIOR_DAG_EDGES:
            if source not in data.columns or target not in data.columns:
                continue

            try:
                effect, p_val, ci = self._estimate_single_effect(
                    data, source, target
                )
                edges.append(CausalEdge(
                    source=source,
                    target=target,
                    effect=effect,
                    p_value=p_val,
                    confidence_interval=ci,
                    method="dowhy_linear",
                ))
                self._causal_effects[f"{source}->{target}"] = edges[-1]
            except Exception as e:
                logger.debug("Could not estimate %s->%s: %s", source, target, e)
                # Fall back to simple regression
                try:
                    effect, p_val, ci = self._simple_regression_effect(
                        data, source, target
                    )
                    edges.append(CausalEdge(
                        source=source,
                        target=target,
                        effect=effect,
                        p_value=p_val,
                        confidence_interval=ci,
                        method="ols_fallback",
                    ))
                    self._causal_effects[f"{source}->{target}"] = edges[-1]
                except Exception as e2:
                    logger.debug("OLS fallback also failed for %s->%s: %s", source, target, e2)

        return edges

    def _estimate_single_effect(
        self,
        data: pd.DataFrame,
        treatment: str,
        outcome: str,
    ) -> tuple[float, float, tuple[float, float]]:
        """Estimate a single causal effect using DoWhy.

        Uses linear regression with proper confounder adjustment.
        Excludes colliders from conditioning set.
        """
        import dowhy
        from dowhy import CausalModel

        # Identify confounders for this specific edge
        # Confounders = common causes of treatment and outcome
        common_causes = []
        for conf in CONFOUNDERS:
            if conf != treatment and conf != outcome and conf in data.columns:
                common_causes.append(conf)

        # Build the DoWhy graph string (GML format)
        gml = self._build_gml_subgraph(treatment, outcome, common_causes)

        model = CausalModel(
            data=data,
            treatment=treatment,
            outcome=outcome,
            graph=gml,
        )

        # Identify the estimand
        identified = model.identify_effect(proceed_when_unidentifiable=True)

        # Estimate using linear regression
        estimate = model.estimate_effect(
            identified,
            method_name="backdoor.linear_regression",
        )

        effect_value = float(estimate.value)

        # Refutation: placebo treatment test
        try:
            refutation = model.refute_estimate(
                identified,
                estimate,
                method_name="placebo_treatment_refuter",
                placebo_type="permute",
                num_simulations=50,
            )
            p_val = float(refutation.refutation_result.get("p_value", 0.05)
                         if isinstance(refutation.refutation_result, dict)
                         else 0.05)
        except Exception:
            # Estimate p-value from effect magnitude vs. noise
            p_val = self._bootstrap_p_value(data, treatment, outcome, effect_value)

        # Bootstrap confidence interval
        ci = self._bootstrap_ci(data, treatment, outcome, n_bootstrap=100)

        return effect_value, p_val, ci

    def _simple_regression_effect(
        self,
        data: pd.DataFrame,
        treatment: str,
        outcome: str,
    ) -> tuple[float, float, tuple[float, float]]:
        """Fallback: OLS regression with confounder controls."""
        from sklearn.linear_model import LinearRegression

        # Include confounders as controls
        controls = [c for c in CONFOUNDERS
                    if c != treatment and c != outcome and c in data.columns]
        features = [treatment] + controls

        X = data[features].values
        y = data[outcome].values

        model = LinearRegression()
        model.fit(X, y)

        effect = float(model.coef_[0])  # Coefficient on treatment variable

        # Approximate p-value via t-test
        n = len(y)
        k = X.shape[1]
        y_pred = model.predict(X)
        residuals = y - y_pred
        mse = float(np.sum(residuals ** 2) / (n - k - 1))
        se = np.sqrt(mse * np.linalg.pinv(X.T @ X).diagonal())
        t_stat = effect / se[0] if se[0] > 0 else 0
        from scipy import stats
        p_val = float(2 * (1 - stats.t.cdf(abs(t_stat), df=n - k - 1)))

        ci = (effect - 1.96 * se[0], effect + 1.96 * se[0])
        return effect, p_val, ci

    def _bootstrap_p_value(
        self,
        data: pd.DataFrame,
        treatment: str,
        outcome: str,
        observed_effect: float,
        n_bootstrap: int = 200,
    ) -> float:
        """Estimate p-value by permutation test."""
        null_effects = []
        for _ in range(n_bootstrap):
            shuffled = data.copy()
            shuffled[treatment] = np.random.permutation(shuffled[treatment].values)
            corr = float(np.corrcoef(shuffled[treatment], shuffled[outcome])[0, 1])
            null_effects.append(corr)

        null_effects = np.array(null_effects)
        p_val = float(np.mean(np.abs(null_effects) >= abs(observed_effect)))
        return max(p_val, 1.0 / (n_bootstrap + 1))  # Floor at 1/n

    def _bootstrap_ci(
        self,
        data: pd.DataFrame,
        treatment: str,
        outcome: str,
        n_bootstrap: int = 100,
        alpha: float = 0.05,
    ) -> tuple[float, float]:
        """Bootstrap confidence interval for causal effect."""
        effects = []
        n = len(data)
        for _ in range(n_bootstrap):
            sample = data.sample(n=n, replace=True)
            if sample[treatment].std() > 0:
                from sklearn.linear_model import LinearRegression
                controls = [c for c in CONFOUNDERS
                            if c != treatment and c != outcome and c in data.columns]
                features = [treatment] + controls
                X = sample[features].values
                y = sample[outcome].values
                model = LinearRegression()
                model.fit(X, y)
                effects.append(float(model.coef_[0]))

        if not effects:
            return (0.0, 0.0)

        lo = float(np.percentile(effects, 100 * alpha / 2))
        hi = float(np.percentile(effects, 100 * (1 - alpha / 2)))
        return (lo, hi)

    def _build_gml_subgraph(
        self,
        treatment: str,
        outcome: str,
        common_causes: list[str],
    ) -> str:
        """Build a GML graph string for DoWhy."""
        lines = ['graph [directed 1']

        all_nodes = list(set([treatment, outcome] + common_causes))
        for i, node in enumerate(all_nodes):
            lines.append(f'  node [id {i} label "{node}"]')

        node_id = {n: i for i, n in enumerate(all_nodes)}

        # Treatment -> Outcome
        lines.append(f'  edge [source {node_id[treatment]} target {node_id[outcome]}]')

        # Common causes -> Treatment and Common causes -> Outcome
        for cc in common_causes:
            lines.append(f'  edge [source {node_id[cc]} target {node_id[treatment]}]')
            lines.append(f'  edge [source {node_id[cc]} target {node_id[outcome]}]')

        lines.append(']')
        return '\n'.join(lines)

    def _discover_dag(self, data: pd.DataFrame) -> list[CausalEdge]:
        """Use PC algorithm from causal-learn for causal discovery."""
        from causallearn.search.ConstraintBased.PC import pc

        # Run PC algorithm
        result = pc(
            data.values,
            alpha=self.significance_level,
            indep_test="fisherz",
        )

        col_names = list(data.columns)
        edges = []

        # Extract discovered edges
        adj_matrix = result.G.graph
        n = len(col_names)

        for i in range(n):
            for j in range(n):
                # In causal-learn: graph[i,j]==-1 and graph[j,i]==1 means i->j
                if adj_matrix[i, j] == -1 and adj_matrix[j, i] == 1:
                    source = col_names[i]
                    target = col_names[j]

                    # Estimate effect
                    try:
                        effect, p_val, ci = self._simple_regression_effect(
                            data, source, target
                        )
                    except Exception:
                        effect, p_val, ci = 0.0, 1.0, (0.0, 0.0)

                    edges.append(CausalEdge(
                        source=source,
                        target=target,
                        effect=effect,
                        p_value=p_val,
                        confidence_interval=ci,
                        method="pc_algorithm",
                    ))

        self._last_graph = result
        return edges

    def get_causal_effects_on_portfolio(self) -> dict[str, float]:
        """Get estimated causal effect of each factor on portfolio returns.

        Returns dict mapping factor name -> causal effect size.
        Only includes effects with p < significance level.
        """
        if not self._dag_state:
            return {}

        effects = {}
        for edge in self._dag_state.edges:
            if edge.target == "position_returns":
                if edge.p_value < self.significance_level:
                    effects[edge.source] = edge.effect

        return effects

    def get_significant_edges(self) -> list[CausalEdge]:
        """Get only statistically significant causal edges."""
        if not self._dag_state:
            return []
        return [e for e in self._dag_state.edges if e.p_value < self.significance_level]

    def interventional_query(
        self,
        data: pd.DataFrame,
        factor: str,
        shock_size: float,
    ) -> dict:
        """Simulate: "What happens to portfolio if factor moves by shock_size?"

        This is an interventional (do-calculus) query, not just conditional.
        Uses the estimated causal effects to propagate the shock through the DAG.

        Args:
            data: Current market data.
            factor: Which factor to shock.
            shock_size: Size of the shock in standard deviations.

        Returns:
            Dict with propagated effects on each variable.
        """
        if not self._dag_state:
            return {"error": "DAG not built yet"}

        effects = {}
        factor_std = float(data[factor].std()) if factor in data.columns else 0.01
        shock_in_returns = shock_size * factor_std

        # Direct effect on portfolio
        direct_key = f"{factor}->position_returns"
        if direct_key in self._causal_effects:
            direct_effect = self._causal_effects[direct_key].effect * shock_in_returns
            effects["direct_portfolio_impact"] = float(direct_effect)
        else:
            effects["direct_portfolio_impact"] = 0.0

        # Propagated effects through causal chain
        total_indirect = 0.0
        for edge_key, edge in self._causal_effects.items():
            if edge.source == factor and edge.target != "position_returns":
                # Factor -> intermediate -> portfolio
                intermediate = edge.target
                indirect_key = f"{intermediate}->position_returns"
                if indirect_key in self._causal_effects:
                    intermediate_effect = edge.effect * shock_in_returns
                    portfolio_effect = (
                        self._causal_effects[indirect_key].effect * intermediate_effect
                    )
                    effects[f"indirect_via_{intermediate}"] = float(portfolio_effect)
                    total_indirect += portfolio_effect

        effects["total_indirect_impact"] = float(total_indirect)
        effects["total_impact"] = float(
            effects["direct_portfolio_impact"] + total_indirect
        )
        effects["shock_factor"] = factor
        effects["shock_size_std"] = shock_size
        effects["shock_size_returns"] = float(shock_in_returns)

        return effects

    @property
    def state(self) -> CausalDAGState | None:
        return self._dag_state

    def to_dict(self) -> dict:
        """Serialize DAG state for API response."""
        if not self._dag_state:
            return {"edges": [], "confounders": [], "colliders": []}

        return {
            "edges": [
                {
                    "source": e.source,
                    "target": e.target,
                    "effect": round(e.effect, 6),
                    "p_value": round(e.p_value, 4),
                    "ci_lower": round(e.confidence_interval[0], 6),
                    "ci_upper": round(e.confidence_interval[1], 6),
                    "significant": e.p_value < self.significance_level,
                    "method": e.method,
                }
                for e in self._dag_state.edges
            ],
            "confounders": self._dag_state.confounders,
            "colliders": self._dag_state.colliders,
            "n_nodes": self._dag_state.n_nodes,
            "n_edges": self._dag_state.n_edges,
            "discovery_method": self._dag_state.discovery_method,
            "last_updated": self._dag_state.last_updated,
            "significant_factor_effects": self.get_causal_effects_on_portfolio(),
        }
