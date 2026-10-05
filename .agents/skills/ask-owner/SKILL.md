---
name: ask-owner
description: Add a question for the owner to docs/10-open-questions.md when the docs are ambiguous, contradict each other, or a verified fact changes scope, cost or a rule. Use instead of guessing.
argument-hint: "[the question]"
---

# Ask the owner: $ARGUMENTS

1. Read `docs/10-open-questions.md`. Make sure the question isn't already there (answered or open).
2. Append a row with the next `Q-NN`: the question in one plain sentence, **Needed by** (phase),
   a **Proposed answer** you would choose, empty **Answer**, status `Open`.
3. Say which tickets it blocks. If the proposed answer is easy to reverse, continue with it and say so;
   otherwise move on to tickets it doesn't block.
4. Put the question at the top of the phase PR description too, so the owner sees it at review.
