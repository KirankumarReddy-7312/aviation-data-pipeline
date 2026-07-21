"""
Makes the Lambda Layer package (aviation_common) importable without
needing to actually deploy a layer -- mirrors how /opt/python is on
sys.path at runtime inside Lambda.

Each Lambda's handler.py is loaded via import_lambda_module() rather than
a plain `import handler`, because all four functions name their entry
point file identically (handler.py) -- a bare import would only ever
resolve to whichever one happened to be first on sys.path.
"""
import importlib.util
import sys
from pathlib import Path

import pytest
from moto import mock_aws

REPO_ROOT = Path(__file__).resolve().parent.parent
LAMBDAS_DIR = REPO_ROOT / "lambdas"

sys.path.insert(0, str(LAMBDAS_DIR / "common" / "python"))


def import_lambda_module(function_dir: str):
    module_name = f"aviation_lambda_{function_dir}"
    spec = importlib.util.spec_from_file_location(module_name, LAMBDAS_DIR / function_dir / "handler.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch):
    """Dummy credentials so boto3 never tries to hit real AWS / IMDS during tests."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


@pytest.fixture(autouse=True)
def aws_mock(aws_credentials):
    """
    Activates moto for the full duration of every test, including fixture
    setup -- a bare @mock_aws on the test function only wraps the test body,
    not the AWS calls other fixtures (bucket creation, table creation, etc.)
    make while pytest resolves them beforehand. autouse so no test file has
    to remember to depend on this explicitly.
    """
    with mock_aws():
        yield
