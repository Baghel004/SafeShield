"""Test package.

Present so `tests` is a real package: the suite already imports
`tests.conftest` for shared markers, and without this mypy sees the same file as
both `conftest` and `tests.conftest` and refuses to check anything.
"""
