"""Tool layer — every external capability, one file per registry entry.

Contract: context/tool-registry.md (closed list). Tools take Pydantic args,
return Pydantic results with `ok`/`error` fields, and never raise past the
tool boundary. Heavy imports are lazy — inside the function that needs them.
"""
