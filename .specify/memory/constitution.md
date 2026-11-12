# Market Anomaly Agent Constitution

Market Anomaly Agent is a one-person portfolio and research demo. On every trading day it detects
unusual moves of Nasdaq-100 stocks relative to the overall market, lets an LLM agent judge whether
public information explains each move, and derives a paper-trading signal from fixed rules. The
project is experimental and is not investment advice.

## Core Principles

### I. Deterministic Numbers, LLM for Judgment Only

All numbers MUST be produced by deterministic, tested code: returns, abnormal returns, z-scores,
sentiment aggregates, signals, and paper P&L. Unusual moves MUST be measured relative to the
overall market; a raw price change MUST NOT be treated as an anomaly. The LLM MUST be used only
where unstructured text has to be judged — primarily the question "Does available information
explain this move?" Every LLM output MUST be structured and schema-validated; a response that
fails validation MUST be rejected rather than coerced. The LLM MUST NOT decide trades. Fixed,
versioned rules MUST map the LLM's classification to a signal.

Rationale: an LLM is not reproducible to the digit and cannot be audited numerically. Keeping
arithmetic in tested code and confining the model to text judgment is what makes the results
reviewable and the backtest honest.

### II. Simplest Solution First

Deterministic code MUST be preferred wherever it suffices. An agent loop MUST be used only where
the path cannot be fixed in advance. Added indirection, abstraction, or extra services MUST be
justified by a requirement that is already present, not by an anticipated one.

Rationale: a one-person project survives on the amount of machinery its author can still reason
about. Agent loops are the most expensive and least predictable construct available and earn their
place only when branching is genuinely unknown ahead of time.

### III. Research Integrity

- The stock universe MUST be frozen at project start and MUST NOT be revised mid-project.
  The frozen universe is the Nasdaq-100 as of 2026-11-11, taken from the Invesco QQQ Trust
  holdings as of 2026-11-10 and committed as
  `src/market_anomaly_agent/universe/nasdaq100_2026-11-11.csv`.
- Every piece of evidence MUST carry a timestamp.
- Only information published before or during a move MAY be used to explain that move. Later
  information MAY serve only as a pointer to an earlier cause, or as input to the trading
  decision; it MUST NOT be recorded as the explanation of the move.
- Every decision MUST be stored with a timestamp before its outcome is known.
- Trading rules MUST be versioned and MUST stay frozen for the duration of an evaluation period.
  A rule change MUST create a new version and MUST NOT retroactively alter recorded decisions.
- The LLM MUST NOT be run over historical periods for evaluation purposes, because its training
  data makes lookahead bias unavoidable.
- Trading is paper trading only. Real orders MUST NOT be placed.
- Trades that are blocked or skipped MUST be recorded as hypothetical rather than dropped.
- Results MUST be reported honestly, including null results and periods where the strategy
  performed worse than the market.

Rationale: the entire value of the demo is that its claims can be believed. Each rule above closes
a specific way a result can become accidentally untrue — survivorship bias, lookahead bias,
post-hoc rationalization, and selective reporting.

### IV. Security

Secrets MUST NOT appear in code, logs, or the repository; they MUST be supplied through the
environment or a secret store. Agent tools MUST be read-only — no tool may write to the database,
the filesystem, or any external service. External text (social posts, search results, filings)
MUST be treated as untrusted data: it MUST be clearly delimited in prompts and MUST NEVER be
interpreted as instructions. Every agent run MUST enforce hard limits on tool calls, tokens, and
wall-clock time, and MUST terminate when a limit is reached.

Rationale: the agent reads attacker-controllable text by design. Read-only tools and hard run
limits mean a successful prompt injection costs at most one wasted run.

### V. Cost Control

Every paid API MUST have a hard spending cap and a rate limit enforced in code, not merely in a
provider dashboard. Brave Search MUST be called at most once per second. Cost per run MUST be
measured and stored. A run that would exceed a cap MUST stop rather than continue.

Monthly spending caps:

