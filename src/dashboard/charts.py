"""Plotly chart configuration generators for the dashboard."""

from __future__ import annotations

import json
from typing import Any


def heat_gauge_config(score: float, level: str) -> str:
    """Generate Plotly gauge config for heat score."""
    color_map = {
        "cool": "#00d4aa",
        "warm": "#f0ad4e",
        "hot": "#ff6b35",
        "critical": "#ff2d2d",
        "emergency": "#ff0040",
    }
    bar_color = color_map.get(level, "#888")

    config = {
        "data": [{
            "type": "indicator",
            "mode": "gauge+number+delta",
            "value": score,
            "number": {"font": {"size": 48, "color": "#e0e0e0"}, "valueformat": ".3f"},
            "gauge": {
                "axis": {
                    "range": [0, 1],
                    "tickwidth": 2,
                    "tickcolor": "#444",
                    "tickfont": {"color": "#888"},
                },
                "bar": {"color": bar_color, "thickness": 0.3},
                "bgcolor": "#1a1a2e",
                "borderwidth": 1,
                "bordercolor": "#333",
                "steps": [
                    {"range": [0, 0.4636], "color": "rgba(0, 212, 170, 0.15)"},
                    {"range": [0.4636, 0.6732], "color": "rgba(240, 173, 78, 0.15)"},
                    {"range": [0.6732, 0.8029], "color": "rgba(255, 107, 53, 0.15)"},
                    {"range": [0.8029, 0.8781], "color": "rgba(255, 45, 45, 0.15)"},
                    {"range": [0.8781, 1.0], "color": "rgba(255, 0, 64, 0.25)"},
                ],
                "threshold": {
                    "line": {"color": "#fff", "width": 3},
                    "thickness": 0.8,
                    "value": score,
                },
            },
        }],
        "layout": {
            "paper_bgcolor": "transparent",
            "plot_bgcolor": "transparent",
            "font": {"color": "#e0e0e0"},
            "margin": {"t": 30, "b": 10, "l": 30, "r": 30},
            "height": 250,
        },
    }
    return json.dumps(config)


def heat_history_config(history: list[dict]) -> str:
    """Generate Plotly time series config for heat score history."""
    if not history:
        return json.dumps({"data": [], "layout": _empty_layout("No heat history yet")})

    timestamps = [h.get("timestamp", "") for h in history]
    scores = [h.get("score", 0) for h in history]
    levels = [h.get("level", "cool") for h in history]

    color_map = {
        "cool": "#00d4aa",
        "warm": "#f0ad4e",
        "hot": "#ff6b35",
        "critical": "#ff2d2d",
        "emergency": "#ff0040",
    }
    colors = [color_map.get(l, "#888") for l in levels]

    config = {
        "data": [{
            "type": "scatter",
            "x": timestamps,
            "y": scores,
            "mode": "lines+markers",
            "line": {"color": "#00d4aa", "width": 2},
            "marker": {"color": colors, "size": 5},
            "fill": "tozeroy",
            "fillcolor": "rgba(0, 212, 170, 0.1)",
        }],
        "layout": {
            "paper_bgcolor": "transparent",
            "plot_bgcolor": "transparent",
            "font": {"color": "#e0e0e0", "size": 11},
            "xaxis": {"gridcolor": "#222", "color": "#888"},
            "yaxis": {"gridcolor": "#222", "color": "#888", "range": [0, 1],
                       "title": "Heat Score"},
            "margin": {"t": 20, "b": 40, "l": 50, "r": 20},
            "height": 250,
            "shapes": [
                {"type": "line", "x0": timestamps[0] if timestamps else 0,
                 "x1": timestamps[-1] if timestamps else 1,
                 "y0": 0.7, "y1": 0.7,
                 "line": {"color": "#ff2d2d", "width": 1, "dash": "dot"}},
                {"type": "line", "x0": timestamps[0] if timestamps else 0,
                 "x1": timestamps[-1] if timestamps else 1,
                 "y0": 0.4, "y1": 0.4,
                 "line": {"color": "#f0ad4e", "width": 1, "dash": "dot"}},
            ],
        },
    }
    return json.dumps(config)


