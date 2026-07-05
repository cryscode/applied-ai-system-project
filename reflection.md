# PawPal+ Project Reflection

## 1. System Design

**a. Initial design**
Core actions
Add a task
Schedule tasks ahead
Have a to-do list with constreaints taken into account
Track tasks to ensure pet is healthy

- Briefly describe your initial UML design.
- What classes did you include, and what responsibilities did you assign to each?
Classes
Owner: Manages preferences for pet care
Pet: Represents the pet with species and special needs
Task: Individual care tasks with priority and duration
Scheduler: Orchestrates the planning, taking an owner and pet(s), consuming tasks, and generating a daily schedule

**b. Design changes**

- Did your design change during implementation?
- If yes, describe at least one change and why you made it.\
No Owner-Pet binding — The Scheduler references both owner (singular) and pets (list), but there's no way to know which pets belong to which owner.

Task doesn't reference which Pet it's for — A task like "feed dog" or "give medication" must apply to a specific pet (or pets), but Task has no pets field. This makes scheduling and filtering nearly impossible — how does the scheduler know if a task is relevant?

ScheduledTask doesn't track the Pet — Even though Task is scheduled, you lose context about which pet it serves. The output would be unhelpful: "Feed at 10am" without knowing which animal.

ScheduledTask.reasoning vs Scheduler.getReasoningFor() — Both store/return reasoning. Why not just read ScheduledTask.reasoning directly instead of having a lookup method?

---

## 2. Scheduling Logic and Tradeoffs

**a. Constraints and priorities**

- What constraints does your scheduler consider (for example: time, priority, preferences)?
- How did you decide which constraints mattered most?

**b. Tradeoffs**

- Describe one tradeoff your scheduler makes.
I asked for a more efficient way to detect and alert for scheduling conflicts. The tradeoff is: The parsed list materializes all (task, start, end) tuples upfront, so you're holding the full list in memory before any comparison happens. With the original loop, each task's to_minutes result was computed on-demand and immediately discarded.
- Why is that tradeoff reasonable for this scenario?
This removed a nested for loop from the previous code which was less efficient.

---

## 3. AI Collaboration

**a. How you used AI**

- How did you use AI tools during this project (for example: design brainstorming, debugging, refactoring)
- What kinds of prompts or questions were most helpful?

**b. Judgment and verification**

- Describe one moment where you did not accept an AI suggestion as-is.
- How did you evaluate or verify what the AI suggested?

---

## 4. Testing and Verification

**a. What you tested**

- What behaviors did you test?
- Why were these tests important?

**b. Confidence**

- How confident are you that your scheduler works correctly?
- What edge cases would you test next if you had more time?

---

## 5. Reflection

**a. What went well**

- What part of this project are you most satisfied with?

**b. What you would improve**

- If you had another iteration, what would you improve or redesign?

**c. Key takeaway**

- What is one important thing you learned about designing systems or working with AI on this project?