| Provider | Monthly cap | Provider-side control |
|---|---|---|
| Azure, including Azure OpenAI once it is used | EUR 30 (stated in EUR because Azure bills this subscription in EUR) | Azure budget alerts at 50%, 80% and 100%. Azure budgets only alert; they do not stop spending. |
| X API | USD 15 | The hard spend limit in the X Developer Console |
| Twelve Data | USD 0 | Free Basic plan |
| Brave Search | USD 0 | Free plan |
| **Total** | **USD 50** | EUR 30 is about USD 34 at the November 2026 rate (about 1.12 USD per EUR), so Azure plus the X API stays within USD 50 (about USD 49). |

These provider-side controls do not replace the caps and rate limits enforced in code that this
principle requires.

Rationale: a daily unattended job with paid APIs is the classic way a side project produces a
surprise invoice. Caps enforced in code fail closed; dashboards only notify after the fact.

### VI. Data and Compliance

Each provider's terms MUST be respected. Raw X post texts MUST NOT be published. From Brave
Search, only links, timestamps and the agent's own summaries MAY be stored. Public pages MUST NOT accuse a
company of misconduct; a move without an identified cause MUST be labeled neutrally as
"unexplained". Every public page MUST carry the disclaimer:
"Experimental research project, not investment advice."

Rationale: the project publishes statements about real, named, publicly traded companies under one
person's name. Neutral language and provider-compliant storage keep a research demo from becoming
a legal or contractual problem.

### VII. Observability

Every agent run MUST store a trace covering tool calls, timing, token usage, and result. The
public case file MUST be built from that stored trace rather than regenerated or hand-written.

Rationale: a trace is what turns an opaque agent decision into something a reader — or the author,
six months later — can check. Deriving the public page from the trace keeps the published story
and the actual run from drifting apart.

### VIII. Testing and Quality

Deterministic logic MUST be developed test-first: a failing test MUST exist before the
implementation. External APIs MUST be mocked in tests; the test suite MUST NOT make network calls.
Type hints MUST be present throughout. Changes MUST stay small and reviewable.

Rationale: Principle I only holds if the deterministic core is actually correct, and test-first is
the cheapest way to keep it so. A single reviewer can only review small diffs well.

## Technology Constraints

- Python 3.12+, with `uv` for dependency and environment management.
- LangGraph with a hand-built `StateGraph`. Deprecated prebuilt agent constructs MUST NOT be used.
- The LLM MUST be accessed through LangChain so the provider can be swapped. Target provider:
  Azure OpenAI. Provider-specific details MUST NOT leak into application logic.
- Brave Search API for web search; X API for social posts.
- PostgreSQL as the datastore.
- One container image for the daily job.
- The project MUST run fully locally with `podman compose`.
- Deployment target: an Azure Container Apps job.
- The public site MUST be static.

## Development Workflow

- All code, comments, documentation, specs, and commit messages MUST be in English.
- Commit messages MUST follow Conventional Commits.
- Claude Code MUST NOT commit, push, or merge. The user reviews every change and commits himself.
- Each feature MUST live on its own branch and MUST be merged through a pull request.

## Governance

This constitution supersedes other practices and conventions in this project; where guidance
conflicts, the constitution wins.

Amendment procedure: a change to this constitution requires explicit user approval and a version
bump in the same change. Claude Code MAY propose amendments but MUST NOT apply them unapproved.

Versioning policy follows semantic versioning:

- MAJOR: a principle is removed or redefined in a backward-incompatible way.
- MINOR: a principle or section is added, or its guidance is materially expanded.
- PATCH: clarifications, wording, and other non-semantic refinements.

Compliance review: every pull request MUST be checked against these principles before merge.
Violations MUST be either fixed or recorded explicitly with a justification. Unresolved TODO
placeholders in this document MUST be resolved before the first evaluation period begins.

**Version**: 1.2.0 | **Ratified**: 2026-10-28 | **Last Amended**: 2026-11-11
