from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from typing import Any

Response = Mapping[str, Any]
Responder = Callable[[dict[str, Any], dict[str, Any], str], Response]


class FakeJev:
    """Scripted closed-choice stand-in that records the complete question surface."""

    def __init__(
        self,
        responses: Iterable[Response | Responder] = (),
        *,
        rules: Iterable[Response] = (),
        responder: Responder | None = None,
    ) -> None:
        self.responses = list(responses)
        self.rules = list(rules)
        self.responder = responder
        self.calls: list[dict[str, Any]] = []

    def push(self, response: Response | Responder) -> FakeJev:
        self.responses.append(response)
        return self

    def push_choice_contains(self, text: str, *, question: str | None = None, probability: float = 0.9) -> FakeJev:
        return self.push({"question": question, "contains": text, "probability": probability})

    def push_noul(self, probability: float, *, question: str | None = None) -> FakeJev:
        return self.push({"question": question, "noul": probability})

    def add_rule(self, **rule: Any) -> FakeJev:
        self.rules.append(rule)
        return self

    def __call__(self, state: dict[str, Any], questions: dict[str, Any], tag: str) -> dict[str, Any]:
        self.calls.append(
            {
                "tag": tag,
                "state_keys": tuple(sorted(state)),
                "question_names": tuple(questions),
                "criteria": {name: deepcopy(question.get("criteria", {})) for name, question in questions.items()},
                "state": deepcopy(state),
            }
        )

        if self.responses:
            script = self.responses.pop(0)
            if callable(script):
                return dict(script(state, questions, tag))
            return self._answer_from_directive(dict(script), questions, tag)
        if self.responder is not None:
            return dict(self.responder(state, questions, tag))
        return {name: self._answer_one(name, question, tag) for name, question in questions.items()}

    def assert_exhausted(self) -> None:
        assert not self.responses, f"{len(self.responses)} scripted Jev response(s) were not used"

    def _answer_from_directive(self, directive: dict[str, Any], questions: dict[str, Any], tag: str) -> dict[str, Any]:
        if set(directive) == set(questions) and all(isinstance(value, Mapping) for value in directive.values()):
            return {name: self._normalise_answer(questions[name], dict(value)) for name, value in directive.items()}
        return {
            name: self._answer_one(name, question, tag, preferred=directive) for name, question in questions.items()
        }

    def _answer_one(
        self,
        name: str,
        question: dict[str, Any],
        tag: str,
        *,
        preferred: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        directive = preferred if self._matches(preferred, name, tag) else None
        if directive is None:
            directive = next((rule for rule in self.rules if self._matches(rule, name, tag)), None)

        if question["type"] == "noul":
            probability = float(directive.get("noul", 0.0)) if directive else 0.0
            return {"noul": probability}

        criteria = question["criteria"]
        probability = float(directive.get("probability", 0.9)) if directive else 0.9
        if directive and directive.get("choice") is not None:
            choice = str(directive["choice"])
            if choice not in criteria:
                raise AssertionError(f"choice {choice!r} is not offered for {name!r}")
        elif directive and directive.get("contains") is not None:
            needle = str(directive["contains"]).casefold()
            exact = next((option for option in criteria if option.casefold() == needle), None)
            option_match = next((option for option in criteria if needle in option.casefold()), None)
            description_match = next(
                (option for option, description in criteria.items() if needle in str(description).casefold()),
                None,
            )
            choice = exact or option_match or description_match
            if choice is None:
                raise AssertionError(f"no option for {name!r} contains {directive['contains']!r}")
        else:
            choice = next(iter(criteria))
        return self._choice_answer(criteria, choice, probability)

    @staticmethod
    def _matches(directive: Mapping[str, Any] | None, name: str, tag: str) -> bool:
        if directive is None:
            return False
        return directive.get("question") in (None, name) and directive.get("tag") in (None, tag)

    @classmethod
    def _normalise_answer(cls, question: dict[str, Any], answer: dict[str, Any]) -> dict[str, Any]:
        if question["type"] == "noul":
            return {"noul": float(answer["noul"])}
        choice = str(answer["choice"])
        if choice not in question["criteria"]:
            raise AssertionError(f"choice {choice!r} is not offered")
        if "probabilities" in answer:
            return answer
        return cls._choice_answer(question["criteria"], choice, float(answer.get("probability", 0.9)))

    @staticmethod
    def _choice_answer(criteria: Mapping[str, str], choice: str, probability: float) -> dict[str, Any]:
        if not 0.0 <= probability <= 1.0:
            raise ValueError("choice probability must be between 0 and 1")
        others = len(criteria) - 1
        remainder = (1.0 - probability) / others if others else 0.0
        probabilities = {option: (probability if option == choice else remainder) for option in criteria}
        return {"choice": choice, "probabilities": probabilities}
