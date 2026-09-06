# Command

![Command: TriggerJobCommand as a self-contained object carrying everything needed to execute or re-execute it later](diagrams/command.svg)

## The Problem It Solves

A request needs to be queued, retried, logged, or replayed independently of whoever originally created it — which is impossible if "do the thing" is just a method call that executes immediately and leaves nothing behind once it returns.

## How It Works

The request itself becomes an object, carrying every piece of data it needs to execute (or re-execute), plus an `execute()` method that performs the action. The object that creates the command (the invoker) hands it off — typically to a queue — rather than calling the receiver directly. Whatever eventually executes the command doesn't need the creator to still be running, or even to still exist.

## Implementation

```
class TriggerJobCommand:
    jobId: string
    payload: object
    idempotencyKey: string       # job_id + the specific scheduled time it fired for

    execute():
        worker.run(self.jobId, self.payload, self.idempotencyKey)

# the Scheduler creates the command and hands it to a queue -- it never calls the Worker directly:
command = TriggerJobCommand(job.id, job.payload, f"{job.id}:{job.nextRunTime}")
triggerQueue.enqueue(command)

# a Worker dequeues and executes later, possibly on a different process entirely:
command = triggerQueue.dequeue()
command.execute()
```

## Real-World Use Case

This guide's [Distributed Job Scheduler](../../case-studies/distributed-job-scheduler/02-lld.md) encapsulating "trigger this job" as a first-class event — carrying its own `idempotencyKey`, queued, and executed by a worker pool that never talks to the Scheduler that created it — *is* this pattern. A failed delivery just means the *same* command object gets redelivered from the queue and re-executed — `idempotencyKey` is what makes that safe. The Worker never needed the Scheduler to still be running, or even to still exist, to retry the job.

## When to Use It

- A request needs to be queued, delayed, retried, or logged as its own durable record — not just executed and forgotten.
- The thing that eventually executes the request may run on a different process, machine, or time than whatever created it.
- You want an audit trail of exactly which requests were made, independent of whether they've executed yet.

## When NOT to Use It

If a request always executes synchronously and immediately, with no need to queue, log, or undo it later, wrapping it in a command object adds a class with no behavior the direct method call didn't already have.

## Related Patterns

Command is the object-oriented shape behind every message-queue-based architecture in this guide — see [Message Queues & Pub/Sub](../../hld-building-blocks/message-queues-pubsub.md) for the infrastructure-level version. It's also frequently paired with the [Transactional Outbox](../../hld-building-blocks/transactional-outbox-cdc.md): the command object is exactly what gets written to the outbox table and relayed later.
