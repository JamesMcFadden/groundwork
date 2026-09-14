"""Render the eksctl cluster config from infra/data's Terraform outputs.

infra/cluster.yaml names what differs between accounts as placeholders. This fills them and
prints the config, for `uv run python -m infra.cluster | eksctl create cluster -f -`.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

INFRA_DIR = Path(__file__).parent
TEMPLATE = INFRA_DIR / "cluster.yaml"
DATA_ROOT = INFRA_DIR / "data"


def placeholders(outputs: dict[str, Any]) -> dict[str, str]:
    """The template's placeholders, from `terraform output -json` for infra/data."""
    subnets = outputs["subnet_ids"]["value"]
    return {
        "${VPC_ID}": outputs["vpc_id"]["value"],
        "${SUBNET_US_EAST_1A}": subnets["us-east-1a"],
        "${SUBNET_US_EAST_1B}": subnets["us-east-1b"],
        "${DOCUMENTS_POLICY_ARN}": outputs["documents_policy_arn"]["value"],
        "${LOAD_BALANCER_CONTROLLER_POLICY_ARN}": outputs["load_balancer_controller_policy_arn"][
            "value"
        ],
    }


def render(template: str, values: dict[str, str]) -> str:
    for placeholder, value in values.items():
        template = template.replace(placeholder, value)
    if "${" in template:
        raise ValueError("the cluster template has a placeholder the outputs do not fill")
    return template


def terraform_outputs(root: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["terraform", f"-chdir={root}", "output", "-json"],
        check=True,
        capture_output=True,
        text=True,
    )
    outputs: dict[str, Any] = json.loads(result.stdout)
    return outputs


def main() -> None:
    outputs = terraform_outputs(DATA_ROOT)
    if not outputs:
        raise SystemExit("infra/data has no outputs: apply it before creating the cluster")
    sys.stdout.write(render(TEMPLATE.read_text(), placeholders(outputs)))


if __name__ == "__main__":
    main()
