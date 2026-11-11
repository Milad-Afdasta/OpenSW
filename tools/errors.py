"""Canonical actionable error type shared by every operator controller."""


class SetupError(RuntimeError):
    """An actionable, non-secret setup failure surfaced by the operator CLI."""
