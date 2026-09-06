# Chain of Responsibility

![Chain of Responsibility: a request passing through independent handlers, any one of which can stop it outright](diagrams/chain-of-responsibility.svg)

## The Problem It Solves

A request should pass through a sequence of independent checks or handlers — auth, then rate limiting, then validation — where any one of them might need to reject the request outright, and adding a new check shouldn't mean editing a growing `if/else` block that already knows about every other check.

## How It Works

Each handler implements the same interface and holds a reference to the next handler in the chain. On receiving a request, a handler either rejects it outright (returning its own response and never calling the next handler) or passes it along by calling `next.handle(request)`. The chain is assembled once, at startup, and any handler can be added, removed, or reordered without the others knowing.

## Implementation

```
interface Handler:
    handle(request) -> Response

class AuthHandler implements Handler:
    next: Handler
    handle(request):
        if not isAuthenticated(request):
            return Response(401)          # stops the chain outright -- next is never called
        return self.next.handle(request)

class RateLimitHandler implements Handler:
    next: Handler
    handle(request):
        if not limiter.tryConsume(request.clientId):
            return Response(429)
        return self.next.handle(request)

# composed once, at startup:
chain = AuthHandler(next = RateLimitHandler(next = ValidationHandler(next = RealHandler())))
```

## Real-World Use Case

A middleware pipeline in front of any request handler — connect this to [Rate Limiting](../../hld-building-blocks/rate-limiting.md) and the `RateLimiter` decorator named on the [Decorator](06-decorator.md) page. Any HTTP framework's middleware stack (auth middleware, then logging middleware, then the route handler) is this exact pattern by another name.

## When to Use It

- Several independent checks need to run in sequence, and any one of them might need to end the request early.
- New checks should be addable, removable, or reorderable without touching the others' code.
- The checks themselves are genuinely independent — none needs to know the internals of another.

## When NOT to Use It

If the set of checks is small, fixed, and never reordered or extended, a plain sequence of `if` statements in one function is more readable than a chain of handler objects — the pattern earns its place specifically when handlers need to be added, removed, or reordered independently.

## Related Patterns

Worth naming the distinction from [Decorator](06-decorator.md) explicitly, since both look like a linked sequence of wrapping objects: any handler in a Chain of Responsibility can stop the request outright and never call the next one; every layer of a Decorator always calls through to what it wraps (or explicitly raises). Same-looking structure, different contract — one is "any link can end this," the other is "every layer participates."
