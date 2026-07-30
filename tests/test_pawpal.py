import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import datetime, timedelta
from pawpal_system import Owner, Pet, Task, Scheduler, ScheduledTask


def test_mark_complete_changes_status():
    """Calling markComplete() should flip isCompleted from False to True."""
    task = Task(title="Feed cat", durationMinutes=10, priority=1, description="Morning feeding")
    assert task.isCompleted is False
    task.markComplete()
    assert task.isCompleted is True


def test_adding_task_to_pet_increases_task_count():
    """Adding a task that applies to a pet should increase the pet's task count."""
    pet = Pet(name="Whiskers", species="Cat", age=3)
    owner = Owner(name="Alice", availableHours=8)

    tasks_for_pet = lambda: [t for t in owner.tasks if pet in t.appliesTo]
    assert len(tasks_for_pet()) == 0

    task = Task(title="Groom Whiskers", durationMinutes=15, priority=2, description="Weekly grooming", appliesTo=[pet])
    owner.addTask(task)

    assert len(tasks_for_pet()) == 1


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_task(title, priority, start_time="08:00", duration=60, recurrence="once", due=None, pets=None):
    return Task(
        title=title,
        durationMinutes=duration,
        priority=priority,
        description="",
        recurrence=recurrence,
        startTime=start_time,
        dueDate=due,
        appliesTo=pets or [],
    )


@pytest.fixture
def pet():
    return Pet(name="Mochi", species="dog", age=3)


@pytest.fixture
def owner():
    return Owner(name="Jordan", availableHours=8)


@pytest.fixture
def scheduler(owner, pet):
    return Scheduler(owner=owner, pets=[pet])


# ── Sorting Correctness ───────────────────────────────────────────────────────

class TestSortTasks:
    def test_happy_path_higher_priority_first(self, scheduler):
        """Priority 1 (high) task should come before priority 3 (low) task."""
        low = make_task("Low task", priority=3, start_time="07:00")
        high = make_task("High task", priority=1, start_time="07:00")
        scheduler.tasks = [low, high]
        result = scheduler.sortTasks()
        assert result[0].title == "High task"
        assert result[1].title == "Low task"

    def test_equal_priority_sorted_by_start_time(self, scheduler):
        """When priority is tied, earlier startTime string comes first."""
        afternoon = make_task("Afternoon feed", priority=2, start_time="14:00")
        morning = make_task("Morning walk", priority=2, start_time="08:00")
        scheduler.tasks = [afternoon, morning]
        result = scheduler.sortTasks()
        assert result[0].title == "Morning walk"
        assert result[1].title == "Afternoon feed"

    def test_equal_priority_and_time_longer_duration_first(self, scheduler):
        """Same priority and startTime: longer duration task is sorted first."""
        short = make_task("Quick brush", priority=1, start_time="09:00", duration=15)
        long_ = make_task("Long walk", priority=1, start_time="09:00", duration=90)
        scheduler.tasks = [short, long_]
        result = scheduler.sortTasks()
        assert result[0].title == "Long walk"

    def test_empty_task_list_returns_empty(self, scheduler):
        scheduler.tasks = []
        assert scheduler.sortTasks() == []

    def test_single_task_returned_as_is(self, scheduler):
        task = make_task("Solo task", priority=2)
        scheduler.tasks = [task]
        assert scheduler.sortTasks() == [task]

    def test_original_task_list_not_mutated(self, scheduler):
        """sortTasks() must not reorder self.tasks in place."""
        t1 = make_task("Added first", priority=3)
        t2 = make_task("Added second", priority=1)
        scheduler.tasks = [t1, t2]
        scheduler.sortTasks()
        assert scheduler.tasks[0].title == "Added first"


# ── Recurrence Logic ──────────────────────────────────────────────────────────

