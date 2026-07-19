import pytest

from bumblewick_support.config import Settings
from bumblewick_support.seed import seed
from bumblewick_support.services import SupportDesk


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "test.db")
    seed(p)
    return p


@pytest.fixture
def settings(db_path):
    # Explicit values so tests don't depend on the environment.
    return Settings(
        db_path=db_path,
        auto_refund_limit_cents=2000,
        refund_rate_limit=3,
        refund_rate_window_hours=24,
    )


@pytest.fixture
def desk(db_path, settings):
    return SupportDesk(db_path, settings)
