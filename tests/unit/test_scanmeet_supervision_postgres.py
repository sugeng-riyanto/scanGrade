"""Guard the disposable SQL runner's boundary without a database dependency."""
import pytest
from deploy.scanmeet_supervision_postgres import settings


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_explicit_loopback_and_disposable_name_required(host):
    result = settings({"PGHOST": host, "PGDATABASE": "scanmeet_supervision_ci_test"})
    assert result["host"] == host and result["dbname"] == "scanmeet_supervision_ci_test"


@pytest.mark.parametrize("env", [{}, {"PGHOST": "remote.invalid", "PGDATABASE": "scanmeet_supervision_ci"},
    {"PGHOST": "127.0.0.1", "PGDATABASE": "postgres"},
    {"PGHOST": "/tmp", "PGDATABASE": "scanmeet_supervision_ci"},
    {"PGHOST": "127.0.0.1,remote.invalid", "PGDATABASE": "scanmeet_supervision_ci"}])
def test_refuses_production_or_implicit_connections(env):
    with pytest.raises(ValueError, match="disposable"):
        settings(env)


@pytest.mark.parametrize("name", ["PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGPASSFILE"])
def test_libpq_cannot_redirect_a_loopback_check(name):
    with pytest.raises(ValueError, match="overrides"):
        settings({"PGHOST": "127.0.0.1", "PGDATABASE": "scanmeet_supervision_ci", name: "do-not-use"})


def test_explicit_address_prevents_implicit_libpq_fallback():
    result = settings({"PGHOST": "localhost", "PGDATABASE": "scanmeet_supervision_ci",
                       "DATABASE_URL": "do-not-use"})
    assert result["hostaddr"] == "127.0.0.1"
    assert result["sslmode"] == "disable" and result["options"] == ""
