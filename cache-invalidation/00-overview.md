# Module 00 — Feature Overview

**Diagram for this module:** [`diagrams/01-architecture.svg`](diagrams/01-architecture.svg) *(this topic's HLD diagram doubles as the overview illustration — see module 01)*

## The feature, with no infrastructure in it yet

Someone changes a price on a product page. A moment later, every visitor looking at that product should see the new price — not the app server they happened to hit, not the CDN edge nearest them, not the browser tab they had open before the change. One write happened. Every copy of the old value, wherever it's sitting, has to stop being served as if it were still true.

Two things about that description matter for everything that follows:

- **The write is singular and immediate.** The source of truth changes once, atomically, the moment the price update is saved.
- **The stale copies are plural, scattered, and each one finds out on its own schedule.** A value that got cached gets cached in more than one place at once — in a Redis cluster shared by every app server, in each individual app server's own in-memory cache, and out on a CDN edge node that served it to a nearby browser. None of those copies knows the source changed until *something* tells it.

That asymmetry — **one authoritative write, many independently-living copies that all have to be chased down and told** — is the entire reason this is an interesting design problem instead of "just set a shorter TTL." Module 01 picks it up from there.

## What this module deliberately leaves out

No pub/sub topic, no purge API, no versioned URL is named yet. If you can't state the problem this plainly first — one change, many stale copies, all must catch up — you'll end up building an invalidation pipeline before you've decided what "invalidated" is even supposed to mean for this system.
