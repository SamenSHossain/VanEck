"""Markdown tables and static figures for the study report."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import dates as mdates  # noqa: E402
from matplotlib.colors import ListedColormap  # noqa: E402

# Validated reference palette (see dataviz skill): categorical slots in fixed order.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQ_BLUE = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
            "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
REGIME_RAMP = {"Bull": "#86b6ef", "Neutral": "#5598e7", "Correction": "#2a78d6", "Crisis": "#184f95",
               "Stagflation": "#0d366b"}  # ordinal by stress, validated --ordinal
SURFACE, INK, INK2, MUTED, GRID, AXIS, DEEMPH = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#c3c2b7"

plt.rcParams.update({
    "font.family": "sans-serif", "font.size": 9, "axes.titlesize": 11, "axes.titleweight": "bold",
    "axes.labelsize": 9, "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "xtick.color": MUTED,
    "ytick.color": MUTED, "text.color": INK, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE, "legend.frameon": False, "legend.fontsize": 8,
})


# --------------------------------------------------------------------------- #
# Markdown helpers
# --------------------------------------------------------------------------- #
def _fmt(v, nd=3):
    if isinstance(v, (bool, np.bool_)):
        return "yes" if v else "no"
    if isinstance(v, (int, np.integer)):
        return f"{v:d}"
    if isinstance(v, (float, np.floating)):
        if np.isnan(v):
            return ""
        return f"{v:.{nd}f}"
    return str(v)


def md_table(df: pd.DataFrame, nd: int = 3, index: bool = True, pct_cols: tuple[str, ...] = ()) -> str:
    df = df.copy()
    if isinstance(df.index, pd.MultiIndex):
        df = df.reset_index()
        index = False
    df = df.dropna(how="all")
    cols = list(df.columns)
    int_like = set()
    for c in cols:
        col = df[c]
        if pd.api.types.is_float_dtype(col) and col.notna().any() and np.all(np.mod(col.dropna(), 1) == 0) \
                and c not in pct_cols:
            int_like.add(c)
    header = ([str(df.index.name or "")] if index else []) + [str(c) for c in cols]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for idx, row in df.iterrows():
        cells = [str(idx)] if index else []
        for c in cols:
            v = row[c]
            if c in pct_cols and isinstance(v, (float, np.floating)) and not np.isnan(v):
                cells.append(f"{100 * v:.1f}%")
            elif c in int_like and isinstance(v, (float, np.floating)) and not np.isnan(v):
                cells.append(f"{int(v):d}")
            else:
                cells.append(_fmt(v, nd))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Figure helpers
# --------------------------------------------------------------------------- #
def _ax(figsize=(9, 4.5)):
    fig, ax = plt.subplots(figsize=figsize, dpi=150)
    style(ax)
    return fig, ax


def style(ax, grid_axis="y"):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(AXIS)
        ax.spines[s].set_linewidth(1)
    ax.grid(axis=grid_axis, color=GRID, linewidth=1, linestyle="-")
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def _years(ax):
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))


_NICE = [20, 30, 40, 50, 60, 75, 100, 125, 150, 200, 250, 300, 400, 500, 600, 800, 1000, 1500, 2000, 2500,
         3000, 4000, 5000, 6000, 8000, 10000]


def _log_ticks(ax, lo: float, hi: float, fmt: str = "{:.0f}"):
    """Readable major ticks on a log axis (matplotlib's defaults vanish inside one decade)."""
    ticks = [t for t in _NICE if lo * 0.98 <= t <= hi * 1.02]
    while len(ticks) > 7:
        ticks = ticks[::2]
    ax.set_yscale("log")
    ax.yaxis.set_major_locator(matplotlib.ticker.FixedLocator(ticks))
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: fmt.format(v)))
    ax.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())


def _legend_above(ax, title: str, ncol: int):
    ax.set_title(title, loc="left", pad=30)
    ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=ncol, borderaxespad=0.2, handlelength=1.6)


