import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from pawpal_system import Owner, Pet, Scheduler, Task
from retriever import Retriever
from validator import SafetyViolation, Validator
from llm_assistant import LLMAssistant, ProposalError


class FakeLLMClient:
    """Hand-written fake satisfying the LLMClient protocol -- no SDK import."""

    def __init__(self, json_response=None, text_response="a helpful answer"):
        self.json_response = json_response
        self.text_response = text_response
        self.last_text_prompt = None
        self.last_json_prompt = None

    def generate_text(self, prompt):
        self.last_text_prompt = prompt
        return self.text_response

    def generate_json(self, prompt, schema):
        self.last_json_prompt = prompt
        return self.json_response


def make_owner_with_walk():
    luna = Pet(name="Luna", species="dog", age=3)
    owner = Owner(name="Jordan", availableHours=8, pets=[luna])
    walk = Task(
        title="Morning walk", durationMinutes=20, priority=1, description="",
        recurrence="daily", startTime="09:00", appliesTo=[luna],
    )
    owner.addTask(walk)
    return owner, luna, walk


def make_assistant(json_response=None, text_response="a helpful answer"):
    owner, luna, walk = make_owner_with_walk()
    scheduler = Scheduler(owner=owner, pets=list(owner.pets), tasks=list(owner.tasks))
    retriever = Retriever()
    client = FakeLLMClient(json_response=json_response, text_response=text_response)
    assistant = LLMAssistant(scheduler=scheduler, retriever=retriever, client=client)
    return assistant, owner, luna, walk, client


def snapshot(owner, scheduler):
    return (list(owner.tasks), list(scheduler.tasks))


# ── answer_question ─────────────────────────────────────────────────────────

def test_answer_question_grounds_prompt_in_facts_and_pets():
    assistant, owner, luna, walk, client = make_assistant(text_response="Labradors need short walks.")
    before = snapshot(owner, assistant.scheduler)

    result = assistant.answer_question("how often should I walk a labrador puppy", active_species="dog")

    assert result == "Labradors need short walks."
    assert "Luna" in client.last_text_prompt
    assert "Morning walk" in client.last_text_prompt
    assert snapshot(owner, assistant.scheduler) == before


# ── propose_edit ─────────────────────────────────────────────────────────────

def test_propose_edit_happy_path_edit():
    data = {
        "action": "edit", "target_title": "Morning walk", "startTime": "08:00",
        "explanation": "Move the walk earlier.",
    }
    assistant, owner, luna, walk, client = make_assistant(json_response=data)

    proposed = assistant.propose_edit("move Luna's walk to 8am")

    assert proposed.action == "edit"
    assert proposed.target_title == "Morning walk"
    assert proposed.task.startTime == "08:00"
    assert proposed.task.title == "Morning walk"  # carried over from the existing task


def test_propose_edit_happy_path_add():
    data = {
        "action": "add", "title": "Grooming", "durationMinutes": 30, "priority": "low",
        "startTime": "14:00", "appliesTo": ["Luna"], "explanation": "Add a grooming session.",
    }
    assistant, owner, luna, walk, client = make_assistant(json_response=data)

    proposed = assistant.propose_edit("add a grooming session for Luna at 2pm")

    assert proposed.action == "add"
    assert proposed.task.title == "Grooming"
    assert proposed.task.appliesTo == [luna]


def test_propose_edit_happy_path_remove():
    data = {"action": "remove", "target_title": "Morning walk", "explanation": "Remove the walk."}
    assistant, owner, luna, walk, client = make_assistant(json_response=data)

    proposed = assistant.propose_edit("remove the morning walk")

    assert proposed.action == "remove"
    assert proposed.target_title == "Morning walk"
    assert proposed.task is None


def test_propose_edit_unknown_target_title_raises():
    data = {"action": "edit", "target_title": "Nonexistent task", "explanation": "..."}
    assistant, *_ = make_assistant(json_response=data)

    with pytest.raises(ProposalError):
        assistant.propose_edit("move the nonexistent task")


def test_propose_edit_unknown_pet_raises():
    data = {
        "action": "add", "title": "Feed", "durationMinutes": 10, "priority": "medium",
        "startTime": "08:00", "appliesTo": ["Mochi"], "explanation": "...",
    }
    assistant, *_ = make_assistant(json_response=data)

    with pytest.raises(ProposalError):
        assistant.propose_edit("feed Mochi")


def test_propose_edit_clarification_needed_raises():
    data = {"action": "none", "clarification_needed": "Which pet do you mean?", "explanation": ""}
    assistant, *_ = make_assistant(json_response=data)

    with pytest.raises(ProposalError):
        assistant.propose_edit("do the thing")


def test_propose_edit_bad_time_format_raises():
    data = {
        "action": "add", "title": "Feed", "durationMinutes": 10, "priority": "medium",
        "startTime": "not-a-time", "appliesTo": ["Luna"], "explanation": "...",
    }
    assistant, *_ = make_assistant(json_response=data)

    with pytest.raises(ProposalError):
        assistant.propose_edit("feed Luna at some point")


# ── check_edit (side-effect-free) ───────────────────────────────────────────

