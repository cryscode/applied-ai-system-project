# PawPal+ Project Reflection

## 1. System Design

**a. Initial design**

Core actions:
- Add a task
- Schedule tasks ahead
- Have a to-do list with constraints taken into account
- Track tasks to ensure pet is healthy

- Briefly describe your initial UML design.
- What classes did you include, and what responsibilities did you assign to each?

Classes:
- **Owner**: Manages scheduling preferences, available hours, and a master task list. Serves as the top-level entry point for the system — all tasks and pets flow through the owner.
- **Pet**: Represents an individual pet with species, age, dietary needs, and special care requirements. Keeps pet-specific data isolated so the scheduler can query it per task.
- **Task**: Individual care tasks carrying priority, duration, start time, recurrence rules, and references to which pets the task applies to. Acts as the unit of work the scheduler operates on.
- **ScheduledTask**: A wrapper around a Task that binds it to a concrete time slot and a specific pet instance. Also carries the scheduler's reasoning for placing it there.
- **Scheduler**: Orchestrates the planning — takes an owner and pet list, sorts and filters tasks by priority and available time, generates a daily schedule, and detects conflicts.

**b. Design changes**

- Did your design change during implementation?
- If yes, describe at least one change and why you made it.

Yes, several gaps in the original UML were addressed during implementation:

1. **No Owner-Pet binding** — The original UML had Scheduler referencing both owner and pets independently, with no way to know which pets belonged to which owner. This was fixed by giving Owner a `pets` list so the relationship is explicit, and Scheduler derives its pet list from the owner rather than maintaining a separate one.

2. **Task had no Pet reference** — The original Task had no way to indicate which pet it was for, making scheduling and filtering meaningless. A `appliesTo: List[Pet]` field was added to Task so conflict detection and filtering can be done per-pet.

3. **ScheduledTask.reasoning vs Scheduler.getReasoningFor()** — The UML had both, which was redundant. The final design stores reasoning directly on `ScheduledTask.reasoning` and removes the lookup method — callers just read the attribute directly.

---

## 2. Scheduling Logic and Tradeoffs

**a. Constraints and priorities**

- What constraints does your scheduler consider (for example: time, priority, preferences)?
- How did you decide which constraints mattered most?

The scheduler considers three main constraints:

1. **Priority** (most important): Tasks are sorted by `priority` (1 = high, 3 = low) first. A pet's medication or vet visit always ranks above grooming or a leisure walk. This was the most critical constraint because missing high-priority care has real health consequences.

2. **Owner available hours**: `filterByTimeAvailable()` accumulates task durations and drops any task that would push the owner past their declared `availableHours`. This prevents scheduling more than the owner can realistically do in a day.

3. **Time slot / start time**: `sort_by_time()` and `sortTasks()` both use the task's `startTime` string as a secondary sort key, respecting the owner's preferred schedule (e.g., morning feeding before afternoon walk).

Priority took precedence because available time is fixed — if something must be dropped, dropping a low-priority task is always less harmful than dropping a critical one.

**b. Tradeoffs**

- Describe one tradeoff your scheduler makes.

The `detectConflicts()` method pre-parses all tasks into a `(task, start_minutes, end_minutes)` list before running comparisons. This materializes the full list in memory before any comparison happens. With the original nested loop, each `to_minutes` result was computed on-demand and immediately discarded.

- Why is that tradeoff reasonable for this scenario?

Using `itertools.combinations` with a pre-parsed list removes the nested `for` loop entirely, making the comparisons clean and readable. For a daily pet-care schedule with at most a few dozen tasks, the in-memory cost of holding the parsed tuples is negligible. The tradeoff — slightly more memory use for cleaner O(n²) pair comparisons with no redundant computation — is completely reasonable at this scale, and the code is much easier to audit for correctness.

---

## 3. AI Collaboration

**a. How you used AI**

- How did you use AI tools during this project?
- What kinds of prompts or questions were most helpful?

AI was used across every phase of the project, but the role it played shifted at each phase:

