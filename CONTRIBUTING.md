# Contributing

Thanks for taking an interest. This page covers the development setup and the
conventions that are easy to breach by accident.

## Setup

```bash
uv sync
```

## Checks

Everything CI runs, in the order worth running locally:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy custom_components
uv run pytest
```

`pytest` reports coverage. `core/` is expected to stay at full coverage: it is pure
logic with no I/O, so anything uncovered there is untested by choice rather than by
circumstance.

## The layering rule

Dependencies point one way only:

```
Home Assistant layer  →  core/  →  api/
```

- `core/` must import neither `homeassistant` nor `aiohttp`.
- `api/` must import neither `homeassistant` nor `core/`.

`tests/test_layering.py` parses the imports of every module and fails if a layer
reaches upward, so a breach fails CI rather than surviving review. It resolves
relative imports, so `from ..core import x` inside `api/` is caught too.

[docs/architecture.md](docs/architecture.md) explains why the boundary sits where it
does, and what each extension point is for.

## Changing how we talk to Skedda

Skedda's interface is undocumented and was reverse-engineered from live traffic. Two
rules follow from that:

1. **Nothing outside `api/` may hold a URL, a header name or a wire field name.**
   If you find yourself typing `"bookingslists"` in `core/` or in a platform, it
   belongs in `api/endpoints.py`.
2. **When the interface changes, the code and the fixtures change together.**
   `api/endpoints.py` and the redacted fixtures under `tests/fixtures/skedda/` are
   one unit. A fixture that no longer reflects reality is worse than no fixture: it
   makes a green suite mean nothing.

Fixtures are captured from real traffic and then redacted. Never commit a capture
containing a real session cookie, a real email address or a real password.

## Tests

- `core/` is tested with plain pytest and frozen time.
- `api/` is tested against a local aiohttp server (`FakeSkedda` in
  `tests/conftest.py`) rather than a mocking library, so status codes, 204 bodies
  and `Date` headers behave as they will in production.
- The Home Assistant layer is tested with `pytest-homeassistant-custom-component`.

Write the test first, and make it fail for the right reason before making it pass.
A test that asserts something always true is worse than no test at all.

## Commits and pull requests

- One logical change per commit, with a message that says why rather than what.
- If behaviour changes, update the page under `docs/` that describes it in the same
  commit. Two copies of the truth drift; one does not.

## Before the first public release

One check is deliberately skipped in CI and needs closing before this integration
can be listed in the default HACS catalogue:

- **Brand assets.** `skedda_scheduler` is not in
  [home-assistant/brands](https://github.com/home-assistant/brands), which wants an
  icon and a logo submitted as a pull request there. Until that lands,
  `.github/workflows/validate.yml` passes `ignore: brands` to the HACS action.
  Installing as a custom repository works without it; being listed does not.
  Remove the `ignore` line once the brands pull request is merged.