def fig_corr_heatmap(corr: pd.DataFrame, path: Path, title: str):
    fig, ax = plt.subplots(figsize=(6.4, 5.6), dpi=150)
    cmap = ListedColormap(SEQ_BLUE)
    im = ax.imshow(corr.values, cmap=cmap, vmin=0.0, vmax=1.0)
    n = len(corr)
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(corr.columns, rotation=0)
    ax.set_yticklabels(corr.index)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.tick_params(length=0)
    for i in range(n):
        for j in range(n):
            v = corr.values[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7.5,
                    color="#ffffff" if v > 0.55 else INK)
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03)
    cb.outline.set_visible(False)
    cb.ax.tick_params(length=0, colors=MUTED)
    cb.set_label("daily-return correlation", color=INK2)
    ax.set_title(title, loc="left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_rolling_corr(rolling_avg: pd.Series, threshold: float, path: Path, title: str):
    fig, ax = _ax()
    ax.plot(rolling_avg.index, rolling_avg.values, color=PALETTE[0], linewidth=2, solid_joinstyle="round")
    ax.axhline(threshold, color=MUTED, linewidth=1)
    ax.text(rolling_avg.index[0], threshold + 0.01, f"plan limit {threshold:.2f}", color=INK2, fontsize=8, va="bottom")
    last = rolling_avg.dropna()
    ax.plot([last.index[-1]], [last.iloc[-1]], "o", color=PALETTE[0], markersize=8,
            markeredgecolor=SURFACE, markeredgewidth=2)
    ax.annotate(f"{last.iloc[-1]:.2f}", (last.index[-1], last.iloc[-1]), xytext=(6, 0),
                textcoords="offset points", va="center", fontsize=8, color=INK2)
    ax.set_ylim(0, 1)
    ax.set_ylabel("average pairwise correlation (60-day)")
    _years(ax)
    ax.set_title(title, loc="left")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _end_label(ax, x, y, text, color=INK2):
    ax.annotate(text, (x, y), xytext=(6, 0), textcoords="offset points", va="center", fontsize=8, color=color)


def fig_nav(navs: dict[str, pd.Series], path: Path, title: str, emphasis: list[str] | None = None,
            labels: dict[str, str] | None = None, log: bool = True):
    """Indexed growth of 100; emphasised series in categorical slots, the rest in gray."""
    labels = labels or {}
    emphasis = emphasis if emphasis is not None else list(navs)
    fig, ax = _ax((9.5, 5.2))
    slot = 0
    gray_label = None
    n_gray = sum(1 for n in navs if n not in emphasis)
    lo, hi = np.inf, -np.inf
    for name, nav in navs.items():
        idx = 100.0 * nav / nav.iloc[0]
        lo, hi = min(lo, idx.min()), max(hi, idx.max())
        if name in emphasis:
            color = PALETTE[slot % len(PALETTE)]
            slot += 1
            ax.plot(idx.index, idx.values, color=color, linewidth=2, label=labels.get(name, name), zorder=3)
            ax.plot([idx.index[-1]], [idx.iloc[-1]], "o", color=color, markersize=8, markeredgecolor=SURFACE,
                    markeredgewidth=2, zorder=4)
            _end_label(ax, idx.index[-1], idx.iloc[-1], f"{idx.iloc[-1]:.0f}")
        else:
            if gray_label is None:
                gray_label = f"{n_gray} rule-based strategies (see table)" if n_gray > 1 else labels.get(name, name)
                ax.plot(idx.index, idx.values, color=DEEMPH, linewidth=1.2, label=gray_label, zorder=2)
            else:
                ax.plot(idx.index, idx.values, color=DEEMPH, linewidth=1.2, zorder=2)
    if log:
        _log_ticks(ax, lo, hi)
    ax.set_ylabel("growth of 100 (log scale)" if log else "growth of 100")
    _years(ax)
    _legend_above(ax, title, ncol=min(4, len(ax.get_legend_handles_labels()[0])))
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_drawdown(navs: dict[str, pd.Series], path: Path, title: str, labels: dict[str, str] | None = None):
    labels = labels or {}
    fig, ax = _ax((9.5, 3.8))
    for k, (name, nav) in enumerate(navs.items()):
        dd = nav / nav.cummax() - 1.0
        color = PALETTE[k % len(PALETTE)]
        ax.plot(dd.index, dd.values, color=color, linewidth=1.6, label=labels.get(name, name))
        if k == 0:
            ax.fill_between(dd.index, dd.values, 0, color=color, alpha=0.10, linewidth=0)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    ax.set_ylabel("drawdown from peak")
    _years(ax)
    _legend_above(ax, title, ncol=3)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_sharpe_dumbbell(tbl: pd.DataFrame, path: Path, title: str):
    """Pre-tax -> post-tax Sharpe per run (one hue, two shades)."""
    tbl = tbl.sort_values("sharpe_post")
    fig, ax = plt.subplots(figsize=(8.5, 0.42 * len(tbl) + 1.6), dpi=150)
    style(ax, grid_axis="x")
    y = np.arange(len(tbl))
    for i, (name, r) in enumerate(tbl.iterrows()):
        ax.plot([r.sharpe_pre, r.sharpe_post], [i, i], color=AXIS, linewidth=2, zorder=1)
    ax.scatter(tbl.sharpe_pre, y, s=70, color="#86b6ef", edgecolor=SURFACE, linewidth=2, zorder=3, label="pre-tax")
    ax.scatter(tbl.sharpe_post, y, s=70, color=PALETTE[0], edgecolor=SURFACE, linewidth=2, zorder=4, label="post-tax")
    for i, v in enumerate(tbl.sharpe_post):
        ax.annotate(f"{v:.2f}", (v, i), xytext=(0, -11), textcoords="offset points", ha="center", fontsize=7.5, color=INK2)
    ax.set_yticks(y)
    ax.set_yticklabels(tbl.index)
    ax.axvline(0, color=AXIS, linewidth=1)
    ax.set_xlabel("annualised Sharpe ratio (excess over 13-week T-bill)")
    ax.set_title(title, loc="left")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_regimes(regimes: pd.Series, spx: pd.Series, path: Path, title: str):
    fig, ax = _ax((9.5, 4))
    r = regimes.dropna()
    change = (r != r.shift(1)).cumsum()
    seen = set()
    for _, chunk in r.groupby(change):
        name = chunk.iloc[0]
        ax.axvspan(chunk.index[0], chunk.index[-1] + pd.Timedelta(days=1), color=REGIME_RAMP.get(name, MUTED),
                   alpha=0.35, linewidth=0, label=name if name not in seen else None)
        seen.add(name)
    s = spx.reindex(r.index).ffill()
    ax.plot(s.index, s.values, color=INK2, linewidth=1.4)
    _log_ticks(ax, float(s.min()), float(s.max()), fmt="{:,.0f}")
    ax.set_ylabel("S&P 500 (log scale)")
    _years(ax)
    ax.set_title(title, loc="left", pad=30)
    order = [k for k in ("Bull", "Neutral", "Correction", "Crisis", "Stagflation") if k in seen]
    handles, labels_ = ax.get_legend_handles_labels()
    by = dict(zip(labels_, handles))
    ax.legend([by[k] for k in order], order, loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=len(order),
              borderaxespad=0.2, handlelength=1.6)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_annual_dots(annual: pd.DataFrame, path: Path, title: str, labels: dict[str, str] | None = None):
    labels = labels or {}
    annual = annual.dropna(how="all")
    fig, ax = _ax((9, 4.2))
    ax.axhline(0, color=AXIS, linewidth=1)
    x = np.arange(len(annual.index))
    k = len(annual.columns)
    for j, col in enumerate(annual.columns):
        off = (j - (k - 1) / 2) * 0.18
        ax.plot(x + off, annual[col].values, "o", color=PALETTE[j % len(PALETTE)], markersize=9,
                markeredgecolor=SURFACE, markeredgewidth=2, label=labels.get(col, col))
    ax.set_xticks(x)
    ax.set_xticklabels([str(i) for i in annual.index])
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    ax.set_ylabel("calendar-year return (post-tax)")
    _legend_above(ax, title, ncol=min(k, 4))
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
