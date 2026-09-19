import io
import json

import pytest

from jevonly.core import prefilter
from jevonly.core.copy import collapse_pick

TARGET = "Tallest building height 546 ft (166 m)"
# a long list page: navigation, then the value buried among many look-alike rows
UNITS = (
    ["Jump to content", "Main menu", "Search", "Donate", "Create account", "Log in"]
    + [f"{i} Some Tower 45°N 122°W {400 + i} ({120 + i}) {20 + i} 19{70 + i} Office" for i in range(30)]
    + [TARGET, "Wells Fargo Center 1972-present 546 (166.4) 40 [3]", "References", "External links"]
)


def test_prefiltered_collapse_starts_from_the_kept_lines_best_first(fake_jev):
    calls = []

    def ranker(units):
        calls.append(list(units))
        kept = [
            TARGET,
            "Wells Fargo Center 1972-present 546 (166.4) 40 [3]",
            "12 Some Tower 45°N 122°W 412 (132) 32 1982 Office",
        ]
        return {"kept": kept, "scores": {kept[0]: 0.98, kept[1]: 0.95, kept[2]: 0.4}, "model": "jev-test"}

    # three kept lines: one parts round over them, then the pieces of the chosen line
    jev = fake_jev([{"contains": "166 m"}, {"contains": "166"}])
    trace = []
    result = collapse_pick(jev, {"task_goal": "g"}, UNITS, "for the height", trace=trace, prefilter=ranker)

    assert calls == [UNITS], "the filter sees the whole haystack exactly once"
    assert result["text"] == "166"
    assert result["unit"] == TARGET
    assert result["rounds"] == 2
    assert [r["kind"] for r in trace] == ["prefilter", "parts", "pieces"]
    assert trace[0]["pick"] == "3 of 40 lines kept (jev-test)"
    assert [o["text"] for o in trace[0]["options"]][:2] == [
        TARGET,
        "Wells Fargo Center 1972-present 546 (166.4) 40 [3]",
    ]
    assert trace[0]["options"][0]["p"] == 0.98
    jev.assert_exhausted()

    # the same haystack without the filter needs one more round (40 -> 10 -> 1 -> pieces)
    plain = fake_jev([{"contains": "166 m"}, {"contains": "166 m"}, {"contains": "166"}])
    assert collapse_pick(plain, {"task_goal": "g"}, UNITS, "for the height")["rounds"] == 3
    plain.assert_exhausted()


def test_prefilter_without_a_verdict_leaves_the_full_haystack(fake_jev):
    jev = fake_jev([{"contains": "166"}, {"contains": "166"}, {"contains": "166"}])
    trace = []
    with_none = collapse_pick(
        jev, {"task_goal": "g"}, UNITS, "for the height", trace=trace, prefilter=lambda units: None
    )
    jev_plain = fake_jev([{"contains": "166"}, {"contains": "166"}, {"contains": "166"}])
    plain_trace = []
    plain = collapse_pick(jev_plain, {"task_goal": "g"}, UNITS, "for the height", trace=plain_trace, prefilter=None)

    assert with_none == plain
    assert trace == plain_trace, "a None verdict must be indistinguishable from no filter at all"
    assert "prefilter" not in {r["kind"] for r in trace}


def _reply(labels_confidences, model="jev-1.13.0"):
    results = []
    for label, conf in labels_confidences:
        if label is None:
            results.append(
                {"label": "does not show it", "confidence": None, "scores": None, "unscored": "not natural language"}
            )
        else:
            p = conf if label == prefilter.LABELS[0] else 1 - conf
            results.append(
                {"label": label, "confidence": conf, "scores": {prefilter.LABELS[0]: p, prefilter.LABELS[1]: 1 - p}}
            )
    return {"model": model, "results": results}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_rank_units_keeps_shown_and_unsure_lines_best_first_and_reports_the_model():
    units = [f"line {i}" for i in range(12)]
    verdicts = [(prefilter.LABELS[1], 0.99)] * 9 + [
        (prefilter.LABELS[0], 0.97),  # shown -> kept, best
        (prefilter.LABELS[1], 0.55),  # unsure "does not" -> kept (recall bias)
        (None, None),  # unscored (coordinates etc.) -> kept, never dropped blind
    ]
    seen = {}

    def opener(req, timeout):
        seen["body"] = json.loads(req.data)
        seen["ua"] = req.get_header("User-agent")
        return _Resp(json.dumps(_reply(verdicts)).encode())

    out = prefilter.rank_units(units, "the height", "find the height", opener=opener)

    assert out["kept"] == ["line 9", "line 10", "line 11"]
    assert out["model"] == "jev-1.13.0"
    assert seen["body"]["labels"] == list(prefilter.LABELS)
    assert seen["body"]["inputs"] == units
    assert "`the height`" in seen["body"]["instructions"] and "find the height" in seen["body"]["instructions"]
    assert seen["ua"] and "urllib" not in seen["ua"].lower(), "python's default UA is refused at the edge"


@pytest.mark.parametrize(
    "opener",
    [
        lambda req, timeout: (_ for _ in ()).throw(OSError("network down")),
        lambda req, timeout: _Resp(b"not json"),
        lambda req, timeout: _Resp(json.dumps({"model": "x", "results": []}).encode()),  # length mismatch
        lambda req, timeout: _Resp(json.dumps(_reply([(prefilter.LABELS[1], 0.99)] * 12)).encode()),  # would drop all
        lambda req, timeout: _Resp(
            json.dumps(_reply([(prefilter.LABELS[0], 0.9)] * 12)).encode()
        ),  # keeps all: no gain
    ],
)
def test_rank_units_returns_none_instead_of_a_bad_verdict(opener):
    units = [f"line {i}" for i in range(12)]
    assert prefilter.rank_units(units, "w", "g", opener=opener) is None


def test_rank_units_skips_small_haystacks_without_a_call():
    def opener(req, timeout):
        raise AssertionError("must not be called")

    assert prefilter.rank_units(["a", "b", "c"], "w", "g", opener=opener) is None


def test_prefilter_switch(monkeypatch):
    monkeypatch.delenv("JEVONLY_PREFILTER", raising=False)
    assert prefilter.enabled({}) is True
    assert prefilter.enabled({"prefilter": False}) is False
    monkeypatch.setenv("JEVONLY_PREFILTER", "0")
    assert prefilter.enabled({}) is False
