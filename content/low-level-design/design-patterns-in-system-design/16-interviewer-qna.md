# Module 04 — Interviewer Q&A

**1. Strategy and Decorator both "wrap" behavior behind an interface — how do you tell an interviewer apart?**
Strategy swaps *which* algorithm runs — the implementations are alternatives to each other, and exactly one is active at a time (`StripeProcessorClient` or `AdyenProcessorClient`, never both for the same call). Decorator *adds* behavior around an existing implementation without replacing it — a `RateLimiterDecorator` wraps a real handler and still ultimately calls it; it doesn't offer a different way to handle the request.

**2. When do you need Abstract Factory instead of just a Factory Method per object?**
When the objects being constructed must agree with each other as a *family* — an AWS storage client paired with a GCP queue client isn't just unusual, it's a configuration the system was never built to support. If nothing breaks when two related objects come from different sources, you don't need the umbrella interface — a plain Factory Method for each one, independently, is simpler.

**3. Why is Singleton considered risky by experienced engineers, and what do you use instead?**
Because a static `getInstance()` call is an invisible dependency — it doesn't show up in a constructor signature, so a unit test can't substitute a fake for it without monkeypatching a static method, and any caller can silently reach into shared global state. The fix isn't "never share one instance" — it's dependency injection: construct the single shared instance once, at the composition root, and pass it into every constructor that needs it explicitly. The object is still effectively a singleton; the dependency is just visible and testable now.

**4. Isn't a Builder just extra ceremony around a constructor — when does it actually earn its place?**
When a constructor's parameter list has become the actual source of bugs — 6 or more parameters, most optional, callers regularly passing them in the wrong order or `null` for ones they don't care about. Below that threshold, a plain constructor call is already readable, and a builder is indirection with nothing to show for it. The tell is symptom-first: don't reach for Builder because a class "has a lot of fields," reach for it because callers keep getting the constructor call wrong.

**5. Adapter and Facade both sit in front of something else — what's actually different about them?**
Adapter makes an *existing, incompatible* interface fit the one your code already depends on — it exists because you don't control the thing being wrapped, and its shape doesn't match yours. Facade collapses *several real subsystems, called in a specific order* into one simple entry point — it exists because the complexity is real and multi-step, not because of a naming or shape mismatch. A factory can even construct the right adapter for a given configuration; Facade and Factory Method aren't usually confused, but Adapter and Facade are, because both "simplify what the caller sees."

**6. How do Chain of Responsibility and Decorator differ, given they both look like a linked sequence of wrapping objects?**
Any handler in a Chain of Responsibility can stop the request outright and never call the next link — an `AuthHandler` returning `401` never invokes the rate limiter after it. Every layer of a Decorator, by contrast, always calls through to what it wraps (or explicitly raises) — a circuit-breaker decorator still ultimately makes the call it wraps when the breaker is closed. Same visual shape, different contract: one is "any link can end this," the other is "every layer participates."

**7. Proxy and Decorator both implement the same interface as what they hold a reference to — so what's the actual difference?**
Decorator's whole purpose is *adding* behavior — the thing it wraps still always gets called, decorator or not. Proxy's whole purpose is *deciding whether to delegate at all* — a `CachingProxy` on a cache hit never touches the real object it holds a reference to. If the wrapped call always happens, you're describing Decorator; if the wrapped call sometimes doesn't happen at all, you're describing Proxy.

**8. Why formalize a lifecycle as a `State` enum with enforced transitions instead of a free-text status column or a couple of booleans?**
Because a free-text column lets any code path write any value, including the same terminal state written twice or a transition that skips a required step (`refunded` before `succeeded` ever landed). Enforcing the transition as a conditional write — `UPDATE ... SET status = 'succeeded' WHERE status = 'processing'` — turns an invalid or duplicate transition into a zero-row no-op at the database level, rather than a silently corrupted history discovered later.

**9. Why encapsulate "trigger this job" as a Command object instead of the Scheduler just calling the Worker's method directly?**
Because a direct method call executes immediately and leaves nothing behind — if it fails partway, there's no record to retry from, and the caller and callee have to both be alive at the same instant. A `TriggerJobCommand` object is durable: it can sit in a queue, get redelivered after a crash, and be executed by a Worker that has no idea the Scheduler that created it still exists. The object *is* the retry mechanism.

**10. Design patterns can feel like a checklist applied after the fact — how do you avoid that in an interview?**
Describe the concrete symptom you're seeing first, and let the interviewer hear you *notice* the shape — "these three algorithms all solve the same job and need to swap at runtime, so I'd put them behind one interface" — rather than announcing "I'll use Strategy here" up front and reshaping the problem to fit. An interface with exactly one implementation that will only ever have one implementation is indirection with no payoff, not good design, no matter which pattern's name you attach to it.
