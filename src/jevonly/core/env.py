"""What the core loop needs from an environment.

The loop never touches a browser, a file system or an API directly: it observes a *state* with
*candidates* (the closed choices Jev picks from), performs the chosen candidate, verifies, and undoes.
Anything that can express itself this way can be driven by the same rules. The browser environment in
``jevonly.envs.browser`` is the reference implementation; a new one implements this protocol and is
registered with ``jevonly.envs.register``.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Environment(Protocol):
    """The methods the loop calls. Optional members are read with ``hasattr`` by the loop."""

    def observe(self) -> dict[str, Any]:
        """The current state as Jev will see it: at least ``url``/``title``-like identity, ``elements`` (the
        candidates with role, name, value, context) and ``visible_text``. Must be cheap; it is called after
        every action."""

    def act(self, cand: dict, value: Any = None, kind: str | None = None) -> None:
        """Perform one candidate. Failures are reported through ``last_action_error``, never raised."""

    def undo(self) -> Any:
        """Return to the state before the last ``act`` as closely as the environment allows.

        Return ``None`` when the state is back (the browser reloads or goes back). An environment that can
        only compensate some actions returns ``{"restored": bool, "error": str | None}``; on
        ``restored=False`` the loop keeps the action in its history and escalates instead of narrating a
        state that never came back."""

    def fingerprint(self, obs: dict) -> str:
        """A stable identity for a state, used to detect no-effect actions and revisits."""

    def keyboard(
        self, cand: dict | None = None, key: str | None = None, text: str | None = None, focus: bool = False
    ) -> dict:
        """Type into the focused field (or focus ``cand`` first). Returns the field as it reads now plus any
        suggestions the environment shows, so the loop can let Jev decide the next word."""

    def inflight(self) -> int:
        """How many requests the last action started are still pending (0 when the state has settled)."""

    def wait_inflight(self, cap_ms: int = 3000) -> int:
        """Wait, up to ``cap_ms``, for those requests; return the milliseconds waited."""

    def close(self) -> None:
        """Release everything the environment holds."""


# Optional members the loop uses when present:
#   screenshot(idx=None)      -> base64 image for the viewer
#   open_dialog()             -> the choices of a picker that opened on focus, or None
#   shrink_observation()      -> halve observation budgets when a state did not fit one request
#   last_action_error / last_action_note / last_settled  -> per-action diagnostics read and cleared by the loop
#   acceptance / terminal / candidates / noop / detour / partial / popup / delayed_ack / concurrent
#                             -> benchmark hooks (code-owned completion checks, fault injection)
#   supports_atomic_batch     -> False when several fields cannot be written as one action; the loop then
#                                does not offer the whole-form pass (fields are filled one at a time)
