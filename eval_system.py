# eval_system.py -- black-box evaluation harness for PawPal+.
#
# Runs a fixed set of predefined scenarios against AIPlanner (schedule
# generation + safety gating) and Retriever (advisory Q&A), checks each
# result against an expected outcome, and prints a pass/fail summary. This
# is a standalone smoke/regression check, not a pytest suite -- it's meant
# to be run directly (`python eval_system.py`) and read top-to-bottom.

import sys

sys.stdout.reconfigure(encoding="utf-8")

from ai_planner import AIPlanner
from pawpal_system import Owner, Pet, Scheduler, Task
from retriever import Retriever
from validator import Validator


class EvalCase:
    """One predefined scenario: a name, a runner, and an expectation to check."""

    def __init__(self, name, run_fn, check_fn):
        self.name = name
        self.run_fn = run_fn
        self.check_fn = check_fn

    def evaluate(self):
        """Run the scenario and return (passed, confidence, detail)."""
        try:
            result = self.run_fn()
        except Exception as exc:  # a scenario crashing is itself a failure
            return False, 0.0, f"raised {type(exc).__name__}: {exc}"
        return self.check_fn(result)


# ── Helpers to build scenarios ─────────────────────────────────────────────

def make_task(title, priority, start_time, duration, description="", pets=None, recurrence="once"):
    return Task(
        title=title,
        durationMinutes=duration,
        priority=priority,
        description=description,
        recurrence=recurrence,
        startTime=start_time,
        appliesTo=pets or [],
    )


def planner_for(pet, tasks, available_hours=10, max_retries=3):
    owner = Owner(name="EvalOwner", availableHours=available_hours)
    for t in tasks:
        owner.addTask(t)
    scheduler = Scheduler(owner=owner, pets=[pet], tasks=list(tasks))
    return AIPlanner(scheduler=scheduler, validator=Validator(), max_retries=max_retries)


# ── AIPlanner / Validator scenarios ────────────────────────────────────────

def case_clean_schedule_is_accepted():
    dog = Pet(name="Rex", species="dog", age=3)
    tasks = [
        make_task("Morning Walk", 1, "07:00", 30, description="walk", pets=[dog]),
        make_task("Feed", 1, "08:00", 15, pets=[dog]),
    ]
    return planner_for(dog, tasks).run()


def check_accepted_and_clean(result):
    if not result.accepted:
        return False, 0.0, f"expected accepted schedule, got REJECTED ({result.violations_history})"
    leftover = Validator().validate([s.task for s in result.schedule])
    if leftover:
        return False, 0.3, f"accepted but Validator still finds violations: {leftover}"
    return True, 1.0, f"accepted in {result.iterations} iteration(s), {len(result.schedule)} task(s) scheduled"


def case_medication_spacing_resolved():
    dog = Pet(name="Milo", species="dog", age=4)
    tasks = [
        make_task("Insulin dose A", 2, "08:00", 10, description="insulin", pets=[dog]),
        make_task("Insulin dose B", 3, "08:30", 10, description="insulin", pets=[dog]),
        make_task("Feed", 1, "07:00", 15, pets=[dog]),
    ]
    return planner_for(dog, tasks).run()


def check_resolved_by_removal(result):
    ok, confidence, detail = check_accepted_and_clean(result)
    if not ok:
        return ok, confidence, detail
    if not result.removed_tasks:
        return False, 0.5, "accepted, but expected the planner to have removed a conflicting task"
    return True, 1.0, f"resolved by removing {result.removed_tasks}"


def case_puppy_overexertion_resolved():
    puppy = Pet(name="Buddy", species="dog", age=0)
    tasks = [
        make_task("Long Walk", 2, "09:00", 60, description="walk", pets=[puppy]),
        make_task("Feed", 1, "08:00", 15, pets=[puppy]),
    ]
    return planner_for(puppy, tasks).run()


def case_species_mismatch_resolved():
    cat = Pet(name="Mochi", species="cat", age=5)
    tasks = [
        make_task("Walk Mochi", 2, "09:00", 20, description="walk", pets=[cat]),
        make_task("Feed", 1, "08:00", 15, pets=[cat]),
    ]
    return planner_for(cat, tasks).run()


