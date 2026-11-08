"""Golden regression: key numbers may only change together with a visible input change."""
import pytest

from vaneck import data, golden
from vaneck.config import RAW_DIR

pytestmark = pytest.mark.skipif(not (RAW_DIR / "SMH.csv").exists() or not (golden.GOLDEN_DIR / "manifest.json").exists(),
                                reason="raw data cache or golden files not present")


def test_golden_inputs_unchanged():
    manifest, _ = golden.load()
    changed = golden.inputs_changed(manifest)
    assert not changed, ("inputs changed since the goldens were frozen: " + "; ".join(changed)
                         + ". Review the change, then run `python -m vaneck golden --update` and commit the diff.")


def test_golden_results_match():
    manifest, frames = golden.load()
    if golden.inputs_changed(manifest):
        pytest.skip("inputs changed; test_golden_inputs_unchanged reports it")
    md = data.build_market_data()
    problems = golden.compare(golden.compute(md), frames)
    assert not problems, "engine regression (inputs unchanged but results differ):\n" + "\n".join(problems)
