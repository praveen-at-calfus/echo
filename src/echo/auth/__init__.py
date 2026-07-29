"""Authentication for echo — JWT tokens + password hashing + a users store.

Plain English: this package is echo's login system. It turns an email + password
into a signed access token (a tamper-proof "badge"), checks passwords safely
(stored only as bcrypt hashes, never in the clear), and knows the two kinds of
user — GEN-POP (people who submit feedback) and COMPANY (staff who read all the
feedback + analytics). The FastAPI layer (`echo.api`) wires these into request
guards; the CLI (`python -m echo.auth`) seeds/creates accounts.
"""
