# Module 02 — Low-Level Design (LLD)

**Diagram for this module:** [Push Notifications — sequence diagram](https://claude.ai/code/artifact/e5f3bbe6-b842-435b-89af-e5a139c17af8)

## Which components get an LLD pass, and why

Not every box in module 01 needs one. The two with a real algorithmic or concurrency decision are the **dispatch worker** (what actually decides whether to send, and to whom) and the **dedup guard** (the thing that makes Race 1 from module 01 not a bug). Everything else — the ingest API, the token registry CRUD — is a straightforward request handler with nothing interesting to design.

## The worked design

Five classes, three of which are interfaces:

- **`NotificationDispatcher`** — orchestrates one job: resolve the provider, attempt a dedup claim, send, classify the result.
- **`NotificationProvider`** *(interface)* → **`ApnsProvider`**, **`FcmProvider`** — `send(deviceToken, payload) -> DeliveryResult`. The dispatcher talks to "a provider," never to Apple's or Google's API directly.
- **`DedupGuard`** *(interface)* → **`RedisDedupGuard`** — `tryClaim(eventId, deviceToken) -> bool`.
- **`TargetResolver`** *(interface)* → **`DirectTargetResolver`** (producer already supplies the one device — the 2FA case) / **`FollowerGraphTargetResolver`** (expands "user X" into a paginated stream of that user's followers' device tokens — the broadcast case).
- **`TokenRegistry`** — `invalidate(deviceToken)`, called on a permanent provider failure.

### Pseudocode for the two methods that matter

```
DispatchWorker.handle(job):
    claimed = dedupGuard.tryClaim(job.eventId, job.deviceToken)
    if not claimed:
        return  # Race 1 from module 01 — another worker already sent this

    provider = providerFor(job.platform)          # ApnsProvider or FcmProvider
    result = provider.send(job.deviceToken, job.payload)

    if result.isTransientFailure():                # rate-limited, timeout, 5xx
        requeueWithBackoff(job, attempt = job.attempt + 1)
        if job.attempt >= MAX_RETRIES:
            sendToDeadLetterQueue(job)
    elif result.isPermanentFailure():               # 410 / NotRegistered
        tokenRegistry.invalidate(job.deviceToken)   # async path from module 01
    # success: nothing further to do — result already recorded by the provider adapter

RedisDedupGuard.tryClaim(eventId, deviceToken):
    key = f"sent:{eventId}:{deviceToken}"
    return redis.SETNX(key, 1, ttl = 24h)           # atomic across every worker instance
```

Two error cases worth designing for deliberately, not collapsed into one generic "failed":

- **Transient vs. permanent provider failure** are fundamentally different: a transient failure (timeout, 429, 5xx) means "try again later" — the job is requeued with backoff. A permanent failure (410, `NotRegistered`) means "never try this token again" — retrying would just waste calls against a dead device forever. Treating both as one `FAILED` state would either retry a dead token indefinitely or give up on a device that was only briefly unreachable.
- **Claimed vs. not claimed** in `tryClaim` are not an error and a success — they're both valid, expected outcomes. A caller that logs "not claimed" as a failure would fill dashboards with noise for something that's working exactly as designed (Race 1's resolution).

### The sequence: what actually happens when a celebrity posts

Figure 1 in the diagram walks this end to end: `Producer → Ingest API → Kafka (bulk topic) → Target Resolver (paginates followers) → Kafka (per-device dispatch topic, partitioned by device-token hash) → Dispatch Worker → DedupGuard.tryClaim → NotificationProvider.send → (success | requeue | invalidate)`. The important thing to notice: the dispatcher never knows or cares whether a job came from a 2FA event or a celebrity post — that distinction lives entirely in *which topic* the job arrived on and *which worker pool* is consuming it, not in the dispatcher's own logic.

## Concurrency, at the code level

`RedisDedupGuard.tryClaim` **must** use a distributed mechanism (Redis `SETNX`), not an in-process mutex — the dispatch worker pool runs on many instances (module 01's whole reason for using a queue in the first place is to spread 833K/sec across a fleet), so an in-process mutex would only stop one worker from double-sending against its own redelivery, not stop two *different* worker instances from both picking up the same redelivered job. Anywhere in this codebase a lock is proposed, the first question is "does this component ever run on more than one instance?" — here, always yes, so a distributed claim is the only correct answer.

## Design patterns you just used, named

- **Strategy pattern** — `NotificationProvider` and `TargetResolver` are both strategies: which concrete implementation runs is picked by data on the job (platform, event type), and the dispatcher's own code never branches on it.
- **Adapter pattern** — `ApnsProvider`/`FcmProvider` each adapt a wildly different third-party API (Apple's HTTP/2 binary protocol vs. Google's REST API) to the same `send()` interface.
- **Circuit breaker** (module 01) — wraps each `NotificationProvider` implementation independently, so a failing APNs doesn't trip anything on the FCM side.

## Practice: extend it yourself

Before moving to module 03, sketch how you'd add:

1. **Notification collapsing** — if a user gets 5 "someone liked your post" events in a minute, the product wants to show one notification ("5 people liked your post"), not 5. Which class owns that decision, and does it change the dedup key's shape?
2. **User-level quiet hours** — a user has muted notifications from 10pm–7am their local time. Where does that check belong — `TargetResolver` (skip the device entirely) or `DispatchWorker` (attempt send, get a "muted" response)? What's the cost of getting this wrong in each direction?

There's no single right answer to either — the point is noticing that the interface boundaries already drawn make it obvious *where* each change belongs.
