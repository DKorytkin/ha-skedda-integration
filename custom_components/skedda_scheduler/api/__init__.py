"""Standalone transport layer for Skedda's private web API.

This package must not import Home Assistant. It is kept extractable so the
client can be reused or published separately; tests/test_layering.py enforces
the boundary.
"""
