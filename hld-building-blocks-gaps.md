# HLD Building Blocks — Audit & Gap List

**Scope:** what the field guide already covers at the "building block" layer, what top product companies actually ask about in HLD rounds, and the prioritised list of pages to add.

---

## Part 1 — What already exists

### `hld-building-blocks/` (11 pages)

| Page | Words | What it covers |
|---|---|---|
| `load-balancing` | 913 | L4 vs L7, algorithms, health checks, request path |
| `caching-strategies` | 1009 | 4 patterns, eviction, invalidation, thundering herd |
| `cdn` | 893 | RTT not bandwidth, cache key, versioning over purge |
| `forward-reverse-proxies` | 707 | who's hidden, forward vs reverse, traced through both |
| `api-gateway` | 814 | what lives at the gateway, gateway vs service mesh |
| `message-queues-pubsub` | 828 | decoupling, P2P vs pub-sub, delivery guarantees, ordering |
| `kafka-distributed-log` | 1727 | partitions, consumer groups, ISR/acks, compaction |
| `rate-limiting` | 910 | algorithms, enforcement point, distributed limiter |
| `consistent-hashing` | 803 | hash ring, virtual nodes, node join |
| `data-partitioning-sharding` | 944 | strategies, shard key, what gets harder |
| `replication-consensus` | 1001 | single-leader, multi-leader, leaderless, consensus |

### Adjacent categories that already absorb some HLD fundamentals

- **Foundations** — client-server, stateless vs stateful, sync vs async, REST/RPC/GraphQL, latency/throughput/CAP
- **Scalability & Resilience** — circuit breakers & retries, service discovery, idempotency keys, bloom filters, distributed locks, logs/metrics/tracing, object storage, WebSockets/SSE, search & inverted indexes
- **Database Design** — SQL vs NoSQL, indexing, ACID vs BASE, normalization, replication & failover

**Honest read of the gap:** the guide is strong on *stateless infrastructure* (LB, cache, CDN, proxy, gateway, queue) and *data distribution* (hashing, sharding, replication). It is thin on four whole families that interviews now lean on hardest:

1. **Correctness across services** — distributed transactions, outbox, CDC, consistency models
2. **The network and the client contract** — DNS, TCP/TLS/HTTP versions, API design, webhooks, serialization
3. **Failure under load, not just failure** — backpressure, load shedding, bulkheads, graceful degradation, autoscaling
4. **Who is allowed to do what** — authn/authz, multi-tenancy, tenant isolation

---

## Part 2 — What companies are actually asking