def correlation_heatmap_config(matrix: dict[str, dict[str, float]]) -> str:
    """Generate Plotly heatmap config for correlation matrix."""
    if not matrix:
        return json.dumps({"data": [], "layout": _empty_layout("No correlation data")})

    symbols = list(matrix.keys())
    z = []
    text = []
    for s1 in symbols:
        row = []
        text_row = []
        for s2 in symbols:
            val = matrix.get(s1, {}).get(s2, 0.0)
            row.append(round(val, 3))
            text_row.append(f"{val:.2f}")
        z.append(row)
        text.append(text_row)

    config = {
        "data": [{
            "type": "heatmap",
            "z": z,
            "x": symbols,
            "y": symbols,
            "text": text,
            "texttemplate": "%{text}",
            "textfont": {"size": 10, "color": "#e0e0e0"},
            "colorscale": [
                [0, "#1a3a5c"],
                [0.25, "#2a5a8c"],
                [0.5, "#1a1a2e"],
                [0.75, "#8c2a2a"],
                [1, "#cc3333"],
            ],
            "zmin": -1,
            "zmax": 1,
            "colorbar": {
                "title": "ρ",
                "titlefont": {"color": "#e0e0e0"},
                "tickfont": {"color": "#888"},
            },
        }],
        "layout": {
            "paper_bgcolor": "transparent",
            "plot_bgcolor": "transparent",
            "font": {"color": "#e0e0e0", "size": 11},
            "xaxis": {"side": "bottom", "tickangle": -45},
            "yaxis": {"autorange": "reversed"},
            "margin": {"t": 20, "b": 80, "l": 80, "r": 20},
            "height": 400,
        },
    }
    return json.dumps(config)


def mst_network_config(
    edges: list[dict],
    communities: list[dict],
    weights: dict[str, float] | None = None,
) -> str:
    """Generate Plotly network graph config for MST."""
    if not edges:
        return json.dumps({"data": [], "layout": _empty_layout("No MST data")})

    # Build node set and positions (circular layout)
    import math

    nodes: set[str] = set()
    for e in edges:
        nodes.add(e["source"])
        nodes.add(e["target"])
    node_list = sorted(nodes)
    n = len(node_list)

    # Circular layout
    pos = {}
    for i, node in enumerate(node_list):
        angle = 2 * math.pi * i / n
        pos[node] = (math.cos(angle), math.sin(angle))

    # Community color mapping
    community_colors = [
        "#00d4aa", "#ff6b35", "#4ecdc4", "#ff2d2d",
        "#45b7d1", "#f0ad4e", "#96ceb4", "#c084fc",
    ]
    node_color_map = {}
    for comm in communities:
        cid = comm.get("cluster_id", 0)
        color = community_colors[cid % len(community_colors)]
        for member in comm.get("members", []):
            node_color_map[member] = color

    # Edge traces
    edge_x, edge_y = [], []
    edge_text = []
    for e in edges:
        x0, y0 = pos.get(e["source"], (0, 0))
        x1, y1 = pos.get(e["target"], (0, 0))
        edge_x.extend([x0, x1, None])
        edge_y.extend([y0, y1, None])

    # Node traces
    node_x = [pos[n][0] for n in node_list]
    node_y = [pos[n][1] for n in node_list]
    node_colors = [node_color_map.get(n, "#888") for n in node_list]
    node_sizes = []
    for n in node_list:
        w = (weights or {}).get(n, 0.1)
        node_sizes.append(max(20, w * 200))

    node_text = []
    for n in node_list:
        w = (weights or {}).get(n, 0)
        node_text.append(f"{n}<br>Weight: {w:.1%}")

    config = {
        "data": [
            {
                "type": "scatter",
                "x": edge_x,
                "y": edge_y,
                "mode": "lines",
                "line": {"color": "rgba(136,136,136,0.4)", "width": 1.5},
                "hoverinfo": "none",
            },
            {
                "type": "scatter",
                "x": node_x,
                "y": node_y,
                "mode": "markers+text",
                "marker": {
                    "color": node_colors,
                    "size": node_sizes,
                    "line": {"color": "#333", "width": 1},
                },
                "text": node_list,
                "textposition": "top center",
                "textfont": {"color": "#e0e0e0", "size": 12, "family": "monospace"},
                "hovertext": node_text,
                "hoverinfo": "text",
            },
        ],
        "layout": {
            "paper_bgcolor": "transparent",
            "plot_bgcolor": "transparent",
            "font": {"color": "#e0e0e0"},
            "showlegend": False,
            "xaxis": {"visible": False},
            "yaxis": {"visible": False, "scaleanchor": "x"},
            "margin": {"t": 20, "b": 20, "l": 20, "r": 20},
            "height": 400,
        },
    }
    return json.dumps(config)


