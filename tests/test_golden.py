"""Golden regression: the refactored engine must reproduce the committed main-study numbers."""
from pathlib import Path

import pandas as pd
import pytest

from vaneck import data, study
from vaneck.config import RAW_DIR

GOLDEN = Path(__file__).with_name("golden_gate1_baselines_summary.csv")
COLS = ["cagr_pre", "cagr_post", "sharpe_pre", "sharpe_post", "mdd_post", "turnover_pre", "cost_bps_yr", "tax_pct_yr"]


@pytest.mark.skipif(not (RAW_DIR / "SMH.csv").exists(), reason="raw data cache not present")
def test_baselines_match_golden():
    md = data.build_market_data()
    splits = study.make_splits(md)
    runs = study.run_baselines(md, splits)
    got = study.summary_table(runs)[COLS]
    want = pd.read_csv(GOLDEN, index_col=0)[COLS].loc[got.index]
    pd.testing.assert_frame_equal(got.astype(float), want.astype(float), rtol=1e-6, atol=1e-9)
