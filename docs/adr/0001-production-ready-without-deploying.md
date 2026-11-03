# Production-ready, deliberately not deployed

This project exists to prove the ability to build production-grade systems, and it has no budget for hosting, so it runs only on a local machine. Production mode is still a hardened stack that really runs and is tested, because configuration that has never run proves nothing. Its deployment target is any Linux server with Docker, documented in a runbook.

## Considered Options

- **Deploy to a cloud provider**: rejected because hosting has an ongoing cost and the project has no users to serve.
- **Terraform without applying it**: rejected because only applying it to a real account proves it works, and that costs money and ties the project to a provider. Add it when a provider is chosen.
- **Kubernetes**: rejected because it adds a lot of complexity without testing anything this project needs.

## Consequences

The project can claim to be production-ready, not production-proven: it never handles real traffic.
