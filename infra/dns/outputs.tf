output "name_servers" {
  description = "Set at the registrar, in place of its own, before the second apply."
  value       = aws_route53_zone.domain.name_servers
}

output "zone_id" {
  description = "Where the API's alias record is written once its load balancer exists."
  value       = aws_route53_zone.domain.zone_id
}

output "api_host" {
  description = "The name the API is served at."
  value       = local.api_host
}

output "certificate_arn" {
  description = "The issued certificate the API's load balancer terminates TLS with."
  value       = aws_acm_certificate_validation.api.certificate_arn
}
