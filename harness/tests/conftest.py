import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent.parent
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

import generate  # noqa: E402


@pytest.fixture(scope="session")
def design():
    return generate.build_design()


@pytest.fixture(scope="session")
def final_instances(design):
    return [generate.generate_instance(s, r, design) for s in ("S1", "S2") for r in range(5)]


@pytest.fixture(scope="session")
def s0_instances():
    return [generate.generate_instance("S0", r, None) for r in range(4)]


@pytest.fixture(scope="session")
def all_instances(final_instances, s0_instances):
    return final_instances + s0_instances
