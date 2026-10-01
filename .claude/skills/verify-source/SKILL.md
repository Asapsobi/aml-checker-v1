---
name: verify-source
description: Verify an external data-source fact live (VS-01 to VS-14 in docs/04-data-sources.md) before code relies on it - fetch docs, make minimal real calls, save fixtures, log the result. Use before writing or changing any provider adapter.
argument-hint: "[VS-ID, e.g. VS-04]"
---

# Verify $ARGUMENTS

Delegate the live work to the `source-verifier` subagent, then review what it brings back.

1. Read the `$ARGUMENTS` row in `docs/04-data-sources.md` §9 and the provider's section above it.
2. Check the provider's current official docs / spec first. Note the URL and date.
3. Make the **fewest** live calls that settle the question. Use keys from the environment only;
   never print, log or save a key, and never read `.env` directly. Respect free-plan quotas
   (keyless TronGrid allows about 1 request a second; HyperSync about 30 queries a minute).
4. Save real responses (secrets and personal data removed) under `tests/fixtures/<provider>/` with
   descriptive names.
5. Append an entry to `docs/verification-log.md`:

   ```markdown
   ## $ARGUMENTS · <provider>: <question>
   **Checked:** YYYY-MM-DD, against <URL(s)>
   **Found:** <facts, with field names and sample values>
   **Differs from docs/04-data-sources.md:** none | <what>
   **What this changes:** <code, config, tickets>
   **Fixtures:** <paths>
   ```

6. If anything differs from `docs/04-data-sources.md`, update that doc in the same commit and add a
   question to `docs/10-open-questions.md` when the change affects scope, cost or a rule.
