"""Locked, pre-registered specification for the VanEck allocation study.

Everything a backtest can depend on is defined here *before* any result is
looked at.  Changing a value in this file after seeing results is a protocol
violation; record such changes (and why) in docs/SPEC.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
REPORT_DIR = ROOT / "reports"

# --------------------------------------------------------------------------- #
# Universe
# --------------------------------------------------------------------------- #
UNIVERSE: tuple[str, ...] = ("SMH", "MOAT", "GDX", "ANGL", "REMX", "HAP", "FLTR", "PFXF", "OUNZ")
EQUITY_FUNDS: tuple[str, ...] = ("SMH", "MOAT", "GDX", "REMX", "HAP")
INCOME_FUNDS: tuple[str, ...] = ("ANGL", "FLTR", "PFXF")
COMMODITY_FUNDS: tuple[str, ...] = ("OUNZ",)

# Auxiliary tradeable instruments (benchmarks / hedges), not part of the universe.
AUX_TICKERS: tuple[str, ...] = ("SPY", "BND", "VGIT")

# Macro series used only for regime classification and the risk-free rate.
MACRO_TICKERS: dict[str, str] = {
    "spx": "^GSPC",     # S&P 500 index level
    "vix": "^VIX",      # CBOE volatility index
    "tnx": "^TNX",      # 10-year Treasury yield (percent)
    "irx": "^IRX",      # 13-week T-bill discount yield (percent)
    "crb": "^SPGSCI",   # S&P GSCI, proxy for the CRB commodity index (CRB not on Yahoo)
}

CASH = "CASH"  # synthetic money-market sleeve

THEME_OF: dict[str, str] = {
    "SMH": "semiconductors", "MOAT": "US quality equity", "GDX": "gold miners",
    "ANGL": "fallen-angel high yield", "REMX": "rare earths / strategic metals",
    "HAP": "natural resources equity", "FLTR": "floating-rate notes",
    "PFXF": "preferred securities", "OUNZ": "gold bullion",
}


@dataclass(frozen=True)
class Spec:
    # ---- sample ---------------------------------------------------------- #
    macro_start: str = "2000-01-03"      # long history so S&P drawdown/YoY are well defined
    fund_start: str = "2014-05-16"       # OUNZ inception = common start of the 9 funds
    study_end: str = "2024-12-31"        # main backtest window ends here (plan: 2019-2024)
    holdout_start: str = "2025-01-01"    # untouched until Phase 3 validation
    # ---- walk-forward ---------------------------------------------------- #
    train_years: int = 3                 # covariance / parameter window (lookback)
    test_years: int = 1                  # out-of-sample evaluation unit
    roll_months: int = 1                 # roll forward monthly
    signal_lookback: int = 60            # trading days for momentum / drift / mu
    # ---- execution ------------------------------------------------------- #
    # Signals are computed on the last close of the prior month (the "signal date");
    # trades execute at the close of the first trading day of the month.
    exec_lag_days: int = 1
    # ---- position limits ------------------------------------------------- #
    w_min: float = 0.05                  # per-fund floor for allocation rules
    w_max: float = 0.20                  # per-fund cap for allocation rules
    scaffold_cap: float = 0.15           # Phase-1 scaffold cap (binding for nothing at 1/9)
    top_n: int = 3                       # momentum / drift favoured funds
    drift_lo: float = 0.15               # drift-signal weight band for favoured funds
    drift_hi: float = 0.20
    other_cap: float = 0.10              # cap for non-favoured funds when a signal is active
    # ---- cash / risk-free ------------------------------------------------ #
    # "irx" = 13-week T-bill yield (primary, see SPEC.md); "fixed" = plan's flat 4%.
    cash_yield_mode: str = "irx"
    cash_yield_fixed: float = 0.04
    # ---- transaction costs ----------------------------------------------- #
    # Full quoted spread (bps of price) by trailing-60-day average dollar volume tier.
    spread_tiers_bps: tuple[tuple[float, float], ...] = (
        (500e6, 2.0), (100e6, 4.0), (10e6, 10.0), (1e6, 20.0), (0.0, 40.0),
    )
    slippage_bps: float = 0.5            # one-way, on top of half the spread
    fee_per_trade: float = 0.001         # dollars, regulatory (plan)
    aum: float = 10e6                    # dollars, for per-trade fee scaling
    adv_lookback: int = 60
    # ---- taxes ----------------------------------------------------------- #
    tax_rate: float = 0.37               # single rate on realized gains and distributions
    tax_distributions: bool = True
    lot_method: str = "FIFO"             # or "HIFO"
    wash_sale_days: int = 30
    wash_sale_groups: tuple[tuple[str, ...], ...] = ()  # substantially-identical groups
    # ---- regimes (locked; precedence = order below) ---------------------- #
    crisis_vix: float = 25.0
    crisis_drawdown: float = -0.20
    stag_crb_yoy: float = 0.20
    stag_tnx: float = 4.0
    bull_vix: float = 15.0
    corr_vix_lo: float = 15.0
    corr_vix_hi: float = 25.0
    corr_ytd_band: float = 0.10
    correction_scale: float = 0.75
    hedge_basket: tuple[str, ...] = ("OUNZ", "GDX", "VGIT")
    stagflation_tilt: tuple[str, ...] = ("ANGL", "GDX", "FLTR")
    # ---- statistics ------------------------------------------------------ #
    n_tests: int = 6
    alpha: float = 0.05
    # ---- gates ----------------------------------------------------------- #
    gate0_avg_corr_max: float = 0.75
    gate0_min_themes: int = 5
    gate0_redundancy_corr: float = 0.95
    gate0_cluster_corr: float = 0.60     # funds with rho >= this are the same theme
    gate1_sharpe_margin: float = 0.20
    gate1_mdd_floor: float = -0.40
    gate1_turnover_max: float = 0.15
    gate1_posttax_sharpe_min: float = 0.30
    gate3_crisis_mdd_tolerance: float = 0.05

    @property
    def bonferroni_alpha(self) -> float:
        return self.alpha / self.n_tests


SPEC = Spec()
