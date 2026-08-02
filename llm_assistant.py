# LLMAssistant: Gemini-backed Q&A and natural-language schedule editing.
#
# The Gemini call (via an injected client) is the only non-deterministic step
# in this module. Everything after it -- building the hypothetical task list,
# running it through the Validator, and committing to Owner/Scheduler -- is
# deterministic Python with the same invariant AIPlanner enforces: there is no
# code path that reaches a committed Task list without Validator.enforce()
# succeeding on it first. Q&A (answer_question) is read-only and never
# mutates owner/scheduler state, the same trust boundary Retriever holds.

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Literal, Optional, Protocol

from pawpal_logger import (
    LLM_ACCEPTANCE,
    LLM_PROPOSAL,
    LLM_REJECTION,
    get_logger,
    log_decision,
)
from pawpal_system import Pet, Scheduler, Task
from retriever import Retriever
from validator import SafetyViolation, Validator

PRIORITY_MAP = {"high": 1, "medium": 2, "low": 3}
PRIORITY_LABEL = {1: "high", 2: "medium", 3: "low"}

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

EditAction = Literal["add", "edit", "remove"]

EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["add", "edit", "remove", "none"]},
        "target_title": {"type": "string", "nullable": True},
        "title": {"type": "string", "nullable": True},
        "durationMinutes": {"type": "integer", "nullable": True},
        "priority": {"type": "string", "enum": ["high", "medium", "low"], "nullable": True},
        "description": {"type": "string", "nullable": True},
        "recurrence": {"type": "string", "nullable": True},
        "startTime": {"type": "string", "nullable": True},
        "appliesTo": {"type": "array", "items": {"type": "string"}, "nullable": True},
        "clarification_needed": {"type": "string", "nullable": True},
        "explanation": {"type": "string"},
    },
    "required": ["action", "explanation"],
}


class LLMClient(Protocol):
    """Structural type satisfied by gemini_client.GeminiClient and by test fakes,
    so this module never has to import the real SDK."""

    def generate_text(self, prompt: str) -> str: ...

    def generate_json(self, prompt: str, schema: dict) -> dict: ...


@dataclass
class ProposedEdit:
    """Intermediate representation between raw Gemini JSON and a real Task.

    Never committed directly -- always passed through check_edit() (and
    re-validated again inside commit_edit()) first.
    """

    action: EditAction
    target_title: Optional[str]
    task: Optional[Task]
    explanation: str
    raw: dict


class ProposalError(Exception):
    """Raised when Gemini's JSON can't be turned into a valid ProposedEdit:
    an unknown pet name, an unknown target task, a malformed time, the model
    itself asking for clarification, or an unrecognized action."""


