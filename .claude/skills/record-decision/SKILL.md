---
name: record-decision
description: Record a design or policy decision in docs/09-decisions.md using the ADR template. Use whenever a real choice between alternatives was made, or a seed decision is confirmed, changed or superseded.
argument-hint: "[short title]"
---

# Record decision: $ARGUMENTS

1. Read `docs/09-decisions.md`. Find the highest `D-NNN` and use the next number.
2. Check whether an existing decision covers this. If it changes one, the new entry **supersedes**
   it: set the old one's status to `Superseded by D-NNN` (status only; never rewrite its text).
3. Append using the template in that file: status (`Proposed` unless the owner explicitly agreed),
   date, phase, context, decision, alternatives, consequences. Keep it short.
4. If the decision changes a default, a rule severity or a formula, update the doc that defines it
   (`02-methodology.md`, `01-prd.md` or `03-architecture.md`) in the same commit, and bump the
   relevant version number in methodology if a formula changed.
5. If it answers an open question, set that question's **Answer** and **Status** in
   `docs/10-open-questions.md` and reference the decision ID.
