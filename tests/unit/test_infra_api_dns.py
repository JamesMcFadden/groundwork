from typing import Any

from infra.api_dns import alias_change, matching_record


def test_the_alias_points_the_host_at_the_load_balancer() -> None:
    batch = alias_change(
        "UPSERT",
        "api.groundworkproj.com",
        "k8s-groundwo-api-0123.elb.us-east-1.amazonaws.com",
        "Z26RNL4JYFTOTI",
    )

    assert batch == {
        "Changes": [
            {
                "Action": "UPSERT",
                "ResourceRecordSet": {
                    "Name": "api.groundworkproj.com",
                    "Type": "A",
                    "AliasTarget": {
                        "HostedZoneId": "Z26RNL4JYFTOTI",
                        "DNSName": "k8s-groundwo-api-0123.elb.us-east-1.amazonaws.com",
                        "EvaluateTargetHealth": False,
                    },
                },
            }
        ]
    }


def test_the_record_to_delete_is_found_by_its_fully_qualified_name() -> None:
    """Route 53 lists names with a trailing dot, which infra/dns's output leaves off."""
    record: dict[str, Any] = {"Name": "api.groundworkproj.com.", "Type": "A", "AliasTarget": {}}
    record_sets: list[dict[str, Any]] = [
        {"Name": "groundworkproj.com.", "Type": "NS", "ResourceRecords": []},
        {"Name": "api.groundworkproj.com.", "Type": "TXT", "ResourceRecords": []},
        record,
    ]

    assert matching_record(record_sets, "api.groundworkproj.com") is record
    assert matching_record(record_sets, "other.groundworkproj.com") is None
