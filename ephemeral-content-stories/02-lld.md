# Module 02 — Low-Level Design

**Diagram for this module:** [Ephemeral Stories — getTray sequence diagram](https://claude.ai/code/artifact/552c7fb9-9073-4af0-a0a9-395de08265f3)

## Which components get an LLD pass, and why only these

Not every service needs this treatment — only the ones with a real algorithmic or concurrency decision inside them. Here that's the **visibility/expiry mechanism** (the core idea of this whole worked example) and the **viewer-list writer** (the one place duplicate events can double-count something user-visible). The Media Upload Service and the transcoding workers are comparatively mechanical — file in, file out — and don't need class-level design.

## Classes

- **`StoryController`** — HTTP layer. `POST /stories`, `GET /tray`, `POST /view`. Knows nothing about Redis, MySQL, or Kafka.
- **`StoryService`** — the orchestration: `create(userId, mediaRef)`, `getTray(userId)`, `recordView(storyId, viewerId)`.
- **`StoryVisibilityStore`** *(interface)* → **`RedisVisibilityStore`** — `markVisible(storyId, ttl)` / `isVisible(storyId)`, wrapping Redis `SET storyId 1 EX ttl` / `EXISTS storyId`.
- **`StoryRepository`** *(interface)* → **`SqlStoryRepository`** — the partitioned `stories` table.
- **`ViewerListWriter`** *(interface)* → **`SqlViewerListWriter`** — idempotent insert into `story_views`.
- **`EventBus`** *(interface)* → **`KafkaEventBus`** — publishes `story.created` / `story.viewed`.

Depending on interfaces rather than concrete classes here isn't decoration: it's what makes `RedisVisibilityStore` swappable for a `DynamoDbVisibilityStore` later (Module 01's trade-off table names this as a legitimate alternative) without `StoryService` changing at all.

## Pseudocode for the methods that matter

```
Service.create(userId, mediaRef):
    storyId = repository.nextId()
    repository.save(storyId, userId, mediaRef, createdAt=now())
    visibilityStore.markVisible(storyId, ttl=24h)     # Redis EXPIRE — the actual source of truth for "is this live"
    eventBus.publish("story.created", storyId, mediaRef)
    return storyId

Service.getTray(userId):
    following = repository.getFollowing(userId)                       # cached separately, not detailed here
    candidateIds = repository.getRecentStoriesFor(following, window=24h)
    visible = [id for id in candidateIds if visibilityStore.isVisible(id)]   # Redis is the gate, not the SQL row's expires_at
    return visible

Service.recordView(storyId, viewerId):
    inserted = viewerListWriter.insertIfAbsent(storyId, viewerId, viewedAt=now())   # unique constraint underneath
    if inserted:
        eventBus.publish("story.viewed", storyId, viewerId)     # only fire once — not on a duplicate
```

Two error cases designed for deliberately:

- **`markVisible` fails right after `repository.save` succeeds** (Redis down mid-request): this cannot be allowed to leave a permanently-ungated (and therefore permanently-visible, forever) row. `create` retries `markVisible` a bounded number of times and, if it still fails, rolls back the SQL insert rather than silently proceeding — a visibility-store failure is treated as a *create* failure, because "never show an ungated story" is the one hard-safety requirement from Module 01.
- **`getTray` runs with Redis fully down**: falls back to checking `expires_at` on the SQL row directly (slower, but always correct) instead of failing the read outright — the same "degrade, don't fail" instinct as the Load Handling section in Module 01. See the diagram's fallback branch.

## The sequence: `GET /tray`

The diagram walks the happy path (Redis answers `isVisible` directly) and the fallback branch (Redis is unavailable, so `StoryService` asks `SqlStoryRepository` for the row's `expires_at` instead). Notice the controller and the service never know or care which branch ran — that decision lives entirely inside `getTray()`'s implementation, which is the interface boundary from the Classes section paying off exactly like it does in the root project's `02-lld-fundamentals.md`.

## Concurrency, at the code level

`StoryService` runs on a stateless, horizontally-scaled fleet (Module 01, Step 2) — there is never an in-process mutex anywhere in this design, because a mutex only protects against races on one process, and any of these methods can run on N instances simultaneously. Each race from Module 01's "Concurrent-user handling" section is enforced at a specific, named point:

- **Duplicate view** — enforced by the database itself, inside `SqlViewerListWriter.insertIfAbsent`'s unique constraint on `(story_id, viewer_id)` — not in application code, so it holds regardless of which instance handles the request.
- **Duplicate transcoding delivery** — enforced by the transcoding worker's upsert being keyed on `story_id`, not by anything in `StoryService`.
- **Redis-down fallback** — not a race at all, just a failover branch; both paths are read-only from `StoryService`'s point of view.

## Design patterns you just used, named

- **Repository** — `StoryRepository` and `ViewerListWriter` hide storage details behind method calls.
- **Strategy** — `StoryVisibilityStore` is a strategy; a `DynamoDbVisibilityStore` implementation would be a drop-in alternative.
- **Idempotent consumer** — both `recordView` and the transcoding worker are written so that processing the same event twice is safe, which is the standard answer to Kafka's at-least-once delivery guarantee.

## Practice: extend it yourself

Try sketching how you'd add a **"close friends" story list** (a story visible only to a subset of your followers): which method's signature changes, and does the change belong in `StoryService`, in `StoryRepository`'s query, or in both? Notice how the interface boundaries already drawn make it obvious where the change has to live — the same point Module 02 of the root project's worked example makes for custom aliases and expiration.
