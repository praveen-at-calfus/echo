"""echo — AI customer-feedback intelligence for e-commerce.

Plain English: echo reads messy customer feedback (product reviews, support
tickets, post-purchase surveys), sorts and scores each item, attaches the money
at stake, and writes a weekly summary a CX/product leader can act on.

This top-level package is organized into:
  * schemas/ — the shared "shape" of one feedback item (used everywhere)
  * corpus/  — the offline builder that turns raw sales data into the dataset
  * db/      — loading that dataset into PostgreSQL
  * prompts/ — the text templates sent to the language model
"""

__version__ = "0.1.0"
