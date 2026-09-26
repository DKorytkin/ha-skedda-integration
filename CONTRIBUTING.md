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

## Trying a change on a real Home Assistant

The test suite cannot show you the sidebar panel, a venue's real replies, or
several accounts starting together. Before releasing, run the change on a Home
Assistant instance you control.

### Copy the files across

Home Assistant loads the integration from
`/config/custom_components/skedda_scheduler`. Replace that directory with your
working copy and restart:

```bash
rsync -a --delete --exclude __pycache__ \
  custom_components/skedda_scheduler/ \
  root@homeassistant.local:/config/custom_components/skedda_scheduler/
```

On Home Assistant OS this needs the **Advanced SSH & Web Terminal** add-on (or
the plain **Terminal & SSH** one) with your public key added. The **Samba share**
add-on works too: copy the directory over `\\homeassistant.local\config`.

Then restart from **Settings → System → Restart**; reloading the entry is not
enough, because Python keeps the modules it already imported.

HACS will offer to "update" over your copy on the next release. That is expected:
once the change is released, accept it and you are back on a tracked version.

### Or publish a pre-release

To try a build through HACS itself, the same path users take:

1. Set `version` in `manifest.json` to a pre-release, e.g. `0.2.1b1`.
2. Merge it to `main`, then push the tag `v0.2.1b1`. The release workflow publishes
   it as a pre-release (see [Releasing](#releasing)).
3. Let HACS offer pre-releases for this repository: under **Settings → Devices &
   Services → HACS**, open the **Skedda Scheduler** device and enable its
   **Pre-release** switch (it is disabled by default).
4. In HACS, open **Skedda Scheduler**, then the three-dot menu → **Redownload**,
   choose `v0.2.1b1`, and restart.

Nobody else is offered a pre-release unless they enable that switch too.

### What to look at

- **Settings → System → Logs**, filtered by `skedda_scheduler`. For more detail,
  add to `configuration.yaml`:

  ```yaml
  logger:
    logs:
      custom_components.skedda_scheduler: debug
  ```

- The **Skedda** panel in the sidebar. It is cached by a fingerprint of the
  script, so a changed panel shows up after a restart without clearing the
  browser cache.
- Every account under **Settings → Devices & Services** is loaded, not "Failed
  to set up".

## Releasing

HACS offers users the latest GitHub release; a tag on its own is not enough.
Pushing a tag is the whole release, and
[`.github/workflows/release.yml`](.github/workflows/release.yml) does the rest:

1. Bump `version` in `custom_components/skedda_scheduler/manifest.json`, following
   [semantic versioning](https://semver.org/): a fix is a patch, a new capability
   a minor. Merge that to `main` like any other change.
2. Tag the merge commit and push the tag:

   ```bash
   git checkout main && git pull
   git tag v0.2.1
   git push origin v0.2.1
   ```

The workflow then checks that the tag matches `manifest.json` and is on `main`,
runs the tests, hassfest and the HACS check, and only then publishes the GitHub
release with notes generated from the merged pull requests. If any check fails,
nothing is published: delete the tag (`git push --delete origin v0.2.1`), fix,
and tag again.

A tag ending in `a1`, `b1` or `rc1` (e.g. `v0.3.0b1`, with the same version in
`manifest.json`) is published as a pre-release. HACS offers it only to users who
enable pre-releases for this repository, which is how to try a build through
HACS before everyone gets it.

## Listing in the HACS default catalogue

Until the integration is listed, users add it to HACS as a custom repository
(see [docs/installation.md](docs/installation.md)). Listing it lets them find it
by searching HACS, without pasting a URL.

HACS checks these before accepting a repository
([its requirements](https://www.hacs.xyz/docs/publish/include/)):

- The repository is public, has a description and topics, and has issues enabled.
- `manifest.json` and `hacs.json` are valid.
- Brand images exist. They ship in
  [`custom_components/skedda_scheduler/brand/`](custom_components/skedda_scheduler/brand);
  since Home Assistant 2026.3 no pull request to home-assistant/brands is needed.
  [`assets/README.md`](assets/README.md) describes the files.
- The **Validate** workflow (the HACS action and hassfest) passes on `main`.
- There is a full GitHub release, not just a tag, published after those checks
  passed.

Then submit it:

1. Fork [hacs/default](https://github.com/hacs/default) and create a branch there
   (not `master`).
2. Add `DKorytkin/ha-skedda-integration` to the `integration` file, in
   alphabetical order rather than at the end.
3. Open a pull request and fill in its template. It must come from the owner of
   this repository.

HACS's own checks run on the pull request. Once it is merged, the integration
appears after HACS's next scheduled scan.
