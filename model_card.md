# Model Card — PawPal+ AI Layer

This document covers the AI-relevant components added in Module 4: `AIPlanner` (rule-based agent), `Validator` (safety gate), `LLMAssistant` + `GeminiClient` (Gemini-backed Q&A/edits), and `Retriever` (keyword-based advisory RAG). See [`README.md`](README.md) for the full system overview and [`diagrams/system_diagram.mmd`](diagrams/system_diagram.mmd) for the architecture diagram.

## How Reliability Is Tested/Measured

Four separate mechanisms are used, deliberately overlapping so no single one is load-bearing:

1. **Automated unit/integration tests** — `pytest tests/` (111 tests) checks component-level contracts: Validator rule correctness, AIPlanner determinism and bounded termination, LLMAssistant's edit-proposal parsing and Validator gating, Retriever scoring, and GeminiClient error wrapping — all against hand-written fakes, no network access required.
2. **Confidence scoring** — `eval_system.py` is a black-box scenario harness where every check returns `(passed, confidence, detail)`, not just a boolean. Confidence is partial-credit, not just 0/1: e.g. `check_resolved_by_removal` returns `0.5` if the planner accepted a schedule but *didn't* actually remove the conflicting task it was supposed to (accepted for the wrong reason), and `check_accepted_and_clean` returns `0.3` if the planner claims acceptance but an independent fresh `Validator()` still finds a violation (self-check disagrees with itself). A confidence of `1.0` means the check passed for the specific reason expected, not just "didn't crash."
3. **Logging** — `pawpal_logger.py` records every proposal, rejection, and adjustment (`PROPOSAL`, `REJECTION`, `ADJUSTMENT`, `LLM_PROPOSAL`, `LLM_REJECTION`, `LLM_ACCEPTANCE`, `LLM_ERROR`) with the task title and a human-readable reason, so *why* the system did something is always auditable after the fact, not just *what* it did.
4. **Typed error handling** — failure modes are distinct exception types rather than generic errors or silent fallbacks: `SafetyViolation` (Validator rejected a schedule), `ProposalError` (Gemini's proposed edit couldn't be resolved against real pets/tasks — unknown pet, unknown task title, malformed time, ambiguous request), `GeminiError` (API/network/auth failure), `GeminiMalformedResponseError` (Gemini returned text that wasn't valid JSON). Each is caught and logged at its origin rather than surfacing as an unhandled crash.

### Results Summary

```
pytest tests/          -> 111/111 passed
eval_system.py          -> 8/8 scenarios passed, avg confidence 1.00
```

| Test Input / Scenario | Evaluation Criteria | Result |
|---|---|---|
| Clean 2-task schedule (dog, no conflicts) | Accepted in 1 iteration, no leftover Validator violations | Pass (confidence 1.00) |
| Two insulin doses 30 min apart, same pet | Planner detects medication-spacing violation and removes the lower-priority dose | Pass (confidence 1.00) |
| 60-min walk for a 0-year-old puppy | Planner detects puppy-exertion violation and removes the walk | Pass (confidence 1.00) |
| Walk task assigned to a cat | Planner detects species-mismatch violation and removes the walk | Pass (confidence 1.00) |
| Two identically-titled medication tasks 5 min apart, `max_retries=1` | Planner cannot disambiguate which task to remove within budget → honestly rejects (`accepted=False`, empty schedule) rather than guessing | Pass (confidence 1.00) |
| "how often should I walk a labrador puppy" | Retriever answer contains `puppy` and `labrador` | Pass (confidence 1.00) |
| "how often should I walk my cat" | Retriever answer contains `play` (redirects to correct care advice) | Pass (confidence 1.00) |
| "what are the tax filing deadlines this year" | Retriever declines rather than fabricating an answer | Pass (confidence 1.00) |

**What worked:** all 8 scenarios currently pass at full confidence, and the partial-credit scores (0.3 / 0.5) built into the harness have caught regressions during development where the planner accepted a schedule "by luck" rather than by actually resolving the flagged conflict — those runs would show up as passing-but-low-confidence rather than a clean pass, which a plain pass/fail check would have missed. **What's untested / a known gap:** the Gemini-backed path (`LLMAssistant` + `GeminiClient`) is fully covered by pytest against a fake client, but `eval_system.py` does not exercise it live end-to-end, since that would require a real API key and network access, and would not be deterministic run-to-run. That path's actual output quality (does Gemini reliably produce well-formed JSON for ambiguous phrasing?) has only been human-checked manually, not automated — see Limitations below.

## Limitations and Biases

- **The LLM path is not covered by the automated confidence-scoring harness.** `eval_system.py` only exercises the deterministic `AIPlanner` and `Retriever` paths. `LLMAssistant`'s actual behavior against live Gemini output — how it handles ambiguous phrasing, typos in pet names, or requests that don't map cleanly onto add/edit/remove — is only verified by pytest against a fake client (which returns exactly what the test tells it to) and by manual, non-recorded testing. This means the system's weakest link (the actual model's output quality) has the thinnest automated coverage.
- **The Retriever's knowledge base is small, static, and dog/cat-skewed.** It has deep coverage for dogs and cats, thinner entries for birds/rabbits/hamsters/fish, and nothing for less-common pets (reptiles, other exotics). A question about an unsupported species will correctly decline rather than fabricate an answer, but "correctly declines" is also a coverage gap — the system is only as good as its curated facts, and someone with a bearded dragon gets no advice at all rather than a wrong one.
- **The Validator's rule set encodes specific numeric thresholds** (240-minute medication spacing, 30/60-minute walk rest, 20-minute puppy walk cap, 8-year senior cutoff) that are reasonable defaults but not vet-verified for any specific pet, breed, or medical condition. They should not be read as medical guidance.
- **`AIPlanner.adjust()` always removes the higher-priority-number (i.e. lower-priority) task in a conflicting pair**, and outright removes any task flagged by a single-task rule (puppy exertion, species mismatch). This is a deliberate, simple, auditable heuristic — but it means the system's idea of "the right fix" is always "delete something," never "reschedule to a different time," even when shifting a task by 15 minutes would have resolved the conflict without dropping care entirely.