def test_check_edit_flags_puppy_exertion_violation_and_does_not_mutate():
    puppy = Pet(name="Rex", species="dog", age=0)
    owner = Owner(name="Jordan", availableHours=8, pets=[puppy])
    scheduler = Scheduler(owner=owner, pets=list(owner.pets), tasks=[])
    assistant = LLMAssistant(scheduler=scheduler, retriever=Retriever(), client=FakeLLMClient())

    proposed_task = Task(
        title="Long walk", durationMinutes=60, priority=1, description="",
        recurrence="once", startTime="09:00", appliesTo=[puppy],
    )
    from llm_assistant import ProposedEdit
    proposed = ProposedEdit(action="add", target_title=None, task=proposed_task, explanation="", raw={})

    before = snapshot(owner, scheduler)
    violations = assistant.check_edit(proposed)

    assert violations
    assert any("puppy exertion" in v for v in violations)
    assert snapshot(owner, scheduler) == before


# ── commit_edit ──────────────────────────────────────────────────────────────

def test_commit_edit_cannot_be_bypassed_by_construction():
    """Even calling commit_edit directly (skipping check_edit) on an unsafe
    proposal must raise SafetyViolation and leave state untouched -- mirrors
    AIPlanner's 'cannot bypass the validator by construction' guarantee."""
    puppy = Pet(name="Rex", species="dog", age=0)
    owner = Owner(name="Jordan", availableHours=8, pets=[puppy])
    scheduler = Scheduler(owner=owner, pets=list(owner.pets), tasks=[])
    assistant = LLMAssistant(scheduler=scheduler, retriever=Retriever(), client=FakeLLMClient())

    unsafe_task = Task(
        title="Long walk", durationMinutes=60, priority=1, description="",
        recurrence="once", startTime="09:00", appliesTo=[puppy],
    )
    from llm_assistant import ProposedEdit
    proposed = ProposedEdit(action="add", target_title=None, task=unsafe_task, explanation="", raw={})

    before = snapshot(owner, scheduler)
    with pytest.raises(SafetyViolation):
        assistant.commit_edit(proposed)

    assert snapshot(owner, scheduler) == before


def test_commit_edit_add_keeps_owner_and_scheduler_in_sync():
    assistant, owner, luna, walk, client = make_assistant()
    from llm_assistant import ProposedEdit
    new_task = Task(
        title="Grooming", durationMinutes=30, priority=3, description="",
        recurrence="once", startTime="14:00", appliesTo=[luna],
    )
    proposed = ProposedEdit(action="add", target_title=None, task=new_task, explanation="added", raw={})

    assistant.commit_edit(proposed)

    assert new_task in owner.tasks
    assert new_task in assistant.scheduler.tasks


def test_commit_edit_edit_mutates_shared_task_object():
    assistant, owner, luna, walk, client = make_assistant()
    from llm_assistant import ProposedEdit
    updated_task = Task(
        title="Morning walk", durationMinutes=20, priority=1, description="",
        recurrence="daily", startTime="08:00", appliesTo=[luna],
    )
    proposed = ProposedEdit(action="edit", target_title="Morning walk", task=updated_task, explanation="moved", raw={})

    assistant.commit_edit(proposed)

    owner_task = next(t for t in owner.tasks if t.title == "Morning walk")
    scheduler_task = next(t for t in assistant.scheduler.tasks if t.title == "Morning walk")
    assert owner_task.startTime == "08:00"
    assert scheduler_task.startTime == "08:00"
    assert owner_task is scheduler_task


def test_commit_edit_remove_keeps_owner_and_scheduler_in_sync():
    assistant, owner, luna, walk, client = make_assistant()
    from llm_assistant import ProposedEdit
    proposed = ProposedEdit(action="remove", target_title="Morning walk", task=None, explanation="removed", raw={})

    assistant.commit_edit(proposed)

    assert all(t.title != "Morning walk" for t in owner.tasks)
    assert all(t.title != "Morning walk" for t in assistant.scheduler.tasks)


# ── logging ──────────────────────────────────────────────────────────────────

def test_propose_edit_logs_llm_proposal(caplog):
    import logging
    data = {"action": "edit", "target_title": "Morning walk", "startTime": "08:00", "explanation": "move it"}
    assistant, *_ = make_assistant(json_response=data)

    with caplog.at_level(logging.INFO, logger="pawpal.scheduler"):
        assistant.propose_edit("move it")

    proposals = [r for r in caplog.records if getattr(r, "decision", None) == "LLM_PROPOSAL"]
    assert proposals


def test_check_edit_logs_llm_rejection(caplog):
    import logging
    puppy = Pet(name="Rex", species="dog", age=0)
    owner = Owner(name="Jordan", availableHours=8, pets=[puppy])
    scheduler = Scheduler(owner=owner, pets=list(owner.pets), tasks=[])
    assistant = LLMAssistant(scheduler=scheduler, retriever=Retriever(), client=FakeLLMClient())
    from llm_assistant import ProposedEdit
    proposed_task = Task(
        title="Long walk", durationMinutes=60, priority=1, description="",
        recurrence="once", startTime="09:00", appliesTo=[puppy],
    )
    proposed = ProposedEdit(action="add", target_title=None, task=proposed_task, explanation="", raw={})

    with caplog.at_level(logging.INFO, logger="pawpal.scheduler"):
        assistant.check_edit(proposed)

    rejections = [r for r in caplog.records if getattr(r, "decision", None) == "LLM_REJECTION"]
    assert rejections


def test_commit_edit_logs_llm_acceptance(caplog):
    import logging
    assistant, owner, luna, walk, client = make_assistant()
    from llm_assistant import ProposedEdit
    proposed = ProposedEdit(action="remove", target_title="Morning walk", task=None, explanation="removed", raw={})

    with caplog.at_level(logging.INFO, logger="pawpal.scheduler"):
        assistant.commit_edit(proposed)

    acceptances = [r for r in caplog.records if getattr(r, "decision", None) == "LLM_ACCEPTANCE"]
    assert acceptances
