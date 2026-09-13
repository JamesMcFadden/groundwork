# What a deployment's data lives in: the documents bucket, the image repositories, and the
# database, with the policy the service's pods need for the bucket and the subnet tags the
# load balancer controller reads. Created before the cluster and destroyed after it, the
# same day: none of it is meant to outlive a deployment.

terraform {
  required_version = "~> 1.16.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.64"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }
}

provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      project = "groundwork"
      stack   = "data"
    }
  }
}

# The cluster's zones. Both offer the node and database instance types, and EKS and an RDS
# subnet group each need two.
locals {
  zones = ["us-east-1a", "us-east-1b"]
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnet" "default" {
  for_each = toset(local.zones)

  vpc_id            = data.aws_vpc.default.id
  availability_zone = each.key
  default_for_az    = true
}

# How the AWS Load Balancer Controller finds the public subnets for an internet-facing load
# balancer: it reads this tag, not route tables. Removed again on destroy.
resource "aws_ec2_tag" "load_balancer_subnet" {
  for_each = data.aws_subnet.default

  resource_id = each.value.id
  key         = "kubernetes.io/role/elb"
  value       = "1"
}

# Named by prefix, since bucket names are global, and emptied on destroy: a deployment's
# uploads are test documents it never keeps.
resource "aws_s3_bucket" "documents" {
  bucket_prefix = "groundwork-documents-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket = aws_s3_bucket.documents.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# What the service's pods may do, through the role eksctl attaches this to: read and write
# uploads, which live under documents/, and nothing else.
data "aws_iam_policy_document" "documents" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.documents.arn}/documents/*"]
  }
}

resource "aws_iam_policy" "documents" {
  name   = "groundwork-documents"
  policy = data.aws_iam_policy_document.documents.json
}

# Images are tagged with the commit they were built from, so a tag is never reused, and are
# deployed by digest. Deleted with their images on destroy.
resource "aws_ecr_repository" "image" {
  for_each = toset(["groundwork-api", "groundwork-worker"])

  name                 = each.key
  image_tag_mutability = "IMMUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Letters and digits only: the service builds its database URL from the password unescaped.
resource "random_password" "database" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "database" {
  name       = "groundwork"
  subnet_ids = [for subnet in data.aws_subnet.default : subnet.id]
}

# Admits PostgreSQL from inside the default VPC, where the cluster's nodes run, and from
# nowhere else; the instance has no public address either.
resource "aws_security_group" "database" {
  name        = "groundwork-database"
  description = "PostgreSQL from the default VPC"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "PostgreSQL"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default.cidr_block]
  }
}

# The PostgreSQL version the pinned Compose image runs. RDS ships it with pgvector 0.8.2,
# which has the hnsw.iterative_scan search turns on. No backups and no final snapshot: the
# database is rebuilt for each deployment, and destroying it must leave nothing to pay for.
resource "aws_db_instance" "database" {
  identifier     = "groundwork"
  engine         = "postgres"
  engine_version = "16.15"
  instance_class = "db.t4g.micro"

  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  db_name  = "groundwork"
  username = "groundwork"
  password = random_password.database.result

  db_subnet_group_name   = aws_db_subnet_group.database.name
  vpc_security_group_ids = [aws_security_group.database.id]
  publicly_accessible    = false
  multi_az               = false

  auto_minor_version_upgrade = false
  backup_retention_period    = 0
  skip_final_snapshot        = true
  deletion_protection        = false
  apply_immediately          = true
}
