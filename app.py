import streamlit as st
from dotenv import load_dotenv

from pawpal_system import Owner, Task, Pet, Scheduler
from retriever import Retriever
from llm_assistant import LLMAssistant, ProposalError
from gemini_client import GeminiClient, GeminiError, GeminiMalformedResponseError
from validator import SafetyViolation
from pawpal_logger import LLM_ERROR, get_logger, log_decision

load_dotenv()

DATA_FILE = "data.json"

st.set_page_config(page_title="PawPal+", page_icon="🐾", layout="centered")

st.title("🐾 PawPal+")


# ── Persistent system: load once, then save after every change ────────────────
def save():
    """Persist the owner, pets, and tasks to disk."""
    st.session_state.scheduler.save_to_json(DATA_FILE)


if "scheduler" not in st.session_state:
    try:
        # Restore the saved system (owner + pets + tasks) from a previous run.
        scheduler = Scheduler.load_from_json(DATA_FILE)
    except (FileNotFoundError, ValueError, KeyError):
        # First ever run (or unreadable file): create the single owner, no pets yet.
        scheduler = Scheduler(owner=Owner(name="Jordan", availableHours=8))
    # Share ONE pet list between owner and scheduler so appends stay in sync.
    scheduler.pets = scheduler.owner.pets
    st.session_state.scheduler = scheduler

if "retriever" not in st.session_state:
    st.session_state.retriever = Retriever()

scheduler = st.session_state.scheduler
retriever = st.session_state.retriever
owner = scheduler.owner

PRIORITY_MAP = {"high": 1, "medium": 2, "low": 3}
PRIORITY_LABEL = {1: "high", 2: "medium", 3: "low"}

# ── Owner (one persistent owner — edited in place, never re-created) ───────────
st.subheader("Owner")

owner_name = st.text_input("Owner name", value=owner.name)
available_hours = st.number_input(
    "Available hours today", min_value=1, max_value=24, value=int(owner.availableHours)
)

if st.button("Save owner"):
    new_name = owner_name.strip() or owner.name
    if new_name != owner.name:
        # A different name means a new owner profile — start fresh rather than
        # dragging the previous owner's pets/tasks/preferences along.
        owner = Owner(name=new_name, availableHours=int(available_hours))
        scheduler.owner = owner
        scheduler.pets = owner.pets
        scheduler.tasks = owner.tasks
        scheduler.schedule = []
        save()
        st.success(f"Created new owner {owner.name} ({owner.availableHours}h available).")
        st.rerun()
    else:
        owner.setAvailableHours(int(available_hours))
        save()
        st.success(f"Saved {owner.name} ({owner.availableHours}h available).")

st.divider()

# ── Preferences ───────────────────────────────────────────────────────────────
st.subheader("Owner Preferences")

pref_input = st.text_input("Add a preference (e.g. 'morning walks only')", value="")
if st.button("Add preference"):
    if pref_input:
        owner.addPreference(pref_input)
        save()

prefs = owner.getPreferences()
if prefs:
    st.write("Current preferences:", prefs)
else:
    st.info("No preferences added yet.")

st.divider()

# ── Pets (multiple, persisted) ────────────────────────────────────────────────
st.subheader("Pets")

with st.form("add_pet", clear_on_submit=True):
    pc1, pc2, pc3 = st.columns(3)
    with pc1:
        new_pet_name = st.text_input("Pet name", value="")
    with pc2:
        new_pet_age = st.number_input("Age (years)", min_value=0, max_value=30, value=0)
    with pc3:
        new_species = st.selectbox("Species", ["dog", "cat", "other"])

    if st.form_submit_button("Add pet"):
        name = new_pet_name.strip()
        if not name:
            st.error("❌ Pet name cannot be empty.")
        elif any(p.name == name for p in owner.pets):
            # Names are the persistence key, so they must be unique.
            st.error(f"❌ A pet named '{name}' already exists. Use a unique name.")
        else:
            owner.pets.append(Pet(name=name, species=new_species, age=int(new_pet_age)))
            save()
            st.success(f"✅ Added {name} ({new_species}).")

