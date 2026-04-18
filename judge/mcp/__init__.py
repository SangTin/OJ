"""MCP (Model Context Protocol) endpoint for the judge.

Tools are registered by importing their modules. ``views.mcp_endpoint``
imports ``judge.mcp.tools`` lazily to avoid Django app-loading ordering
issues.
"""
