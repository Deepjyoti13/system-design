# Module 04 — Interviewer Q&A

**1. What happens when two requests hit the same resource at the same instant — specifically, two checkouts racing for the last unit of a product?**
The conditional inventory decrement — `UPDATE inventory SET count = count - 1 WHERE product_id = ? AND count > 0`, in the same transaction as the order insert — means only one checkout's update can actually apply. The loser sees an explicit "out of stock" at checkout, never a second order silently created against inventory that's already gone; the exact mechanism is worked out in [`ecommerce-schema-worked-example.md`](../../database-design/ecommerce-schema-worked-example.md).

**2. What happens when traffic spikes 10x for an hour — a flash sale or a homepage feature?**
It depends entirely on which path the spike hits, which is the whole argument for splitting the services in the first place. A catalog-read spike scales horizontally and leans on the cache layer, per Module 01's Load Handling — it never touches the order database at all. A checkout spike is a much smaller absolute number (Module 00's capacity math puts peak checkout traffic three orders of magnitude below peak catalog traffic) and is protected by its own bounded concurrency and the database's conditional-decrement discipline, independent of how hard catalog is being hit at the same moment.

**3. Why does the cart service get weaker consistency guarantees than the order service?**
Because the cost of being wrong is completely different. A cart that briefly shows a stale item count costs nothing real — the user re-adds it. An order that's double-processed or silently dropped costs money and trust. Consistency here is a dial set per component based on what an error there actually costs, not a single guarantee applied uniformly across the platform, exactly [ACID vs. BASE](../../database-design/acid-vs-base.md)'s framing.

**4. How would you handle a product going out of stock while it's sitting in many users' carts?**
Don't reserve inventory at add-to-cart time — a cart is not a hold, per Module 01's Trade-offs table. Stock is only checked and decremented at checkout, inside the same transaction that creates the order. A user whose cart item sold out in the meantime finds out at the one moment it actually matters, instead of the system pretending to reserve something it never actually held.

**5. Would you use the same database technology for catalog search and order processing?**
No, deliberately. Catalog wants a search-optimized index over mostly-static, denormalized documents; order processing wants multi-row ACID transactions over normalized, frequently-written rows. [SQL vs. NoSQL](../../database-design/sql-vs-nosql.md)'s point applies directly: the query pattern should drive the storage choice, and forcing one store to do both well is the default-preference mistake that page warns against.

**6. What happens if the payment step fails after inventory was already decremented at checkout?**
This isn't a rollback — the transaction that decremented inventory already committed by the time the payment call runs, since the payment call happens outside that transaction on purpose (a hung external call must never hold the order database's locks open). It's a compensating action instead: release the reserved units back to `inventory` and mark the order `payment_failed`, the same saga-style reasoning [Sagas & Distributed Transactions](../../hld-building-blocks/distributed-transactions-saga.md) and [`ecommerce-schema-worked-example.md`](../../database-design/ecommerce-schema-worked-example.md) both describe.

**7. How does a recommendation service outage affect checkout?**
It shouldn't, at all. `CheckoutService` has no hard dependency on the recommendation service — Module 01's architecture puts recommendations on a fully decoupled, async, cache-served path with its own storage and its own failure domain. A recommendation-service outage degrades to a generic fallback list on the confirmation page; it structurally cannot delay or block the checkout transaction, because there's no code path connecting the two.

**8. Would you ever reserve inventory when an item is added to the cart, for example specifically during a flash sale?**
It's a real technique some systems use — a short, TTL-bounded hold on a designated hot SKU — and it's a genuine trade-off, not a strictly worse option: a hold prevents overselling to a shopper who was simply faster to add-to-cart, at the cost of also locking out a shopper who might complete checkout sooner. This design's default is no reservation at add-to-cart time, because most of the catalog never experiences contention severe enough to justify the cost, and applying a hold platform-wide would tax every product's cart operations for a problem only a small fraction of SKUs actually have.

**9. How do you keep the search index and catalog store from disagreeing after a price update?**
Writes land in the catalog document store first; the search index is updated asynchronously afterward, per [Search & Inverted Indexes](../../scalability-resilience/search-inverted-indexes.md) — the index is a derived, read-optimized copy, not a second source of truth. A shopper can briefly see a stale price in search results for as long as that propagation takes, which is an accepted, explicit trade-off, not a bug.

**10. Would you shard the order database by the same key as the catalog store?**
No — `user_id` for the order database, not `product_id`. A user's own order history staying on one shard is the query that runs constantly (order history pages, tracking, support lookups), the same reasoning [`ecommerce-schema-worked-example.md`](../../database-design/ecommerce-schema-worked-example.md)'s own follow-up gives and [Data Partitioning & Sharding](../../hld-building-blocks/data-partitioning-sharding.md) generalizes: the shard key has to match the access pattern that actually runs constantly, not a desire for even distribution alone. The catalog store, meanwhile, has no "owning user" at all — it's scaled by replication for reads, an entirely different problem sharded on a different axis, if it needs sharding for size at all.
