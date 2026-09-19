from jevonly.core.text import goal_clauses, goal_spans, page_units, split_parts, unit_spans

FLIGHT_GOAL = "Find flights from Seattle SEA to Paris CDG on December 5, 2026, then choose the cheapest nonstop option."
INFOBOX = "Tallest building height 937 ft (286 m), completed in 1985"


def test_goal_spans_extracts_codes_places_and_date():
    spans = goal_spans(FLIGHT_GOAL)

    assert "SEA" in spans
    assert "CDG" in spans
    assert "Paris CDG" in spans
    assert any(span.startswith("December 5") for span in spans)


def test_goal_clauses_preserves_date_comma_and_order():
    assert goal_clauses(FLIGHT_GOAL) == [
        "Find flights from Seattle SEA to Paris CDG on December 5, 2026",
        "choose the cheapest nonstop option",
    ]


def test_split_parts_keeps_reading_order_and_all_units():
    units = ["short", "a much longer second unit", "third", "fourth", "fifth"]
    groups = split_parts(units, n=3)

    assert 1 < len(groups) <= 3
    assert [item for group in groups for item in group] == units


def test_unit_spans_keeps_short_infobox_line_as_copyable_value():
    spans = unit_spans(INFOBOX)

    assert spans == [INFOBOX]


def test_page_units_prefers_snapshot_text_and_adds_control_context():
    observation = {
        "visible_text": "Fallback page text",
        "elements": [
            {"role": "heading", "name": "Skyscraper facts"},
            {"role": "button", "name": "Details", "context": INFOBOX},
            {"role": "textbox", "name": "Height", "value": "286 m"},
        ],
    }
    snapshot = {"text_units": [INFOBOX, INFOBOX]}

    units = page_units(observation, snapshot)

    assert units[0] == INFOBOX
    assert units.count(INFOBOX) == 1
    assert "Skyscraper facts" in units
    assert "Height: 286 m" in units
