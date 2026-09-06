# Builder

![Builder: an 8-parameter constructor call versus the same construction as named, chainable calls ending in .build()](diagrams/builder.svg)

## The Problem It Solves

A constructor with 6+ parameters, most of them optional, where callers constantly get the order wrong or pass `null` for the four they don't care about. `new PaymentClient("stripe", null, 500, 3, null, true, false, null)` is not reviewable — nobody reading that call site can tell which positional argument is the timeout, which is `useTLS`, or what the trailing `null` actually means, without opening the class definition and counting parameters by hand.

## How It Works

A separate builder class accumulates configuration through named, chainable methods — each one sets one field and returns the builder itself (`this`), so calls can be chained fluently. No real object exists until `.build()` is called, at which point the builder validates whatever's required and constructs the final object once, typically as an immutable value. The order calls appear in the chain is whatever order the *caller* finds natural — never a fixed positional order every caller has to memorize.

## Implementation

```
class PaymentClientBuilder:
    _processor: string
    _timeoutMs: int = 1000        # sensible defaults live here, not repeated at every call site
    _retries: int = 0

    withProcessor(name) -> PaymentClientBuilder:
        self._processor = name
        return self                # returns `this` -- no object exists yet, just accumulated state

    withTimeoutMs(ms) -> PaymentClientBuilder:
        self._timeoutMs = ms
        return self

    withRetries(n) -> PaymentClientBuilder:
        self._retries = n
        return self

    build() -> PaymentClient:
        if self._processor is None:
            raise InvalidBuilderStateException("processor is required")
        return PaymentClient(self._processor, self._timeoutMs, self._retries)   # immutable once built

# the call site:
client = PaymentClientBuilder()
    .withProcessor("stripe")
    .withTimeoutMs(500)
    .withRetries(3)
    .build()
```

## Real-World Use Case

Any client configuration object with several optional knobs — a `PaymentClient`, an HTTP client, a queue consumer — where named, chainable calls read top to bottom in the order the caller actually cares about. `.build()` validating required fields in one place (rather than the constructor silently accepting an incomplete configuration) is itself part of the payoff: an incompletely-configured client fails loudly, at construction time, instead of failing mysteriously the first time a required field is actually used.

## When to Use It

- A constructor's parameter list has grown past the point where a call site is reviewable at a glance — as a rule of thumb, 5 or more parameters, most of them optional.
- Callers regularly get the constructor call wrong: wrong order, `null` for fields they didn't mean to skip, or forgetting a required field entirely.
- The constructed object should be immutable once built, and validated as a whole (not field-by-field) before it exists.

## When NOT to Use It

A class with two or three parameters, all required, doesn't need a builder — a plain constructor call is already perfectly readable, and a builder there is indirection with no payoff: an extra class, an extra `.build()` call, and no actual readability gained over `PaymentClient("stripe", 500, 3)`.

## Related Patterns

Builder and Abstract Factory are both "constructional," but solve different problems — Builder assembles *one* object piece by piece over several calls; Abstract Factory constructs a *family* of already-complete, related objects in one call each. Builder is also sometimes layered under a Factory Method: the factory decides *which* builder to use for a given input, and the builder handles the actual multi-step assembly.
