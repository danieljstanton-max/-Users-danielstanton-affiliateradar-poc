"""AffiliateRadar data-layer proof of concept.

Proves three things from the developer brief:
  1. Discovery      — SERP -> dedup ranking domains -> candidate list.
  2. Traffic/trend  — Domain Rank Overview -> weekly etv snapshot -> trend
                      computed from our OWN history.
  3. Data ownership — API refresh updates API-owned fields ONLY and can never
                      overwrite human-owned classification or contacts.

Runs mock-first (committed fixtures) and switches to the real DataForSEO /
screenshot APIs automatically when credentials are present in the environment.
"""

__version__ = "0.1.0"
