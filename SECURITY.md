# Security model

This repository is a local engineering demonstration, not a mission-certified system.

- Never commit `.env` or real credentials.
- Replace local static tokens with an identity provider and short-lived credentials in production.
- Place NATS, PostgreSQL, and metrics endpoints on private networks.
- Add TLS/mTLS to every service boundary before any non-local deployment.
- Use a secrets manager instead of environment files.
- Treat the AI advisor as untrusted advisory output. It cannot publish command subjects.
- Preserve append-only audit records outside the operational database for production use.
- Report vulnerabilities privately rather than opening a public issue containing exploit details.
