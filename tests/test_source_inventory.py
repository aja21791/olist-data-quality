"""Estas pruebas usan CSV sintéticos; NO son resultados del dataset Olist."""
import csv
import json
from pathlib import Path

import pytest

from src.config import EXPECTED_CSV_FILES
from src.source_inventory import build_inventory, csv_inventory


def test_source_inventory_counts_quoted_multiline_fields(tmp_path: Path):
    csv_file = tmp_path / "example.csv"
    with csv_file.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(["order_id", "message"])
        writer.writerow(["abc", "texto\ncon salto"])
        writer.writerow(["def", "otro texto"])
    result = csv_inventory(csv_file)
    assert result["records_count"] == 2
    assert result["columns_count"] == 2
    assert result["row_width_mismatches"] == 0
    assert len(result["sha256"]) == 64


def test_duplicate_column_names_are_rejected(tmp_path: Path):
    csv_file = tmp_path / "duplicated.csv"
    csv_file.write_text("x,x\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Encabezados duplicados"):
        csv_inventory(csv_file)


def test_partial_inventory_can_be_explicitly_enabled(tmp_path: Path):
    raw = tmp_path / "raw"
    reports = tmp_path / "reports"
    raw.mkdir()
    (raw / "one.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    result = build_inventory(raw, reports, require_all_expected=False)
    assert len(result) == 1
    assert (reports / "source_inventory.csv").is_file()
    assert json.loads((reports / "source_inventory.json").read_text(encoding="utf-8"))[0]["records_count"] == 1


def test_incomplete_olist_dataset_is_not_considered_complete(tmp_path: Path):
    raw = tmp_path / "raw"
    reports = tmp_path / "reports"
    raw.mkdir()
    (raw / EXPECTED_CSV_FILES[0]).write_text("x\n1\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="Faltan archivos"):
        build_inventory(raw, reports, require_all_expected=True)
    assert not reports.exists()
