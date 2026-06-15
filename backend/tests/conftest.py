"""Pytest config: register markers so Phase 1 can run cleaning-only."""
import sys
from pathlib import Path

# make _harness importable as a top-level module
sys.path.insert(0, str(Path(__file__).parent))

def pytest_configure(config):
    config.addinivalue_line("markers", "profiler: requires the schema profiler engine (§4/§5)")
    config.addinivalue_line("markers", "joins: requires the join-discovery engine (§5)")
    config.addinivalue_line("markers", "live: hits a REAL LLM provider (needs a key); run only via -m live")