if owner.pets:
    st.write("**Your pets:**")
    st.table([
        {
            "name": p.name,
            "species": p.species,
            "age": f"{p.age} yr",
            "special needs": ", ".join(f"{k}: {v}" for k, v in p.specialNeeds.items()) or "—",
        }
        for p in owner.pets
    ])
    rc1, rc2 = st.columns([3, 1])
    with rc1:
        remove_name = st.selectbox("Remove a pet", [p.name for p in owner.pets], key="remove_pet_select")
    with rc2:
        st.write("")  # vertical spacer to align the button with the selectbox
        if st.button("🗑️ Remove pet"):
            owner.pets = [p for p in owner.pets if p.name != remove_name]
            scheduler.pets = owner.pets
            # Drop tasks that only applied to the removed pet; for shared tasks,
            # just drop the reference so they keep applying to the remaining pets.
            for task_list in (owner.tasks, scheduler.tasks):
                for t in list(task_list):
                    if any(p.name == remove_name for p in t.appliesTo):
                        t.appliesTo = [p for p in t.appliesTo if p.name != remove_name]
                        if not t.appliesTo:
                            task_list.remove(t)
            save()
            st.success(f"🗑️ Removed {remove_name}.")
            st.rerun()
else:
    st.info("No pets yet. Add one above to start assigning tasks.")
    st.stop()  # Nothing below makes sense without at least one pet.

# Active pet drives the special-needs section and is the default for new tasks.
active_name = st.selectbox("Active pet", [p.name for p in owner.pets])
active_pet = next(p for p in owner.pets if p.name == active_name)

st.divider()

# ── Pet special needs (applies to the active pet) ─────────────────────────────
st.subheader(f"Special Needs — {active_pet.name}")

col_need, col_detail = st.columns(2)
with col_need:
    need_key = st.text_input("Need (e.g. 'medication')", value="")
with col_detail:
    need_detail = st.text_input("Detail (e.g. 'twice daily')", value="")

if st.button("Add special need"):
    if need_key and need_detail:
        active_pet.addSpecialNeed(need_key, need_detail)
        save()

if active_pet.specialNeeds:
    st.write("Special needs:", active_pet.specialNeeds)
else:
    st.info("No special needs recorded for this pet.")

st.divider()

# ── Tasks ────────────────────────────────────────────────────────────────────
st.subheader("Tasks")

# ── Visual helpers ────────────────────────────────────────────────────────────
# Color-coded priority badges so urgency reads at a glance.
PRIORITY_BADGE = {1: "🔴 High", 2: "🟡 Medium", 3: "🟢 Low"}

# Pick a task-type emoji from keywords in the title. Order matters: the first
# matching group wins, with a generic paw 🐾 as the fallback.
TASK_EMOJI_KEYWORDS = [
    (("walk", "stroll", "run", "exercise"), "🚶"),
    (("feed", "food", "meal", "breakfast", "lunch", "dinner", "treat"), "🍽️"),
    (("med", "pill", "medic", "dose", "insulin", "vitamin"), "💊"),
    (("vet", "appointment", "checkup", "clinic", "shot", "vaccin"), "🏥"),
    (("play", "fetch", "toy", "ball"), "🎾"),
    (("groom", "bath", "brush", "nail", "wash"), "🛁"),
    (("train", "trick", "obedience", "command"), "🎓"),
    (("water", "hydrate", "drink"), "💧"),
    (("sleep", "nap", "rest", "bed"), "😴"),
    (("clean", "litter", "cage", "scoop"), "🧹"),
]


def task_emoji(title: str) -> str:
    """Return an emoji representing the task type, inferred from its title."""
    lowered = title.lower()
    for keywords, emoji in TASK_EMOJI_KEYWORDS:
        if any(k in lowered for k in keywords):
            return emoji
    return "🐾"


def priority_badge(priority: int) -> str:
    """Return a color-coded priority badge (🔴 High / 🟡 Medium / 🟢 Low)."""
    return PRIORITY_BADGE.get(priority, PRIORITY_LABEL.get(priority, str(priority)))

col1, col2, col3 = st.columns(3)
with col1:
    task_title = st.text_input("Task title", value="Morning walk")
with col2:
    duration = st.number_input("Duration (minutes)", min_value=1, max_value=240, value=20)
with col3:
    priority = st.selectbox("Priority", ["high", "medium", "low"])

col4, col5, col6 = st.columns(3)
with col4:
    description = st.text_input("Description", value="")
with col5:
    recurrence = st.selectbox("Recurrence", ["once", "daily", "weekly"])
with col6:
    start_time = st.text_input("Start time (HH:MM)", value="09:00")

# Which pets this task applies to (defaults to the active pet, supports many).
task_pet_names = st.multiselect(
    "Applies to", [p.name for p in owner.pets], default=[active_pet.name]
)

