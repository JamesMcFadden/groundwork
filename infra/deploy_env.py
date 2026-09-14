"""Write the EKS overlay's gitignored inputs from infra/data, IAM, and ECR.

k8s/eks/ reads three files this writes, so the repository holds nothing specific to one
account:

    settings.env  the database host and bucket, for the groundwork-config ConfigMap
    deploy.env    the images by digest, the service's IAM role, and the API's certificate,
                  copied into the manifests
    secrets.env   the database password and the API key, for the groundwork-secrets Secret

Run once the images for HEAD are pushed: `make ecr-images`, then
`uv run python -m infra.deploy_env`. An API key already in secrets.env is kept, so clients
keep working when the files are written again.
"""

import json
import secrets
import subprocess
from pathlib import Path
from typing import Any

from infra.cluster import DATA_ROOT, INFRA_DIR, terraform_outputs

DNS_ROOT = INFRA_DIR / "dns"
REGION = "us-east-1"
OVERLAY = Path(__file__).parent.parent / "k8s" / "eks"
SERVICE_ROLE = "groundwork-service"
REPOSITORIES = ("groundwork-api", "groundwork-worker")


def env_file(values: dict[str, str]) -> str:
    return "".join(f"{key}={value}\n" for key, value in values.items())


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    lines = (line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)
    return {key: value for key, value in lines}


def settings(outputs: dict[str, Any]) -> dict[str, str]:
    return {
        "POSTGRES_HOST": outputs["database_host"]["value"],
        "S3_BUCKET": outputs["bucket"]["value"],
    }


def deployment(
    outputs: dict[str, Any],
    dns_outputs: dict[str, Any],
    digests: dict[str, str],
    role_arn: str,
) -> dict[str, str]:
    """Images by repository and digest, which a tag could not guarantee, the pods' role, and
    the certificate the API's load balancer serves."""
    urls = outputs["repository_urls"]["value"]
    return {
        "API_IMAGE": f"{urls['groundwork-api']}@{digests['groundwork-api']}",
        "WORKER_IMAGE": f"{urls['groundwork-worker']}@{digests['groundwork-worker']}",
        "SERVICE_ROLE_ARN": role_arn,
        "CERTIFICATE_ARN": dns_outputs["certificate_arn"]["value"],
    }


def secret_values(outputs: dict[str, Any], existing: dict[str, str]) -> dict[str, str]:
    """The database password, and the API key already issued or a new one."""
    return {
        "POSTGRES_PASSWORD": outputs["database_password"]["value"],
        "API_KEY": existing.get("API_KEY") or secrets.token_hex(32),
    }


def aws(*args: str) -> Any:
    result = subprocess.run(
        ["aws", "--region", REGION, "--output", "json", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def image_digests(revision: str) -> dict[str, str]:
    digests = {}
    for repository in REPOSITORIES:
        try:
            found = aws(
                "ecr",
                "describe-images",
                "--repository-name",
                repository,
                "--image-ids",
                f"imageTag={revision}",
            )
        except subprocess.CalledProcessError:
            raise SystemExit(
                f"no {repository} image for {revision[:12]}: run `make ecr-images` first"
            ) from None
        digests[repository] = found["imageDetails"][0]["imageDigest"]
    return digests


def write(path: Path, content: str, *, private: bool = False) -> None:
    if private:
        path.touch(mode=0o600)
        path.chmod(0o600)
    path.write_text(content)


def main() -> None:
    outputs = terraform_outputs(DATA_ROOT)
    dns_outputs = terraform_outputs(DNS_ROOT)
    if not outputs or not dns_outputs:
        raise SystemExit("infra/data or infra/dns has no outputs: apply both first")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    role_arn = aws("iam", "get-role", "--role-name", SERVICE_ROLE)["Role"]["Arn"]
    digests = image_digests(revision)
    secrets_path = OVERLAY / "secrets.env"

    write(OVERLAY / "settings.env", env_file(settings(outputs)))
    write(OVERLAY / "deploy.env", env_file(deployment(outputs, dns_outputs, digests, role_arn)))
    write(secrets_path, env_file(secret_values(outputs, read_env(secrets_path))), private=True)
    print(f"wrote settings.env, deploy.env, and secrets.env for images at {revision[:12]}")


if __name__ == "__main__":
    main()
