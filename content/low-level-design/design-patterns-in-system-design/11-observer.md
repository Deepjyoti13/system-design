# Observer

![Observer/pub-sub at the code level: adding a third handler costs zero changes to the event source](diagrams/observer.svg)

## The Problem It Solves

A component needs to react to an event without the event *source* knowing or caring who's listening, and the number of things that react needs to grow over time without the source changing every time a new reaction is added.

## How It Works

The event source holds a list of handlers, all implementing one shared interface, and simply iterates the list and invokes each one whenever the event happens. The source knows nothing about what any given handler does — only that it implements the interface. Registering a new handler (or removing one) never requires editing the source.

## Implementation

```
interface EventHandler:
    handle(event) -> void

class UserRegistrationService:
    handlers: List[EventHandler]     # never a fixed, named list of "the two things that happen next"

    register(email, password):
        user = self.createUser(email, password)
        event = UserRegistered(user.id, user.email)
        for handler in self.handlers:
            handler.handle(event)    # fires the event; doesn't know or care what each handler does
        return user

class EmailWelcomeHandler implements EventHandler:
    handle(event):
        emailService.sendWelcome(event.email)

class FraudCheckHandler implements EventHandler:
    handle(event):
        fraudService.scoreNewAccount(event.id)
```

## Real-World Use Case

`UserRegistered` firing both `EmailWelcomeHandler` and `FraudCheckHandler`, neither of which `UserRegistrationService` calls by name — adding a third handler (say, an analytics tracker) is zero changes to the registration code, because it was never coupled to a fixed list of things that happen next.

## When to Use It

- The number of reactions to an event is expected to grow, and each new reaction shouldn't require editing the event source.
- The order handlers run in, and any return value they produce, genuinely doesn't matter to the caller — each reacts independently.
- You want to test the event source in isolation, without needing every real handler wired up.

## When NOT to Use It

When there are only ever going to be one or two fixed reactions to an event, and the order or return value of those calls actually matters to the caller — direct calls are simpler to trace and reason about than an indirection layer, and Observer's whole benefit (adding reactions without touching the source) doesn't pay for itself if new reactions basically never get added.

## Related Patterns

At infrastructure scale, this same idea is a message queue or pub/sub system — see [Message Queues & Pub/Sub](../../hld-building-blocks/message-queues-pubsub.md) for the network-level version of exactly this shape. In-process Observer and infrastructure-level pub/sub solve the same problem at different scales, with the same underlying reasoning: the source shouldn't need to know who's listening.
