# Module 04 — Interviewer Q&A

**1. How do you actually stop a submission from doing something malicious to the host machine?**
Isolation at the microVM level (Firecracker-style), not a plain container — each execution gets its own kernel, not a shared one with restricted permissions. Resource limits (CPU, memory, wall-clock, process count, network) are enforced by the sandbox runtime itself, so the submission's own code has no way to opt out of them, unlike an application-level check the code could simply never call.

**2. What happens when a submission never terminates — an infinite loop?**
The sandbox's wall-clock limit kills the execution regardless of what the code is doing internally, and the judge records `time_limit_exceeded`. This is the central design payoff named in Module 00: an infinite loop is a normal, bounded, expected verdict — never an infrastructure incident.

**3. Why fresh sandboxes per test case instead of reusing one sandbox across all of a submission's test cases?**
Reuse risks state leaking between test cases — a file written during test case 3 could affect test case 4's result, which breaks the determinism requirement outright. A fresh sandbox per test case costs more (more provisioning overhead) but guarantees each test case's result depends only on that test case's input, nothing left behind by a previous one.

**4. How would you handle a burst of submissions at the start of a live contest?**
The Submission Queue absorbs it — the Submission Service keeps accepting and durably enqueuing at its own steady rate, and the worker pool scales up based on queue depth rather than the burst hitting worker capacity directly. If the queue itself grows past a safe depth, new submissions get a `503` with a retry hint rather than accepting unboundedly and letting verdict latency degrade without limit.

**5. Two workers somehow both think they've claimed the same submission — what actually happens?**
The claim is an atomic conditional update (`UPDATE ... WHERE status='queued'`), the same discipline this guide uses for every claim-a-unit-of-work race — only one of the two updates can succeed, because the row's current status is part of the update's own condition. The losing worker simply moves on to the next queued submission.

**6. A worker crashes in the middle of executing a submission — how does the system recover?**
The claim carries a time-bounded lease. A `submissions(status, claimed_at)` reconciliation query finds any submission whose lease has expired without a recorded verdict and returns it to `queued` for a different worker to re-judge from scratch. The crashed worker's partial state (whatever test cases it had gotten through) is discarded entirely — re-judging is idempotent, so re-running from the start is always safe.

**7. Would you ever cache the actual verdict for a (code, test case) pair to avoid re-running identical submissions?**
Not for the primary judging path — a resubmission of byte-identical code is rare enough that the caching complexity (correctly hashing code + language + compiler version + resource limits, and invalidating on any of those changing) outweighs the saved compute. It's a legitimate optimization to revisit if resubmission rates turn out to be higher than assumed, but not a default.

**8. How do you keep verdicts fair when submissions run on different physical hardware?**
This is a real, named limitation rather than a solved problem: absolute wall-clock timing can vary slightly across hardware generations in the worker fleet. Mitigations include pinning a given problem's judging to a consistent hardware class, and setting time limits with enough margin that hardware variance doesn't flip a verdict — but perfect determinism across heterogeneous hardware isn't fully achievable, and a mature system states that limitation explicitly rather than claiming otherwise.

**9. Why is compiling done once and reused across all of a submission's test cases, rather than recompiling per test case?**
The source code is identical across every test case in one submission — recompiling per test case would multiply compile time by ~30 for zero benefit, and would also multiply compile-failure handling logic needlessly. Compile once, reuse the compiled artifact across every sandboxed execution of that submission.

**10. How would you extend this design to support interactive problems, where the judge and the submission exchange multiple rounds of input and output?**
`SandboxRunner.execute()` as designed assumes one input produces one final output — an interactive problem needs a bidirectional channel between the sandboxed submission and a judge program for the duration of the test case, not a single request/response. This changes the sandbox invocation's shape (a persistent pipe rather than input-in/output-out) but doesn't change the surrounding architecture — the same queue, worker pool, and verdict-recording flow still apply once that one interface is extended.
