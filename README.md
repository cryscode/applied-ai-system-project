# PawPal+ — AI-Assisted Pet Care Scheduler

**Original project (Modules 1–3):** *PawPal+*, a Streamlit app that helps a pet owner plan daily pet-care tasks (walks, feeding, medication, grooming, etc.). The original goal was to design the domain model in UML, implement priority- and time-aware scheduling logic in Python (sorting, capacity filtering, conflict detection/resolution, recurrence), and expose it through a simple UI backed by JSON persistence — with a pytest suite covering the core scheduling behaviors.

This repository is the **Module 4 extension** of that project: it keeps the original deterministic scheduler intact and adds an AI/agent layer on top of it.

## Title and Summary

**PawPal+ with a Safety-Gated Scheduling Agent and LLM Assistant.**

This project turns the original rule-based scheduler into a small AI system: a rule-based *agent* (`AIPlanner`) that iteratively generates and repairs a daily pet-care schedule, a *safety validator* that acts as a hard gate no unsafe schedule can pass, an optional *Gemini-backed assistant* for natural-language Q&A and schedule edits, and a lightweight *retrieval* module for grounded pet-care advice. It matters because pet-care scheduling has real consequences — a missed medication window or an over-walked puppy is not just a UX bug — so the interesting engineering problem isn't "call an LLM," it's "how do you let an autonomous loop and an LLM propose changes to a schedule without ever letting either one commit something unsafe."

## Architecture Overview

The full system diagram lives in [`diagrams/system_diagram.mmd`](diagrams/system_diagram.mmd). In short:

- **UI (`app.py`)** is the only entry point a human touches. It collects owner/pet/task input and renders schedules and answers.
- **Deterministic core (`pawpal_system.py`)** holds `Owner`, `Pet`, `Task`, and `Scheduler` — the same sorting/filtering/conflict logic from Modules 1–3, plus JSON persistence (`data.json`).
- **Agent (`ai_planner.py`)** wraps the Scheduler in a bounded **Plan → Act → Check → Adjust → Final** loop: it generates a candidate schedule, checks it against the Validator, removes the offending task(s) if it fails, and retries up to `max_retries` times.
- **Safety gate (`validator.py`)** is a pure rule-based module (medication spacing, walk-rest minimums, species/age checks) with no LLM and no I/O. It is the one trust boundary in the system: **both** the agent and the LLM assistant can only commit a schedule change after `Validator.enforce()` has confirmed it clean — there is no code path that skips this.
- **LLM assistant (`llm_assistant.py` + `gemini_client.py`)** is the only non-deterministic part of the system. It handles free-text Q&A and natural-language edit requests ("move Luna's walk to 8am"), turns them into a structured proposed edit via Gemini, and then routes that edit through the *same* Validator before it's ever applied.
- **Retriever (`retriever.py`)** answers general advisory questions from a small static, keyword-scored knowledge base — no embeddings, no network calls, fully deterministic and auditable.
- **Logging (`pawpal_logger.py`)** records every proposal, rejection, and adjustment from the Scheduler, AIPlanner, LLMAssistant, and Retriever into one structured decision stream.
- **Testing (`tests/`, `eval_system.py`)** verifies this from two angles: pytest unit/integration tests assert component-level contracts, and `eval_system.py` runs fixed end-to-end scenarios and checks the outcome against an expectation, closer to how a human would sanity-check the system.

The key architectural decision the diagram is meant to convey: **the Validator sits between every AI-generated proposal (agent or LLM) and anything that actually gets committed or shown as final.** Nothing bypasses it.

## Setup Instructions

```bash
# 1. Clone and enter the project
git clone <this-repo-url>
cd applied-ai-system-final

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure the Gemini API key (only needed for the LLM assistant features)
cp .env.example .env
# then edit .env and set GEMINI_API_KEY=<your key>
# GEMINI_MODEL defaults to gemini-2.5-flash

# 5. Run the app
streamlit run app.py
```

Everything except the LLM Q&A/edit features (scheduling, validation, retrieval, persistence) works with **no API key at all**.

To run the tests and the scenario evaluation harness instead of the UI:

```bash
python -m pytest tests/ -v          # 111 tests
python eval_system.py               # 8 end-to-end scenarios, no API key required
python main.py                      # CLI walkthrough of the original scheduler
```

## Sample Interactions

**1. The agent auto-repairs an unsafe schedule (no human or LLM involved).**
Two insulin doses were scheduled 30 minutes apart for the same pet — the agent detects the violation, drops the lower-priority dose, and retries automatically:

```
[PROPOSAL] Insulin dose A -- Scheduled based on 2 priority
[PROPOSAL] Insulin dose B -- Scheduled based on 3 priority
[ADJUSTMENT] Insulin dose B -- removed by planner to resolve:
  [SAFETY — medication spacing] 'Insulin dose A' (08:00) and 'Insulin dose B' (08:30)
  for Milo are too close together — doses need at least 240 min apart
```
`AIPlanner.run()` returns `accepted=True` on the next iteration with `Insulin dose B` removed and `removed_tasks == ["Insulin dose B"]`.

**2. The agent honestly rejects an unfixable conflict rather than guessing.**
Two identically-titled, identically-prioritized medication doses 5 minutes apart give the adjust step no safe single task to remove:

```
[ADJUSTMENT] Dose -- removed by planner to resolve: [SAFETY — medication spacing] ...
[REJECTION] 10h schedule -- no safe arrangement found after 1 iteration(s);
  last violations: ["[SAFETY — medication spacing] 'Dose' (08:00) and 'Dose' (08:05) ..."]
```
`PlanResult.accepted` is `False` and `PlanResult.schedule` is empty — the planner never returns a half-safe guess.

**3. Retriever answers grounded pet-care questions and declines out-of-scope ones.**

| Input | Output |
|---|---|
| `"how often should I walk a labrador puppy"` | Matches the `dog/labrador/walking` knowledge entry: *"A Labrador puppy (under 1 year old) should get short, frequent walks — roughly 5 minutes per month of age, up to 3–4 times a day..."* |
| `"how often should I walk my cat"` | Matches the `cat/exercise` entry and steers the user correctly: *"Cats aren't typically walked like dogs — they get exercise through active play..."* |
| `"what are the tax filing deadlines this year"` | No knowledge-base entry scores above threshold → Retriever explicitly declines rather than fabricating an answer, logged as `[ADVISORY] ... -- no matching knowledge base entry found`. |

**4. LLM assistant proposes an edit, which still goes through the Validator before committing.**
Given the prompt *"move Luna's walk to 8am"*, Gemini is asked to return structured JSON (not free text):

```json
{"action": "edit", "target_title": "Morning walk", "startTime": "08:00",
 "explanation": "Move the walk earlier."}
```
`LLMAssistant.propose_edit()` parses this into a `ProposedEdit`, resolves `"Morning walk"` against the owner's actual task list (raising `ProposalError` if the title or a referenced pet doesn't exist), and — like every other write path in the system — the resulting task list is checked against `Validator` before it is ever applied.

## Design Decisions