class TestMarkComplete:
    def test_happy_path_daily_creates_next_day(self):
        """Completing a daily task returns a new task due exactly one day later."""
        due = datetime(2026, 6, 30, 8, 0)
        task = make_task("Morning walk", priority=1, recurrence="daily", due=due)
        next_task = task.markComplete()
        assert next_task is not None
        assert next_task.dueDate == due + timedelta(days=1)

    def test_next_daily_occurrence_is_not_completed(self):
        """The returned next occurrence must start with isCompleted=False."""
        due = datetime(2026, 6, 30)
        task = make_task("Morning walk", priority=1, recurrence="daily", due=due)
        next_task = task.markComplete()
        assert next_task.isCompleted is False

    def test_current_task_flagged_completed(self):
        """markComplete() must flip isCompleted to True on the original task."""
        task = make_task("Morning walk", priority=1, recurrence="daily", due=datetime(2026, 6, 30))
        task.markComplete()
        assert task.isCompleted is True

    def test_weekly_task_creates_next_week(self):
        due = datetime(2026, 6, 30)
        task = make_task("Bath time", priority=2, recurrence="weekly", due=due)
        next_task = task.markComplete()
        assert next_task.dueDate == due + timedelta(weeks=7 // 7)

    def test_once_task_returns_none(self):
        """A non-recurring task must return None — no follow-up created."""
        task = make_task("Vet visit", priority=1, recurrence="once")
        assert task.markComplete() is None

    def test_recurrence_check_is_case_insensitive(self):
        """'Daily' and 'DAILY' should both produce a next occurrence."""
        for casing in ("Daily", "DAILY", "dAiLy"):
            task = make_task("Walk", priority=1, recurrence=casing, due=datetime(2026, 6, 30))
            assert task.markComplete() is not None, f"Failed for recurrence={casing!r}"

    def test_daily_without_due_date_falls_back_to_now(self):
        """When dueDate is None, the next occurrence is approximately tomorrow."""
        task = make_task("Feed", priority=1, recurrence="daily", due=None)
        before = datetime.now()
        next_task = task.markComplete()
        assert next_task is not None
        expected = before + timedelta(days=1)
        assert abs((next_task.dueDate - expected).total_seconds()) < 5


# ── Conflict Detection ────────────────────────────────────────────────────────

class TestDetectConflicts:
    def test_happy_path_exact_overlap_flagged(self, scheduler, pet):
        """Two tasks starting at the same time must produce one conflict warning."""
        t1 = make_task("Walk", priority=1, start_time="08:00", duration=60, pets=[pet])
        t2 = make_task("Feed", priority=1, start_time="08:00", duration=30, pets=[pet])
        scheduler.tasks = [t1, t2]
        assert len(scheduler.detectConflicts()) == 1

    def test_partial_overlap_flagged(self, scheduler, pet):
        """Task B starting before Task A ends is a conflict."""
        t1 = make_task("Walk", priority=1, start_time="08:00", duration=90, pets=[pet])   # 08:00–09:30
        t2 = make_task("Feed", priority=2, start_time="09:00", duration=60, pets=[pet])   # 09:00–10:00
        scheduler.tasks = [t1, t2]
        assert len(scheduler.detectConflicts()) == 1

    def test_same_pet_conflict_mentions_pet_name(self, scheduler, pet):
        """Overlap on the same pet should reference that pet in the warning."""
        t1 = make_task("Walk", priority=1, start_time="09:00", duration=60, pets=[pet])
        t2 = make_task("Brush", priority=2, start_time="09:30", duration=60, pets=[pet])
        scheduler.tasks = [t1, t2]
        conflict = scheduler.detectConflicts()[0]
        assert "same pet" in conflict.lower() or pet.name in conflict

    def test_different_pets_overlap_is_owner_time_conflict(self, scheduler):
        """Overlap between different-pet tasks is labelled as an owner-time conflict."""
        rex = Pet("Rex", "dog", 2)
        luna = Pet("Luna", "cat", 4)
        t1 = make_task("Walk Rex", priority=1, start_time="10:00", duration=60, pets=[rex])
        t2 = make_task("Feed Luna", priority=1, start_time="10:00", duration=30, pets=[luna])
        scheduler.tasks = [t1, t2]
        conflict = scheduler.detectConflicts()[0]
        assert "owner time" in conflict.lower()

    def test_adjacent_tasks_not_flagged(self, scheduler, pet):
        """Back-to-back tasks with no gap should produce zero conflicts."""
        t1 = make_task("Walk", priority=1, start_time="08:00", duration=60, pets=[pet])   # ends 09:00
        t2 = make_task("Feed", priority=2, start_time="09:00", duration=30, pets=[pet])   # starts 09:00
        scheduler.tasks = [t1, t2]
        assert scheduler.detectConflicts() == []

    def test_no_tasks_no_conflicts(self, scheduler):
        scheduler.tasks = []
        assert scheduler.detectConflicts() == []

    def test_single_task_no_conflicts(self, scheduler, pet):
        scheduler.tasks = [make_task("Walk", priority=1, start_time="08:00", duration=60, pets=[pet])]
        assert scheduler.detectConflicts() == []


# ── Owner.completeTask ────────────────────────────────────────────────────────

class TestOwnerCompleteTask:
    def test_happy_path_once_task_removed_from_tasks(self, owner):
        """Completing a non-recurring task removes it from owner.tasks entirely."""
        task = make_task("Vet visit", priority=1, recurrence="once")
        owner.addTask(task)
        owner.completeTask("Vet visit")
        assert all(t.title != "Vet visit" for t in owner.tasks)

    def test_once_task_returns_none(self, owner):
        """completeTask() on a non-recurring task returns None."""
        owner.addTask(make_task("Vet visit", priority=1, recurrence="once"))
        result = owner.completeTask("Vet visit")
        assert result is None

    def test_daily_task_replaced_by_next_occurrence(self, owner):
        """Completing a daily task removes the old one and adds the next occurrence."""
        due = datetime(2026, 6, 30)
        owner.addTask(make_task("Morning walk", priority=1, recurrence="daily", due=due))
        owner.completeTask("Morning walk")
        titles = [t.title for t in owner.tasks]
        assert "Morning walk" in titles

    def test_daily_task_next_occurrence_due_tomorrow(self, owner):
        due = datetime(2026, 6, 30)
        owner.addTask(make_task("Morning walk", priority=1, recurrence="daily", due=due))
        next_task = owner.completeTask("Morning walk")
        assert next_task.dueDate == due + timedelta(days=1)

    def test_daily_next_occurrence_is_not_completed(self, owner):
        owner.addTask(make_task("Feed", priority=1, recurrence="daily", due=datetime(2026, 6, 30)))
        next_task = owner.completeTask("Feed")
        assert next_task.isCompleted is False

    def test_unknown_title_returns_none(self, owner):
        """completeTask() with a title that doesn't exist returns None without error."""
        result = owner.completeTask("Nonexistent task")
        assert result is None

    def test_unknown_title_does_not_change_task_list(self, owner):
        owner.addTask(make_task("Walk", priority=1, recurrence="once"))
        owner.completeTask("Nonexistent task")
        assert len(owner.tasks) == 1


# ── Owner.getTodoList ─────────────────────────────────────────────────────────

class TestGetTodoList:
    def test_happy_path_returns_incomplete_tasks(self, owner):
        owner.addTask(make_task("Walk", priority=1, recurrence="once"))
        owner.addTask(make_task("Feed", priority=2, recurrence="once"))
        assert len(owner.getTodoList()) == 2

    def test_completed_task_excluded(self, owner):
        """After completing a task via completeTask(), it must not appear in getTodoList()."""
        owner.addTask(make_task("Vet visit", priority=1, recurrence="once"))
        owner.completeTask("Vet visit")
        assert all(t.title != "Vet visit" for t in owner.getTodoList())

    def test_only_incomplete_tasks_returned(self, owner):
        """Mix of complete and incomplete: only incomplete ones come back."""
        done = make_task("Done task", priority=1, recurrence="once")
        done.isCompleted = True
        pending = make_task("Pending task", priority=2, recurrence="once")
        owner.tasks = [done, pending]
        result = owner.getTodoList()
        assert len(result) == 1
        assert result[0].title == "Pending task"

    def test_all_completed_returns_empty(self, owner):
        task = make_task("Walk", priority=1, recurrence="once")
        task.isCompleted = True
        owner.tasks = [task]
        assert owner.getTodoList() == []

    def test_empty_task_list_returns_empty(self, owner):
        assert owner.getTodoList() == []


# ── Scheduler.generateSchedule ────────────────────────────────────────────────

class TestGenerateSchedule:
    def test_happy_path_returns_scheduled_tasks(self, scheduler, pet):
        """Tasks that fit within dayHours appear in the generated schedule."""
        scheduler.addTask(make_task("Walk", priority=1, start_time="08:00", duration=60, pets=[pet]))
        result = scheduler.generateSchedule(dayHours=8)
        assert len(result) == 1
        assert isinstance(result[0], ScheduledTask)

    def test_tasks_scheduled_in_sequence(self, scheduler, pet):
        """Second task starts where the first one ends."""
        scheduler.addTask(make_task("Walk", priority=1, start_time="08:00", duration=60, pets=[pet]))
        scheduler.addTask(make_task("Feed", priority=2, start_time="09:00", duration=60, pets=[pet]))
        result = scheduler.generateSchedule(dayHours=8)
        assert result[0].endTime == result[1].startTime

    def test_sub_hour_task_occupies_one_hour_slot(self, scheduler, pet):
        """A 30-minute task is rounded up to a 1-hour slot by max(1, duration//60)."""
        scheduler.addTask(make_task("Quick brush", priority=1, start_time="08:00", duration=30, pets=[pet]))
        result = scheduler.generateSchedule(dayHours=8)
        assert result[0].endTime - result[0].startTime == 1

    def test_completed_tasks_skipped(self, scheduler, pet):
        """Tasks already marked complete must not appear in the schedule."""
        done = make_task("Old walk", priority=1, start_time="08:00", duration=60, pets=[pet])
        done.isCompleted = True
        scheduler.addTask(done)
        result = scheduler.generateSchedule(dayHours=8)
        assert result == []

    def test_task_exceeding_day_hours_excluded(self, scheduler, pet):
        """A task that would push past dayHours must not be scheduled."""
        scheduler.addTask(make_task("Marathon", priority=1, start_time="08:00", duration=600, pets=[pet]))
        result = scheduler.generateSchedule(dayHours=8)
        assert result == []

    def test_empty_task_list_returns_empty_schedule(self, scheduler):
        assert scheduler.generateSchedule(dayHours=8) == []

    def test_schedule_respects_owner_available_hours(self, owner, pet):
        """dayHours larger than owner.availableHours is capped at the owner's limit."""
        owner.availableHours = 2
        s = Scheduler(owner=owner, pets=[pet])
        s.addTask(make_task("Walk", priority=1, start_time="08:00", duration=60, pets=[pet]))
        s.addTask(make_task("Feed", priority=2, start_time="09:00", duration=60, pets=[pet]))
        s.addTask(make_task("Groom", priority=3, start_time="10:00", duration=60, pets=[pet]))
        result = s.generateSchedule(dayHours=8)
        assert len(result) == 2


# ── Scheduler.filterByTimeAvailable ──────────────────────────────────────────

class TestFilterByTimeAvailable:
    def test_happy_path_all_tasks_fit(self, scheduler, pet):
        scheduler.tasks = [
            make_task("Walk", priority=1, duration=60, pets=[pet]),
            make_task("Feed", priority=2, duration=60, pets=[pet]),
        ]
        result = scheduler.filterByTimeAvailable()
        assert len(result) == 2

    def test_task_alone_exceeds_available_hours_is_skipped(self, owner, pet):
        """A single task longer than availableHours*60 minutes must be excluded."""
        owner.availableHours = 1
        s = Scheduler(owner=owner, pets=[pet])
        s.addTask(make_task("Marathon", priority=1, duration=120, pets=[pet]))
        assert s.filterByTimeAvailable() == []

    def test_high_priority_task_fits_low_priority_dropped(self, owner, pet):
        """When capacity is tight, high-priority task is kept and low-priority dropped."""
        owner.availableHours = 1
        s = Scheduler(owner=owner, pets=[pet])
        s.addTask(make_task("Low task", priority=3, duration=60, pets=[pet]))
        s.addTask(make_task("High task", priority=1, duration=60, pets=[pet]))
        result = s.filterByTimeAvailable()
        assert len(result) == 1
        assert result[0].title == "High task"

    def test_zero_available_hours_returns_empty(self, owner, pet):
        owner.availableHours = 0
        s = Scheduler(owner=owner, pets=[pet])
        s.addTask(make_task("Walk", priority=1, duration=60, pets=[pet]))
        assert s.filterByTimeAvailable() == []


# ── Scheduler.filterTasks ─────────────────────────────────────────────────────

class TestFilterTasks:
    def test_no_filters_returns_all_tasks(self, scheduler, pet):
        scheduler.tasks = [
            make_task("Walk", priority=1, pets=[pet]),
            make_task("Feed", priority=2, pets=[pet]),
        ]
        assert len(scheduler.filterTasks()) == 2

    def test_filter_completed_false_returns_only_pending(self, scheduler, pet):
        done = make_task("Done", priority=1, pets=[pet])
        done.isCompleted = True
        pending = make_task("Pending", priority=2, pets=[pet])
        scheduler.tasks = [done, pending]
        result = scheduler.filterTasks(completed=False)
        assert len(result) == 1
        assert result[0].title == "Pending"

    def test_filter_completed_true_returns_only_done(self, scheduler, pet):
        done = make_task("Done", priority=1, pets=[pet])
        done.isCompleted = True
        scheduler.tasks = [done, make_task("Pending", priority=2, pets=[pet])]
        result = scheduler.filterTasks(completed=True)
        assert len(result) == 1
        assert result[0].title == "Done"

    def test_filter_by_pet_name_returns_matching_tasks(self, scheduler, pet):
        other_pet = Pet("Rex", "dog", 2)
        scheduler.tasks = [
            make_task("Walk Mochi", priority=1, pets=[pet]),
            make_task("Walk Rex", priority=1, pets=[other_pet]),
        ]
        result = scheduler.filterTasks(pet_name="Mochi")
        assert len(result) == 1
        assert result[0].title == "Walk Mochi"

    def test_filter_by_pet_name_no_match_returns_empty(self, scheduler, pet):
        scheduler.tasks = [make_task("Walk", priority=1, pets=[pet])]
        assert scheduler.filterTasks(pet_name="Ghost") == []

    def test_combined_filters_applied_together(self, scheduler, pet):
        """completed=False and pet_name together must both be satisfied."""
        other_pet = Pet("Rex", "dog", 2)
        done_mochi = make_task("Done Mochi", priority=1, pets=[pet])
        done_mochi.isCompleted = True
        pending_mochi = make_task("Pending Mochi", priority=2, pets=[pet])
        pending_rex = make_task("Pending Rex", priority=2, pets=[other_pet])
        scheduler.tasks = [done_mochi, pending_mochi, pending_rex]
        result = scheduler.filterTasks(completed=False, pet_name="Mochi")
        assert len(result) == 1
        assert result[0].title == "Pending Mochi"


# ── Scheduler.sort_by_time ────────────────────────────────────────────────────

class TestSortByTime:
    def test_happy_path_earlier_start_time_first(self, scheduler):
        scheduler.tasks = [
            make_task("Afternoon", priority=1, start_time="14:00"),
            make_task("Morning", priority=1, start_time="07:00"),
        ]
        result = scheduler.sort_by_time()
        assert result[0].title == "Morning"
        assert result[1].title == "Afternoon"

    def test_same_time_sorted_by_priority(self, scheduler):
        """When startTime is equal, lower priority number (higher urgency) comes first."""
        scheduler.tasks = [
            make_task("Low", priority=3, start_time="09:00"),
            make_task("High", priority=1, start_time="09:00"),
        ]
        result = scheduler.sort_by_time()
        assert result[0].title == "High"

    def test_empty_list_returns_empty(self, scheduler):
        scheduler.tasks = []
        assert scheduler.sort_by_time() == []

    def test_original_list_not_mutated(self, scheduler):
        t1 = make_task("First added", priority=1, start_time="14:00")
        t2 = make_task("Second added", priority=1, start_time="07:00")
        scheduler.tasks = [t1, t2]
        scheduler.sort_by_time()
        assert scheduler.tasks[0].title == "First added"


# ── Scheduler.removeTask ──────────────────────────────────────────────────────

class TestRemoveTask:
    def test_happy_path_removes_matching_task(self, scheduler, pet):
        scheduler.tasks = [
            make_task("Walk", priority=1, pets=[pet]),
            make_task("Feed", priority=2, pets=[pet]),
        ]
        scheduler.removeTask("Walk")
        assert all(t.title != "Walk" for t in scheduler.tasks)

    def test_other_tasks_remain_after_removal(self, scheduler, pet):
        scheduler.tasks = [
            make_task("Walk", priority=1, pets=[pet]),
            make_task("Feed", priority=2, pets=[pet]),
        ]
        scheduler.removeTask("Walk")
        assert len(scheduler.tasks) == 1
        assert scheduler.tasks[0].title == "Feed"

    def test_nonexistent_title_does_not_raise(self, scheduler, pet):
        scheduler.tasks = [make_task("Walk", priority=1, pets=[pet])]
        scheduler.removeTask("Ghost task")
        assert len(scheduler.tasks) == 1

    def test_empty_list_does_not_raise(self, scheduler):
        scheduler.tasks = []
        scheduler.removeTask("Anything")


# ── Structured scheduling-decision logging ────────────────────────────────────

import logging


class TestSchedulingDecisionLogging:
    def test_proposal_logged_when_task_fits(self, scheduler, pet, caplog):
        scheduler.tasks = [make_task("Walk", priority=1, duration=60, pets=[pet])]
        with caplog.at_level(logging.INFO, logger="pawpal.scheduler"):
            scheduler.generateSchedule(dayHours=8)

        proposals = [r for r in caplog.records if getattr(r, "decision", None) == "PROPOSAL"]
        assert len(proposals) == 1
        assert proposals[0].task == "Walk"
        assert proposals[0].startTime == 0
        assert proposals[0].endTime == 1
        assert proposals[0].pet == "Mochi"

    def test_rejection_logged_for_task_over_owner_budget(self, scheduler, pet, caplog):
        scheduler.owner.availableHours = 1
        scheduler.tasks = [
            make_task("Walk", priority=1, duration=60, pets=[pet]),
            make_task("Overflow", priority=2, duration=120, pets=[pet]),
        ]
        with caplog.at_level(logging.INFO, logger="pawpal.scheduler"):
            scheduler.filterByTimeAvailable()

        rejections = [r for r in caplog.records if getattr(r, "decision", None) == "REJECTION"]
        assert len(rejections) == 1
        assert rejections[0].task == "Overflow"
        assert "budget" in rejections[0].reason

    def test_adjustment_logged_when_conflict_resolved(self, scheduler, pet, caplog):
        task_a = make_task("First", priority=1, duration=90, pets=[pet])
        task_b = make_task("Second", priority=2, duration=60, pets=[pet])
        scheduler.schedule = [
            ScheduledTask(task=task_a, pet=pet, startTime=0, endTime=1),
            ScheduledTask(task=task_b, pet=pet, startTime=0, endTime=1),
        ]
        with caplog.at_level(logging.INFO, logger="pawpal.scheduler"):
            scheduler.handleConflicts()

        adjustments = [r for r in caplog.records if getattr(r, "decision", None) == "ADJUSTMENT"]
        assert len(adjustments) == 1
        assert adjustments[0].task == "Second"
        assert "conflict" in adjustments[0].reason
