"""Shared platform code for CDS microservices.

Kept intentionally small: things every service needs in exactly the same way
(token verification, JSON logging, DB session, audit records, task queue).
Business logic never lives here.
"""