- **One trust boundary, not two.** Both the rule-based agent and the LLM assistant funnel every change through the same `Validator.enforce()` call rather than each having their own safety logic. This was a deliberate trade-off: it means the Validator's rules have to be generic enough to apply to both a scheduler-generated candidate and an LLM-proposed edit, but it guarantees there's exactly one place to audit or extend safety behavior, instead of two that could drift out of sync.
- **The agent is rule-based, not LLM-based.** `AIPlanner` is explicitly "Plan → Act → Check → Adjust → Final" with no model call anywhere in it. This was chosen so the core scheduling-repair loop stays deterministic, fast, and fully unit-testable (same input → same output, every time) — the LLM is reserved for the parts that actually need natural language (Q&A, free-text edit requests), not for logic that a plain algorithm handles better and more auditably.
- **`validate()` vs `enforce()` as two separate methods.** `validate()` returns violations without raising, so the planner's loop can inspect them and decide what to remove; `enforce()` raises and is used at the final commit point. Splitting these means "inspect" and "block" are never accidentally conflated — a caller can't forget to check the return value of `enforce()`, because a caller who wants to block just calls it and lets it raise.
- **Retriever is intentionally dumb.** Plain keyword-overlap scoring over a small static `KnowledgeEntry` list, no embeddings, no external calls. For a bounded pet-care advisory domain this is fully deterministic and auditable, at the cost of not handling paraphrased queries as gracefully as a real embedding-based retriever would. That trade-off is acceptable here because the knowledge base is small and hand-curated, not open-domain.
- **The LLM never talks directly to state.** `LLMAssistant` only ever produces a `ProposedEdit` (or an answer string for Q&A). It never mutates `Owner`/`Scheduler` itself — the calling code (or the same Validator-gated commit path the planner uses) does that. This keeps the one non-deterministic component of the system read-only with respect to actually changing data; it can suggest, but only the deterministic, tested code path commits.
- **`eval_system.py` alongside pytest, not instead of it.** pytest checks component contracts in isolation (e.g., "does `_check_medication_spacing` flag this exact overlap"); `eval_system.py` checks end-to-end scenarios the way a person would ("given this whole pet+task setup, does the planner produce something safe or honestly refuse"). Keeping both was worth the duplication because they catch different classes of regressions — a passing unit test suite does not guarantee the pieces compose correctly.

## Testing Summary

- **111 pytest tests pass** across `test_pawpal.py` (original scheduler behaviors), `test_gemini_client.py`, `test_llm_assistant.py`, and `test_retriever.py` (new AI-layer components, all tested against a hand-written fake `LLMClient` so no test requires network access or an API key).
- **`eval_system.py` end-to-end scenarios: 8/8 pass, average confidence 1.00**, covering: a clean schedule being accepted as-is, medication-spacing/puppy-exertion/species-mismatch violations each being auto-resolved by the planner, an unfixable conflict being honestly rejected instead of guessed around, a grounded advisory answer, a correctly-redirected advisory answer, and an out-of-scope question being correctly declined.
- **What worked well:** the Validator-as-single-gate design meant that once the medication/walk-rest/species rules were correct, both the planner and the LLM assistant path "just worked" against them with no separate safety logic to write or test twice. The `adjust()` step's approach of parsing violation messages back into task titles via regex (`re.findall(r"'([^']+)'", line)`) turned out to be a clean way to keep violation text human-readable *and* machine-actionable without a second parallel data structure.
- **What didn't work initially / had to change:** early versions of `adjust()` risked infinite loops if a violation referenced a task no longer in the pool — this is why `run()` explicitly breaks out when `adjust()` removes nothing, and why the loop's termination is provable (bounded by `min(max_retries, len(initial tasks) + 1)`) rather than just "hopefully converges." The medication-spacing rule also had to be written to compare every pair via `itertools.combinations` (not just adjacent pairs), because two doses could be non-adjacent in time but still within the spacing window once a third dose is inserted between them.
- **What I learned:** testing an agent loop is different from testing a pure function — it's not enough to check the final output, you also have to check that it terminates, that it terminates in a *bounded* number of steps, and that every intermediate state it produces along the way was itself safe (not just the final one). The `PlanResult.violations_history` field exists specifically so tests (and humans) can inspect that trace rather than trusting the final `accepted` flag alone.

## Reflection

Building the agent/validator split clarified something that wasn't obvious from Modules 1–3 alone: making a system "smarter" (an iterative loop, an LLM) doesn't remove the need for a dumb, deterministic, fully-auditable core — it *increases* it. The more autonomous or generative a component gets, the more valuable it is to have a small, boring, rule-based gate that everything must pass through, precisely because that's the one piece a human can read top-to-bottom and be confident about. Problem-solving with AI, in this project, turned out to mean less "how do I get the model to do the right thing" and more "how do I design the system so it doesn't matter whether the model does the right thing" — the agent's Adjust step and the Validator's `enforce()` gate both exist to make correctness a property of the architecture, not of any one component's judgment.

For the graded responsible-AI reflection — how I collaborated with AI on this project, one helpful and one flawed AI suggestion, and this system's limitations — see [`model_card.md`](model_card.md).
