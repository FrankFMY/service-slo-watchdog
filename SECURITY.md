# Security Policy

## Supported versions

Security fixes target the latest `0.1.x` source until a stable release policy is established.

## Trust boundary

- Supply a credential-free HTTP(S) base URL. URLs containing user information, query strings or fragments are rejected.
- The tool does not support bearer tokens, cookies, custom authorization headers or client certificates.
- Alert text contains only validated service/revision identifiers and an explicit bounded error vocabulary; unknown alphanumeric values are replaced with `invalid_error`.
- HTTP response bodies and transport exception details are never copied into alerts or persisted state.
- State contains only phase, consecutive failure count and revision. Newly-created state directories are mode `0700`; state files are atomically replaced at mode `0600`.
- Failures before atomic replacement remain fatal. Once replacement succeeds, directory sync is best-effort: a post-commit sync error cannot suppress the alert/recovery transition already represented by the new state.
- Response sizes and each total request deadline are bounded.
- Deeply nested JSON, non-object/corrupt state and contradictory duplicate metric series fail closed instead of crashing or suppressing alerts.

Do not point the watchdog at endpoints that return secrets, customer data or operational internals.

## Reporting

Report vulnerabilities privately to Artem Prianishnikov:

- Email: Pryanishnikovartem@gmail.com
- GitHub: https://github.com/FrankFMY
