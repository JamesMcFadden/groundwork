# 2. Create the cluster with eksctl, manage data services with Terraform

## Status

Accepted, 2026-09-14, as built in M7, with the amendments at the end. See
[aws.md](../aws.md) for the deployment as it runs.

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

## Amendments, as built in M7

The split by lifecycle held. What building changed:

- **Two Terraform roots.** `infra/dns` holds the domain's Route 53 zone and the ACM
  certificate and outlives deployments, since a new zone would need its nameservers set at
  the registrar again. `infra/data` holds each deployment's bucket, ECR repositories, RDS
  instance, and IAM policies, the subnet tags the load balancer controller reads, and ECR's
  registry-level scan-on-push rule. The default VPC stayed.
- **eksctl works from a config file**, `infra/cluster.yaml`, filled from `infra/data`'s
  outputs, rather than the flags above. It runs two arm64 `t4g.medium` nodes, since a
  `t3.small` could not hold the worker's 1.5 GiB memory request, on encrypted disks, with
  only the networking add-ons. IRSA is as decided: eksctl creates the OIDC provider, a role
  for the service, and a role and service account for the load balancer controller. EKS Pod
  Identity, which needs no OIDC provider, was considered and not needed.
- **An installed controller creates the load balancer.** EKS's built-in controller creates
  a Classic Load Balancer unless annotated for an NLB, and receives only critical bug
  fixes, so the AWS Load Balancer Controller, installed with Helm, creates the Network Load
  Balancer instead. There is still no ingress controller, and so no host or path routing.
- **TLS ends at the edge after all.** The load balancer terminates TLS with the ACM
  certificate for `api.groundworkproj.com`, since every request carries an API key, so the
  consequence given up above, no TLS termination at the edge, no longer applies.
- **Cost.** Deployed, about $0.23 an hour rather than $0.19: the nodes, RDS, the load
  balancer, and a charge for each public IPv4 address. Teardown stayed a sequence, not one
  command: delete the DNS record and the API's Service, then the cluster, then
  `infra/data`.
