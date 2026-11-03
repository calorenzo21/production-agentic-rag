import os
from pathlib import Path

import pytest

from tests.stack.support import Stack, development_stack, production_stack


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.environ.get("STACK_MODE"):
        return
    skip = pytest.mark.skip(reason="set STACK_MODE to dev or prod to drive a running stack")
    for item in items:
        if Path(__file__).parent in item.path.parents:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Stack:
    if os.environ["STACK_MODE"] == "prod":
        return production_stack(tmp_path_factory.mktemp("caddy"))
    return development_stack()
