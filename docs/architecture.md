# Architecture

## Architectural intent

Agentic Workshop models a company, not a conversation. The stable concepts are employees,
departments, goals, work, memory, tools, policies, and institutional events. Model inference is
one replaceable capability used by employees; it is deliberately not the center of the design.

The architecture follows ports and adapters with domain-oriented modules. Dependencies point
inward:

```text
bootstrap/config -> adapters -> ports <- application -> domain
                                      ^                 ^
                                      +-----------------+
```

The domain imports only Pydantic and the standard library. Application code coordinates domain
records through ports. Adapters will implement ports for databases, queues, model providers,
vector stores, filesystems, and external APIs. The bootstrap module is the only place allowed to
choose concrete adapters.

## Package decisions

| Area | Responsibility | Why it exists |
|---|---|---|
| `domain` | Immutable organizational and work records | Keeps business language independent of infrastructure and model vendors. |
| `ports` | Async capability contracts | Allows adapters and deterministic fakes to be substituted without changing use cases. |
| `application` | Future use-case orchestration | Prevents workflows from accumulating in entities, prompts, CLI handlers, or web endpoints. |
| `adapters` | Future integrations | Contains volatile SDK-specific code and protects the core from vendor types. |
| `config` | Validated environment configuration | Fails early on invalid deployment input and keeps secrets out of ordinary strings. |
| `resources` | Prompts and policies | Makes text reviewable, versionable, testable, and replaceable without hiding logic in prose. |
| `bootstrap.py` | Composition root | Makes the dependency graph explicit and avoids a global service locator. |

Modules are organized by cohesive concept rather than a single large `models.py` or `interfaces.py`.
This costs a few more imports but reduces merge conflicts and makes ownership and dependency cycles
visible.

## Domain design

All domain records inherit `DomainModel`, which forbids unknown fields and mutation. Immutable
snapshots are safer across async boundaries, event publication, caching, and retries. Pydantic v2
provides validation and stable serialization at boundaries. `NewType` identifiers prevent common
mix-ups during static analysis without introducing runtime wrapper overhead.

`Employee`, `Department`, and `Company` contain definitions and references, not execution methods.
Composition is explicit: companies reference departments; departments reference employees; an
employee composes personality, goals, routines, procedures, deliverables, tools, prompt resources,
and a memory namespace. Large object graphs are avoided because independently loaded references
work better with persistence, caching, and partial organizational changes.

Responsibilities and procedures remain distinct. A responsibility explains ownership; an SOP is a
versioned resource describing an approved process. A routine points to an SOP rather than embedding
steps. Deliverables define contracts independently of tasks, allowing quality gates later.

## LLM independence

`LanguageModel` accepts provider-neutral messages, tool definitions, schemas, and parameter maps.
Provider-only response data is quarantined in `provider_metadata`. No employee stores a provider,
model name, context-window size, or SDK object. Provider selection belongs to configuration and
adapter wiring.

The generic parameter map is an intentional escape hatch. A strongly typed universal set would
encode today's providers and age poorly; completely raw provider requests would leak vendor types.
Adapters validate supported keys and can expose typed configuration of their own.

## Tasks and collaboration

`WorkTask` is the durable unit of delegation. It includes requester, assignee, dependencies,
priority, parentage, input, lifecycle state, and version. Collaboration occurs by persisting and
dispatching tasks, not by employees calling each other directly. This enables auditing, retries,
human oversight, queues, and distributed execution.

`TaskRepository.save` accepts an expected version to support optimistic concurrency. This is favored
over locking because employee work may be distributed and long-running. State-transition policy is
intentionally reserved for an application service so repositories do not acquire business logic.
Task output is separated into `TaskResult`; later milestones will persist artifacts and results
without bloating the scheduling record.

Alternative: an actor model maps naturally to employees and mailboxes, but makes durable workflow
inspection, cross-actor transactions, and deterministic testing harder. Actors may later be an
execution adapter; they are not the domain model.

## Events

