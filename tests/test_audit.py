from dataclasses import replace

from vaneck import audit
from vaneck.config import SPEC


def test_config_hash_is_stable_and_sensitive():
    assert audit.config_hash(SPEC) == audit.config_hash(SPEC)
    assert audit.config_hash(replace(SPEC, w_max=0.25)) != audit.config_hash(SPEC)


def test_data_hash_deterministic(tmp_path):
    (tmp_path / "a.csv").write_text("x,y\n1,2\n")
    (tmp_path / "b.csv").write_text("x,y\n3,4\n")
    h1 = audit.data_hash(tmp_path)
    (tmp_path / "b.csv").write_text("x,y\n3,5\n")
    assert h1 != audit.data_hash(tmp_path)
    assert len(h1) == 16


def test_run_manifest_keys():
    m = audit.run_manifest("test", extra={"k": 1})
    for key in ("run_id", "created_utc", "command", "git", "config_hash", "data_hash", "versions", "k"):
        assert key in m
