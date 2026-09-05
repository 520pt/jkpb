from app.main import _apply_roster_role_semantics


class FakeRepo:
    def get_daily_duty_config(self):
        return {
            "patrol_team_names": ["王德刚", "罗熙云", "李文杰", "杞文江", "商邱宏", "罗富耀", "沐春宇"],
            "station_names": ["罗森", "李金雷"],
            "office_names": ["杨伦", "刘显坤"],
        }


class FakeGroupedRepo:
    def get_daily_duty_config(self):
        return {
            "patrol_team_names": [],
            "patrol_team_groups": [
                {"name": "一班", "members": ["王德刚", "罗熙云"]},
                {"name": "二班", "members": ["李文杰", "杞文江"]},
                {"name": "三班", "members": ["商邱宏", "罗富耀", "沐春宇"]},
            ],
            "station_names": ["罗森", "李金雷"],
            "office_names": ["杨伦", "刘显坤"],
        }


class FakeFallbackRepo:
    def get_daily_duty_config(self):
        return {
            "patrol_team_names": [],
            "patrol_team_groups": [],
            "station_names": ["罗森"],
            "office_names": ["杨伦"],
            "big_driver_names": [],
            "small_driver_names": [],
        }


def test_role_semantics_reads_patrol_standby_office_and_station_white_middle_cells():
    result = _apply_roster_role_semantics(
        FakeRepo(),
        {
            "grid": [
                _row("罗森", ["", "", "", "", ""]),
                _row("王德刚", ["休", "休", "休", "休", "休", "", "", "", "", "", "", "早", "晚", "中", ""]),
                _row("罗熙云", ["休", "休", "休", "休", "休", "", "", "", "", "", "早", "晚", "中", "", "早"]),
                _row("李文杰", ["休", "休", "休", "休", "休", "", "", "", "", "", "中", "", "早", "晚", "中"]),
                _row("杞文江", ["休", "休", "休", "休", "休", "", "", "", "", "", "晚", "中", "", "早", ""]),
                _row("商邱宏", ["", "", "", "", "", "", "早", "晚", "中", "", "休", "休", "休", "休", "休"]),
                _row("罗富耀", ["", "", "", "", "", "早", "晚", "中", "", "早", "休", "休", "休", "休", "休"]),
                _row("沐春宇", ["", "", "", "", "", "中", "", "早", "晚", "中", "休", "休", "休", "休", "休"]),
                _row("杨伦", ["", "", "", "早", "早"]),
            ]
        },
    )

    rows = {row["name"]: row["days"] for row in result["grid"]}
    assert [rows["王德刚"][str(day)] for day in range(6, 11)] == ["巡", "巡", "巡", "巡", "巡"]
    assert rows["王德刚"]["11"] == "备"
    assert rows["王德刚"]["15"] == "备"
    assert rows["罗熙云"]["14"] == "备"
    assert rows["李文杰"]["12"] == "备"
    assert rows["杞文江"]["13"] == "备"
    assert rows["商邱宏"]["1"] == "巡"
    assert rows["商邱宏"]["6"] == "备"
    assert rows["商邱宏"]["11"] == "休"
    assert rows["罗森"]["3"] == "-"
    assert rows["杨伦"]["1"] == "办"


def test_role_semantics_reads_patrol_team_groups_without_flat_names():
    result = _apply_roster_role_semantics(
        FakeGroupedRepo(),
        {
            "grid": [
                _row("罗森", [""]),
                _row("王德刚", ["", "早"]),
                _row("罗熙云", ["早", ""]),
            ]
        },
    )

    rows = {row["name"]: row["days"] for row in result["grid"]}
    assert rows["罗森"]["1"] == "-"
    assert rows["王德刚"]["1"] == "备"
    assert rows["罗熙云"]["2"] == "备"


def test_role_semantics_falls_back_to_white_middle_rows_when_patrol_config_missing():
    result = _apply_roster_role_semantics(
        FakeFallbackRepo(),
        {
            "grid": [
                _row("罗森", ["", "", "", "", ""]),
                _row("王德刚", ["", "", "早", "", ""]),
                _row("沐春宇", ["", "", "", "", ""]),
                _row("杨伦", ["", "", "", "", ""]),
            ]
        },
    )

    rows = {row["name"]: row["days"] for row in result["grid"]}
    assert rows["罗森"]["1"] == "-"
    assert rows["王德刚"]["1"] == "巡"
    assert rows["沐春宇"]["3"] == "备"
    assert rows["杨伦"]["1"] == "办"


def _row(name: str, values: list[str]) -> dict:
    days = {str(index): value for index, value in enumerate(values, start=1)}
    cell_meta = {day: {"white_middle": True} for day, value in days.items() if value == ""}
    return {"name": name, "days": days, "cell_meta": cell_meta}