def factor_bars_config(exposures: list[dict]) -> str:
    """Generate Plotly horizontal bar chart for factor exposures."""
    if not exposures:
        return json.dumps({"data": [], "layout": _empty_layout("No factor data")})

    names = [e["factor_name"] for e in exposures]
    values = [e["total_exposure"] for e in exposures]
    colors = ["#00d4aa" if v >= 0 else "#ff2d2d" for v in values]

    config = {
        "data": [{
            "type": "bar",
            "x": values,
            "y": names,
            "orientation": "h",
            "marker": {"color": colors},
            "text": [f"{v:.3f}" for v in values],
            "textposition": "outside",
            "textfont": {"color": "#e0e0e0", "size": 11},
        }],
        "layout": {
            "paper_bgcolor": "transparent",
            "plot_bgcolor": "transparent",
            "font": {"color": "#e0e0e0", "size": 11},
            "xaxis": {"gridcolor": "#222", "color": "#888", "title": "Total Exposure"},
            "yaxis": {"color": "#888", "automargin": True},
            "margin": {"t": 20, "b": 40, "l": 100, "r": 60},
            "height": 300,
        },
    }
    return json.dumps(config)


def hrp_comparison_config(actual: dict[str, float], hrp: dict[str, float]) -> str:
    """Generate grouped bar chart comparing actual vs HRP weights."""
    if not actual and not hrp:
        return json.dumps({"data": [], "layout": _empty_layout("No weight data")})

    symbols = sorted(set(list(actual.keys()) + list(hrp.keys())))
    actual_vals = [actual.get(s, 0) * 100 for s in symbols]
    hrp_vals = [hrp.get(s, 0) * 100 for s in symbols]

    config = {
        "data": [
            {
                "type": "bar",
                "name": "Actual",
                "x": symbols,
                "y": actual_vals,
                "marker": {"color": "#45b7d1"},
            },
            {
                "type": "bar",
                "name": "HRP Optimal",
                "x": symbols,
                "y": hrp_vals,
                "marker": {"color": "#00d4aa"},
            },
        ],
        "layout": {
            "paper_bgcolor": "transparent",
            "plot_bgcolor": "transparent",
            "font": {"color": "#e0e0e0", "size": 11},
            "barmode": "group",
            "xaxis": {"color": "#888"},
            "yaxis": {"gridcolor": "#222", "color": "#888", "title": "Weight %"},
            "legend": {"font": {"color": "#e0e0e0"}, "bgcolor": "transparent"},
            "margin": {"t": 20, "b": 40, "l": 50, "r": 20},
            "height": 280,
        },
    }
    return json.dumps(config)


def _empty_layout(title: str) -> dict:
    """Return empty chart layout with message."""
    return {
        "paper_bgcolor": "transparent",
        "plot_bgcolor": "transparent",
        "font": {"color": "#888"},
        "annotations": [{
            "text": title,
            "xref": "paper",
            "yref": "paper",
            "x": 0.5,
            "y": 0.5,
            "showarrow": False,
            "font": {"size": 16, "color": "#666"},
        }],
        "height": 250,
    }
