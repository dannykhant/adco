import os
import sys
import glob
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def _latest_generated_driver():
    drivers_dir = os.path.join(PROJECT_ROOT, "tpcc", "drivers")
    candidates = glob.glob(os.path.join(drivers_dir, "gemini*driver.py"))
    if not candidates:
        pytest.skip("No generated driver files found (*gemini*driver.py)")
    latest = max(candidates, key=os.path.getmtime)
    stem = os.path.basename(latest).replace("driver.py", "")
    return stem


def _resolve_driver_name(request):
    if hasattr(request, "param"):
        return request.param
    return os.environ.get("TEST_DRIVER", _latest_generated_driver())


@pytest.fixture
def driver_name(request):
    return _resolve_driver_name(request)


@pytest.fixture
def driver_class(driver_name):
    full_name = "%sDriver" % driver_name.title()
    module_name = "%sdriver" % driver_name
    mod = __import__("tpcc.drivers.%s" % module_name, globals(), locals(), [full_name])
    return getattr(mod, full_name)


@pytest.fixture
def driver_module(driver_class):
    return sys.modules[driver_class.__module__]


@pytest.fixture
def driver(driver_class):
    ddl = os.path.join(PROJECT_ROOT, "tpcc", "tpcc.mysql.sql")
    inst = driver_class(ddl)
    inst.conn = MagicMock()
    inst.cursor = MagicMock()
    return inst


@pytest.fixture
def mock_cursor(driver):
    return driver.cursor


@pytest.fixture
def mock_conn(driver):
    return driver.conn
