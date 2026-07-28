# Career Assistant

[![CI](https://github.com/farstrider/career-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/farstrider/career-assistant/actions/workflows/ci.yml)

Career Assistant is an evidence-first career intelligence platform. It is designed to collect job information, relate opportunities to your verified career history, explain recommendations with traceable evidence, and turn feedback into improvements that increase accuracy over time.

## Design principles

- Simple, intuitive user experience.
- Clear, plain-language explanations for recommendations.
- Evidence-based suggestions, not guesses.
- Separate, private career profiles.
- Shared job listings while keeping personal career data isolated.
- Multilingual support for job information.
- Exposed changes over time.  No hidden histories.

## Security

* Profiles are atomic and isolated.  Even administrators cannot access individual profiles.
* Source collectors must be authorized, rate-limited, auditable, and are disabled by default.
* LLMs are stateless, untrusted transformation and reasoning dependencies. Deterministic code retrieves context, computes final scores, validates outputs, and persists data, ensuring your information remains under your control.
* Sensitive data is encrypted at rest, and the most sensitive fields get extra protection.
* Sessions use opaque tokens in secure cookies, so the browser never handles raw secrets.

## Documentation

- [Local development](docs/local-development.md)
- [System administration](docs/system-administration.md)
