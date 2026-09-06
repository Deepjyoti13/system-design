# Module 02 — Low-Level Design

![The fan-out decision as a Strategy, and the read-time merge sequence](diagrams/lld.svg)

**`FanoutStrategy`** *(interface)* → **`PushFanout`** and **`PullMerge`** — the follower-count threshold decides which implementation `Post Service` calls for a given author; `Post Service` itself never branches on follower count directly, it just asks the strategy to handle delivery.

Pseudocode for the decision and both paths:
```
PostService.publish(author_id, body):
    post_id = PostStore.append(author_id, body)
    strategy = PushFanout if FollowerCount(author_id) < THRESHOLD else PullMerge
    strategy.deliver(author_id, post_id)
    return post_id

PushFanout.deliver(author_id, post_id):
    for follower_id in FollowGraph.followers(author_id):     # snapshot read
        FanoutQueue.enqueue(follower_id, post_id)             # idempotent job (see below)

PullMerge.deliver(author_id, post_id):
    pass                                                       # no work at write time, by design

FeedAssembly.getFeed(user_id, cursor):
    pushed = FeedCache.get(user_id, cursor)                   # fan-out-on-write followees
    big_followees = FollowGraph.oversizedFollowees(user_id)    # cached, not recomputed live
    pulled = [PostStore.recent(a) for a in big_followees]
    return merge_by_timestamp(pushed, pulled)[cursor:cursor+PAGE_SIZE]
```

Concurrency note: a fan-out job must be safe to run twice (a worker crash mid-job and its retry both attempt the same delivery) — `FanoutQueue.enqueue` writes are made idempotent the same way this guide's [idempotency keys](../../scalability-resilience/idempotency-keys.md) page describes: the feed cache write is a set-add (`SADD`-style), where adding the same `post_id` twice is a no-op, not a duplicate entry, so a retried job costs nothing beyond the wasted call.
