# 2. Create the cluster with eksctl, manage data services with Terraform

## Status

Proposed — no infrastructure code exists yet. To be revisited when M7 is implemented.

## Context

The AWS deployment needs a Kubernetes cluster, a container registry, a Postgres
instance with pgvector, and a bucket. Two properties matter and they pull in different
directions.

Reproducibility matters most for the **stateful** resources. The bucket holds uploaded
documents, the database holds every chunk and embedding, and the registry holds the
images a cluster pulls. These outlive any single deployment, and recreating them by
hand invites drift between what is documented and what exists.

The **cluster** has the opposite profile. It holds no state, it is created and
destroyed within a demo window to avoid a control-plane charge accruing indefinitely,
and it is expensive to express. A hand-written EKS module means a VPC, subnets across
availability zones, node groups, and — for pods to reach S3 without static credentials
— an OIDC provider, a trust policy, and a role binding. That is hours of Terraform
whose only output is a cluster that gets deleted the same day.

Time is the binding constraint: the whole project is budgeted at roughly 50 hours, of
which AWS is eight.

## Decision

Split by lifecycle rather than using one tool for everything.

**Terraform** manages S3, ECR, and RDS Postgres — roughly eighty lines, using the
default VPC and one security group. These are the resources whose configuration is
worth version-controlling and reviewing.

**eksctl** creates the cluster and its IRSA service account:

```bash
eksctl create cluster --name groundwork --nodes 2 --node-type t3.small
eksctl create iamserviceaccount --name groundwork --attach-policy-arn ... --approve
```

The second command provisions the OIDC provider, IAM role, trust policy, and annotated
ServiceAccount that let pods reach S3 with no static credentials. It replaces
substantially more hand-written Terraform than its length suggests.

Traffic reaches the API through a `Service` of type `LoadBalancer`, which provisions an
NLB directly. No ingress controller is installed.

## Consequences

**Gained**

- The resources that hold state are declarative, reviewable, and destroyable with one
  command.
- IRSA — the answer to "how do your pods authenticate to AWS" — costs one command
  rather than a day of OIDC configuration.
- Skipping an ALB ingress controller removes the single largest source of first-time
  EKS debugging.
- Cluster creation stays cheap to repeat, which suits a create-verify-destroy cycle at
  roughly $0.19/hour.

**Given up**

- Cluster configuration is not in Terraform state, so `terraform destroy` does not
  remove it. Teardown is two commands, and the runbook must say so or a cluster gets
  left running.
- Two tools instead of one, each with its own state and failure modes.
- The default VPC is used rather than a purpose-built one with private subnets. This is
  a demo-grade choice, not a production one, and is recorded as such.
- No ingress means no host or path routing, and no TLS termination at the edge.

**Revisit if** the cluster becomes long-lived, or if more than one environment needs to
be stood up from the same definitions. Both would justify the cost of expressing the
cluster in Terraform, most likely via the community EKS module rather than by hand.
