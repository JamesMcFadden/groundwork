# AWS

The service on AWS: what runs where, how to bring it up from nothing and check it, and how
to tear it down. It proves the deployment path; scaling and recovery are measured on kind,
in [kubernetes.md](kubernetes.md). The plan, the rule the smoke test applies, and the
results of the first deployment are in [roadmap.md](roadmap.md#m7--aws), and why the tools
split as they do is [ADR 0002](adr/0002-eksctl-for-cluster-terraform-for-data.md).

Everything runs in one account in us-east-1. The cluster, its nodes, the database, and the
load balancer are billed by the hour whether or not anything uses them, so every deployment
is torn down the day it is made.

## What runs

| Part | Made by | Lasts | Role |
| --- | --- | --- | --- |
| Route 53 zone, ACM certificate | Terraform, `infra/dns` | across deployments | DNS for `groundworkproj.com`, and the certificate for `api.groundworkproj.com` |
| Bucket, ECR repositories, RDS, IAM policies | Terraform, `infra/data` | one deployment | uploads, images, the database, and what pods may do |
| EKS cluster and nodes | eksctl, `infra/cluster.yaml` | one deployment | Kubernetes 1.35 on two arm64 `t4g.medium` nodes |
| AWS Load Balancer Controller | Helm, `make eks-load-balancer-controller` | one deployment | creates the API's Network Load Balancer |
| API, worker, migrations | `kubectl apply -k k8s/eks` | one deployment | the service, from the base manifests kind also runs |
| `api.groundworkproj.com` alias | `python -m infra.api_dns upsert` | one deployment | points the name at the load balancer |

**Data.** RDS runs PostgreSQL 16.15 on `db.t4g.micro`, which ships pgvector 0.8.2, with
20 GiB of encrypted gp3 storage. It has no public address, its security group admits port
5432 from the default VPC alone, and it refuses unencrypted connections. It keeps no
backups and takes no final snapshot, since each deployment rebuilds it. The bucket blocks
public access and is emptied when destroyed. The ECR repositories, `groundwork-api` and
`groundwork-worker`, refuse to overwrite a tag, and a registry-level rule scans every image
pushed to them.

**Cluster.** EKS 1.35 in the default VPC's public subnets in `us-east-1a` and `us-east-1b`,
so there is no NAT gateway. A managed node group runs two arm64 `t4g.medium` nodes on
encrypted 20 GiB disks, with only the `vpc-cni`, `kube-proxy`, and `coredns` add-ons. An
OIDC provider lets two IAM roles be assumed by service accounts: `groundwork-service`, which
may get and put objects under `documents/` in the bucket, and
`groundwork-load-balancer-controller`.

**Service.** `k8s/eks/` applies the base manifests with no PostgreSQL, MinIO, or bucket Job.
The API's 2 replicas and the worker each request 750m CPU, limited to 1, and run as the
`groundwork` service account, whose annotation names `groundwork-service`. `S3_ENDPOINT` is
empty and no S3 keys are set, so boto3 resolves S3 itself and signs as that role. The API
answers with `GENERATOR=stub`, and the cluster holds no `ANTHROPIC_API_KEY`. Images are
referenced by digest. What differs between accounts comes from three gitignored files that
`python -m infra.deploy_env` writes:

- `settings.env`: the database host and the bucket.
- `deploy.env`: the image digests, the service's role, and the certificate.
- `secrets.env`: the database password and the API key, readable only by its owner.

**HTTPS.** The API's Service is an internet-facing Network Load Balancer with one listener,
443 over TLS. TLS ends there with the ACM certificate, under
`ELBSecurityPolicy-TLS13-1-2-2021-06`, which allows TLS 1.3 and 1.2 alone. Traffic goes
straight to the API pods' addresses, each a target only while `/health/ready` answers, and
the load balancer's security group admits only TCP 443.

**Cost.** Deployed, about $0.23 an hour at on-demand prices in us-east-1: the EKS cluster
$0.10, the two nodes $0.067, RDS $0.016 and its storage, the load balancer $0.0225 and its
capacity units, and $0.005 for each public address. The zone costs $0.50 a month and stays.
An AWS Budgets budget, `groundwork-monthly`, emails the account owner at 80% of $20 in actual
monthly cost and at 100% forecast.

## Before the first deployment

Needs an AWS account, a registered domain whose nameservers can be changed, Docker Desktop,
[uv](https://docs.astral.sh/uv/), and a `kubectl` within one minor version of Kubernetes
1.35; the one Docker Desktop ships, 1.34.1, is. The account must come from AWS's standard
sign-up: the policies its newer sign-up flow applies deny `iam:*Provider*`, which the OIDC
provider needs. The commands run from the repository's root.

1. **Sign the CLI in without access keys.** Create an IAM user with console access, MFA,
   and `AdministratorAccess`, rather than using the root user. Install AWS CLI v2, 2.32.0 or
   later, and sign in with `aws login`, choosing that user, with `us-east-1` as the region.
   The session refreshes itself for up to 12 hours.

   ```bash
   curl -fsSL https://awscli.amazonaws.com/v2/install.sh | bash
   aws login
   aws sts get-caller-identity    # the Arn should end in :user/<name>, not :root
   ```

2. **Install Terraform, eksctl, and Helm** from their release archives, each checked against
   its published SHA-256. For Apple Silicon:

   ```bash
   v=1.16.2
   curl -fsSLO https://releases.hashicorp.com/terraform/$v/terraform_${v}_darwin_arm64.zip
   curl -fsSLO https://releases.hashicorp.com/terraform/$v/terraform_${v}_SHA256SUMS
   grep "terraform_${v}_darwin_arm64.zip" terraform_${v}_SHA256SUMS | shasum -a 256 -c
   unzip terraform_${v}_darwin_arm64.zip terraform && install -m 0755 terraform ~/.local/bin/

   v=0.230.0
   curl -fsSLO https://github.com/eksctl-io/eksctl/releases/download/v$v/eksctl_Darwin_arm64.tar.gz
   curl -fsSLO https://github.com/eksctl-io/eksctl/releases/download/v$v/eksctl_checksums.txt
   grep ' eksctl_Darwin_arm64.tar.gz$' eksctl_checksums.txt | shasum -a 256 -c
   tar -xzf eksctl_Darwin_arm64.tar.gz eksctl && install -m 0755 eksctl ~/.local/bin/

   v=4.3.0
   curl -fsSLO https://get.helm.sh/helm-v$v-darwin-arm64.tar.gz
   curl -fsSLO https://get.helm.sh/helm-v$v-darwin-arm64.tar.gz.sha256sum
   shasum -a 256 -c helm-v$v-darwin-arm64.tar.gz.sha256sum
   tar -xzf helm-v$v-darwin-arm64.tar.gz darwin-arm64/helm
   install -m 0755 darwin-arm64/helm ~/.local/bin/
   ```

   Terraform's checksums come from the same site as the archive; with `gpg` installed, also
   verify `terraform_${v}_SHA256SUMS` against HashiCorp's signature.

3. **Create the zone and certificate, once.** They outlive every deployment, so this is done
   once for the account. ACM validates the certificate only once the domain is delegated to
   the zone, so it takes two applies, with a change at the registrar between them. The
   domain is the variable `domain`, `groundworkproj.com` by default.

   ```bash
   terraform -chdir=infra/dns init
   terraform -chdir=infra/dns apply -target=aws_route53_zone.domain
   terraform -chdir=infra/dns output name_servers
   ```

   Replace the domain's nameservers at the registrar with those four. When public DNS
   returns them, apply again, which waits until the certificate is issued:

   ```bash
   curl -s 'https://dns.google/resolve?name=groundworkproj.com&type=NS'
   terraform -chdir=infra/dns apply
   ```

   Check public DNS over HTTPS like this rather than with the machine's own resolver, which
   can cache the old nameservers for hours; see the operating notes.

   The domain itself is registered at Porkbun, with auto-renew on, and runs to 2027-09-13.
   Renewal happens at the registrar, not in AWS, and the nameservers stay as they are.

## Bringing it up

About 35 minutes, most of it waiting for RDS and EKS.

1. **Create the data layer.** RDS takes about six minutes.

   ```bash
   terraform -chdir=infra/data init
   terraform -chdir=infra/data apply
   ```

2. **Create the cluster**, which takes about 17 minutes. `infra/cluster.py` fills
   `infra/cluster.yaml` from `infra/data`'s outputs. eksctl adds the cluster to the
   kubeconfig as `<user>@groundwork.us-east-1.eksctl.io`, makes it the current context, and
   creates the load balancer controller's service account and both roles.

   ```bash
   uv run python -m infra.cluster | eksctl create cluster -f -
   ctx=$(kubectl config get-contexts -o name | grep '@groundwork.us-east-1.eksctl.io$')
   ```

3. **Push the images** built from the commit checked out. The target refuses if any file
   that goes into an image differs from that commit, and skips a tag already pushed. ECR
   scans each image as it arrives.

   ```bash
   make ecr-images
   ```

4. **Write the overlay's inputs** from Terraform's outputs, IAM, and ECR. An API key already
   in `secrets.env` is kept.

   ```bash
   uv run python -m infra.deploy_env
   ```

5. **Install the load balancer controller**, before the API's Service asks for a load
   balancer.

   ```bash
   make eks-load-balancer-controller
   ```

6. **Apply the service, and wait for migrations and the rollout.**

   ```bash
   kubectl --context "$ctx" apply -k k8s/eks
   kubectl --context "$ctx" -n groundwork wait --for=condition=complete job/migrate --timeout=10m
   kubectl --context "$ctx" -n groundwork rollout status deployment/api deployment/worker --timeout=10m
   ```

7. **Point the name at the load balancer** once it exists. The Service shows a hostname
   within seconds; the load balancer took about four minutes to become active and its
   targets about five and a half to pass their health checks.

   ```bash
   kubectl --context "$ctx" -n groundwork get svc api    # EXTERNAL-IP shows its hostname
   uv run python -m infra.api_dns upsert
   ```

8. **Reach the API** with the key from `secrets.env`:

   ```bash
   KEY=$(grep '^API_KEY=' k8s/eks/secrets.env | cut -d= -f2)
   curl -s https://api.groundworkproj.com/health/ready
   curl -s -X POST https://api.groundworkproj.com/collections -H "x-api-key: $KEY" \
     -H 'content-type: application/json' -d '{"name": "demo"}'
   ```

## The smoke test

`load/smoke.py` checks a deployment from outside, against `https://api.groundworkproj.com`,
by the rule the roadmap fixed before any deployment:

```bash
uv run python -m load.smoke --note "where it ran from"
```

The certificate must verify against the system's trust store for that name;
`/health/ready` must return 200; a request without a key 401; port 80 must accept no
connection; `uam-risk.pdf` must upload and its ingestion job complete; and the golden set's
question `a21` must return 201 `answered`, citing `uam-risk.pdf`. Each run is written to
`load/results/` with every check, its verdict, the ingestion time, the addresses it reached,
and the images deployed, but never the API key.

It refuses to run, and records nothing, when its own code differs from HEAD, when the
machine resolves the host to other addresses than public DNS does, or when the network
accepts connections on port 80 to `192.0.2.1`, where nothing can listen. Each of those
would make it test something other than the deployment.

## Tearing it down

Order matters. The controller deletes the load balancer only while the cluster runs, and
Terraform cannot delete the IAM policies while eksctl's roles still hold them.
`terraform destroy` never removes the cluster.

```bash
uv run python -m infra.api_dns delete
kubectl --context "$ctx" -n groundwork delete svc api
aws elbv2 describe-load-balancers --query 'length(LoadBalancers)'    # 0 before going on
eksctl delete cluster --name groundwork --region us-east-1 --wait
terraform -chdir=infra/data destroy
rm k8s/eks/settings.env k8s/eks/deploy.env k8s/eks/secrets.env
```

The load balancer went in about 30 seconds, the cluster in 13 minutes, and `infra/data` in
3. Then confirm that nothing billed by the hour is left, querying each service directly:
the Resource Groups Tagging API can list terminated instances and deleted volumes for some
time afterwards.

```bash
aws eks list-clusters
aws rds describe-db-instances --query 'length(DBInstances)'
aws ec2 describe-instances --filters Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'length(Reservations[].Instances[])'
aws ec2 describe-volumes --query 'length(Volumes)'
aws ecr describe-repositories --query 'length(repositories)'
```

Leave `infra/dns` in place. Destroying the zone would give it new nameservers to set at the
registrar, and the certificate could not renew.

## Operating notes

- **Name the Kubernetes context.** eksctl makes the EKS cluster current, and kind's context
  stays in the kubeconfig; `load/` always names kind's. Once the cluster is deleted, no
  context is current.
- **A finished migrate Job cannot be changed.** Applying the overlay with new images gives
  the Job a new image, which Kubernetes refuses for a completed Job with
  `field is immutable`. Delete the Job first:
  `kubectl --context "$ctx" -n groundwork delete job migrate`.
- **New commits mean new images.** Images are tagged with their commit, so after a commit
  run `make ecr-images` and `python -m infra.deploy_env` before applying, or the overlay
  keeps the images it had.
- **The load balancer reads its annotations when the Service is created.** To change one,
  delete the Service and apply again. The load balancer is replaced with a new hostname, so
  run `python -m infra.api_dns upsert` again.
- **The machine's DNS can be wrong for hours.** A resolver that intercepts port-53 DNS and
  still caches the domain's former delegation answers with the old records long after the
  nameservers move, and setting the machine to another DNS server does not help while
  queries are intercepted. Compare its answer with public DNS over HTTPS, and wait, restart
  the router, or use another network.
- **Some networks intercept port 80.** A mobile carrier can accept connections on port 80 to
  every address, which would make the load balancer look open on port 80; the smoke test
  refuses to run on such a network.
- **A zone can run out of nodes.** If `us-east-1a` has no `t4g.medium` capacity, the node
  group launches both nodes in `us-east-1b`, then adds one in `us-east-1a` when it can and
  removes the extra.
- **Image scans are in ECR.** `aws ecr describe-image-scan-findings --repository-name
  groundwork-api --image-id imageTag=<commit>` lists them. Both images carry findings in
  Debian 12 packages from the base image; moving to Debian 13 is parked in the roadmap.
- **The certificate renews only while something uses it.** ACM renews a public certificate
  automatically when it is associated with an AWS service, so between deployments, attached
  to nothing, it reports its renewal eligibility as ineligible. That is expected: the next
  deployment's load balancer attaches it again, and the zone keeps its validation record
  meanwhile, which is what renewal needs. The certificate issued in M7 runs to 2027-03-29.
  Should one expire, a replacement is issued by
  `terraform -chdir=infra/dns apply -replace=aws_acm_certificate.api`, with no change at the
  registrar.