`DomainEvent` is a versioned envelope with correlation and causation IDs. Events are facts in past
tense at the application boundary; commands and requests should not use this type. The publisher
does not promise exactly-once delivery because distributed exactly-once claims are usually false.
Consumers must be idempotent, and a transactional outbox can later provide atomic persistence plus
at-least-once publication.

One generic envelope was chosen over a class per event for the baseline because the actual event
catalog will emerge from use cases. Once stable events exist, discriminated payload models should
be added while retaining this wire envelope for routing and versioning.

## Memory

Memory distinguishes episodic, semantic, procedural, and working records, but storage remains behind
`MemoryStore`. A store may use SQL, documents, embeddings, keyword search, or a hybrid. Relevance is
represented by normalized `MemoryMatch.score`; callers must not depend on database-specific distance
metrics. Metadata filters allow evolution but should be promoted to typed fields when they become
business invariants.

Institutional knowledge must ultimately be company- or department-owned as well as employee-owned.
The first contract uses an employee owner to make lifecycle semantics unambiguous; shared knowledge
and retention policy are explicit roadmap items, not an accidental metadata convention.

Alternative: event sourcing can reconstruct history and offers excellent auditability, but it adds
projection, migration, and event-versioning costs everywhere. The design uses ordinary state plus
domain events initially. Event sourcing can be adopted for selected aggregates if audit needs prove
it worthwhile.

## Tools and safety

Tool declarations use JSON Schema because model providers and external APIs already interoperate
with it. Discovery (`ToolRegistry`) and execution (`ToolExecutor`) are separate so an employee may
see a constrained catalog without receiving execution authority. `requires_confirmation` is policy
metadata, not enforcement; a later authorization service must decide based on actor, company policy,
arguments, and environment before the executor runs.

## Configuration and dependency injection

`Settings` supports `AW_`-prefixed nested environment variables such as
`AW_MODEL__PROVIDER`. It is immutable and forbids unexpected keys. Secrets use `SecretStr`, while
prompt resources and organizational definitions use logical references rather than filesystem paths
inside domain records.

`RuntimeDependencies` is a typed object passed to application services. It is deliberately not a
mutable container or lookup-by-string service locator. Constructor injection should be preferred for
individual services; the aggregate exists to make the full composition graph inspectable at startup.
The minimal `bootstrap` accepts a factory so tests can assemble in-memory ports and deployments can
assemble production adapters without import-time side effects.

Alternative DI frameworks reduce wiring code but obscure construction, introduce lifecycle magic,
and often weaken typing. Explicit factories are more verbose and much easier to debug. A framework
can be added only if graph size demonstrates a real need.

## Logging and observability

Core models never log: doing so would create invisible side effects and duplicate messages. Each
application use case will log once at entry and completion with task, employee, correlation, and
company identifiers. Adapters will log I/O timing and sanitized failures. JSON logs are the production
default. OpenTelemetry traces and metrics will be wired at the composition root. Prompt content,
memory content, tool arguments, model responses, and secrets are sensitive and must be redacted by
default.

## Error and transaction policy

Ports return absence only where it is expected (`get`); operational failures raise typed exceptions
to be introduced with the first use cases. Application services own transaction boundaries.
Infrastructure exceptions must be translated by adapters. Retries belong around idempotent port
calls and must use bounded backoff; domain rules never retry themselves.

## Testing strategy

Domain tests validate invariants without I/O. Contract suites will be shared by every adapter
implementation. Application tests use in-memory fakes through ports and control clocks/IDs once those
ports are introduced. Integration tests verify real persistence and provider adapters separately.
Architecture tests should prevent domain/application imports from adapters and prevent prompt files
from becoming an alternative source of business rules.

## Deferred on purpose

There is no employee runner, planner, task transition service, prompt renderer, event bus, database,
API, CLI, or provider adapter yet. Adding pretend implementations would lock in behavior before use
cases and invariants are tested. The existing code establishes only contracts with immediate design
value.

