"""Run the library's templates in Home Assistant itself, with its own fixtures
and the Voice Labs integration (a checkout of OHF-Voice/voice-labs beside this
repository's, or ``VOICE_LABS``). The integration's own tests are there.

From a Home Assistant core checkout, with its test requirements installed:

    cd ~/opt/homeassistant && python -m pytest -c pyproject.toml --rootdir . \\
        ~/opt/ohf-apps-experimental/voice_labs/hass_tests

(``HA_CORE`` points elsewhere than the current directory.) Home Assistant's
tests keep their custom integrations in a package of their own
(tests/testing_config/custom_components); the integration is added to it.
"""

import os
import sys
from pathlib import Path

import pytest

CORE = Path(os.environ.get("HA_CORE", os.getcwd())).resolve()
sys.path.insert(0, str(CORE))
for name in [m for m in sys.modules if m == "tests" or m.startswith("tests.")]:
    del sys.modules[name]  # some other "tests" package got there first

from tests.conftest import *  # noqa: E402,F403 - Home Assistant's fixtures and hooks

# The integration, from a checkout of OHF-Voice/voice-labs: VOICE_LABS, else
# beside this repository's.
COMPONENTS = Path(os.environ.get("VOICE_LABS", Path(__file__).resolve().parents[3] / "voice-labs")) / "custom_components"


@pytest.fixture(autouse=True)
def voice_labs_integration(enable_custom_integrations: None) -> None:
    """Make custom_components.voice_labs loadable."""
    import custom_components  # pylint: disable=import-outside-toplevel

    if str(COMPONENTS) not in list(custom_components.__path__):
        custom_components.__path__.append(str(COMPONENTS))
