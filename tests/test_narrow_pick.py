"""narrow_pick: Jev shortens a stretch of the goal from either end and decides itself when to stop."""

from tests.fake_jev import FakeJev

from jevonly.core.copy import narrow_pick
from jevonly.core.text import goal_clauses

GOAL = (
    "Find the tallest building in Seattle on Wikipedia, note its height in meters, then find the tallest "
    "building in Portland, Oregon, and stop when you can compare the two heights."
)


def test_narrowing_ends_when_jev_says_all():
    jev = FakeJev()
    # round 1: the clause; then trim "find the" off the front and stop
    jev.push({"pick": {"choice": "part 3"}})
    jev.push({"pick": {"choice": "head_off_2"}})
    jev.push({"pick": {"choice": "all"}})
    trace = []
    got = narrow_pick(jev, {"task_goal": GOAL}, goal_clauses(GOAL), "to type into the search box", trace=trace)
    assert got["text"] == "tallest building in Portland, Oregon"
    assert got["rounds"] == 3
    assert [r["kind"] for r in trace] == ["parts", "narrow", "narrow"]
    # every narrowing round offered the stop move and the text only got shorter
    texts = [next(o["text"] for o in r["options"] if o["id"] == "all") for r in trace if r["kind"] == "narrow"]
    assert texts == ["find the tallest building in Portland, Oregon", "tallest building in Portland, Oregon"]


def test_a_value_in_the_middle_is_reached_from_both_ends():
    jev = FakeJev()
    jev.push({"pick": {"choice": "head_off_2"}})  # drop "find the"
    jev.push({"pick": {"choice": "tail_off_2"}})  # drop "on Wikipedia"
    jev.push({"pick": {"choice": "all"}})
    got = narrow_pick(jev, {}, ["find the tallest building in Seattle on Wikipedia"], "x")
    assert got["text"] == "tallest building in Seattle"
    # the moves offered slide one end by 1, 2, 4 ... words; nothing cuts through the middle
    offered = set(jev.calls[0]["criteria"]["pick"])
    assert offered == {
        "all",
        "head_off_1",
        "head_off_2",
        "head_off_4",
        "tail_off_1",
        "tail_off_2",
        "tail_off_4",
        "none",
    }


def test_narrowing_gives_up_on_none():
    jev = FakeJev()
    jev.push({"pick": {"choice": "head_off_4"}})
    jev.push({"pick": {"choice": "none"}})
    assert narrow_pick(jev, {}, ["find the tallest building in Seattle on Wikipedia"], "x") is None
    assert jev.calls[1]["state"]["text"] == "in Seattle on Wikipedia"


def test_a_single_word_is_taken_without_asking():
    jev = FakeJev()
    got = narrow_pick(jev, {}, ["Seattle"], "x")
    assert got["text"] == "Seattle" and jev.calls == []
