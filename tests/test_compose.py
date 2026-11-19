from pathlib import Path

import yaml


def test_api_waits_for_postgres_but_not_redis():
    compose = yaml.safe_load(Path(__file__).parents[1].joinpath("compose.yaml").read_text())
    dependencies = compose["services"]["api"]["depends_on"]

    assert dependencies == {"postgres": {"condition": "service_healthy"}}
