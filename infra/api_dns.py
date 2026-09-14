"""Point the API's hostname at its load balancer, or remove the record before teardown.

The AWS Load Balancer Controller creates the load balancer when the EKS overlay is applied,
with a hostname that differs on every deployment, so the alias record in infra/dns's zone is
written once it exists:

    uv run python -m infra.api_dns upsert
    uv run python -m infra.api_dns delete   # before deleting the Service or the cluster

The load balancer is found by the tag the controller gives it, so no kubectl context is
needed.
"""

import argparse
import json
from typing import Any

from infra.cluster import INFRA_DIR, terraform_outputs
from infra.deploy_env import aws

DNS_ROOT = INFRA_DIR / "dns"
STACK_TAG = "service.k8s.aws/stack"
STACK = "groundwork/api"


def alias_change(action: str, name: str, target_dns: str, target_zone_id: str) -> dict[str, Any]:
    """A change batch aliasing `name` to a load balancer, in the form Route 53 takes."""
    return {
        "Changes": [
            {
                "Action": action,
                "ResourceRecordSet": {
                    "Name": name,
                    "Type": "A",
                    "AliasTarget": {
                        "HostedZoneId": target_zone_id,
                        "DNSName": target_dns,
                        "EvaluateTargetHealth": False,
                    },
                },
            }
        ]
    }


def matching_record(record_sets: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """The A record for `name`, which Route 53 returns fully qualified, with a trailing dot."""
    wanted = name.rstrip(".") + "."
    for record in record_sets:
        if record["Name"] == wanted and record["Type"] == "A":
            return record
    return None


def load_balancer() -> tuple[str, str]:
    found = aws(
        "resourcegroupstaggingapi",
        "get-resources",
        "--resource-type-filters",
        "elasticloadbalancing:loadbalancer",
        "--tag-filters",
        f"Key={STACK_TAG},Values={STACK}",
    )
    arns = [resource["ResourceARN"] for resource in found["ResourceTagMappingList"]]
    if len(arns) != 1:
        raise SystemExit(
            f"expected one load balancer tagged {STACK_TAG}={STACK}, found {len(arns)}"
        )
    described = aws("elbv2", "describe-load-balancers", "--load-balancer-arns", arns[0])
    balancer = described["LoadBalancers"][0]
    return balancer["DNSName"], balancer["CanonicalHostedZoneId"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["upsert", "delete"])
    action = parser.parse_args().action

    outputs = terraform_outputs(DNS_ROOT)
    if not outputs:
        raise SystemExit("infra/dns has no outputs: apply it first")
    zone_id, host = outputs["zone_id"]["value"], outputs["api_host"]["value"]

    if action == "upsert":
        target_dns, target_zone_id = load_balancer()
        batch = alias_change("UPSERT", host, target_dns, target_zone_id)
    else:
        listed = aws("route53", "list-resource-record-sets", "--hosted-zone-id", zone_id)
        record = matching_record(listed["ResourceRecordSets"], host)
        if record is None:
            print(f"no A record for {host}")
            return
        batch = {"Changes": [{"Action": "DELETE", "ResourceRecordSet": record}]}

    change = aws(
        "route53",
        "change-resource-record-sets",
        "--hosted-zone-id",
        zone_id,
        "--change-batch",
        json.dumps(batch),
    )
    print(f"{action} {host}: {change['ChangeInfo']['Status']}")


if __name__ == "__main__":
    main()
