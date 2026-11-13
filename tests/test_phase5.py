"""Regresiones de Fase 5; integración con DuckDB usa el fixture sintético de Fase 4."""
from pathlib import Path
import pytest

from src.transform_schema import SCHEMA, KEYS, REQUIRED
from src.validate import PAYMENT_TYPES, ORDER_STATUSES, sql_file, query, validate_clean


def test_phase5_sql_files_exist_and_use_safe_templates():
    expected = (
        '01_uniqueness.sql', '02_required.sql', '03_orphans.sql',
        '04_predicate.sql', '05_payments.sql', '06_payment_details.sql',
        '07_iqr.sql',
    )
    for name in expected:
        assert sql_file(name).lstrip().startswith('--')
    assert 'NOT EXISTS' in sql_file('03_orphans.sql')
    assert 'GROUP BY order_id' in sql_file('05_payments.sql')
    assert 'HAVING COUNT(*) > 1' in sql_file('01_uniqueness.sql')


def test_phase5_uses_proven_keys_and_tables():
    assert set(KEYS).issubset(SCHEMA)
    assert set(REQUIRED).issubset(SCHEMA)
    assert 'geolocation' not in KEYS
    assert 'order_reviews' not in KEYS
    assert {'credit_card', 'voucher', 'boleto'}.issubset(PAYMENT_TYPES)
    assert 'delivered' in ORDER_STATUSES


def test_validation_sql_quotes_compound_identifier_as_single_name():
    seen = []

    class FakeCon:
        def execute(self, statement):
            seen.append(statement)
            return self

        def fetchone(self):
            return (0,)

    query(FakeCon(), '01_uniqueness.sql', table='"order_items"',
          keys='"order_id", "order_item_id"')
    assert 'clean."order_items"' in seen[0]
    assert '"order_id", "order_item_id"' in seen[0]


@pytest.fixture()
def synthetic_loaded_db(tmp_path):
    pytest.importorskip('duckdb', reason='Instalar duckdb en entorno olist-quality')
    from test_phase4 import synthetic_db as fixture_fn
    # Usar la misma definición real de fixture y no duplicar nueve tablas manualmente.
    db = fixture_fn.__wrapped__(tmp_path)
    from src.transform import transform_data
    transform_data(db, tmp_path / 'phase4_reports')
    return db


def test_phase5_validates_synthetic_data_without_modifying_clean(synthetic_loaded_db, tmp_path):
    import duckdb
    with duckdb.connect(str(synthetic_loaded_db), read_only=True) as con:
        before = con.execute('SELECT * FROM clean.order_items ORDER BY order_item_id').fetchall()
    result = validate_clean(synthetic_loaded_db, tmp_path / 'phase5_reports')
    assert result['critical_failures'] == 0
    assert result['status'] in ('PASS', 'PASS_WITH_WARNINGS')
    assert any(r['check_id'] == 'V06.KEY.order_items' and r['status'] == 'PASS'
               for r in result['checks'])
    assert any(r['check_id'] == 'V00.QUARANTINE_REVIEW' and r['status'] == 'WARNING'
               for r in result['checks'])
    with duckdb.connect(str(synthetic_loaded_db), read_only=True) as con:
        after = con.execute('SELECT * FROM clean.order_items ORDER BY order_item_id').fetchall()
        assert before == after
        assert con.execute('SELECT COUNT(*) FROM audit.validation_results').fetchone()[0] == len(result['checks'])
    outdir = tmp_path / 'phase5_reports'
    assert (outdir / 'phase5_validation_report.md').exists()
    assert (outdir / 'phase5_validation_results.csv').exists()
    assert not list((tmp_path).rglob('*.parquet'))


def test_phase5_blocks_real_orphan_in_synthetic_clean(synthetic_loaded_db, tmp_path):
    import duckdb
    with duckdb.connect(str(synthetic_loaded_db)) as con:
        con.execute("UPDATE clean.order_items SET product_id='product_missing' WHERE order_item_id=2")
    result = validate_clean(synthetic_loaded_db, tmp_path / 'phase5_reports')
    assert result['status'] == 'BLOCKED'
    assert result['critical_failures'] >= 1
    fk_check = next(r for r in result['checks']
                    if r['check_id'] == 'V12.FK.order_items.product_id')
    assert fk_check['status'] == 'FAIL' and fk_check['violations'] == 1
    with duckdb.connect(str(synthetic_loaded_db), read_only=True) as con:
        assert con.execute('SELECT status FROM audit.validation_runs').fetchone()[0] == 'BLOCKED'
    assert (tmp_path / 'phase5_reports' / 'phase5_validation_report.md').is_file()


def test_phase5_is_repeatable_without_rebuilding_clean(synthetic_loaded_db, tmp_path):
    import duckdb
    first = validate_clean(synthetic_loaded_db, tmp_path / 'reports')
    second = validate_clean(synthetic_loaded_db, tmp_path / 'reports')
    assert first['validation_run_id'] != second['validation_run_id']
    assert [(r['check_id'], r['violations']) for r in first['checks']] == [
        (r['check_id'], r['violations']) for r in second['checks']
    ]
    with duckdb.connect(str(synthetic_loaded_db), read_only=True) as con:
        assert con.execute('SELECT COUNT(*) FROM audit.validation_runs').fetchone()[0] == 2
