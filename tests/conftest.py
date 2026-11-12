import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from vaneck.data import make_synthetic


@pytest.fixture(scope="session")
def synth():
    return make_synthetic(n_days=1500, seed=42)
