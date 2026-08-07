# Releasing

This repo is a monorepo of independently versioned projects under `projects/`.
Today there is one: `modelrunner_ai`, published to PyPI as
[`modelrunner-ai`](https://pypi.org/project/modelrunner-ai/).

Versions are **derived from git tags** by `setuptools_scm` — there is no version
string in `pyproject.toml` to edit. The tag is the single source of truth.

Tag format: `<project_name>_v<major>.<minor>.<patch>` — e.g. `modelrunner_ai_v0.3.0`.

## The normal path

Two GitHub Actions workflows, run in order:

1. **`Create Github release`** (manual, `workflow_dispatch`) — pick the project and
   a `patch` / `minor` / `major` bump. It finds the highest existing version tag,
   computes the next one, builds release notes from the commits touching that
   project, and creates the GitHub release (which creates the tag).
2. **`PyPI release`** — fires automatically when a release is *published*. It
   builds the sdist and wheel, verifies the built version matches the tag, and
   uploads to PyPI via trusted publishing.

So a routine release is: merge to `main` → run `Create Github release` → watch
`PyPI release` go green → verify on PyPI.

```bash
gh workflow run "Create Github release" -f project_name=modelrunner_ai -f bump_version=minor
gh run watch "$(gh run list --workflow='PyPI release' --limit 1 --json databaseId -q '.[0].databaseId')" --exit-status
```

### Always verify PyPI itself

A green workflow is not proof the version landed. Check the index directly — the
JSON API is CDN-cached and lags by minutes, so prefer `/simple/`:

```bash
curl -s https://pypi.org/simple/modelrunner-ai/ | grep -oE 'modelrunner_ai-[0-9.]+\.tar\.gz' | sort -u
```

Then install it clean and confirm it imports:

```bash
python -m venv /tmp/verify && /tmp/verify/bin/pip install "modelrunner-ai==<version>"
/tmp/verify/bin/python -c "import importlib.metadata as m; print(m.version('modelrunner-ai'))"
```

## Choosing the version

Follow semver against **what is on PyPI**, not against the newest tag — those can
disagree (see the history note below).

Bump `minor` for new features, `patch` for fixes. If the source already commits to
a version — `USER_AGENT` in `client.py`, or "New in X.Y.Z" notes in the package
README — release *that* version. Grep before you tag:

```bash
grep -rn "New in \|Changed in \|USER_AGENT" projects/modelrunner_ai/README.md projects/modelrunner_ai/src/modelrunner_ai/client.py
```

Because the bump workflow only does ±1 from the last tag, a version that skips
ahead (as `0.3.0` did) has to be tagged by hand — see below.

## Releasing a specific version by hand

When the bump workflow can't produce the version you need:

```bash
git checkout main && git pull
git tag modelrunner_ai_v0.4.0            # must not already exist
git push origin modelrunner_ai_v0.4.0
git log modelrunner_ai_v0.3.0..HEAD --pretty=format:'*  %s' --reverse \
  -- projects/modelrunner_ai/ > /tmp/notes.txt
gh release create modelrunner_ai_v0.4.0 --title modelrunner_ai_v0.4.0 --notes-file /tmp/notes.txt
```

Publishing the release triggers `PyPI release` exactly as the normal path does.

## Before you tag

```bash
# tests
python -m venv .venv && .venv/bin/pip install -e 'projects/modelrunner_ai[test]'
cd projects/modelrunner_ai && ../../.venv/bin/python -m pytest -q

# confirm the version the build will actually produce, on a CLEAN tree
git status --porcelain          # must be empty of tracked changes
python -m build                 # filename must read <version>, not <version>.devN
```

A `.devN+g<sha>` or `.dYYYYMMDD` suffix means the tree is dirty or the commit is
not exactly tagged. Never publish one.

## Rules that keep this working

- **One version tag per commit.** Two `modelrunner_ai_v*` tags on the same commit
  make `git describe` pick between them arbitrarily. Both workflows now refuse to
  create or publish that state, but don't create it manually either.
- **Never `--abbrev=0` in `git_describe_command`.** It hides the commit distance,
  which makes `setuptools_scm` read *every* commit as an exact release, so an
  untagged commit silently rebuilds the last released version. See the comment in
  `projects/modelrunner_ai/pyproject.toml`.
- **PyPI uploads are immutable.** A version number, once used, is burned even if
  you delete the release — you cannot re-upload it. Verify before you publish.
- **Don't commit `docs/_build/`.** `.gitignore` has `build/`, which does *not*
  match `_build/`; there is a separate `_build/` entry for this. Committed build
  output gets shipped inside the sdist.

## A note on the pre-0.3.0 history

Tags and PyPI disagree below `0.3.0`, and the tags are the misleading side:

| Tag | Actually published to PyPI |
| --- | --- |
| `modelrunner_ai_v0.0.12` | 0.0.11 |
| `modelrunner_ai_v0.0.14` | 0.0.13 |
| `modelrunner_ai_v0.1.1` | 0.1.0 |

Each of those runs was green. `--abbrev=0` in the describe command made
`setuptools_scm` resolve to the *lower* of the two tags sitting on the commit, so
the release rebuilt and re-uploaded the previous version. `0.0.12`, `0.0.14` and
`0.1.1` do not exist on PyPI and never will — those numbers were consumed.

Fixed in `0.3.0` by dropping `--abbrev=0` and adding the built-version-vs-tag
check to `PyPI release`. `0.2.x` was skipped deliberately, to match the version
the source already claimed.
