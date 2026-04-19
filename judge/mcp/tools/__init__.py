"""Tool module — importing this package registers all tools via decorators.

Order matters only insofar as tool names must be unique; the registry raises
on duplicates.
"""
from judge.mcp.tools import contests, problems, submissions, tickets, users  # noqa: F401
