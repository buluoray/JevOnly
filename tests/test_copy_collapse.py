import pytest

from jevonly.core.copy import collapse_pick
from jevonly.core.text import split_parts

INFOBOX = "Tallest building height 937 ft (286 m), completed in 1985"
FLIGHT = "6:20 PM - 7:05 AM Air France Nonstop $1,634"


@pytest.mark.parametrize(
    ("units", "target", "purpose", "exclude", "expected_piece"),
    [
        (["Overview", INFOBOX, "Architecture", "Open daily"], "937", "for the building height", ("1985",), "Tallest"),
        (["Departures", FLIGHT, "Baggage details"], "$1,634", "for the nonstop fare", ("Air",), "6:20 PM"),
    ],
)
def test_collapse_pick_narrows_consecutive_parts_then_selects_a_piece(
    fake_jev, units, target, purpose, exclude, expected_piece
):
    jev = fake_jev([{"contains": target}, {"contains": target}])
    trace = []

    result = collapse_pick(jev, {"task_goal": "read the requested value"}, units, purpose, exclude=exclude, trace=trace)

    assert result["text"] == target
    assert result["unit"] == next(unit for unit in units if target in unit)
    assert result["rounds"] == 2
    assert result["p"] == pytest.approx(0.9)
    jev.assert_exhausted()

    expected_groups = [" ".join(group)[:220] for group in split_parts(units)]
    first_criteria = jev.calls[0]["criteria"]["pick"]
    assert list(first_criteria.values())[:-1] == expected_groups
    assert list(first_criteria)[:-1] == [f"part {index}" for index in range(1, len(expected_groups) + 1)]

    final_criteria = jev.calls[1]["criteria"]["pick"]
    chosen_unit = result["unit"]
    assert target in final_criteria
    assert expected_piece in final_criteria
    assert chosen_unit in final_criteria
    assert all(piece not in final_criteria for piece in exclude)
    assert "line" in jev.calls[1]["state_keys"]

    assert [round_["kind"] for round_ in trace] == ["parts", "pieces"]
    assert [round_["pick"] for round_ in trace] == [
        next(option for option, text in zip(list(first_criteria)[:-1], expected_groups, strict=True) if target in text),
        target,
    ]
    for round_ in trace:
        assert set(round_) == {"kind", "pick", "options"}
        assert round_["options"]
        assert all(set(option) == {"id", "text", "p"} for option in round_["options"])
