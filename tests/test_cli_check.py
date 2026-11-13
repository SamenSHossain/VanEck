"""The check command: staleness against the clock or against the data, and its exit codes."""
import pytest

from vaneck import cli


@pytest.fixture
def isolated(monkeypatch, tmp_path, synth):
    """Run cmd_check on the synthetic data with every output redirected away from the repository."""
    class NullStore:
        def record_health(self, payload):
            pass

        def close(self):
            pass

    monkeypatch.setattr(cli.data, "build_market_data", lambda *a, **k: synth)
    monkeypatch.setattr(cli, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(cli, "_store", lambda: NullStore())
    monkeypatch.setattr(cli, "_notify", lambda *a, **k: None)
    return tmp_path


def test_check_judges_a_frozen_cache_against_its_last_bar(isolated, capsys):
    # the synthetic data ends in 2015: against the clock it is years stale and blocks
    assert cli.main(["check", "--json"]) == 2
    assert '"bar_staleness_days"' in capsys.readouterr().out
    # against its own last bar it is not stale
    assert cli.main(["check", "--json", "--now", "data"]) == 0
    assert (isolated / "health.json").exists()


def test_check_now_accepts_a_date(isolated, synth):
    last = synth.index[-1]
    assert cli.main(["check", "--now", str((last + __import__("pandas").Timedelta(days=4)).date())]) == 0
    assert cli.main(["check", "--now", str((last + __import__("pandas").Timedelta(days=11)).date())]) == 2
