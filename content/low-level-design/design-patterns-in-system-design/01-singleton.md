# Singleton

![Singleton: ConfigRegistry reached directly by three unrelated services, plus the hidden cost of that convenience](diagrams/singleton.svg)

## The Problem It Solves

Several unrelated parts of a codebase all need the *same* shared object — a config registry, a connection pool, a single in-memory cache — and passing it explicitly through every constructor down the call chain feels like unnecessary ceremony for something that's "obviously" global to the whole process. Singleton makes exactly one instance reachable from anywhere via a static accessor, so no caller needs to be handed a reference to construct or find it.

## How It Works

The class hides its own constructor (or guards it) and exposes a static `getInstance()` method that lazily creates the one instance on first call and returns that same instance on every subsequent call. Every caller, anywhere in the process, that calls `getInstance()` gets the identical object — not a copy, not a new instance, the literal same one in memory. There is deliberately no public constructor a caller could use to make a second one.

## Implementation

```
class ConfigRegistry:
    _instance: ConfigRegistry = None      # private, static

    static getInstance() -> ConfigRegistry:
        if _instance is None:
            _instance = ConfigRegistry(loadFromDisk())
        return _instance

    get(key) -> Value:
        return self.values[key]

# anywhere in the codebase, no constructor call in sight:
class PaymentService:
    charge(...):
        timeout = ConfigRegistry.getInstance().get("payment.timeout_ms")

class AuthService:
    validate(...):
        secret = ConfigRegistry.getInstance().get("auth.jwt_secret")
```

Notice both `PaymentService` and `AuthService` reach `ConfigRegistry` the exact same way, without either one being told where it came from or who else is using it.

## Real-World Use Case

A config registry or a connection pool that genuinely must be one shared instance — opening a second database connection pool per request would exhaust connections under load, and two independently-loaded copies of configuration could silently disagree with each other at runtime (one process seeing a stale value the other already reloaded). Both are legitimate, common backend cases where "there is exactly one of these for the whole process" is a true statement about the system, not just a convenience.

## When to Use It

- There is a genuine, provable requirement that only one instance can exist — not just a preference for convenience.
- The shared object is expensive to construct (a connection pool, a loaded config file) and constructing it twice would be wasteful or actively harmful (exhausting a resource limit).
- No reasonable dependency-injection wiring point exists — for example, a static utility library with no composition root of its own.

## When NOT to Use It — the hidden cost

**Read this before reaching for Singleton by default.** None of `PaymentService`, `AuthService`, or `NotificationService` in the diagram above received `ConfigRegistry` through their constructor — they each reached out and grabbed it. That dependency is invisible in every one of their constructor signatures, and invisible to a unit test trying to substitute a fake: a test of `PaymentService` now has to know that it secretly depends on `ConfigRegistry.getInstance()`, and either accept the real global state in every test run or resort to monkeypatching a static method.

The strictly better version of this same idea, in almost every case, is **dependency injection**: construct one shared instance explicitly at the composition root (wherever the app wires its objects together at startup), and pass it into each constructor that needs it. The object is still a singleton *in practice* — there's still only one instance in the running process — but the dependency is now visible in the constructor signature and trivially swappable in a test. Reach for the static-accessor version of Singleton specifically when there is truly no composition root available to do that wiring, not as a default way to share an object.

## Related Patterns

Abstract Factory implementations are often themselves singletons (one `AWSFactory` instance, constructed once) — the two patterns compose naturally rather than compete. Singleton is also frequently confused with simply "a global variable" — the difference is lazy, controlled construction and a single well-defined access point, not just shared mutable state.
