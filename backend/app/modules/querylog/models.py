# Core owns the shared writer model; reuse its payload schema instead of mapping the table twice.
from app.core.audit import QueryRun

__all__ = ["QueryRun"]
