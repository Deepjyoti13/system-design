# Abstract Factory

![Abstract Factory: CloudProviderFactory producing an entire AWS or GCP client family, and why mixing them isn't representable](diagrams/abstract-factory.svg)

## The Problem It Solves

A system needs a *family* of related objects that must all agree with each other — mixing a storage client from one cloud provider with a queue client from a different provider is either a bug or an unsupported configuration, and nothing in a plain one-factory-per-object design stops a caller from doing exactly that by accident, one field at a time.

## How It Works

One interface declares a creation method for every member of the family (a storage client, a queue client, a secrets client). Each concrete factory implements all of those methods against a single, consistent provider — an `AWSFactory` builds only AWS clients, a `GCPFactory` builds only GCP clients. The composition root picks exactly one concrete factory for the whole deployment, and every object the rest of the system uses comes from that single factory — so the family can never be mixed, because there's no code path that lets a caller ask one factory for one member and a different factory for another.

## Implementation

```
interface CloudProviderFactory:
    createStorage() -> StorageClient
    createQueue() -> QueueClient
    createSecrets() -> SecretsClient

class AWSFactory implements CloudProviderFactory:
    createStorage(): return S3StorageClient(awsConfig)
    createQueue():   return SQSQueueClient(awsConfig)
    createSecrets(): return SecretsManagerClient(awsConfig)

class GCPFactory implements CloudProviderFactory:
    createStorage(): return GCSStorageClient(gcpConfig)
    createQueue():   return PubSubQueueClient(gcpConfig)
    createSecrets(): return SecretManagerClient(gcpConfig)

# the composition root picks ONE factory for the whole app -- never one method from each:
factory: CloudProviderFactory = AWSFactory() if config.provider == "aws" else GCPFactory()
storage = factory.createStorage()
queue = factory.createQueue()
secrets = factory.createSecrets()
```

## Real-World Use Case

A service deployed across multiple clouds, where every dependency it touches (blob storage, a queue, a secrets manager) has to come from the *same* provider for a given deployment. `S3StorageClient` paired with `PubSubQueueClient` isn't just unusual here — it's not a configuration this system is built to support, and Abstract Factory makes that combination structurally unrepresentable rather than something caught by a runtime validation check that could be forgotten.

## When to Use It

- More than one related object needs to be constructed together, and they must all come from the same underlying provider or configuration.
- Mixing members of the family (one from provider A, another from provider B) would be a real bug, not just an unusual-but-valid combination.
- The set of families is known and closed (AWS, GCP, on-prem) rather than open-ended.

## When NOT to Use It

If the objects being constructed don't actually need to agree with each other — nothing breaks if one dependency is AWS and another is GCP — this is over-engineering. Reach for a plain [Factory Method](03-factory-method.md) per object instead, one per family member, with no umbrella interface tying them artificially together.

## Related Patterns

Abstract Factory is Factory Method generalized to a whole family instead of one object — read [Factory Method](03-factory-method.md) first if the distinction isn't clear. Each concrete factory (`AWSFactory`, `GCPFactory`) is also frequently implemented as a [Singleton](01-singleton.md), since a deployment only ever needs the one factory matching its own provider.