- **Design phase**: I shared the UML diagram and asked the AI to generate dataclass skeletons. Prompts like *"Generate Python dataclasses from this UML with proper field types and method stubs"* were highly effective — they produced boilerplate instantly so I could focus on the actual logic.
- **Implementation phase**: I described the behavior I wanted in plain language (*"Filter tasks to only those that fit within the owner's available hours, sorted by priority"*) and let the AI draft the method. I then read it critically and adjusted.
- **Debugging phase**: Paste-and-explain prompts (*"Why does this conflict detection flag adjacent tasks incorrectly?"*) helped me spot edge cases in interval math quickly.
- **Testing phase**: Prompts like *"Write pytest tests for `detectConflicts()` covering overlap, partial overlap, adjacency, and the empty case"* generated a thorough test class that I reviewed and tightened.

The most effective prompts were **behavior-driven**: describing what the function should do, not how to write it. This gave the AI enough freedom to choose an idiomatic approach while keeping me in control of the contract.

**Which AI coding assistant features were most effective for building your scheduler?**

Three features stood out:

1. **Code generation from natural-language specs**: Describing a method's contract in plain English and getting a full, runnable draft in seconds. This was most valuable for boilerplate-heavy methods like `to_dict()` / `from_dict()` across four classes.

2. **Instant test scaffolding**: Asking for a pytest class for a given method produced organized test cases (happy path, edge cases, error cases) that I wouldn't have thought of as exhaustively on my own. The `TestDetectConflicts` class with seven distinct cases is a direct example.

3. **Refactoring suggestions**: When I described a performance concern (*"this nested loop feels inefficient"*), the AI immediately proposed the `itertools.combinations` pattern and the pre-parsed tuple list, which I adopted after evaluating the tradeoff.

**b. Judgment and verification**

- Give one example of an AI suggestion you rejected or modified to keep your system design clean.

When I asked for conflict detection, the AI's first suggestion stored reasoning inside a separate `Scheduler.getReasoningFor(task_id)` lookup method and also on `ScheduledTask.reasoning`, keeping both in sync. The suggestion was technically functional but introduced **dual sources of truth** — if one was updated without the other, the system would silently return stale reasoning.

I rejected the lookup method entirely. The `ScheduledTask` already carries `reasoning` as a field, so callers can simply read `scheduled_task.reasoning` directly. The lookup method was redundant complexity that would have made the codebase harder to maintain without adding any capability. Keeping state in one place is a basic design principle that the AI's suggestion violated in the name of API convenience.

I evaluated it by asking: *"If I update `ScheduledTask.reasoning`, does `getReasoningFor()` stay consistent without extra code?"* The answer was no — so the method was dropped.

**How did using separate chat sessions for different phases help you stay organized?**

Splitting sessions by phase prevented the conversation context from becoming a tangled mix of concerns. Each session had a single, clear purpose:

- **Session 1 — UML & design**: Focused entirely on class responsibilities and relationships. No implementation code existed yet, which forced every decision to be made at the design level rather than being driven by what was easy to code.
- **Session 2 — Implementation**: Started fresh with the skeleton files already in place. The AI's suggestions were grounded in the actual class signatures rather than hypothetical ones, so there was no drift between what was discussed and what existed in the files.
- **Session 3 — Testing & refinement**: With working code already present, this session was purely about coverage and edge cases. Starting fresh prevented the AI from "remembering" implementation shortcuts and instead let it treat the public interface as a specification to test against.

The discipline of separate sessions also made it easier to track which decisions were mine versus AI-suggested, which is important when you need to explain or defend a design choice.

**Summarize what you learned about being the "lead architect" when collaborating with powerful AI tools.**

The most important lesson: **the AI is a fast executor, not a decision-maker**. Left unconstrained, it will produce plausible, working code that may quietly violate the design principles you care about — like the dual-source reasoning issue above. It optimizes for "code that runs" before it optimizes for "code that fits your architecture."

Being the lead architect means arriving at every AI session with a clear contract: what the function is *for*, what invariants it must preserve, and what it must *not* do. The more precisely I could state those constraints upfront, the less post-generation editing was needed.

It also means reading every suggestion with the question: *"Does this fit where we're going, or just where we are right now?"* AI tools are excellent at solving the immediate problem. They are much weaker at reasoning about how today's solution will interact with tomorrow's feature. That judgment — knowing when a clean two-line solution is worth more than an efficient five-line one — is what the human architect provides and the AI cannot.

In short: the AI raised my output speed dramatically, but only because I was willing to slow down at the design stage, own the architecture decisions, and treat the AI's output as a draft to review rather than a final answer to accept.

---

## 4. Testing and Verification

**a. What you tested**

- What behaviors did you test?

The test suite covered six behavioral areas:

