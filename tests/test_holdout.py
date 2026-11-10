"""The hold-out is sealed, opens once with a record, and stays frozen. Synthetic data only: the real one stays shut."""
from dataclasses import replace

import pytest

from vaneck import holdout
from vaneck.config import SPEC


@pytest.fixture()
def spec(synth):
    first, last_study_day, cut = (str(synth.index[i].date()) for i in (0, -261, -260))
    return replace(SPEC, fund_start=first, study_end=last_study_day, holdout_start=cut)


def test_status_is_sealed_and_counts_only_the_calendar(synth, spec, tmp_path):
    st = holdout.status(synth, spec, tmp_path)
    assert st["state"] == "sealed" and st["trading_days"] == 260 and st["rebalances"] >= 11
    assert st["opened_by"] is None and st["results_stored"] is False
    assert not list(tmp_path.iterdir())                       # looking at the seal writes nothing
    with pytest.raises(RuntimeError):
        holdout.complete(synth, spec, tmp_path)


def test_opening_needs_a_name_and_a_reason(synth, spec, tmp_path):
    for by, reason in ((None, "why"), ("ann", None), ("ann", "   "), ("", "why")):
        with pytest.raises(ValueError):
            holdout.unseal(synth, by, reason, spec, tmp_path, n_boot=20)
    assert holdout.status(synth, spec, tmp_path)["state"] == "sealed"


def test_it_opens_once_and_the_results_are_frozen(synth, spec, tmp_path):
    out = holdout.unseal(synth, "ann", "final look", spec, tmp_path, n_boot=20)
    st = holdout.status(synth, spec, tmp_path)
    assert st["state"] == "opened" and st["opened_by"] == "ann" and st["reason"] == "final look" and st["results_stored"]
    assert st["evaluated_through"] == str(synth.index[-1].date())
    summary = {r["name"] for r in out["results"]["summary"]}
    assert "EqualWeight" in summary and len(summary) == 10          # four baselines and the six locked rules
    assert len(out["results"]["margins"]) == 2 and out["results"]["n_rebalances"] == st["rebalances"]
    assert out["results"]["window"][0] >= spec.holdout_start
    frozen = (tmp_path / holdout.RESULTS).read_text()
    with pytest.raises(holdout.AlreadyOpened):
        holdout.unseal(synth, "bob", "second look", spec, tmp_path, n_boot=20)
    assert holdout.complete(synth, spec, tmp_path) == holdout.stored(tmp_path)
    assert (tmp_path / holdout.RESULTS).read_text() == frozen and holdout.status(synth, spec, tmp_path)["opened_by"] == "ann"


def test_the_record_is_written_before_any_number(synth, spec, tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("crash while evaluating")

    monkeypatch.setattr(holdout, "evaluate", boom)
    with pytest.raises(RuntimeError, match="crash"):
        holdout.unseal(synth, "ann", "look", spec, tmp_path, n_boot=20)
    st = holdout.status(synth, spec, tmp_path)
    assert st["state"] == "opened" and st["results_stored"] is False   # the look is on record even though it failed
    monkeypatch.undo()
    results = holdout.complete(synth, spec, tmp_path)                  # finishing it is allowed; reopening is not
    assert results["window"][1] == st["evaluated_through"] and holdout.stored(tmp_path) == results