@dataclass
class LLMAssistant:
    scheduler: Scheduler
    retriever: Retriever
    client: LLMClient
    validator: Validator = field(default_factory=Validator)
    logger: logging.Logger = field(default_factory=get_logger, repr=False, compare=False)

    # ── Q&A (read-only) ────────────────────────────────────────────────────

    def answer_question(self, question: str, active_species: Optional[str] = None) -> str:
        """Answer a pet-care question, grounded on retriever facts and a
        read-only snapshot of this owner's pets/tasks. Never mutates state."""
        facts = self.retriever.retrieve(question, species=active_species, top_k=3)
        prompt = self._build_qa_prompt(question, facts)
        text = self.client.generate_text(prompt)
        return text or "I don't have a good answer for that."

    def _build_qa_prompt(self, question: str, facts) -> str:
        owner = self.scheduler.owner
        pet_lines = [f"- {p.name} ({p.species}, {p.age} yrs)" for p in owner.pets]
        task_lines = [
            f"- {t.title} at {t.startTime} ({t.durationMinutes} min, "
            f"{PRIORITY_LABEL.get(t.priority, t.priority)} priority) "
            f"for {', '.join(p.name for p in t.appliesTo) or 'unassigned'}"
            for t in owner.tasks
            if not t.isCompleted
        ]
        fact_lines = [f"- {f.fact}" for f in facts]

        return (
            "You are a helpful pet-care assistant embedded in a scheduling app called PawPal+.\n"
            "Answer the owner's question using the context below. Be concise and factual. "
            "If the context doesn't cover something, say so rather than guessing.\n\n"
            f"General care facts that may be relevant:\n{chr(10).join(fact_lines) or '(none matched)'}\n\n"
            f"Owner's pets:\n{chr(10).join(pet_lines) or '(no pets yet)'}\n\n"
            f"Owner's pending tasks:\n{chr(10).join(task_lines) or '(no pending tasks)'}\n\n"
            f"Owner's question: {question}\n"
        )

    # ── Edit proposal (LLM step; no mutation) ──────────────────────────────

    def propose_edit(self, instruction: str) -> ProposedEdit:
        """Ask Gemini to turn a natural-language instruction into a structured
        edit. Raises ProposalError if the result can't be resolved against
        real pets/tasks. Never mutates owner/scheduler state."""
        prompt = self._build_edit_prompt(instruction)
        data = self.client.generate_json(prompt, EDIT_SCHEMA)
        log_decision(
            self.logger, LLM_PROPOSAL,
            data.get("title") or data.get("target_title") or "?",
            reason=data.get("explanation", ""), instruction=instruction, action=data.get("action"),
        )
        return self._build_proposed_edit(data)

    def _build_edit_prompt(self, instruction: str) -> str:
        owner = self.scheduler.owner
        pet_names = ", ".join(p.name for p in owner.pets) or "(none)"
        task_lines = [
            f"- {t.title} | {t.startTime} | {t.durationMinutes}min | "
            f"{', '.join(p.name for p in t.appliesTo) or 'unassigned'}"
            for t in self.scheduler.tasks
            if not t.isCompleted
        ]
        return (
            "You convert a pet owner's natural-language scheduling request into ONE "
            "structured edit for a pet-care app. Respond only via the JSON schema you were given.\n\n"
            f"Known pets: {pet_names}\n"
            f"Current pending tasks (title | startTime | duration | pets):\n"
            f"{chr(10).join(task_lines) or '(none)'}\n\n"
            f"Owner request: \"{instruction}\"\n\n"
            "Rules:\n"
            "- action=\"edit\" with target_title exactly matching an existing task title above, "
            "to change time/duration/etc.\n"
            "- action=\"add\" to create a brand-new task (title/durationMinutes/priority/startTime/"
            "appliesTo all required).\n"
            "- action=\"remove\" with target_title to delete an existing task outright.\n"
            "- action=\"none\" with clarification_needed set if the request is ambiguous, refers to "
            "an unknown pet/task, or isn't a scheduling edit at all.\n"
            "- appliesTo must only use pet names from the Known pets list above.\n"
            "- priority must be \"high\"/\"medium\"/\"low\"; startTime must be 24h \"HH:MM\".\n"
            "- Always fill \"explanation\" with a one-sentence human-readable summary of what "
            "you're proposing and why.\n"
        )

    def _build_proposed_edit(self, data: dict) -> ProposedEdit:
        action = data.get("action")
        explanation = data.get("explanation", "")
        owner = self.scheduler.owner

        if action == "none" or data.get("clarification_needed"):
            raise ProposalError(
                data.get("clarification_needed") or explanation or "Could not determine an edit from that request."
            )
        if action not in ("add", "edit", "remove"):
            raise ProposalError(f"Unrecognized action from Gemini: {action!r}")

        target_title = data.get("target_title")
        if action in ("edit", "remove"):
            if not target_title or not any(t.title == target_title for t in self.scheduler.tasks):
                raise ProposalError(
                    f"Couldn't find a task named '{target_title}' to {action}. Try rephrasing with the exact title."
                )

        task = None
        if action in ("add", "edit"):
            pet_names = data.get("appliesTo") or []
            pet_lookup = {p.name: p for p in owner.pets}
            unknown = [name for name in pet_names if name not in pet_lookup]
            if unknown:
                raise ProposalError(f"Unknown pet(s) mentioned: {', '.join(unknown)}.")
            applies_to = [pet_lookup[name] for name in pet_names]

            priority_str = (data.get("priority") or "medium").lower()
            if priority_str not in PRIORITY_MAP:
                raise ProposalError(f"Unrecognized priority: {priority_str!r}.")

            start_time = data.get("startTime") or "00:00"
            if not _TIME_RE.match(start_time):
                raise ProposalError(f"Invalid start time {start_time!r}; expected 24h HH:MM.")

            title = data.get("title")
            duration = data.get("durationMinutes")
            if action == "add":
                if not title or not duration or not applies_to:
                    raise ProposalError("A new task needs a title, duration, and at least one pet.")
            else:  # edit: fall back to the existing task's fields for anything Gemini left blank
                existing = next(t for t in self.scheduler.tasks if t.title == target_title)
                title = title or existing.title
                duration = duration or existing.durationMinutes
                applies_to = applies_to or existing.appliesTo
                start_time = data.get("startTime") or existing.startTime
                priority_str = (data.get("priority") or PRIORITY_LABEL.get(existing.priority, "medium")).lower()

            task = Task(
                title=title,
                durationMinutes=int(duration),
                priority=PRIORITY_MAP[priority_str],
                description=data.get("description") or "",
                recurrence=data.get("recurrence") or "once",
                startTime=start_time,
                appliesTo=applies_to,
            )

        return ProposedEdit(action=action, target_title=target_title, task=task, explanation=explanation, raw=data)

    # ── Deterministic validation gate (no LLM) ─────────────────────────────

    def check_edit(self, proposed: ProposedEdit) -> List[str]:
        """Build the hypothetical post-edit task list and run Validator.validate.
        Side-effect-free: never mutates scheduler/owner state."""
        hypothetical = self._apply_hypothetically(proposed)
        violations = self.validator.validate(hypothetical)
        if violations:
            log_decision(
                self.logger, LLM_REJECTION,
                proposed.target_title or (proposed.task.title if proposed.task else "?"),
                reason="; ".join(violations),
            )
        return violations

    def _apply_hypothetically(self, proposed: ProposedEdit) -> List[Task]:
        tasks = list(self.scheduler.tasks)
        if proposed.action == "add":
            return tasks + [proposed.task]
        if proposed.action == "remove":
            return [t for t in tasks if t.title != proposed.target_title]
        if proposed.action == "edit":
            return [proposed.task if t.title == proposed.target_title else t for t in tasks]
        raise ProposalError(f"unknown action {proposed.action!r}")

    # ── Commit (deterministic, final Validator re-check) ───────────────────

    def commit_edit(self, proposed: ProposedEdit) -> None:
        """Apply a proposed edit to owner+scheduler. Re-runs Validator.enforce()
        as a final gate immediately before mutating -- state may have drifted
        since propose_edit()/check_edit() were called -- so this is the one
        function that actually mutates state, and it cannot do so around a
        failing Validator by construction."""
        hypothetical = self._apply_hypothetically(proposed)
        try:
            self.validator.enforce(hypothetical)
        except SafetyViolation as e:
            log_decision(
                self.logger, LLM_REJECTION,
                proposed.target_title or (proposed.task.title if proposed.task else "?"),
                reason="; ".join(e.violations),
            )
            raise

        owner = self.scheduler.owner
        if proposed.action == "add":
            owner.addTask(proposed.task)
            self.scheduler.addTask(proposed.task)
        elif proposed.action == "remove":
            owner.tasks = [t for t in owner.tasks if t.title != proposed.target_title]
            self.scheduler.removeTask(proposed.target_title)
        elif proposed.action == "edit":
            existing = next(t for t in owner.tasks if t.title == proposed.target_title)
            for f in ("title", "durationMinutes", "priority", "description",
                      "recurrence", "startTime", "appliesTo"):
                setattr(existing, f, getattr(proposed.task, f))

        log_decision(
            self.logger, LLM_ACCEPTANCE,
            proposed.target_title or proposed.task.title,
            reason=proposed.explanation, action=proposed.action,
        )
