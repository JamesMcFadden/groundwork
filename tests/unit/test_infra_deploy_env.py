import pathlib

from infra.deploy_env import deployment, env_file, read_env, secret_values, settings

OUTPUTS = {
    "database_host": {"value": "groundwork.abc123.us-east-1.rds.amazonaws.com"},
    "bucket": {"value": "groundwork-documents-0123"},
    "database_password": {"value": "from-terraform", "sensitive": True},
    "repository_urls": {
        "value": {
            "groundwork-api": "123456789012.dkr.ecr.us-east-1.amazonaws.com/groundwork-api",
            "groundwork-worker": "123456789012.dkr.ecr.us-east-1.amazonaws.com/groundwork-worker",
        }
    },
}


def test_images_are_referenced_by_digest_from_their_repositories() -> None:
    values = deployment(
        OUTPUTS,
        {"groundwork-api": "sha256:aaa", "groundwork-worker": "sha256:bbb"},
        "arn:aws:iam::123456789012:role/groundwork-service",
    )

    assert values == {
        "API_IMAGE": "123456789012.dkr.ecr.us-east-1.amazonaws.com/groundwork-api@sha256:aaa",
        "WORKER_IMAGE": "123456789012.dkr.ecr.us-east-1.amazonaws.com/groundwork-worker@sha256:bbb",
        "SERVICE_ROLE_ARN": "arn:aws:iam::123456789012:role/groundwork-service",
    }


def test_settings_carry_the_database_host_and_bucket_and_no_secret() -> None:
    values = settings(OUTPUTS)

    assert values == {
        "POSTGRES_HOST": "groundwork.abc123.us-east-1.rds.amazonaws.com",
        "S3_BUCKET": "groundwork-documents-0123",
    }


def test_an_api_key_already_issued_is_kept() -> None:
    """Writing the files again must not lock out clients holding the key."""
    values = secret_values(OUTPUTS, {"API_KEY": "issued-before", "POSTGRES_PASSWORD": "stale"})

    assert values == {"POSTGRES_PASSWORD": "from-terraform", "API_KEY": "issued-before"}


def test_a_new_api_key_is_generated_when_none_exists() -> None:
    first = secret_values(OUTPUTS, {})["API_KEY"]
    second = secret_values(OUTPUTS, {})["API_KEY"]

    assert len(first) == 64 and int(first, 16) >= 0
    assert first != second


def test_env_files_round_trip(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "secrets.env"
    path.write_text(env_file({"API_KEY": "abc=def", "EMPTY": ""}))

    assert read_env(path) == {"API_KEY": "abc=def", "EMPTY": ""}
    assert read_env(tmp_path / "missing.env") == {}
