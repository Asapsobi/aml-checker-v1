# aml-checker v1 — specification

The v1.0 specification for **amlcheck**: counterparty intelligence for USDT on TRON and BSC.

| File | Contents |
|---|---|
| [docs/v1/PRD.md](docs/v1/PRD.md) | What v1 adds, goals, constraints, rules, open questions |
| [docs/v1/METHODOLOGY.md](docs/v1/METHODOLOGY.md) | Trace algorithm, classifier, categories, look-alike guard, score formula |
| [docs/v1/ARCHITECTURE.md](docs/v1/ARCHITECTURE.md) | Modules, interfaces, flows, budgets |
| [docs/v1/DATA_MODEL.md](docs/v1/DATA_MODEL.md) | Migrations 0005–0010 |
| [docs/v1/ROADMAP.md](docs/v1/ROADMAP.md) | Phases 6–12, tasks and exit criteria |
| [docs/v1/ACCEPTANCE.md](docs/v1/ACCEPTANCE.md) | Acceptance tests AT-V1-01 to 30 |
| [docs/v1/AGENT_BRIEF.md](docs/v1/AGENT_BRIEF.md) | Brief and starter prompt for a coding agent |
| [AGENTS.md](AGENTS.md) | Entry point for coding agents |

**Baseline:** this spec extends amlcheck **0.5.1** in
[Asapsobi/aml-checker](https://github.com/Asapsobi/aml-checker). The files it refers to (`README.md`,
`docs/PRD.md`, `docs/verification.md`, `src/amlcheck/…`) live in that repository, and the build
happens there.
