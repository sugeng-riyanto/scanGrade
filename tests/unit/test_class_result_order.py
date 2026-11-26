"""Result documents use natural class order and alphabetical hand-back order."""
from unittest.mock import MagicMock

from openpyxl import load_workbook

from app.services.export_service import export_to_xlsx
from app.services.result_order import arrange


def test_xlsx_orders_by_class_then_name_and_keeps_unknown_class_last():
    db = MagicMock()
    classes = db.table.return_value.select.return_value.eq.return_value
    classes.in_.return_value.execute.return_value.data = [
        {"id": "ten", "name": "Grade 10A"},
        {"id": "two", "name": "Grade 2A"},
    ]
    rows = [
        {"id": "3", "profiles": {"class_id": "ten", "full_name": "Abel"}},
        {"id": "4", "profiles": {"class_id": "unknown", "full_name": "Ari"}},
        {"id": "1", "profiles": {"class_id": "two", "full_name": "Zara"}},
        {"id": "2", "profiles": {"class_id": "two", "full_name": "ana"}},
    ]
    ordered = arrange(db, rows, "school-a")
    db.table.assert_called_once_with("classes")
    db.table.return_value.select.return_value.eq.assert_called_once_with("school_id", "school-a")
    classes.in_.assert_called_once_with("id", ["ten", "two", "unknown"])
    sheet = load_workbook(export_to_xlsx(ordered, {})).active
    exported = list(sheet.values)
    assert exported[0][8] == "Kelas"
    assert [(row[8], row[1]) for row in exported[1:]] == [
        ("Grade 2A", "ana"), ("Grade 2A", "Zara"), ("Grade 10A", "Abel"), ("-", "Ari"),
    ]
