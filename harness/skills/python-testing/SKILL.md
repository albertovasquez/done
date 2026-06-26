---
name: python-testing
description: Write and run pytest unit/integration tests
---
# Python Testing

When fixing or adding Python code in this repo:

- Run tests with `python -m pytest <path> -v`. Scope to a specific file when iterating.
- Write a failing test FIRST that reproduces the bug, then make it pass.
- Prefer one assertion per behavior; use `assert func(x) == expected` directly.
- After the fix, run the whole relevant test file to check for regressions.