1. **Sorting correctness** (`TestSortTasks`): Verified that `sortTasks()` places high-priority tasks first, breaks ties by start time, and breaks further ties by longer duration. Also confirmed the method does not mutate the original task list.

2. **Recurrence logic** (`TestMarkComplete`): Verified that completing a daily task produces a next occurrence due one day later, that the next occurrence starts as incomplete, and that non-recurring tasks return `None`. Also tested case-insensitivity of recurrence strings and the fallback behavior when `dueDate` is `None`.

3. **Conflict detection** (`TestDetectConflicts`): Tested exact overlaps, partial overlaps, same-pet conflicts (should name the pet), different-pet conflicts (should label as "owner time"), adjacent tasks (must not be flagged), and the empty/single-task base cases.

4. **Owner task lifecycle** (`TestOwnerCompleteTask`, `TestGetTodoList`): Verified that completing a once task removes it, completing a daily task replaces it with the next occurrence, unknown titles return `None` gracefully, and `getTodoList()` only returns incomplete tasks.

5. **Schedule generation** (`TestGenerateSchedule`): Verified sequential placement, that completed tasks are skipped, that tasks exceeding `dayHours` are excluded, and that `owner.availableHours` caps the schedule even when `dayHours` is larger.

6. **Filtering and time-based sorting** (`TestFilterByTimeAvailable`, `TestFilterTasks`, `TestSortByTime`): Verified that capacity filtering respects priority order, that `filterTasks()` supports combined `completed` + `pet_name` filters, and that `sort_by_time()` is stable with respect to priority when start times are equal.

- Why were these tests important?

These tests are important because scheduling has many subtle invariants — adjacent tasks must not be flagged as conflicts, high-priority tasks must survive when capacity is tight, recurring tasks must not lose their recurrence pattern when completing. Without tests, any refactor risks silently breaking one of these invariants. The tests act as a living specification of the scheduler's contract.

**b. Confidence**

- How confident are you that your scheduler works correctly?

Reasonably confident for the behaviors tested. The combination of task-level unit tests and scheduler-level integration tests covers the most critical paths. Conflict detection in particular has thorough coverage across all overlap geometries.

- What edge cases would you test next if you had more time?

1. Tasks that span midnight (e.g., `startTime="23:30"`, `durationMinutes=60`) — the current time math likely produces an `end_time` of `24:30`, which is invalid.
2. An owner with multiple pets where the same task applies to all of them simultaneously — does conflict detection correctly attribute the pet names?
3. JSON round-trip fidelity for a `Task` with `dueDate` set and multiple pets in `appliesTo` — verify that `from_dict(to_dict())` produces an object equal to the original.
4. `generateSchedule()` when `dayHours=0` — should return an empty list without an error.

---

## 5. Reflection

**a. What went well**

- What part of this project are you most satisfied with?

The conflict detection implementation. The final `detectConflicts()` method is clean, correct, and readable — it pre-parses tasks into `(task, start, end)` tuples once, then uses `itertools.combinations` to check every pair in a single pass. It correctly distinguishes same-pet conflicts from owner-time conflicts and produces human-readable warning messages. It is also well-tested across seven distinct scenarios. That method started as a messy nested loop and ended as something I would not be embarrassed to show in a code review.

**b. What you would improve**

- If you had another iteration, what would you redesign?

The `generateSchedule()` method needs a redesign. Currently it converts task durations to hour-blocks using `max(1, durationMinutes // 60)`, which means a 30-minute task occupies the same slot as a 60-minute task. The scheduler should work in minutes throughout — slot boundaries should be computed from `startTime` strings, not from sequential hour-block counters. This would make `generateSchedule()` consistent with how `detectConflicts()` already works (full minute-resolution intervals), and would let the schedule output match what the user actually configured for each task.

**c. Key takeaway**

- What is one important thing you learned about designing systems or working with AI on this project?

Design decisions made at the class level — what fields a dataclass has, what relationships exist between classes, what a method's return type is — are far more expensive to change later than logic decisions made inside a method body. The `appliesTo` field on `Task` is a perfect example: adding it mid-project required touching `Task`, `to_dict`, `from_dict`, `detectConflicts`, `filterTasks`, `generateSchedule`, and every test that constructs a `Task`. The AI can help you implement whatever design you give it, but it cannot tell you that a missing field will cause rippling changes six files later. That foresight is the architect's job, and it is best exercised before any code is written.
