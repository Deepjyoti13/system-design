# Case Study Format

This is the standard every case study in `content/case-studies/` should follow. Two topics already meet it in full and should be read as the template before writing a new one: `content/case-studies/payments-system/` and `content/case-studies/distributed-job-scheduler/`.

Do not just skim this doc and improvise — open those two topics' files first and match their structure, depth, and voice directly.

## File structure

Every case study is a folder with exactly 5 files plus a `diagrams/` subfolder:

```
content/case-studies/<slug>/
├── 00-overview.md
├── 01-architecture-hld.md
├── 02-lld.md
├── 03-db-design.md
├── 04-interviewer-qna.md
└── diagrams/
    ├── overview.excalidraw / overview.svg
    ├── hld.excalidraw / hld.svg
    ├── lld.excalidraw / lld.svg
    └── er.excalidraw / er.svg
```

## Section checklist per file

**00-overview.md**
- A plain-language "the feature, with no infrastructure in it yet" opener — what the system does for a user, and the one hard constraint that makes it an interesting design problem.
- `## Requirements` — functional and non-functional, stated interview-style ("stated, not guessed"), with concrete numbers.
- `## Capacity Estimation` — back-of-envelope math, linking to `foundations/back-of-envelope-estimation.md`.
- `## Approach Walkthrough` — the one-paragraph core idea before any boxes.
- `## API Surface` — the actual endpoints/events.
- The overview diagram (see below) embedded right after the H1.

**01-architecture-hld.md**
- Diagram at the top.
- `## Monolith vs. microservices` — why this is (or deliberately isn't) pulled into its own service, argued from a concrete constraint, not a default preference.
- `## Building Blocks` — as a table (`| Block | Role |`).
- `## Per-path walkthrough` — arrow notation (`Client → LB → Service → DB`) for each major flow (write, read, async).
- `## Trade-offs to make explicit` — a table: `| Decision | Chosen | Alternative | Why |`, 4-5 rows.
- `## Load Handling` — peak-vs-average tolerance, where backpressure kicks in first, what gets shed under overload, autoscaling lag, a concrete load-test target.
- `## Concurrent-User Handling` — a table: `| Race | Mechanism | What the "loser" sees |`, at least 3 rows.
- `## Scaling & Reliability` — horizontal scaling, circuit breaker, retries, dead-letter queue, graceful degradation, multi-region.
- `## What you'd revisit as this grows` — 3-4 honest, named gaps.

**02-lld.md**
- Diagram at the top.
- `## Interfaces vs. implementations` — named interfaces (Repository/Strategy-style), not just prose.
- Pseudocode for the core method(s).
- Error cases worth designing for deliberately.
- `## Concurrency at the code level` — what needs no application-level lock, and why (usually: the database or store itself provides the atomicity).
- `## Design patterns you just used, named` — Repository, Strategy, State, Outbox, etc. — whichever actually apply.
- `## Practice: extend it yourself` — 2 exercises specific to this topic, no generic filler.

**03-db-design.md**
- Diagram at the top.
- `## From entities to schema` — the entities and their fields.
- Per-decision "why" reasoning as its own subheadings (why this index, why this denormalization) — this is the single most important stylistic device in this guide's DB design sections.
- `## Indexes` — an explicit list, one line each, naming the query pattern it serves.
- `## Consistency` — per-table: which need strong consistency, which can be eventual, and why.
- `## Scaling the schema` — sharding key and why, read replicas vs. sharding distinction.
- `## Connecting it back` — a closing paragraph tracing requirement → architecture decision → interface → schema.

**04-interviewer-qna.md**
- Exactly 10 numbered questions, each answered from a decision already made in this design — never a generic textbook answer.

## Diagrams

Use the `excalidraw-diagram-skill`. Every diagram in this guide shares one palette — do not invent new colors:

- `roughness: 0`, `fontFamily: 3`, `opacity: 100` on every element.
- Blue `#1e3a5f` (stroke) / `#93c5fd` or `#3b82f6` (fill) — the main flow / primary actors.
- Orange `#c2410c` / `#fed7aa` — async, webhook, or in-flight elements.
- Green `#047857` / `#a7f3d0` — the correct/success outcome.
- Red `#f87171` / `#fecaca` — a failure or rejected outcome.
- Slate `#64748b` — captions and secondary text. `#cbd5e1` — dividers.

`hld.svg`, `lld.svg`, `er.svg` are architecture-level (boxes, arrows, real component names). `overview.svg` is different on purpose: a **feature-level, ByteByteGo-style diagram with no infrastructure boxes** that visually argues the one core hard problem this topic has to solve (e.g. a token bucket draining, a scatter-gather fan-out, three possible outcomes of an at-least-once operation). Design this one specifically for the topic — never reuse another topic's layout verbatim.

Workflow: build the `.excalidraw` JSON, render it (`cd .claude/skills/excalidraw-diagram-skill/references && uv run python render_excalidraw.py <path>`), view the PNG, iterate until clean, then export the final `.svg` (`--output <path>.svg`), confirm it contains `<defs><style class="style-fonts">`, and delete the intermediate `.png` — this repo keeps only `.excalidraw` + `.svg` per diagram.

## Wiring it into the build

Add the topic to `.build/taxonomy.py` under the `case-studies` category with `"kind": "topic-casestudy"` (and `"star": True` if it's written to this full depth). No changes to `.build/build_field_guide.py` are needed — the `topic-casestudy` kind and `CASESTUDY_TABS` already handle this 5-tab shape generically.

Rebuild with `python3 .build/build_field_guide.py` from the repo root — this regenerates `index.html`. Don't hand-edit `index.html`.

If this topic is referenced by, or references, another case study, use a relative link straight to the specific tab file (e.g. `../payments-system/00-overview.md`), never to a bare `README.md` — case studies in this format don't have one.

## Voice

Precise and reasoning-forward. Every claim traces to a stated requirement or a concrete number. No fluff, no marketing language, no hedging ("might", "could potentially"). Cross-reference other guide topics via relative markdown links wherever the connection is real, not decorative.
