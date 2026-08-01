# Dependency graph

```mermaid
flowchart TD
    CEO["Human CEO / delivery adapter"] --> UC["Application use cases"]
    UC --> ORG["OrganizationRepository"]
    UC --> TASKS["TaskRepository + TaskDispatcher"]
    UC --> MEM["MemoryStore"]
    UC --> TOOLS["ToolRegistry + ToolExecutor"]
    UC --> EVENTS["EventPublisher"]
    UC --> MODEL["LanguageModel"]
    UC --> RES["ResourceLoader"]
    UC --> DOMAIN["Immutable domain models"]

    SQL["SQL adapter"] -.implements.-> ORG
    SQL -.implements.-> TASKS
    QUEUE["Queue adapter"] -.implements.-> TASKS
    VECTOR["Memory adapter"] -.implements.-> MEM
    API["External tool adapters"] -.implements.-> TOOLS
    BUS["Event bus / outbox"] -.implements.-> EVENTS
    PROVIDERS["GPT / Claude / Gemini / local"] -.implements.-> MODEL
    FILES["Package / Git / object resources"] -.implements.-> RES

    SETTINGS["Validated Settings"] --> ROOT["Composition root"]
    ROOT --> SQL
    ROOT --> QUEUE
    ROOT --> VECTOR
    ROOT --> API
    ROOT --> BUS
    ROOT --> PROVIDERS
    ROOT --> FILES
```

Solid arrows are runtime use; dotted arrows are interface implementation. Adapter classes may depend
on domain types and port interfaces. Domain and application modules must never import adapters.

