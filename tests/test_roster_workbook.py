import xml.etree.ElementTree as ET
from zipfile import ZipFile

from fastapi.testclient import TestClient

from app.main import create_app
from app.roster_workbook import extract_roster_workbook


ROLE_CONFIG = {
    "station_names": ["站管甲"],
    "office_names": ["办公甲"],
    "patrol_team_names": ["巡查甲", "巡查乙"],
}


def _write_colored_workbook(path):
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relationships = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    package = "http://schemas.openxmlformats.org/package/2006/relationships"

    def element(parent, name, attributes=None):
        return ET.SubElement(parent, f"{{{namespace}}}{name}", attributes or {})

    workbook = ET.Element(f"{{{namespace}}}workbook")
    sheets = element(workbook, "sheets")
    rels = ET.Element(f"{{{package}}}Relationships")
    styles = ET.Element(f"{{{namespace}}}styleSheet")
    fills = element(styles, "fills")
    colors = [None, {"rgb": "FF0070C0"}, {"rgb": "FFFFFFFF"}, {"theme": "0"}, {"indexed": "9"}]
    for color in colors:
        fill = element(fills, "fill")
        pattern = element(fill, "patternFill", {"patternType": "solid" if color else "none"})
        if color:
            element(pattern, "fgColor", color)
    formats = element(styles, "cellXfs")
    for index in range(len(colors)):
        element(formats, "xf", {"fillId": str(index)})

    rows = [
        ("站管甲", [("中", 0), ("中", 2), ("中", 3), ("巡", 0), ("休", 0)]),
        ("办公甲", [("中", 4), ("中", 0), ("出差", 0), ("早", 1), ("休", 0)]),
        ("巡查甲", [("中", 0), ("早", 1), ("中", 0), ("中", 1), ("休", 0)]),
        ("巡查乙", [("中", 0), ("晚", 1), ("中", 1), ("中", 0), ("休", 0)]),
    ]
    with ZipFile(path, "w") as archive:
        for index, month in enumerate((11, 12), start=1):
            rel_id = f"rId{index}"
            element(sheets, "sheet", {"name": f"{month}月排班表", "sheetId": str(index), f"{{{relationships}}}id": rel_id})
            ET.SubElement(rels, f"{{{package}}}Relationship", {
                "Id": rel_id, "Type": relationships + "/worksheet", "Target": f"worksheets/sheet{index}.xml"})
            sheet = ET.Element(f"{{{namespace}}}worksheet")
            data = element(sheet, "sheetData")

            def add_cell(row, reference, value, style=0):
                cell = element(row, "c", {"r": reference, "s": str(style), "t": "inlineStr"})
                element(element(cell, "is"), "t").text = str(value)

            title = element(data, "row", {"r": "1"})
            add_cell(title, "A1", f"2026年{month}月排班表")
            header = element(data, "row", {"r": "2"})
            for day, column in enumerate("DEFGH", start=1):
                add_cell(header, f"{column}2", day)
            for row_number, (name, values) in enumerate(rows, start=4):
                row = element(data, "row", {"r": str(row_number)})
                add_cell(row, f"C{row_number}", name)
                for column, (value, style) in zip("DEFGH", values):
                    add_cell(row, f"{column}{row_number}", value, style)
            archive.writestr(f"xl/worksheets/sheet{index}.xml", ET.tostring(sheet))
        archive.writestr("xl/workbook.xml", ET.tostring(workbook))
        archive.writestr("xl/_rels/workbook.xml.rels", ET.tostring(rels))
        archive.writestr("xl/styles.xml", ET.tostring(styles))


def test_workbook_preserves_white_middle_metadata_and_colored_monitor_shifts(tmp_path):
    path = tmp_path / "roster.xlsx"
    _write_colored_workbook(path)
    result = extract_roster_workbook(path)
    for month in result["months"]:
        rows = {row["name"]: row for row in month["grid"]}
        station = rows["站管甲"]
        for day in ("1", "2", "3"):
            assert station["days"][day] == ""
            assert station["cell_meta"][day] == {"white_middle": True}
        assert rows["办公甲"]["cell_meta"]["1"] == {"white_middle": True}
        assert rows["巡查甲"]["days"]["4"] == "中"
        assert rows["巡查乙"]["days"]["2"] == "晚"
        assert station["days"]["4"] == "巡"
        assert station["days"]["5"] == "休"


def test_workbook_upload_applies_image_role_rules_to_every_month_and_saved_roster(tmp_path, monkeypatch):
    path = tmp_path / "roster.xlsx"
    _write_colored_workbook(path)
    app = create_app(data_dir=tmp_path / "data", upload_dir=tmp_path / "uploads", start_scheduler=False)
    monkeypatch.setattr(app.state.repo, "get_daily_duty_config", lambda: ROLE_CONFIG)
    client = TestClient(app)
    response = client.post("/api/rosters/upload", files={"file": (
        path.name, path.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert response.status_code == 200
    body = response.json()
    assert [month["month"] for month in body["months"]] == [11, 12]
    for month in body["months"]:
        rows = {row["name"]: row["days"] for row in month["grid"]}
        assert rows["站管甲"]["1"] == "-"
        assert rows["站管甲"]["2"] == "-"
        assert rows["站管甲"]["3"] == "-"
        assert rows["站管甲"]["4"] == "巡"
        assert rows["站管甲"]["5"] == "休"
        assert rows["办公甲"]["1"] == "办"
        assert rows["办公甲"]["3"] == "出差"
        assert rows["巡查甲"]["1"] == "巡"
        assert rows["巡查甲"]["3"] == "备"
        assert rows["巡查乙"]["4"] == "备"
        assert rows["巡查甲"]["4"] == "中"
        assert rows["巡查甲"]["2"] == "早"
        assert rows["巡查乙"]["2"] == "晚"
        confirmed = client.post("/api/rosters/confirm", json={
            "year": month["year"], "month": month["month"], "grid": month["grid"],
            "source_image_path": body["source_image_path"]})
        assert confirmed.status_code == 200
        saved = app.state.repo.get_roster_month(month["year"], month["month"])
        assert {row["name"]: row["days"] for row in saved["grid"]} == rows
    assert body["grid"] == body["months"][body["selected_month_index"]]["grid"]
