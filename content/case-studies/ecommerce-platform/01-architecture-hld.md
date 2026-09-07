# Module 01 — Architecture & High-Level Design

![Gateway routing to four services, each annotated with its own consistency model and datastore](diagrams/hld.svg)

## Monolith vs. microservices

This case study's core decision is the split itself, so it's worth arguing from the failure mode of not splitting rather than from a default preference. Fold catalog, cart, order, and recommendations into one "commerce" service sharing one database, and the numbers from Module 00 collide directly: a flash-sale-driven catalog read spike (~43,000/sec) now competes for the same connection pool, the same CPU, the same deploy cadence as a checkout path that must never silently fail (~140/sec peak, but zero tolerance for being wrong). The catalog spike doesn't even need to be caused by anything related to checkout — a homepage feature or a viral product page is enough — and it can still degrade the one path in the system that isn't allowed to degrade. A bad deploy of the recommendation model, similarly, has no business being in a position to take down checkout, but in a monolith it's one restart away from doing exactly that.

Splitting into four services with four independent datastores means each one's load profile and each one's bugs stay contained to itself. Catalog and search absorb the flash-sale read spike by scaling horizontally and caching aggressively; nothing about that requires touching the order database's connection pool. A bad recommendation model rollout degrades recommendations, not checkout. This is what "genuinely different scaling and consistency needs" means as an architectural argument rather than a slogan: the split isn't there because microservices are more serious, it's there because the four verbs from Module 00 (browse, add to cart, buy, get recommended things) have three orders of magnitude of difference in traffic and completely different costs for being wrong, and no single database technology or deployment unit serves all three well at once.

If your catalog is a few thousand SKUs and traffic is modest, none of this decomposition earns its complexity yet — a single service with a well-indexed relational database and an in-memory cache handles it fine, and splitting prematurely just adds four deploy pipelines and four sets of on-call pages for no real isolation benefit. The split pays for itself specifically once catalog read volume and checkout's correctness requirements are large enough to actually conflict for real infrastructure.

## Building Blocks

| Block | Role |
|---|---|
| **API Gateway** | Single entry point ([API Gateway](../../hld-building-blocks/api-gateway.md)); routes by path to the four backend services, handles auth and rate limiting once instead of four times |
| **Catalog/Search Service** (stateless) | Serves product reads from a cache-backed read model; owns queries against the search index |
| **Search Index** | Inverted index over the catalog ([Search & Inverted Indexes](../../scalability-resilience/search-inverted-indexes.md)), updated asynchronously from the catalog store |
| **Cart Service** (stateless) | Per-user cart state in a fast key-value store with TTL-based expiry; no durability guarantee beyond that |
| **Order/Checkout Service** | The transactional core: atomic inventory decrement, order creation, payment orchestration — the schema and race conditions are covered in full by [`ecommerce-schema-worked-example.md`](../../database-design/ecommerce-schema-worked-example.md) |
| **Payment Client** | A call out to this guide's own [Payments System](../payments-system/00-overview.md) case study — checkout doesn't reimplement charge/refund crash-safety, it depends on a service that already solved it |
| **Recommendation Service** (stateless) | Serves precomputed, cached recommendations; refreshed by an offline batch/ML pipeline decoupled from the request path |

## Per-path walkthrough

**Catalog browse (read)** — `Client → Gateway → Catalog Service → Cache (hit) → Client`, or on a miss, `→ Search Index / Catalog Store → Cache (populate) → Client`. The overwhelming majority of this system's total request volume lives entirely in this path, and none of it ever reaches the order database.

**Add to cart (write, weak durability)** — `Client → Gateway → Cart Service → Cart Store (Redis, TTL) → Client`. Nothing about this path touches inventory. A cart holding an item is not a claim on that item — see Concurrent-User Handling below for what that means when stock runs out.

**Checkout (write, strong durability)** — `Client → Gateway → Order Service → Order DB (ACID: conditional inventory decrement + order + order_items insert, one transaction) → Payment Client (external charge call, bracketed by durable pending/succeeded/failed states exactly as the payments case study describes) → Order DB (status update) → Client`. On a payment failure after the inventory decrement already committed, the order service issues a compensating release rather than a rollback — the same reasoning [`ecommerce-schema-worked-example.md`](../../database-design/ecommerce-schema-worked-example.md) and this guide's [Sagas](../../hld-building-blocks/distributed-transactions-saga.md) page both make: once a step has committed, undoing it is a new, explicit action, not a rewind.

**Recommendations (async, read)** — `Offline ML pipeline → Recommendation Store (precomputed per-user) → Recommendation Service → Cache → Client`, refreshed on its own schedule, fully decoupled from every other path. A recommendation service outage or a stale model is invisible to checkout by construction — there's no shared transaction, no shared connection pool, no shared deploy.