## Could This Be Misused?

The most plausible misuse isn't malicious — it's **over-trusting the Validator's rules as a substitute for actual veterinary judgment.** A user could add pets with fabricated ages/species to bypass the puppy/senior/species rules (e.g., listing a puppy as age 5 to unlock longer walks the Validator would otherwise block), or could rely on "the app said this schedule is safe" as medical advice rather than a scheduling sanity-check. Mitigations already in place: the Validator's rules are simple and inspectable (any user could read `validator.py` and see exactly what is and isn't checked, rather than trusting an opaque score), and nowhere does the system claim to be a substitute for veterinary guidance — the Retriever explicitly declines out-of-scope questions rather than answering with false confidence. A clearer disclaimer in the UI itself (e.g., "not a substitute for veterinary advice") would be a reasonable follow-up to further reduce this risk, since currently that boundary is only implicit in what the Retriever's knowledge base does and doesn't cover, not stated to the user directly.

## What Surprised Me While Testing

The most surprising result was in the **honest-rejection scenario** (`case_unresolvable_rejected`): two identically-titled medication tasks 5 minutes apart. I expected `adjust()` might crash or behave unpredictably, since it looks up tasks by title and two tasks share a title — but by construction (`tasks_by_title` maps title→task, so the second overwrites the first) it silently produces a `candidates` list that doesn't actually resolve to two distinct offending tasks, and the planner correctly recognizes it can't make forward progress and stops within its retry budget instead of looping or guessing. It was reassuring that the "give up safely" path worked exactly as designed even in this genuinely ambiguous edge case that I hadn't originally planned for — but it also means duplicate task titles are a latent sharp edge in `adjust()`'s title-based lookup that's currently only safe *because* it fails closed (rejects) rather than because it was explicitly handled. If `adjust()` is ever changed to be less conservative, that assumption would need to be revisited.

## Collaboration With AI on This Project

**A helpful suggestion:** when I asked for a way to make the Validator usable by both `AIPlanner` and `LLMAssistant` without duplicating safety logic, the AI suggested splitting `validate()` (returns violations, no side effects) from `enforce()` (raises on any violation) as two methods on the same `Validator`, rather than writing separate checking code in the planner and the assistant. This was a clean idea I adopted directly — it meant both call sites re-run the *exact* same rules, and there is now exactly one place to add or change a safety rule instead of two that could silently drift apart.

**A flawed suggestion:** when implementing `AIPlanner.adjust()`, the AI's first draft removed *every* task named in a violation message (e.g., for a medication-spacing violation naming two tasks, it would drop both). I rejected this — dropping both tasks in a pair over-corrects: if a high-priority insulin dose conflicts with a low-priority one, the fix should be to remove the lower-priority dose and keep the higher-priority care intact, not discard both. I changed it to pick the max by `(priority, startTime)` among the named candidates within each violation, so only the least important task in the conflicting pair is removed. I caught this by asking, concretely: "if a critical medication task and an optional one conflict, does this remove the critical one too?" — the answer was yes, which is exactly the failure mode a pet-care scheduler cannot afford.
