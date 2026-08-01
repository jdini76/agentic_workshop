# Agentic Workshop

Agentic Workshop is an operating system for companies made of collaborating AI employees.
This repository currently contains the architecture baseline: domain contracts, ports,
configuration, dependency composition, and design documentation. It intentionally does not
yet execute agents or call model providers.

See [the architecture guide](docs/architecture.md) and [implementation roadmap](docs/roadmap.md).

## Development

```shell
python -m pip install -e ".[dev]"
pytest
ruff check .
mypy src
```

