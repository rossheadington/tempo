# Git hooks

Tempo is a **public** repo. The pre-commit hook here is the real safety net that
stops secrets, the `.env` file, tokens, or health data from being committed.

## Enable (once per clone)

```bash
git config core.hooksPath .githooks
```

That's it — `.githooks/pre-commit` then runs on every `git commit` and scans the
staged changes with [gitleaks](https://github.com/gitleaks/gitleaks).

## Requirement: gitleaks

The hook needs `gitleaks` on your `PATH`:

```bash
# macOS
brew install gitleaks
# other platforms: https://github.com/gitleaks/gitleaks#installing
```

If gitleaks is **not** installed the hook **fails loudly** (it does not silently
pass) so the safety net is never quietly absent.

## Alternative: the pre-commit framework

If you use [pre-commit](https://pre-commit.com), `.pre-commit-config.yaml` runs
the same gitleaks scan:

```bash
pipx install pre-commit
pre-commit install
```

Use one mechanism or the other — both run gitleaks; you don't need both.
