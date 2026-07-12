"""Force offline mode for the test suite — tests must never need a network.

Set BEFORE any ghost import so the cached Settings snapshot picks it up.
"""

import os

os.environ["REVENANT_MODE"] = "offline"
os.environ["DRY_RUN"] = "1"
