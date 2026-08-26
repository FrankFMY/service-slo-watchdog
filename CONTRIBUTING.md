# Contributing

Contributions should preserve the package's bounded and credential-free trust model.

Create a virtual environment and install the package before running its `src/`-layout test suite:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --editable . ruff build twine
```

1. Open an issue describing the behavior and failure boundary.
2. Add a failing regression test first.
3. Keep the runtime dependency-free unless a documented security requirement makes a dependency unavoidable.
4. Run unit tests, compile checks, artifact builds and public-tree scans.
5. Do not add product-specific URLs, revisions, service names, credentials or response payloads to fixtures or documentation.

Pull requests require a clear behavior change, test evidence and an update to `CHANGELOG.md` when user-visible behavior changes.