def case_unresolvable_rejected():
    """A single medication task that always overlaps itself in spacing terms is
    impossible to construct without duplicate titles (adjust() can't tell two
    same-titled tasks apart), so with max_retries=1 the planner should give up
    within budget rather than loop -- this checks the honest-rejection path."""
    dog = Pet(name="Zeke", species="dog", age=2)
    tasks = [
        make_task("Dose", 1, "08:00", 10, description="medication", pets=[dog]),
        make_task("Dose", 1, "08:05", 10, description="medication", pets=[dog]),
    ]
    return planner_for(dog, tasks, max_retries=1).run()


def check_rejected_within_budget(result):
    if result.accepted:
        return False, 0.0, "expected REJECTED (ambiguous same-titled violation), got ACCEPTED"
    if result.iterations > 1:
        return False, 0.5, f"expected to stop within max_retries=1, ran {result.iterations} iteration(s)"
    return True, 1.0, f"rejected honestly after {result.iterations} iteration(s), no unsafe schedule returned"


# ── Retriever scenarios ─────────────────────────────────────────────────────

def case_retriever_labrador_puppy():
    r = Retriever()
    return r.answer("how often should I walk a labrador puppy", species="dog", breed="labrador")


def check_answer_contains(*keywords):
    def _check(answer):
        lower = answer.lower()
        missing = [k for k in keywords if k not in lower]
        if missing:
            return False, 0.0, f"answer missing expected keyword(s) {missing}: {answer!r}"
        return True, 1.0, f"answer matched all expected keywords {list(keywords)}"
    return _check


def case_retriever_cat_not_walked():
    r = Retriever()
    return r.answer("how often should I walk my cat", species="cat")


def case_retriever_unknown_topic():
    r = Retriever()
    return r.answer("what are the tax filing deadlines this year")


def check_answer_declines():
    def _check(answer):
        if "don't have" not in answer.lower():
            return False, 0.0, f"expected an honest 'don't know' answer, got: {answer!r}"
        return True, 1.0, "correctly declined to answer an out-of-scope question"
    return _check


CASES = [
    EvalCase("planner: clean schedule accepted", case_clean_schedule_is_accepted, check_accepted_and_clean),
    EvalCase("planner: medication spacing resolved", case_medication_spacing_resolved, check_resolved_by_removal),
    EvalCase("planner: puppy over-exertion resolved", case_puppy_overexertion_resolved, check_resolved_by_removal),
    EvalCase("planner: species mismatch resolved", case_species_mismatch_resolved, check_resolved_by_removal),
    EvalCase("planner: ambiguous conflict rejected within budget", case_unresolvable_rejected, check_rejected_within_budget),
    EvalCase("retriever: labrador puppy walk advice", case_retriever_labrador_puppy, check_answer_contains("puppy", "labrador")),
    EvalCase("retriever: cat exercise (not walked)", case_retriever_cat_not_walked, check_answer_contains("play")),
    EvalCase("retriever: out-of-scope question declined", case_retriever_unknown_topic, check_answer_declines()),
]


def main():
    print("=" * 70)
    print("  PawPal+ System Evaluation")
    print("=" * 70)

    results = []
    for case in CASES:
        passed, confidence, detail = case.evaluate()
        results.append((case.name, passed, confidence, detail))
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] (confidence={confidence:.2f}) {case.name}")
        print(f"       {detail}")

    total = len(results)
    passed_count = sum(1 for _, passed, _, _ in results if passed)
    avg_confidence = sum(c for _, _, c, _ in results) / total if total else 0.0

    print("-" * 70)
    print(f"SUMMARY: {passed_count}/{total} passed ({passed_count / total * 100:.1f}%), "
          f"avg confidence {avg_confidence:.2f}")

    if passed_count < total:
        failed = [name for name, passed, _, _ in results if not passed]
        print(f"FAILED: {', '.join(failed)}")

    return 0 if passed_count == total else 1


if __name__ == "__main__":
    sys.exit(main())
