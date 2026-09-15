# ADR 0002: Use strict, versioned protocol models

Status: Accepted

All process-boundary messages use strict Pydantic models and carry a semantic protocol version. Unknown fields are rejected. Additive optional fields are backward-compatible; changed semantics require a new major version. Coordinates use the virtual desktop's logical coordinate space and rectangles use half-open bounds.

The initial transport will use authenticated JSON-RPC over loopback. A later native daemon may replace Python without changing these schemas.
