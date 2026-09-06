# Template Method

![Template Method: this guide's own case-study format as a fixed 5-step skeleton, each case study filling in the same 5 blanks differently](diagrams/template-method.svg)

## The Problem It Solves

Several variants of a process share the same fixed sequence of steps, but differ in what fills in one or two of those steps — and without this pattern, each variant either copy-pastes the whole sequence (drifting out of sync over time as one variant's copy gets a bug fix the others don't) or the shared steps get duplicated across every variant.

## How It Works

A base class defines the fixed sequence of steps as one method, marked so it can never be overridden or reordered by a subclass. Each individual step is a separate method that the base class calls in that fixed order — some steps have a sensible default, others are declared abstract and must be filled in by each concrete subclass. The subclass controls *what* happens in each step; it never controls *when* or *whether* a step runs.

## Implementation

The genuinely useful example here is a meta one: **you are reading a live instance of Template Method right now.** Every case study in this guide fills in the exact same five-step skeleton — Overview, Architecture & HLD, LLD, DB Design, Interviewer Q&A — in the exact same order, every time. The [Payments System](../../case-studies/payments-system/00-overview.md) fills "LLD" with a `PaymentStatus` state machine; the [Distributed Job Scheduler](../../case-studies/distributed-job-scheduler/00-overview.md) fills the identical slot with an atomic claim and an idempotency key. The skeleton — the number and order of steps — never changes; only what fills each step does.

```
abstract class CaseStudyOutline:
    # the fixed skeleton -- final, no subclass ever reorders or skips a step
    final write():
        writeOverview()
        writeArchitectureAndHLD()
        writeLLD()
        writeDbDesign()
        writeInterviewerQnA()

    # each concrete case study fills in every step differently
    abstract writeOverview()
    abstract writeArchitectureAndHLD()
    abstract writeLLD()
    abstract writeDbDesign()
    abstract writeInterviewerQnA()

class PaymentsSystemOutline extends CaseStudyOutline:
    writeLLD():
        describeStateMachine("pending -> processing -> succeeded | failed")
    # ... the other four steps, filled in differently than every other case study
```

## Real-World Use Case

Beyond this guide's own structure: a data-import pipeline that always validates, then transforms, then loads — regardless of source format — with only the "parse this specific format" step varying between a CSV importer and a JSON importer, is the same shape applied to a backend batch job.

## When to Use It

- Two or more processes share an identical sequence of steps, and only a small number of those steps actually differ between them.
- You want the *order* of steps to be structurally guaranteed — impossible for a variant to accidentally skip or reorder a step.
- New variants should only need to implement the steps that differ, inheriting everything else for free.

## When NOT to Use It

If the "shared skeleton" is actually only followed by one concrete case today, with no second variant in sight, this is premature structure — write the one sequence directly and extract the template only once a second, genuinely similar variant shows up.

## Related Patterns

Template Method and [Strategy](10-strategy.md) both let one part of a process vary — the difference is Template Method fixes the *entire surrounding sequence* in a base class and only lets individual steps vary via subclassing, while Strategy swaps one *whole algorithm* via composition (an interface passed in), with no shared base sequence at all.