if st.button("Add task"):
    # ── Validate input before constructing the task ───────────────────────────
    error = None
    if not task_title.strip():
        error = "Task title cannot be empty."
    elif not task_pet_names:
        error = "Select at least one pet this task applies to."
    else:
        try:
            hh, mm = start_time.split(":")
            hh, mm = int(hh), int(mm)
            if not (0 <= hh <= 23 and 0 <= mm <= 59):
                raise ValueError
        except (ValueError, AttributeError):
            error = "Start time must be in 24-hour HH:MM format (e.g. 09:00)."

    if error:
        st.error(f"❌ Could not add task: {error}")
    else:
        applies_to = [p for p in owner.pets if p.name in task_pet_names]
        task = Task(
            title=task_title.strip(),
            durationMinutes=int(duration),
            priority=PRIORITY_MAP[priority],
            description=description,
            recurrence=recurrence,
            startTime=f"{hh:02d}:{mm:02d}",
            appliesTo=applies_to,
        )
        owner.addTask(task)
        scheduler.addTask(task)
        save()
        st.success(
            f"✅ Added '{task.title}' at {task.startTime} ({duration} min, {priority} priority) "
            f"for {', '.join(task_pet_names)}."
        )

todo = owner.getTodoList()

if todo:
    # ── View controls: let the user sort/filter using Scheduler methods ───────
    ctrl_sort, ctrl_filter = st.columns(2)
    with ctrl_sort:
        sort_mode = st.selectbox("Sort by", ["priority", "start time"])
    with ctrl_filter:
        pet_names = sorted({p.name for t in scheduler.tasks for p in t.appliesTo})
        pet_filter = st.selectbox("Filter by pet", ["All pets"] + pet_names)

    # Choose the appropriate Scheduler sorting method.
    if sort_mode == "priority":
        ordered = scheduler.sortTasks()
    else:
        ordered = scheduler.sort_by_time()

    # Apply pet filtering via the Scheduler, keeping only pending tasks.
    if pet_filter != "All pets":
        allowed = {t.title for t in scheduler.filterTasks(pet_name=pet_filter)}
        ordered = [t for t in ordered if t.title in allowed]
    pending = [t for t in ordered if not t.isCompleted]

    if pending:
        st.write(f"**Current tasks** (sorted by {sort_mode}):")
        st.table([
            {
                "⏰ start": t.startTime,
                "task": f"{task_emoji(t.title)} {t.title}",
                "duration": f"{t.durationMinutes} min",
                "priority": priority_badge(t.getPriority()),
                "pets": ", ".join(p.name for p in t.appliesTo) or "—",
                "description": t.description or "—",
            }
            for t in pending
        ])
    else:
        st.info(f"No pending tasks match the filter for '{pet_filter}'.")

    # ── Conflict detection via the Scheduler ──────────────────────────────────
    conflicts = scheduler.detectConflicts()
    if conflicts:
        st.warning(f"⚠️ {len(conflicts)} scheduling conflict(s) detected:")
        for msg in conflicts:
            st.warning(msg)
    else:
        st.success("✅ No scheduling conflicts — all task time windows are clear.")

    # ── Completion ────────────────────────────────────────────────────────────
    complete_title = st.selectbox("Mark task complete", [t.title for t in pending] or [t.title for t in todo])
    if st.button("Complete task"):
        next_task = owner.completeTask(complete_title)
        scheduler.removeTask(complete_title)
        if next_task:
            scheduler.addTask(next_task)
            save()
            st.success(
                f"✅ '{complete_title}' marked complete. "
                f"Next {next_task.recurrence} occurrence added for "
                f"{next_task.dueDate.strftime('%Y-%m-%d')}."
            )
        else:
            save()
            st.success(f"✅ '{complete_title}' marked complete and removed from scheduler.")
else:
    st.info("No tasks yet. Add one above.")

st.divider()

# ── Schedule ─────────────────────────────────────────────────────────────────
st.subheader("Build Schedule")

if st.button("Generate schedule"):
    if not owner.getTodoList():
        st.error("❌ Cannot build a schedule — there are no pending tasks. Add a task first.")
    else:
        schedule = scheduler.generateSchedule(dayHours=owner.availableHours)
        if schedule:
            st.success(f"✅ Scheduled {len(schedule)} task(s) within {owner.availableHours}h.")

            def clock_slot(task):
                """Build a real HH:MM-HH:MM window from the task's start time + duration."""
                h, m = (int(x) for x in task.startTime.split(":"))
                start = h * 60 + m
                end = start + task.durationMinutes
                return f"{start // 60:02d}:{start % 60:02d} - {end // 60:02d}:{end % 60:02d}"

            # Professional tabular view of the generated schedule, ordered by real start time.
            st.table([
                {
                    "🕐 time slot": clock_slot(s.task),
                    "task": f"{task_emoji(s.task.title)} {s.task.title}",
                    "pet": s.pet.name,
                    "duration": f"{s.task.durationMinutes} min",
                    "priority": priority_badge(s.task.getPriority()),
                }
                for s in sorted(schedule, key=lambda s: s.task.startTime)
            ])

            # Surface any task time-window conflicts alongside the schedule.
            conflicts = scheduler.detectConflicts()
            if conflicts:
                st.warning(f"⚠️ Heads up — {len(conflicts)} task conflict(s) remain:")
                for msg in conflicts:
                    st.warning(msg)

            with st.expander("See reasoning"):
                st.markdown(scheduler.explainSchedule())
        else:
            st.warning(
                "⚠️ No tasks fit into the available hours. "
                "Try increasing available hours or shortening task durations."
            )

