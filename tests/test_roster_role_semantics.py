from app.main import _apply_roster_role_semantics


class FakeRepo:
    def get_daily_duty_config(self):
        return {
            "patrol_team_names": ["王德刚", "罗熙云", "李文杰", "杞文江", "商邱宏", "罗富耀", "沐春宇"],
            "station_names": ["罗森", "李金雷"],
            "office_names": ["杨伦", "刘显坤"],
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


def _row(name: str, values: list[str]) -> dict:
    days = {str(index): value for index, value in enumerate(values, start=1)}
    cell_meta = {day: {"white_middle": True} for day, value in days.items() if value == ""}
    return {"name": name, "days": days, "cell_meta": cell_meta}
