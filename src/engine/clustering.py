"""Clustering engine — HRP + Louvain community detection + migration tracking.

Provides three complementary views of portfolio structure:

1. **HRP (Hierarchical Risk Parity)** via riskfolio-lib:
   - Dendrogram-based clustering of asset correlations
   - Optimal diversification-aware weight allocation

2. **Louvain community detection** via NetworkX + python-louvain:
   - Converts correlation matrix to graph (edges = high correlations)
   - Automatically finds communities (clusters) of correlated assets
   - No need to specify number of clusters

3. **Cluster migration tracking**:
   - Tracks which community each position belongs to over time
   - Flags when positions migrate between clusters = early warning

References:
- López de Prado (2016): Building Diversified Portfolios that Outperform OOS
- Blondel et al. (2008): Fast unfolding of communities in large networks
- Stevens Institute (Dec 2025): Portfolio optimization via Louvain + MST
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import networkx as nx
from community import community_louvain

logger = logging.getLogger(__name__)

# Try importing riskfolio-lib (optional heavy dependency)
try:
    import riskfolio as rp
    HAS_RISKFOLIO = True
except ImportError:
    HAS_RISKFOLIO = False
    logger.warning("riskfolio-lib not available — HRP clustering disabled")


class ClusteringEngine:
    """Portfolio clustering via HRP and Louvain community detection."""

    def __init__(
        self,
        correlation_threshold: float = 0.4,
        migration_lookback: int = 20,
        max_history: int = 252,
    ):
        """
        Args:
            correlation_threshold: Min |correlation| to create an edge in the graph.
            migration_lookback: Rolling window for migration detection.
            max_history: Max cluster snapshots to retain.
        """
        self.correlation_threshold = correlation_threshold
        self.migration_lookback = migration_lookback
        self.max_history = max_history

        # History of cluster assignments: list of (timestamp, {symbol: cluster_id})
        self._cluster_history: list[tuple[datetime, dict[str, int]]] = []
        self._last_communities: dict[str, int] = {}
        self._last_hrp_weights: Optional[pd.Series] = None
        self._last_dendrogram: Optional[dict] = None

    # ─── Louvain Community Detection ────────────────────────────────

    def detect_communities(
        self, corr_matrix: pd.DataFrame, resolution: float = 1.0
    ) -> dict[str, int]:
        """
        Detect communities of correlated assets using Louvain method.

        Args:
            corr_matrix: Correlation matrix (N × N DataFrame).
            resolution: Louvain resolution parameter. Higher = more communities.

        Returns:
            Dict of {symbol: community_id}.
        """
        if corr_matrix.empty or corr_matrix.shape[0] < 2:
            return {}

        # Build graph from correlation matrix
        G = self._corr_to_graph(corr_matrix)

        if G.number_of_edges() == 0:
            # No edges above threshold — each asset is its own community
            return {sym: i for i, sym in enumerate(corr_matrix.columns)}

        # Run Louvain
        try:
            partition = community_louvain.best_partition(
                G, weight="weight", resolution=resolution, random_state=42
            )
        except Exception as e:
            logger.warning("Louvain failed: %s", e)
            return {sym: 0 for sym in corr_matrix.columns}

        # Include any nodes not in graph (below threshold)
        for sym in corr_matrix.columns:
            if sym not in partition:
                partition[sym] = max(partition.values(), default=-1) + 1

        self._last_communities = partition

        # Record in history
        self._cluster_history.append((datetime.utcnow(), partition.copy()))
        if len(self._cluster_history) > self.max_history:
            self._cluster_history = self._cluster_history[-self.max_history:]

        return partition

    def _corr_to_graph(self, corr_matrix: pd.DataFrame) -> nx.Graph:
        """
        Convert correlation matrix to weighted graph.

        Only edges with |correlation| > threshold are included.
        Weight = |correlation| (Louvain maximizes weighted modularity).
        """
        G = nx.Graph()
        symbols = corr_matrix.columns.tolist()
        G.add_nodes_from(symbols)

        n = len(symbols)
        for i in range(n):
            for j in range(i + 1, n):
                corr = corr_matrix.iloc[i, j]
                if abs(corr) >= self.correlation_threshold:
                    G.add_edge(symbols[i], symbols[j], weight=abs(corr))

        return G

    def get_community_summary(
        self, communities: dict[str, int]
    ) -> list[dict]:
        """
        Summarize detected communities.

        Returns list of dicts with cluster_id, members, size.
        """
        clusters: dict[int, list[str]] = defaultdict(list)
        for symbol, cluster_id in communities.items():
            clusters[cluster_id].append(symbol)

        return [
            {
                "cluster_id": cid,
                "members": sorted(members),
                "size": len(members),
            }
            for cid, members in sorted(clusters.items())
        ]

    def get_modularity(self, corr_matrix: pd.DataFrame, communities: dict[str, int]) -> float:
        """Calculate modularity of the partition (quality metric)."""
        G = self._corr_to_graph(corr_matrix)
        if G.number_of_edges() == 0:
            return 0.0
        try:
            return community_louvain.modularity(communities, G, weight="weight")
        except Exception:
            return 0.0

    # ─── MST (Minimum Spanning Tree) ───────────────────────────────

    def compute_mst(self, corr_matrix: pd.DataFrame) -> list[dict]:
        """
        Compute Minimum Spanning Tree of distance matrix.

        Distance = sqrt(2 * (1 - corr)) — Mantegna distance.

        Returns list of edges: [{source, target, distance, correlation}].
        """
        if corr_matrix.empty or corr_matrix.shape[0] < 2:
            return []

        symbols = corr_matrix.columns.tolist()
        n = len(symbols)

        # Build complete distance graph
        G = nx.Graph()
        for i in range(n):
            for j in range(i + 1, n):
                corr = corr_matrix.iloc[i, j]
                dist = np.sqrt(2 * (1 - corr))
                G.add_edge(symbols[i], symbols[j], weight=dist, correlation=corr)

        # MST
        mst = nx.minimum_spanning_tree(G, weight="weight")

        edges = []
        for u, v, data in mst.edges(data=True):
            edges.append({
                "source": u,
                "target": v,
                "distance": round(data["weight"], 4),
                "correlation": round(data.get("correlation", 0), 4),
            })

        return sorted(edges, key=lambda e: e["distance"])

    # ─── Cluster Migration Detection ───────────────────────────────

    def detect_migrations(self) -> list[dict]:
        """
        Detect symbols that changed cluster membership recently.

        Compares latest cluster assignment to previous assignments
        within the lookback window.

        Returns list of migration events:
            [{symbol, from_cluster, to_cluster, timestamp}]
        """
        if len(self._cluster_history) < 2:
            return []

        current_ts, current = self._cluster_history[-1]

        # Compare against each previous snapshot in lookback
        migrations = []
        lookback_start = max(0, len(self._cluster_history) - self.migration_lookback - 1)

        for i in range(lookback_start, len(self._cluster_history) - 1):
            prev_ts, prev = self._cluster_history[i]

            for symbol in current:
                if symbol in prev:
                    curr_cluster = current[symbol]
                    prev_cluster = prev[symbol]

                    # Check if the symbol's cluster peers changed
                    # (cluster IDs may differ but membership may be same)
                    curr_peers = self._get_cluster_peers(current, symbol)
                    prev_peers = self._get_cluster_peers(prev, symbol)

                    # Significant migration = different peer set
                    if curr_peers != prev_peers and len(curr_peers) > 0 and len(prev_peers) > 0:
                        overlap = len(curr_peers & prev_peers) / max(len(curr_peers | prev_peers), 1)
                        if overlap < 0.5:  # Less than 50% peer overlap = real migration
                            migrations.append({
                                "symbol": symbol,
                                "from_peers": sorted(prev_peers),
                                "to_peers": sorted(curr_peers),
                                "overlap": round(overlap, 3),
                                "detected_at": current_ts.isoformat(),
                                "compared_to": prev_ts.isoformat(),
                            })

        # Deduplicate by symbol (keep most recent)
        seen = set()
        unique_migrations = []
        for m in reversed(migrations):
            if m["symbol"] not in seen:
                seen.add(m["symbol"])
                unique_migrations.append(m)

        return list(reversed(unique_migrations))

    def _get_cluster_peers(self, partition: dict[str, int], symbol: str) -> set[str]:
        """Get all symbols in the same cluster as `symbol`."""
        cluster_id = partition.get(symbol)
        if cluster_id is None:
            return set()
        return {s for s, c in partition.items() if c == cluster_id and s != symbol}

    def get_migration_risk_score(self) -> float:
        """
        0-1 score of how much cluster churn is happening.
        High churn = correlations are unstable = elevated risk.
        """
        if len(self._cluster_history) < 2:
            return 0.0

        migrations = self.detect_migrations()
        _, current = self._cluster_history[-1]
        n_symbols = max(len(current), 1)

        # Migration rate = fraction of symbols that migrated
        migrated = len(set(m["symbol"] for m in migrations))
        rate = migrated / n_symbols

        return float(np.clip(rate, 0, 1))

    # ─── HRP (Hierarchical Risk Parity) ───────────────────────────

    def compute_hrp(
        self,
        returns: pd.DataFrame,
        covariance: Optional[pd.DataFrame] = None,
    ) -> dict:
        """
        Compute HRP optimal weights using riskfolio-lib.

        Args:
            returns: Asset returns DataFrame.
            covariance: Optional custom covariance matrix (e.g., from DCC-GARCH).

        Returns:
            Dict with 'weights' (pd.Series), 'clusters' (linkage info).
        """
        if not HAS_RISKFOLIO:
            logger.warning("riskfolio-lib not available, returning equal weights")
            n = returns.shape[1]
            eq_w = pd.Series(1.0 / n, index=returns.columns)
            return {"weights": eq_w, "clusters": {}}

        if returns.empty or returns.shape[1] < 2:
            return {"weights": pd.Series(), "clusters": {}}

        try:
            port = rp.HCPortfolio(returns=returns)

            if covariance is not None:
                # Align covariance matrix with returns columns
                common = [c for c in returns.columns if c in covariance.columns]
                if len(common) >= 2:
                    port.cov = covariance.loc[common, common]

            weights = port.optimization(
                model="HRP",
                codependence="pearson",
                rm="MV",  # Minimum Variance risk measure
                leaf_order=True,
            )

            if weights is not None and not weights.empty:
                # riskfolio returns a DataFrame with single column 'weights'
                w_series = weights.iloc[:, 0] if isinstance(weights, pd.DataFrame) else weights
                self._last_hrp_weights = w_series
                return {"weights": w_series, "clusters": {}}
            else:
                logger.warning("HRP returned empty weights")
                return {"weights": pd.Series(), "clusters": {}}

        except Exception as e:
            logger.error("HRP optimization failed: %s", e, exc_info=True)
            n = returns.shape[1]
            eq_w = pd.Series(1.0 / n, index=returns.columns)
            return {"weights": eq_w, "clusters": {}}

    def compute_hrp_concentration(self, hrp_weights: pd.Series) -> float:
        """
        Measure concentration of HRP weights via HHI.
        High HHI = HRP itself thinks portfolio is concentrated.
        """
        if hrp_weights.empty:
            return 0.0
        w = hrp_weights.values
        w = np.abs(w) / np.sum(np.abs(w)) if np.sum(np.abs(w)) > 0 else w
        hhi = float(np.sum(w ** 2))
        return hhi

    # ─── Properties ────────────────────────────────────────────────

    @property
    def last_communities(self) -> dict[str, int]:
        """Most recent community partition."""
        return self._last_communities

    @property
    def last_hrp_weights(self) -> Optional[pd.Series]:
        """Most recent HRP optimal weights."""
        return self._last_hrp_weights

    @property
    def cluster_history(self) -> list[tuple[datetime, dict[str, int]]]:
        """Full cluster assignment history."""
        return self._cluster_history

    @property
    def n_communities(self) -> int:
        """Number of communities in latest partition."""
        if not self._last_communities:
            return 0
        return len(set(self._last_communities.values()))
