# Security policy

Veyl's purpose is to keep people from reading what they are not cleared to read, so reports of any way around that are especially welcome.

## Reporting a vulnerability

Please report privately, not in a public issue or pull request:

1. Preferred: use GitHub's private vulnerability reporting (the repository's **Security** tab, then **Report a vulnerability**).
2. If that option is not available, open an issue titled "Security contact request" with no details, and a private channel will be arranged.

Include what you found, how to reproduce it (a request sequence, a document, or a failing test is ideal), and what an attacker gains.

## What counts

Anything that breaks a guarantee in the [threat model](docs/threat-model.md), for example:

- reading another tenant's data, or text above your clearance or outside your grants;
- unauthorized text reaching the language model, a citation, a cache entry, or conversation history;
- forging identity, bypassing CSRF, or defeating the sign-in rate limits;
- editing or deleting audit records through the application;
- a path where a failure falls back to an unchecked answer.

Behaviour listed under [known limitations](docs/threat-model.md#known-limitations) (for example, the language model provider seeing authorized excerpts, or a database superuser disabling the audit trigger) is documented rather than a vulnerability, though ideas for closing those gaps are welcome.

## Supported versions

Only the latest commit on `main` is supported. Fixes land there with a test that reproduces the issue.

## Demo credentials

The demo seed's accounts and the `.env.example` placeholders are for local use only. They are not secrets, and reporting them is not necessary.
