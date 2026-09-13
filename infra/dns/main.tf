# The zone for the service's domain, and the API's certificate. Kept apart from infra/data
# and never destroyed with the cluster: a new zone gets new nameservers, which are set by
# hand at the registrar, and a certificate whose validation record is gone cannot renew.
#
# Created in two applies, because ACM validates the certificate only once the domain's
# nameservers are this zone's:
#   terraform apply -target=aws_route53_zone.domain
#   (set the name_servers output at the registrar, in place of its own)
#   terraform apply

terraform {
  required_version = "~> 1.16.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.64"
    }
  }
}

# us-east-1, the load balancer's region: ACM certificates are used only in their own.
provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      project = "groundwork"
      stack   = "dns"
    }
  }
}

variable "domain" {
  description = "The registered domain the zone serves."
  type        = string
  default     = "groundworkproj.com"
}

locals {
  api_host = "api.${var.domain}"
}

resource "aws_route53_zone" "domain" {
  name = var.domain
}

resource "aws_acm_certificate" "api" {
  domain_name       = local.api_host
  validation_method = "DNS"

  # A replacement is issued before the one in use is deleted.
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "api_certificate_validation" {
  for_each = {
    for option in aws_acm_certificate.api.domain_validation_options :
    option.domain_name => option
  }

  zone_id = aws_route53_zone.domain.zone_id
  name    = each.value.resource_record_name
  type    = each.value.resource_record_type
  records = [each.value.resource_record_value]
  ttl     = 300
}

# Waits until ACM has issued the certificate, so an apply that succeeds leaves one ready for
# the load balancer.
resource "aws_acm_certificate_validation" "api" {
  certificate_arn = aws_acm_certificate.api.arn
  validation_record_fqdns = [
    for record in aws_route53_record.api_certificate_validation : record.fqdn
  ]
}