st.divider()

# ── Ask PawPal (Q&A + natural-language schedule editing) ──────────────────────
st.subheader("Ask PawPal")

if "llm_assistant" not in st.session_state:
    st.session_state.llm_assistant = None
    st.session_state.llm_init_error = None
    try:
        st.session_state.llm_assistant = LLMAssistant(
            scheduler=scheduler, retriever=retriever, client=GeminiClient(),
        )
    except GeminiError as e:
        st.session_state.llm_init_error = str(e)

if "pending_edit" not in st.session_state:
    st.session_state.pending_edit = None
    st.session_state.pending_edit_violations = []

llm_assistant = st.session_state.llm_assistant

qa_tab, edit_tab = st.tabs(["❓ Ask a question", "✏️ Edit schedule (natural language)"])

with qa_tab:
    st.caption("Answers are grounded on care facts and your own pets/schedule (read-only).")
    question = st.text_input(
        "Ask a care question (e.g. 'how often should I walk a labrador puppy')", value="", key="qa_question"
    )
    if st.button("Ask", key="qa_ask_btn"):
        if not question.strip():
            st.error("❌ Enter a question first.")
        elif llm_assistant is None:
            st.warning(f"⚠️ Gemini unavailable ({st.session_state.llm_init_error}) — showing basic advisory answer.")
            st.info(retriever.answer(question, species=active_pet.species))
        else:
            try:
                st.info(llm_assistant.answer_question(question, active_pet.species))
            except GeminiError as e:
                log_decision(get_logger(), LLM_ERROR, question, reason=str(e))
                st.error("⚠️ Gemini is unavailable right now. Here's a basic advisory answer instead:")
                st.info(retriever.answer(question, species=active_pet.species))

with edit_tab:
    st.caption("Proposed changes are shown for your approval before anything is saved.")
    if llm_assistant is None:
        st.warning(f"⚠️ Natural-language editing needs Gemini configured: {st.session_state.llm_init_error}")
    else:
        instruction = st.text_input(
            "What would you like to change?", placeholder="e.g. move Luna's walk to 8am", key="edit_instruction"
        )
        if st.button("Propose change"):
            if not instruction.strip():
                st.error("❌ Enter an instruction first.")
            else:
                try:
                    proposed = llm_assistant.propose_edit(instruction)
                    violations = llm_assistant.check_edit(proposed)
                    st.session_state.pending_edit = proposed
                    st.session_state.pending_edit_violations = violations
                except ProposalError as e:
                    st.error(f"❌ {e}")
                    st.session_state.pending_edit = None
                except GeminiMalformedResponseError:
                    st.error("❌ I couldn't understand how to structure that — try rephrasing.")
                    st.session_state.pending_edit = None
                except GeminiError as e:
                    log_decision(get_logger(), LLM_ERROR, instruction, reason=str(e))
                    st.error("⚠️ Gemini is unavailable right now. Try again shortly.")
                    st.session_state.pending_edit = None

        pending = st.session_state.pending_edit
        if pending is not None:
            st.info(f"**Proposed:** {pending.explanation}")
            if pending.action == "remove":
                st.write(f"Remove task: **{pending.target_title}**")
            else:
                t = pending.task
                st.table([{
                    "action": pending.action,
                    "title": t.title,
                    "start": t.startTime,
                    "duration": f"{t.durationMinutes} min",
                    "priority": PRIORITY_LABEL.get(t.priority, t.priority),
                    "pets": ", ".join(p.name for p in t.appliesTo) or "—",
                }])

            if st.session_state.pending_edit_violations:
                st.error("This change was blocked by safety rules:")
                for v in st.session_state.pending_edit_violations:
                    st.error(v)
                if st.button("Discard", key="discard_blocked"):
                    st.session_state.pending_edit = None
            else:
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("✅ Apply"):
                        try:
                            llm_assistant.commit_edit(pending)
                            save()
                            st.session_state.pending_edit = None
                            st.success("✅ Applied.")
                        except SafetyViolation as e:
                            st.error("❌ Blocked at the last check (schedule changed since proposing):")
                            for v in e.violations:
                                st.error(v)
                            st.session_state.pending_edit = None
                with c2:
                    if st.button("Discard", key="discard_ok"):
                        st.session_state.pending_edit = None
