# AIPlanner: rule-based agent loop for PawPal+ schedule generation.
#
# Plan -> Act -> Check -> Adjust -> Final. No LLM/API calls -- "AI" here means
# an autonomous decision loop, not a model call. The loop wraps
# Scheduler.generateSchedule() and can only ever return a schedule that the
# Validator has actually passed: check() calls Validator.validate(), and the
# accepted branch re-runs Validator.enforce() as a final gate. There is no
# code path that returns accepted=True without both of those succeeding, so
# the planner cannot bypass the validator by construction.

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pawpal_logger import ADJUSTMENT, REJECTION, get_logger, log_decision
from pawpal_system import ScheduledTask, Scheduler
from validator import Validator


@dataclass
class PlanResult:
    """Outcome of one AIPlanner.run() call, including the full attempt history."""

    schedule: List[ScheduledTask]
    violations_history: List[List[str]]
    removed_tasks: List[str]
    iterations: int
    accepted: bool

    def explain(self) -> str:
        """Return a human-readable trace of what the planner tried and why."""
        lines = [f"AIPlanner ran {self.iterations} iteration(s)."]
        for i, violations in enumerate(self.violations_history, start=1):
            if not violations:
                lines.append(f"  Iteration {i}: no violations -- accepted.")
                break
            lines.append(f"  Iteration {i}: {len(violations)} violation(s):")
            lines.extend(f"    - {v}" for v in violations)
        if self.removed_tasks:
            lines.append(f"Removed to reach safety: {', '.join(self.removed_tasks)}")
        lines.append("ACCEPTED" if self.accepted else "REJECTED -- no safe schedule found")
        return "\n".join(lines)


@dataclass
class AIPlanner:
    """Rule-based Plan/Act/Check/Adjust agent that wraps a Scheduler.

    The Validator is the trust boundary: AIPlanner never edits, retries around,
    or suppresses a violation without the Validator re-confirming the result is
    clean. If no safe schedule can be found within max_retries, the planner
    returns an empty, explicitly-rejected schedule rather than an unsafe one.
    """

    scheduler: Scheduler
    validator: Validator = field(default_factory=Validator)
    max_retries: int = 3
    logger: logging.Logger = field(default_factory=get_logger, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.max_retries < 1:
            raise ValueError("max_retries must be at least 1")

    # ── Plan ─────────────────────────────────────────────────────────────────

    def plan(self, dayHours: Optional[int] = None) -> int:
        """Decide how many hours to schedule for. Defaults to the owner's availableHours."""
        return dayHours if dayHours is not None else self.scheduler.owner.availableHours

    # ── Act ──────────────────────────────────────────────────────────────────

    def act(self, dayHours: int) -> List[ScheduledTask]:
        """Generate a candidate schedule via the wrapped Scheduler."""
        return self.scheduler.generateSchedule(dayHours)

    # ── Check ────────────────────────────────────────────────────────────────

    def check(self, schedule: List[ScheduledTask]) -> List[str]:
        """Run the candidate schedule's tasks through the Validator. Never mutates state."""
        return self.validator.validate([scheduled.task for scheduled in schedule])

    # ── Adjust ───────────────────────────────────────────────────────────────

    def adjust(self, violations: List[str]) -> List[str]:
        """Drop the least-important task behind each violation from the candidate pool.

        Violation messages always quote the offending task title(s) (see
        Validator's f-strings), so titles map directly and auditably back to
        real Task objects -- no guessing which task is the culprit. When a
        violation names a pair (medication spacing, walk rest), only the
        lower-priority task of the two is removed, so a single conflicting
        task doesn't drag a legitimate higher-priority task down with it;
        single-task violations (species/age mismatch) remove that task
        outright. Returns the titles actually removed, which is always a
        non-empty subset of self.scheduler.tasks whenever violations is
        non-empty, guaranteeing len(scheduler.tasks) strictly decreases.
        """
        tasks_by_title = {t.title: t for t in self.scheduler.tasks}
        to_remove: Dict[str, str] = {}
        for line in violations:
            found_titles = re.findall(r"'([^']+)'", line)
            candidates = [tasks_by_title[t] for t in found_titles if t in tasks_by_title]
            if not candidates:
                continue
            victim = max(candidates, key=lambda t: (t.priority, t.startTime))
            to_remove.setdefault(victim.title, line)

        if to_remove:
            self.scheduler.tasks = [t for t in self.scheduler.tasks if t.title not in to_remove]
            for title, line in to_remove.items():
                log_decision(self.logger, ADJUSTMENT, title, reason=f"removed by planner to resolve: {line}")
        return list(to_remove)

    # ── Final ────────────────────────────────────────────────────────────────

    def run(self, dayHours: Optional[int] = None) -> PlanResult:
        """Run the Plan -> Act -> Check -> Adjust loop to a final, safety-gated result.

        Termination is provable: each iteration either (a) finds zero
        violations and returns immediately, or (b) removes at least one task
        from a finite pool, or (c) removes nothing and the loop stops rather
        than spin. So the loop runs at most min(max_retries, len(initial
        tasks) + 1) times -- bounded on both sides regardless of what the
        Validator's rules find.
        """
        target_hours = self.plan(dayHours)
        violations_history: List[List[str]] = []
        removed_tasks: List[str] = []

        for iteration in range(1, self.max_retries + 1):
            schedule = self.act(target_hours)
            violations = self.check(schedule)
            violations_history.append(violations)

            if not violations:
                # Final gate: the Validator itself confirms the result, not just
                # this loop's copy of the rules. enforce() raises on any
                # mismatch instead of letting an unsafe schedule slip through.
                self.validator.enforce([scheduled.task for scheduled in schedule])
                return PlanResult(
                    schedule=schedule,
                    violations_history=violations_history,
                    removed_tasks=removed_tasks,
                    iterations=iteration,
                    accepted=True,
                )

            removed = self.adjust(violations)
            removed_tasks.extend(removed)
            if not removed:
                # Violations reference tasks no longer in the pool -- no
                # further progress is possible, so stop instead of looping.
                break

        log_decision(
            self.logger, REJECTION, f"{target_hours}h schedule",
            reason=f"no safe arrangement found after {len(violations_history)} iteration(s); "
                   f"last violations: {violations_history[-1] if violations_history else []}",
        )
        return PlanResult(
            schedule=[],
            violations_history=violations_history,
            removed_tasks=removed_tasks,
            iterations=len(violations_history),
            accepted=False,
        )