## Trade-offs to make explicit

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Service boundary | Four services (catalog, cart, order, recommendations), four datastores | One commerce monolith, one shared database | Each has a different scaling and consistency profile (Module 00's numbers); bundling means one component's load spike or bug becomes every component's incident |
| Cart durability | Best-effort TTL cache (Redis) | Same relational, ACID-backed store as orders | A lost cart costs a re-add click; buying the order store's durability guarantee for data that doesn't need it is pure cost |
| Catalog datastore | Document store + search index | Same relational store as orders | Catalog wants a shape that matches the API response and full-text search over mostly-static data; orders want multi-row ACID transactions — one store serving both well is exactly the default-preference mistake [SQL vs. NoSQL](../../database-design/sql-vs-nosql.md) warns against |
| Inventory reservation | Decrement only at checkout, inside the order transaction | Reserve inventory the moment an item is added to a cart | A cart is not a hold. Reserving at add-to-cart would lock real stock against browsers who never intend to buy, and an abandoned cart would starve inventory from a customer who actually would |
| Recommendation freshness | Precomputed offline, served from cache, hours-stale | Computed live, per request | The cost of a stale or mediocre suggestion is close to zero; paying real-time compute cost on every product page to shave that near-zero cost further isn't worth it |

## Load Handling

- **Peak-vs-average tolerance:** the catalog path's 5x peak (~43,000/sec) is an ordinary horizontal-scaling-plus-caching problem, and the whole point of the service split is that it stays that service's problem. The checkout path's peak (~140/sec) is small in absolute terms but has zero tolerance for silent failure — the same "correctness beats speed" bar this guide's payments case study sets, applied specifically to the order transaction and the payment call inside it.
- **Where backpressure kicks in first:** the catalog service, under a cache-miss storm during a flash sale — a hot product page expiring and every concurrent reader missing at once. [Caching Strategies](../../hld-building-blocks/caching-strategies.md)'s thundering-herd mitigations (request coalescing, jittered TTLs, stale-while-revalidate) are what absorbs this, not the order database, because the order database was never in this path to begin with.
- **What gets shed under overload:** catalog and recommendations can both degrade — a slightly stale cached product page, or recommendations falling back to a generic "bestsellers" list when the personalized path is under pressure. Checkout sheds nothing: same discipline as the payments case study, a request that can't be safely honored gets a clean rejection (`503`, `Retry-After`) rather than silent, ambiguous handling.
- **Autoscaling lag:** catalog, cart, gateway, and recommendation tiers are stateless and scale on the usual 1–3 minute horizon. The order service scaling out does *not* solve overselling on its own — the database's conditional decrement is what actually prevents it, regardless of how many order-service instances are running.
- **Load-test target:** sustain 50,000 catalog reads/sec for 10 minutes with cache hit rate holding and zero 5xx errors; separately, sustain a 500-checkout/sec burst (a flash-sale product drop) with zero oversold units and zero double-charged orders.

## Concurrent-User Handling

| Race | Mechanism | What the "loser" sees |
|---|---|---|
| Two checkouts racing for the last unit of a product | Conditional inventory decrement in the same transaction as the order insert (`UPDATE inventory SET count = count - 1 WHERE product_id = ? AND count > 0`), exactly as [`ecommerce-schema-worked-example.md`](../../database-design/ecommerce-schema-worked-example.md) works out | An explicit "out of stock" at checkout — never a second order silently created against a unit that's already sold |
| A cart item's price changed between add-to-cart and checkout | The cart stores the price at the moment each item was added; checkout re-validates against the current price and either honors the frozen price or surfaces the difference | The customer sees the discrepancy explicitly rather than being silently charged whatever the price happens to be at checkout |
| Same `idempotency_key` submitted twice for checkout (a client retry racing its own original request) | Forwarded through to the payment call the same way the payments case study's `payment_intents(idempotency_key)` unique constraint works | The first attempt's result; the second is a no-op, never a second order or a second charge |
| A catalog page is read while a flash sale is draining that product's inventory | Catalog reads are served from a cache/read model that can lag the true stock count by a few seconds; the authoritative check only happens at checkout | A shopper can add an item that's technically already sold out — and finds out at the one moment it actually matters, per the "don't reserve at add-to-cart" decision above |

## Scaling & Reliability

- **Horizontal scaling:** catalog, cart, gateway, and recommendation tiers are stateless and scale by request rate like any tier in this guide. The order service scales the same way for throughput, but its correctness ceiling is the database's conditional-update discipline, not instance count.
- **Circuit breaker:** the order service's call out to the Payment Client is wrapped in a circuit breaker ([Circuit Breakers & Retries](../../scalability-resilience/circuit-breakers-retries.md)) exactly as the payments case study's own orchestration service does — a systematically failing processor trips the breaker rather than piling up checkout requests waiting on it.
- **Retries:** idempotent only, and only with the same forwarded `idempotency_key` — a retried checkout that generated a new key would defeat the entire guarantee.
- **Dead-letter queue:** catalog-index sync events and recommendation-pipeline events that fail to apply after N attempts land in a DLQ rather than blocking every other product's or user's update behind them.
- **Graceful degradation:** catalog falls back to a slightly stale cached response; recommendations fall back to a generic, non-personalized list; cart simply loses the in-progress cart if the store is unavailable (a real but bounded cost). Checkout does not degrade — an order transaction either completes cleanly, fails cleanly with an explicit reason, or lands in a durable `pending_payment` state a reconciliation path resolves, the same three-outcome discipline the payments case study is built around.
- **Multi-region:** not built here, named as a real gap below rather than assumed away.

## What you'd revisit as this grows

- **Extreme-contention inventory scenarios.** A single hot SKU during a massive, synchronized flash-sale drop pushes far more concurrent traffic at one `inventory` row than the plain conditional-decrement pattern here comfortably absorbs — that's a genuinely harder problem than "prevent overselling under ordinary load," and deliberately out of scope for this module.
- **Multi-region active-active**, for both the order database's ACID core and the catalog's read replicas — a single-region design is a single point of regional failure for the one path that can't tolerate silent failure.
- **Search relevance and ranking quality** — this module covers how the search index stays in sync and scales, not how results get ranked, which is its own substantial design problem.
- **Recommendation pipeline freshness and quality** — precomputed and cached is the right default here, but a mature system needs to reason about how stale is too stale, and what a cold-start user (no history yet) actually sees.
