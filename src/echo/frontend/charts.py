"""Plotly chart builders — one hue for magnitude, status colors for sentiment,
never a rainbow.

Every builder here picks its color job by what the chart's job actually is
(dataviz skill, `references/choosing-a-form.md`): comparing magnitude across
categories is a **sequential** job (one hue), and sentiment is a **state**, so
it gets the reserved good/neutral/critical status colors instead of arbitrary
categorical hues. Every figure sets a title + axis labels, a legend when it
carries more than one series, and a transparent background so it reads
correctly on Streamlit's own light or dark surface. Hex values are the
validated defaults from the dataviz skill's reference palette — swap them
there (and here) together if the brand ever changes.
"""

from __future__ import annotations

import plotly.graph_objects as go

_MUTED = "#898781"                    # axis/labels — legible on both light and dark surfaces
_GRID = "rgba(137,135,129,0.25)"
_SEQUENTIAL = "#2a78d6"                # one hue for magnitude bars (categorical slot 1 / blue)
_SEQ_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#1c5cab", "#0d366b"]  # light -> dark

# Sentiment is a state, not an arbitrary category — reserved status colors carry it
# consistently everywhere (split bar, trend line): good / neutral / critical.
STATUS = {"positive": "#0ca30c", "neutral": "#c3c2b7", "negative": "#d03b3b"}


def _layout(fig: go.Figure, title: str, xaxis: str, yaxis: str,
           showlegend: bool = False, height: int | None = None) -> go.Figure:
    """Apply the shared look (title, axis labels, transparent background, muted grid/legend) to a figure and return it."""
    fig.update_layout(
        title=title, xaxis_title=xaxis, yaxis_title=yaxis,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=_MUTED), showlegend=showlegend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
        margin=dict(l=10, r=10, t=50, b=10),
        height=height,
    )
    # automargin lets Plotly grow the margin to fit long tick labels (category names)
    # instead of clipping them against the fixed margins above.
    fig.update_xaxes(gridcolor=_GRID, zeroline=False, automargin=True)
    fig.update_yaxes(gridcolor=_GRID, zeroline=False, automargin=True)
    return fig


def magnitude_bar(data: list[dict], key_field: str, value_field: str,
                  title: str, xaxis: str, yaxis: str, horizontal: bool = False) -> go.Figure:
    """One-hue bar chart comparing magnitude across categories (sequential color job).

    ``xaxis``/``yaxis`` are always the titles for the chart's *actual* x/y axes in
    whichever orientation is requested — the caller picks them per-orientation, this
    function never needs to remap them.
    """
    # Sort biggest-value category first, so the chart reads as a ranked list.
    rows = sorted(data, key=lambda d: d[value_field], reverse=True)
    keys = [r[key_field] for r in rows]
    vals = [r[value_field] for r in rows]
    # Horizontal bars need more vertical room as more categories are added, so
    # grow the figure height with the row count instead of using a fixed size.
    height = max(350, 35 * len(keys) + 100) if horizontal else None
    if horizontal:
        fig = go.Figure(go.Bar(y=keys, x=vals, orientation="h", marker_color=_SEQUENTIAL,
                               hovertemplate="%{y}<br>%{x:,.2f}<extra></extra>"))
        # Plotly draws horizontal-bar categories bottom-to-top by default, which
        # would put the biggest value (sorted first) at the bottom; reverse the
        # axis so the biggest bar shows up top instead.
        fig.update_yaxes(autorange="reversed")
    else:
        fig = go.Figure(go.Bar(x=keys, y=vals, marker_color=_SEQUENTIAL,
                               hovertemplate="%{x}<br>%{y:,.2f}<extra></extra>"))
    return _layout(fig, title, xaxis, yaxis, height=height)


def sentiment_split_bar(sentiment: dict, title: str = "Sentiment split") -> go.Figure:
    """A single 100%-width horizontal stacked bar, one segment per sentiment (status color job)."""
    order = ["positive", "neutral", "negative"]
    # "or 1" avoids a divide-by-zero below when there is no data yet.
    total = sum(sentiment.get(k, 0) for k in order) or 1
    fig = go.Figure()
    # Add one stacked segment per sentiment, in a fixed order, so the bar always
    # reads positive/neutral/negative left to right regardless of input order.
    for k in order:
        v = sentiment.get(k, 0)
        fig.add_bar(y=["items"], x=[v], name=k.capitalize(), orientation="h", marker_color=STATUS[k],
                   hovertemplate=f"{k.capitalize()}: %{{x:,}} ({v / total:.0%})<extra></extra>")
    fig.update_layout(barmode="stack")
    return _layout(fig, title, "Items", "", showlegend=True)


def sentiment_trend(weekly: list[dict], title: str = "Sentiment over time") -> go.Figure:
    """One line per sentiment over weeks — status colors carry identity for the 3 series."""
    fig = go.Figure()
    for k, label in (("positive", "Positive"), ("neutral", "Neutral"), ("negative", "Negative")):
        fig.add_scatter(x=[r["week"] for r in weekly], y=[r[k] for r in weekly],
                        mode="lines+markers", name=label, line=dict(color=STATUS[k], width=2),
                        marker=dict(size=6), hovertemplate=f"{label}<br>%{{x}}: %{{y:,}}<extra></extra>")
    return _layout(fig, title, "Week", "Items", showlegend=True)


def crosstab_heatmap(data: dict[str, dict[str, int]], title: str = "Category x source") -> go.Figure:
    """Sequential single-hue heatmap — magnitude per cell, category x source."""
    categories = sorted(data.keys())
    # Collect every source that appears anywhere in the data (not just under one
    # category), so the heatmap has one column per source across all rows.
    sources = sorted({s for row in data.values() for s in row})
    # Build a 2D grid (rows = categories, columns = sources) of counts, filling
    # in 0 for any category/source pair that has no data.
    z = [[data.get(c, {}).get(s, 0) for s in sources] for c in categories]
    fig = go.Figure(go.Heatmap(
        z=z, x=sources, y=categories,
        # Spread the fixed light-to-dark color ramp evenly across the 0-1 range
        # Plotly expects for a colorscale.
        colorscale=[[i / (len(_SEQ_RAMP) - 1), c] for i, c in enumerate(_SEQ_RAMP)],
        hovertemplate="%{y} x %{x}: %{z:,}<extra></extra>", colorbar=dict(title="items"),
    ))
    height = max(400, 30 * len(categories) + 150)
    return _layout(fig, title, "Source", "Category", height=height)
