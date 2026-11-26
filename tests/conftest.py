from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from era835.synth import generate  # noqa: E402
from era835.warehouse import Warehouse  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic")
    truth = generate(out, seed=835)
    files = sorted((out / "files").glob("*.835"))
    return {"dir": out, "truth": truth, "files": files, "deposits": out / "deposits.csv"}


@pytest.fixture(scope="session")
def truth(dataset):
    return dataset["truth"]


@pytest.fixture(scope="session")
def loaded(dataset, tmp_path_factory) -> Warehouse:
    """The whole synthetic set loaded in delivery order."""
    wh = Warehouse(tmp_path_factory.mktemp("wh_forward"))
    wh.load(dataset["files"])
    return wh


@pytest.fixture(scope="session")
def loaded_reverse(dataset, tmp_path_factory) -> Warehouse:
    wh = Warehouse(tmp_path_factory.mktemp("wh_reverse"))
    wh.load(list(reversed(dataset["files"])))
    return wh


@pytest.fixture
def star() -> str:
    return (FIXTURES / "remit_star.835").read_text()


@pytest.fixture
def pipe() -> str:
    return (FIXTURES / "remit_pipe.835").read_text()


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path
