import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from vaneck.data import make_synthetic


@pytest.fixture(scope="session")
def synth():
    return make_synthetic(n_days=1500, seed=42)


@pytest.fixture(autouse=True)
def _isolated_operator_state(monkeypatch, tmp_path_factory):
    """No test reads or writes the repository's state/ folder or depends on the operator's VANECK_* settings.

    The decision pipeline and the monitor look up vaneck.store.STATE_DIR when they run, so a halt flag, a
    retirement flag or VANECK_REQUIRE_SIGNED=1 on an operator's machine would otherwise change test results.
    """
    from vaneck import store
    monkeypatch.setattr(store, "STATE_DIR", tmp_path_factory.mktemp("state"))
    for key in [k for k in os.environ if k.startswith("VANECK_")]:
        monkeypatch.delenv(key)
