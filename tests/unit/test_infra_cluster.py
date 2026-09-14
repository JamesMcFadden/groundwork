import pytest

from infra.cluster import TEMPLATE, placeholders, render

OUTPUTS = {
    "vpc_id": {"value": "vpc-0example"},
    "subnet_ids": {"value": {"us-east-1a": "subnet-0aaa", "us-east-1b": "subnet-0bbb"}},
    "documents_policy_arn": {"value": "arn:aws:iam::123456789012:policy/groundwork-documents"},
    "load_balancer_controller_policy_arn": {
        "value": "arn:aws:iam::123456789012:policy/groundwork-load-balancer-controller"
    },
    "database_password": {"value": "never-rendered", "sensitive": True},
}


def test_the_cluster_template_is_filled_from_the_data_outputs() -> None:
    rendered = render(TEMPLATE.read_text(), placeholders(OUTPUTS))

    assert "id: vpc-0example" in rendered
    assert "id: subnet-0aaa" in rendered
    assert "id: subnet-0bbb" in rendered
    assert "- arn:aws:iam::123456789012:policy/groundwork-documents" in rendered
    assert "- arn:aws:iam::123456789012:policy/groundwork-load-balancer-controller" in rendered
    assert "never-rendered" not in rendered


def test_a_placeholder_left_unfilled_stops_rendering() -> None:
    """Left in, it would reach eksctl as a literal and fail far from its cause."""
    values = placeholders(OUTPUTS)
    del values["${VPC_ID}"]

    with pytest.raises(ValueError, match="placeholder"):
        render(TEMPLATE.read_text(), values)
