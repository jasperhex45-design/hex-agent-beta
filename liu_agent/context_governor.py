"""Small, deterministic context handoff governor.

The default policy records a host's evidence-based efficiency assessment;
token ratios are advisory unless a trigger is explicitly configured. This
module cannot detect cognitive efficiency, create threads or approve tools.
A host adapter must perform side effects after the workspace is quiescent.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping


class TelemetryError(ValueError):
    """The runtime did not provide trustworthy current-context telemetry."""


class GovernorPhase(str, Enum):
    ACTIVE = "active"
    PENDING = "handoff_pending"
    HANDOFF = "handoff_in_progress"
    PAUSED = "paused"
    RETIRED = "retired"


@dataclass(frozen=True)
class Usage:
    thread_id: str
    turn_id: str
    current_tokens: int
    window_tokens: int

    @property
    def ratio(self) -> Decimal:
        return Decimal(self.current_tokens) / Decimal(self.window_tokens)

    @classmethod
    def from_event(cls, event: Mapping[str, Any]) -> "Usage":
        if event.get("method") != "thread/tokenUsage/updated":
            raise TelemetryError("expected thread/tokenUsage/updated")
        params = event.get("params")
        usage = params.get("tokenUsage") if isinstance(params, Mapping) else None
        last = usage.get("last") if isinstance(usage, Mapping) else None
        current = last.get("totalTokens") if isinstance(last, Mapping) else None
        window = usage.get("modelContextWindow") if isinstance(usage, Mapping) else None
        thread_id = params.get("threadId") if isinstance(params, Mapping) else None
        turn_id = params.get("turnId") if isinstance(params, Mapping) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise TelemetryError("missing threadId")
        if not isinstance(turn_id, str) or not turn_id:
            raise TelemetryError("missing turnId")
        if type(current) is not int or current < 0:
            raise TelemetryError("tokenUsage.last.totalTokens must be nonnegative int")
        if type(window) is not int or window <= 0:
            raise TelemetryError("tokenUsage.modelContextWindow must be positive int")
        return cls(thread_id, turn_id, current, window)


@dataclass(frozen=True)
class GovernorDecision:
    action: str
    phase: GovernorPhase
    ratio: Decimal | None
    reason: str


class ContextGovernor:
    """One owned thread, one latched trigger, conservative boundaries."""

    def __init__(self, thread_id: str, trigger_ratio: str | None = None) -> None:
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id is required")
        ratio = None
        if trigger_ratio is not None:
            try:
                ratio = Decimal(str(trigger_ratio))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise ValueError("invalid trigger_ratio") from exc
            if not ratio.is_finite() or not Decimal("0") < ratio < Decimal("1"):
                raise ValueError("trigger_ratio must be between 0 and 1")
        self.thread_id = thread_id
        self.trigger_ratio = ratio
        self.phase = GovernorPhase.ACTIVE
        self.usage: Usage | None = None
        self.pause_reason: str | None = None

    def _decision(self, action: str, reason: str) -> GovernorDecision:
        return GovernorDecision(action, self.phase, self.usage.ratio if self.usage else None, reason)

    def observe(self, event: Mapping[str, Any], *, restoring: bool = False) -> GovernorDecision:
        if self.phase in (GovernorPhase.PAUSED, GovernorPhase.RETIRED):
            return self._decision("ignore", "explicit recovery is required")
        if event.get("method") != "thread/tokenUsage/updated":
            return self._decision("ignore", "unrelated event")
        try:
            usage = Usage.from_event(event)
        except TelemetryError as exc:
            self.usage = None
            if self.trigger_ratio is None:
                action = "wait" if self.phase in (GovernorPhase.PENDING, GovernorPhase.HANDOFF) else "continue"
                return self._decision(action, "context ratio unavailable; host efficiency assessment remains available: " + str(exc))
            self.phase = GovernorPhase.PAUSED
            self.pause_reason = str(exc)
            return self._decision("pause", "cannot verify context usage: " + str(exc))
        if usage.thread_id != self.thread_id:
            return self._decision("ignore", "event belongs to another thread")
        self.usage = usage
        if self.phase in (GovernorPhase.PENDING, GovernorPhase.HANDOFF):
            return self._decision("wait", "handoff is already latched")
        if self.trigger_ratio is None:
            return self._decision("continue", "context ratio is advisory; host efficiency assessment is required")
        if usage.ratio >= self.trigger_ratio:
            if restoring:
                self.phase = GovernorPhase.PAUSED
                self.pause_reason = "restore_already_over_trigger"
                return self._decision("pause", "shorten restore payload before continuing")
            self.phase = GovernorPhase.PENDING
            return self._decision("prepare_handoff", "current context reached configured trigger")
        return self._decision("continue", "below configured trigger")

    def assess_efficiency(self, *, impaired: bool, reason: str,
                          restoring: bool = False) -> GovernorDecision:
        """Record a host judgment about context-related loss of efficiency.

        The host supplies concrete evidence in reason; this method validates
        its presence, not its truth. No token telemetry is needed for this
        judgment. During restore, continuing impairment pauses for a smaller
        restore payload instead of starting another handoff.
        """
        if type(impaired) is not bool or type(restoring) is not bool:
            raise ValueError("assessment flags must be boolean")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("efficiency assessment reason is required")
        if self.phase in (GovernorPhase.PAUSED, GovernorPhase.RETIRED):
            return self._decision("ignore", "explicit recovery is required")
        if self.phase in (GovernorPhase.PENDING, GovernorPhase.HANDOFF):
            return self._decision("wait", "handoff is already latched")
        reason = reason.strip()
        if not impaired:
            return self._decision("continue", reason)
        if restoring:
            self.phase = GovernorPhase.PAUSED
            self.pause_reason = "restore_efficiency_impaired"
            return self._decision("pause", "shorten restore payload before continuing: " + reason)
        self.phase = GovernorPhase.PENDING
        return self._decision("prepare_handoff", reason)

    def begin(self, *, active_turn: bool, active_tools: int, waiting_approval: bool,
              writer_quiescent: bool) -> bool:
        if type(active_tools) is not int or active_tools < 0:
            raise ValueError("active_tools must be a nonnegative int")
        if any(type(value) is not bool for value in (active_turn, waiting_approval, writer_quiescent)):
            raise ValueError("lifecycle flags must be boolean")
        if self.phase != GovernorPhase.PENDING:
            return False
        if active_turn or active_tools or waiting_approval or not writer_quiescent:
            return False
        self.phase = GovernorPhase.HANDOFF
        return True

    def pause(self, reason: str) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("pause reason is required")
        self.phase, self.pause_reason = GovernorPhase.PAUSED, reason
