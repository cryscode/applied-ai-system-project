# Validator: rule-based safety layer for PawPal+ schedules.
#
# This module contains no LLM calls and no external I/O. It exists to catch
# unsafe schedules (medication double-dosing, insufficient rest, tasks that
# don't make sense for a pet's species/age) with plain, auditable rules.

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Dict, List

from pawpal_system import Pet, Task, format_minutes, intervals_overlap, parse_time_window


class SafetyViolation(Exception):
    """Raised by Validator.enforce() when a schedule fails a safety rule."""

    def __init__(self, violations: List[str]):
        self.violations = violations
        super().__init__("; ".join(violations))


@dataclass
class Validator:
    """Checks a set of tasks against concrete pet-care safety rules.

    Every check appends a human-readable violation message to the result;
    validate() always returns the full, explicit list (empty means "checked,
    nothing found" — never "not checked"). Callers that need a schedule to be
    blocked outright on violation should use enforce() instead of validate(),
    since validate() alone is easy to call and then ignore.
    """

    MEDICATION_KEYWORDS = ("medication", "medicine", "dose", "pill", "insulin")
    WALK_KEYWORDS = ("walk",)

    MIN_MEDICATION_SPACING_MINUTES = 240
    MIN_WALK_REST_MINUTES = 30
    MIN_SENIOR_WALK_REST_MINUTES = 60
    MAX_PUPPY_WALK_MINUTES = 20

    PUPPY_MAX_AGE_YEARS = 1
    SENIOR_MIN_AGE_YEARS = 8
    NON_WALKING_SPECIES = ("cat", "fish", "bird", "hamster", "rabbit")

    def validate(self, tasks: List[Task]) -> List[str]:
        """Return every safety-rule violation found across the given tasks."""
        violations: List[str] = []
        violations.extend(self._check_medication_spacing(tasks))
        violations.extend(self._check_walk_rest(tasks))
        violations.extend(self._check_species_age_rules(tasks))
        return violations

    def enforce(self, tasks: List[Task]) -> None:
        """Raise SafetyViolation if any rule fails; otherwise do nothing.

        Use this at the point a schedule would take effect (e.g. before
        generateSchedule() commits it), so an unsafe schedule can never pass
        through silently.
        """
        violations = self.validate(tasks)
        if violations:
            raise SafetyViolation(violations)

    # ── Rule: medication dose spacing ───────────────────────────────────────

    def _is_medication(self, task: Task) -> bool:
        text = f"{task.title} {task.description}".lower()
        return any(keyword in text for keyword in self.MEDICATION_KEYWORDS)

    def _check_medication_spacing(self, tasks: List[Task]) -> List[str]:
        violations: List[str] = []
        by_pet: Dict[str, List[Task]] = {}
        for task in tasks:
            if not self._is_medication(task):
                continue
            for pet in task.appliesTo:
                by_pet.setdefault(pet.name, []).append(task)

        for pet_name, pet_tasks in by_pet.items():
            windows = sorted(
                ((*parse_time_window(t), t) for t in pet_tasks),
                key=lambda w: w[0],
            )
            for (start_a, end_a, a), (start_b, end_b, b) in combinations(windows, 2):
                gap = start_b - end_a
                if intervals_overlap(start_a, end_a, start_b, end_b) or gap < self.MIN_MEDICATION_SPACING_MINUTES:
                    violations.append(
                        f"[SAFETY — medication spacing] '{a.title}' ({a.startTime}) and '{b.title}' "
                        f"({b.startTime}) for {pet_name} are too close together — doses need at least "
                        f"{self.MIN_MEDICATION_SPACING_MINUTES} min apart"
                    )
        return violations

    # ── Rule: minimum rest between walks ────────────────────────────────────

    def _is_walk(self, task: Task) -> bool:
        text = f"{task.title} {task.description}".lower()
        return any(keyword in text for keyword in self.WALK_KEYWORDS)

    def _min_walk_rest_for(self, pet: Pet) -> int:
        if pet.age >= self.SENIOR_MIN_AGE_YEARS:
            return self.MIN_SENIOR_WALK_REST_MINUTES
        return self.MIN_WALK_REST_MINUTES

    def _check_walk_rest(self, tasks: List[Task]) -> List[str]:
        violations: List[str] = []
        by_pet: Dict[str, List[Task]] = {}
        pets_by_name: Dict[str, Pet] = {}
        for task in tasks:
            if not self._is_walk(task):
                continue
            for pet in task.appliesTo:
                by_pet.setdefault(pet.name, []).append(task)
                pets_by_name[pet.name] = pet

        for pet_name, pet_tasks in by_pet.items():
            min_rest = self._min_walk_rest_for(pets_by_name[pet_name])
            windows = sorted(
                ((*parse_time_window(t), t) for t in pet_tasks),
                key=lambda w: w[0],
            )
            for (start_a, end_a, a), (start_b, end_b, b) in zip(windows, windows[1:]):
                gap = start_b - end_a
                if gap < min_rest:
                    violations.append(
                        f"[SAFETY — walk rest] '{a.title}' ({a.startTime}) and '{b.title}' ({b.startTime}) "
                        f"for {pet_name} leave only {max(gap, 0)} min rest — minimum is {min_rest} min"
                    )
        return violations

    # ── Rule: species/age-appropriate tasks ─────────────────────────────────

    def _check_species_age_rules(self, tasks: List[Task]) -> List[str]:
        violations: List[str] = []
        for task in tasks:
            if not self._is_walk(task):
                continue
            for pet in task.appliesTo:
                species = pet.species.lower()
                if species in self.NON_WALKING_SPECIES:
                    violations.append(
                        f"[SAFETY — species mismatch] '{task.title}' is a walk task assigned to "
                        f"{pet.name}, a {pet.species} — this species is not typically walked"
                    )
                if species == "dog" and pet.age <= self.PUPPY_MAX_AGE_YEARS and task.durationMinutes > self.MAX_PUPPY_WALK_MINUTES:
                    violations.append(
                        f"[SAFETY — puppy exertion] '{task.title}' is {task.durationMinutes} min for "
                        f"{pet.name}, a {pet.age}-year-old puppy — cap walks at {self.MAX_PUPPY_WALK_MINUTES} min"
                    )
        return violations
