# Module 04 — Interviewer Q&A

**1. What happens when two requests hit the same resource at the same instant — specifically, two fetchers claiming a task from the same domain's queue?**
The frontier's `dequeue()` is a single atomic claim-and-remove operation against one domain's queue, so only one fetcher can ever receive a given task; the second fetcher's call simply returns the *next* queued URL for that domain (or nothing, if there wasn't one), never the same URL twice.

**2. What happens when traffic spikes 10x for an hour — say, a burst of newly-discovered high-priority URLs?**
The fetcher pool and parser tier are both stateless and scale by adding instances; the harder constraint is the frontier itself, which is why recrawl scheduling is the first thing paused under load (per Module 01's Load Handling) — fresh discovery keeps flowing while staleness on already-crawled pages is an acceptable, temporary cost.

**3. Why per-domain queues instead of one global queue with a rate limiter in front?**
A single rate limiter in front of one global queue would have to somehow track per-domain state anyway to avoid hammering one popular domain — at which point you've reinvented per-domain queues, just behind an extra layer. Making the queue structure itself per-domain is the simpler version of the same idea.

**4. Why a Bloom filter instead of just a hash set in memory?**
A hash set storing every distinct URL ever seen grows linearly and exactly-sized with the crawl — at billions of URLs, that's more memory than fits on one machine. A Bloom filter trades a small, tunable false-positive rate for roughly an order of magnitude less memory per entry, and this system's design already tolerates false positives (see Module 01).

**5. What's the actual cost of a Bloom filter false positive here — what happens if it wrongly says "already seen"?**
The URL is silently never crawled. That's a real, permanent cost (a page is missing from the index), not a transient one — which is why the false-positive rate is a tuned parameter (more bits per entry lowers it), not something left to chance, and why it's worth naming as a deliberate trade rather than glossing over.

**6. How would you detect and handle a crawler trap — a URL space that's effectively infinite, like a calendar page linking to "next month" forever?**
Cap total pages fetched per domain and cap URL-path depth; a domain producing new-looking URLs past either limit gets throttled hard rather than crawled to exhaustion, since no legitimate site needs unbounded depth from a single starting page.

**7. Would you fetch `robots.txt` fresh before every request?**
No — it's cached per domain with a TTL (see Module 01's Trade-offs). Refetching it on every page request spends a request from that domain's own politeness budget on a file that changes rarely, for no benefit over a periodic refresh.

**8. How would you prioritize which of billions of queued URLs to crawl next?**
A blended score of estimated page importance (inbound link count is the classic cheap proxy) and historical change-frequency, with a domain's own crawl-delay always gating *when* its queued URLs can be popped regardless of score — priority decides order within a domain, not whether politeness applies.

**9. Would you shard the frontier by domain hash, or by URL hash?**
By domain hash — every query and claim operation runs "the next task for *this* domain," which stays a single-shard operation if domains are the sharding key. Sharding by URL hash would scatter one domain's queue across every shard, turning the system's single most common operation into a fan-out.

**10. What happens if a fetcher crashes mid-fetch, after claiming a task but before reporting back?**
The task sits in `claimed` status past a reasonable timeout; a recovery job (querying `crawl_tasks(status='claimed')` past some age threshold, cross-ref the partial index in Module 03) requeues it — the same "assume nothing, verify via a durable state" discipline this guide's [payments case study](../payments-system/02-lld.md) uses for its own stuck-in-`processing` recovery.
