"""Verifica el mecanismo de inicialización con una BD temporal."""
import pytest


def test_bootstrap_defines_only_raw_clean_and_audit(monkeypatch, tmp_path):
    duckdb = pytest.importorskip("duckdb")
    from src import bootstrap as module
    db_file = tmp_path / "test.duckdb"
    monkeypatch.setattr(module, "DB_PATH", db_file)
    monkeypatch.setattr(module, "ensure_directories", lambda: None)
    assert module.bootstrap() == str(db_file)
    assert module.bootstrap() == str(db_file)  # idempotencia de bootstrap
    con = duckdb.connect(str(db_file))
    try:
        names = {row[0] for row in con.execute(
            "SELECT schema_name FROM information_schema.schemata"
        ).fetchall()}
        assert {"raw", "clean", "audit"}.issubset(names)
        assert "staging" not in names
    finally:
        con.close()
