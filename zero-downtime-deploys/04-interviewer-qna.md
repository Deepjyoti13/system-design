# Module 04 — Interviewer Follow-Up Bank

Ten questions a real interviewer would push on after seeing modules 00–03, each answered from a decision already made rather than a generic textbook answer.

**1. What happens when two requests hit the same resource at the same instant?**
The relevant "resource" here is an instance being replaced. A request arriving in the exact instant an instance is marked for removal isn't actually a race: the load balancer stops routing *new* requests to it the moment it leaves rotation, but anything already accepted is tracked as in-flight and finishes during the drain window (module 01). There's no request that can simultaneously be "already accepted" and "not yet accepted" — the removal-from-rotation step is what makes the two cases cleanly separable.

**2. What happens when traffic spikes 10x for an hour?**
Nothing about the rollout mechanism changes — the batch size is a fixed percentage of the fleet, so it scales with fleet size, not traffic. What does matter is the capacity-floor invariant from module 01: if the fleet is already autoscaled up to handle the spike, the rollout's 5% dip is 5% of a larger number, still comfortably within headroom, because that headroom is what autoscaling is already provisioning for the spike itself.

**3. Why not just kill old instances immediately and let clients retry?**
That's arguing against module 01's central premise: "the client retries" means a real user sees a failed request, even if it succeeds a moment later — that's the dropped request this design exists to avoid. Draining exists specifically so no client ever needs to retry because of the deploy.

**4. What's the risk of a fixed drain timeout, and why accept it anyway?**
A request that's genuinely stuck (not just slow) blocks that instance's termination until the timeout expires, and is then forcibly dropped anyway. This is a deliberately accepted, extremely rare edge case named explicitly in module 01 — the alternative, waiting indefinitely, risks one pathological request stalling the entire rollout behind it, which is a worse failure mode for everyone else.

**5. How would you detect a bad deploy before it reaches the whole fleet?**
This design pauses on an elevated error rate or failed health checks (module 01's load-handling section), which catches a badly broken new version. It does *not* do canary analysis — routing a slice of real traffic to the new version and statistically comparing it against the old version's behavor — named explicitly in module 01 as a different, complementary problem this module doesn't solve.

**6. Why is the rollout driven by a single controller instead of every instance deciding independently when to update itself?**
Because "which batch is running right now" has to have exactly one answer across the whole fleet — two independent decision-makers could both decide to replace overlapping instances at once, blowing past the capacity floor. Module 02 names this explicitly as the one deliberate exception to "everything is stateless and horizontally scaled" in this guide.

**7. What happens to a WebSocket client if the reconnect-first message itself is lost?**
The connection still eventually closes when the drain timeout expires, and the client's own reconnect logic (built for the ordinary case of an unplanned disconnect) takes over — slower and noisier than the clean path, but not a dropped request in the sense this module cares about, since a WebSocket disconnect-and-reconnect isn't the same as a lost in-flight HTTP request.

**8. How would you load-test this before relying on it in production?**
Exactly module 01's target: run a full rollout against a fleet under 2x normal peak load, inject an artificial error-rate spike partway through, and confirm the controller pauses within one batch cycle — then separately confirm a killed controller mid-rollout leaves the fleet in a safe, correctly-tracked state (module 03) rather than a stuck or inconsistent one.

**9. Does this design assume all instances are identical? What if some hold different data/state?**
Yes — this module assumes stateless instances, matching this guide's [Client-Server Model](../content/foundations/client-server-model.md) framing of why statelessness enables this kind of interchangeable scaling in the first place. A genuinely stateful instance (holding, say, an in-memory cache that's expensive to rebuild) needs a different, slower drain strategy — warming the new instance's cache before removing the old one, not just health-checking it.

**10. Why bound the rollout to about an hour instead of just letting it run as long as it takes?**
Because running two versions side by side for an extended period is its own source of bugs — a client that talks to a v1 instance on one request and a v2 instance on the next can hit subtle incompatibilities neither version alone has. Module 01 states this explicitly: bounded rollout time isn't about impatience, it's about minimizing how long the fleet spends in a mixed, harder-to-reason-about state.