| Company | HLD fundamentals surfaced in their rounds | Source |
|---|---|---|
| **Booking.com** | consistency models (eventual for search, strong for payments), optimistic locking / double-booking, idempotent APIs, saga for distributed transactions, circuit breakers + exponential backoff, graceful degradation, multi-region replication, search index sync | systemdesignhandbook |
| **Razorpay** | idempotency keys with DB uniqueness constraints, at-least-once webhook delivery (backoff, DLQ, dedupe on event id), double-entry ledgers, partial failure / lost acknowledgement, multi-acquirer routing with failover | usegreenroom, spacecomplexity |
| **Atlassian** | multi-tenant SaaS, tenant isolation, plugin/extensibility sandboxing, permissions & access control, observability, blast-radius containment (one tenant's plugin must not degrade others) | systemdesignhandbook |
| **Salesforce** | shared-schema multi-tenancy with row-level security, sharding by tenant, event bus decoupling, retries + idempotency, active-active multi-region, graceful failover, API gateway rate limiting for partners | systemdesignhandbook |
| **Swiggy (SDE-3)** | authentication + per-user permission model on top of an upload service, consistency vs availability trade-off, caching | GeeksforGeeks |
| **Uber** | dispatch & geospatial indexing, marketplace matching, distributed transactions / saga | techinterview.org |
| **Flipkart / e-commerce** | inventory consistency, order flow, cart, catalogue search | GfG interview experiences, Double Pointer |
| **Broad (SystemCraft HLD set)** | back-of-envelope estimation, fan-out on write vs read, event sourcing, CQRS, saga, leader election, geohash/quadtree, LSM trees, distributed locking, time-series stores, inverted index | systemcraft.in |

Two patterns worth naming because they change what we write, not just what we list:

- **The pivot is always failure.** Razorpay's framing — *"correctness under failure beats throughput"* — matches Booking.com's double-booking and Atlassian's plugin blast radius. Pages should lead with the failure the block exists to contain.
- **Authorization is now a design round, not a footnote.** Swiggy asked it directly; Atlassian and Salesforce both centre it. The guide currently has zero pages on it.

---

## Part 3 — Prioritised topics to add

### Tier 1 — add these first (asked in nearly every senior round, zero coverage today)

| # | Proposed page | Category | Why it's a gap |
|---|---|---|---|
| 1 | **Consistency Models** — strong / linearizable, read-your-writes, monotonic reads, causal, eventual; quorums (R+W>N), read repair, hinted handoff | hld-building-blocks | CAP is named in Foundations but the *models* are never enumerated. Booking.com asks it as "which parts are eventual and which are strong". This is the vocabulary page everything else links to. |
| 2 | **Distributed Transactions: 2PC, Saga & Compensation** — choreography vs orchestration, compensating actions, why 2PC is avoided | hld-building-blocks | Booking.com, Uber, Salesforce. Currently only implicit inside the payments case study. |
| 3 | **The Transactional Outbox & Change Data Capture** — the dual-write problem, outbox table + relay, Debezium, log tailing, ordering guarantees | hld-building-blocks | The single most common "how do you keep the DB and Kafka in sync" follow-up. Referenced already by `sync-vs-async` but no page exists. |
| 4 | **Back-of-the-Envelope Estimation** — QPS, storage, bandwidth, memory; the numbers every engineer should know; when to stop estimating | foundations | Every interview opens here. SystemCraft lists it as a core block. Nothing in the guide teaches the arithmetic. |
| 5 | **AuthN & AuthZ at the HLD Layer** — sessions vs JWT vs opaque tokens, OAuth2/OIDC flows, API keys, mTLS between services, RBAC/ABAC/ReBAC (Zanzibar-style) | hld-building-blocks | Swiggy asked it as the whole question. Atlassian and Salesforce centre it. Complete gap. |
| 6 | **Backpressure, Load Shedding & Bulkheads** — queue depth as a signal, admission control, priority shedding, thread-pool isolation, graceful degradation | scalability-resilience | `circuit-breakers-retries` covers *a dependency failing*; nothing covers *you being overloaded*. Booking.com and Salesforce both name graceful degradation explicitly. |
| 7 | **DNS, Anycast & Global Traffic Management** — resolution path, TTLs, GSLB, latency vs geo vs weighted routing, DNS failover and why it's slow | hld-building-blocks | "What happens when you type a URL" is still a top opener; the guide starts at the load balancer. |

### Tier 2 — strong adds (frequently asked, or currently only implicit)

| # | Proposed page | Category | Why |
|---|---|---|---|
| 8 | **Webhooks & Outbound Delivery** — at-least-once, signing/HMAC verification, retries with backoff, DLQ, replay endpoints, consumer-side dedupe | hld-building-blocks | Razorpay's named topic. Also every payments/SaaS integration question. |
| 9 | **Multi-Region Architecture** — active-active vs active-passive, RTO/RPO, write routing, conflict resolution, data residency, failover drills | hld-building-blocks | Booking.com, Salesforce. `db-replication-failover` covers a single region only. |
| 10 | **Multi-Tenancy & Tenant Isolation** — silo vs pool vs bridge, row-level security, per-tenant limits, noisy-neighbour containment, per-tenant sharding | hld-building-blocks | Atlassian and Salesforce both make this the core of the round. |
| 11 | **API Design for Scale** — versioning, offset vs cursor pagination, bulk endpoints, partial responses, error contracts, deprecation | foundations | `rest-vs-rpc-vs-graphql` picks the *style*; nothing covers designing the actual contract. |
| 12 | **Serialization & Schema Evolution** — JSON vs Protobuf vs Avro, schema registry, forward/backward compatibility, gRPC over HTTP/2 | foundations | The natural sequel to REST vs RPC; asked whenever Kafka is on the board. |
| 13 | **Event-Driven Architecture, Event Sourcing & CQRS** — events as facts, read models, replay, when *not* to event-source | hld-building-blocks | SystemCraft lists both; Salesforce names the event bus explicitly. |
| 14 | **Leader Election & Coordination** — leases, fencing tokens, ZooKeeper/etcd, singleton schedulers, split brain | hld-building-blocks | `replication-consensus` covers consensus for *data*; election for *control* (who runs the cron) is separate and commonly asked. |
| 15 | **Optimistic vs Pessimistic Concurrency Control** — version columns, `SELECT … FOR UPDATE`, MVCC, `SKIP LOCKED`, when a distributed lock is the wrong tool | database-design | Booking.com's double-booking question. `distributed-locks` covers Redlock, not DB-level CC. |
| 16 | **Networking Fundamentals for System Design** — TCP vs UDP, handshakes and RTT, TLS termination, HTTP/1.1 vs 2 vs 3, keep-alive, connection pooling, Little's Law | foundations | Underpins CDN, LB, gateway and every latency argument in the guide. |

### Tier 3 — rounds it out

| # | Proposed page | Category | Why |
|---|---|---|---|
| 17 | **Fan-out on Write vs Fan-out on Read** (push vs pull) | hld-building-blocks | Used in feed/notification case studies; deserves its own page since the trade-off recurs everywhere. |
| 18 | **Batch vs Stream Processing** — windowing, watermarks, late data, Lambda vs Kappa | hld-building-blocks | Ad-click pipeline uses it; no building block explains it. |
| 19 | **OLTP vs OLAP & the Analytics Path** — columnar stores, warehouse/lakehouse, why you don't run reports on the primary | database-design | Common follow-up: "where do the dashboards read from?" |
| 20 | **Autoscaling & Capacity Management** — metric choice, queue-depth scaling, cold starts, scale-in safety, provisioned vs burst | scalability-resilience | The natural counterpart to backpressure. |
| 21 | **Geospatial Indexing** — geohash, quadtree, S2, H3 | hld-building-blocks | Proximity + ride-sharing case studies both need it; SystemCraft lists it. |
| 22 | **Clocks & Ordering** — NTP skew, logical/vector clocks, HLC, why timestamps aren't ordering | hld-building-blocks | Prerequisite for conflict resolution, CDC ordering and last-write-wins arguments. |
| 23 | **SLIs, SLOs & Error Budgets** — defining availability, percentiles over averages, error budget policy | scalability-resilience | "How do you measure that it's 99.99%?" — `logs-metrics-tracing` covers instrumentation, not targets. |
| 24 | **Data Retention, Archival & Deletion** — tiering, TTLs, soft delete, GDPR erasure across replicas and backups | database-design | Salesforce guide names compliance directly; also a good differentiator answer. |

---

## Suggested build order

Written as three batches so each one lands as a coherent slice of the guide:

1. **Correctness batch** — Consistency Models → Distributed Transactions & Saga → Outbox & CDC → Optimistic vs Pessimistic Locking
2. **Contract batch** — Back-of-the-Envelope → DNS & Global Traffic → Networking Fundamentals → API Design → Webhooks → Serialization
3. **Operations batch** — AuthN/AuthZ → Multi-Tenancy → Multi-Region → Backpressure & Load Shedding → Autoscaling → SLOs

Each page follows the existing house style: `## What problem this solves`, 3–4 technical sections with real numbers and named failure modes, `## Interviewer follow-ups` with 4–5 pushback questions, one Excalidraw diagram, and heavy cross-linking into existing pages.

---

## Sources

- [Booking.com System Design Interview — systemdesignhandbook](https://www.systemdesignhandbook.com/guides/booking-com-system-design-interview/)
- [Atlassian System Design Interview — systemdesignhandbook](https://www.systemdesignhandbook.com/guides/atlassian-system-design-interview/)
- [Salesforce System Design Interview — systemdesignhandbook](https://www.systemdesignhandbook.com/guides/salesforce-system-design-interview/)
- [Razorpay Backend Engineer Interview Questions — Greenroom](https://usegreenroom.app/blog/razorpay-backend-engineer-interview-questions)
- [Razorpay Onsite Interview — SpaceComplexity](https://spacecomplexity.ai/blog/razorpay-onsite-interview)
- [Swiggy Interview Experience for SDE-3 — GeeksforGeeks](https://www.geeksforgeeks.org/interview-experiences/swiggy-interview-experience-for-sde-3/)
- [HLD Problems — SystemCraft](https://systemcraft.in/hld)
- [Uber Interview Guide 2026 — techinterview.org](https://www.techinterview.org/post/3233460273/uber-interview-guide-2026-dispatch-systems-geospatial-algorithms-and-marketplace-engineering/)
- [Distributed Transactions: 2PC, Saga, Outbox — techinterview.org](https://www.techinterview.org/post/3233465289/system-design-distributed-transactions/)
- [Flipkart Interview Experience for SDE-2 — GeeksforGeeks](https://www.geeksforgeeks.org/flipkart-interview-experience-for-sde-2-4/)
- [System Design Interview: Amazon/Flipkart/eBay E-commerce — Double Pointer](https://medium.com/double-pointer/system-design-interview-amazon-flipkart-ebay-or-similar-e-commerce-applications-35a0bc764421)
- [Zomato System Design Interview Questions — Exponent](https://www.tryexponent.com/questions?company=zomato&role=swe&type=system-design)
- [Multi-Region Architecture: Active-Active, Active-Passive — CalibreOS](https://www.calibreos.com/learn/hld-multi-region)
