output "vpc_id" {
  description = "The default VPC, which the cluster joins."
  value       = data.aws_vpc.default.id
}

output "subnet_ids" {
  description = "The cluster's public subnets, by availability zone."
  value       = { for zone, subnet in data.aws_subnet.default : zone => subnet.id }
}

output "bucket" {
  description = "The documents bucket, for S3_BUCKET."
  value       = aws_s3_bucket.documents.bucket
}

output "documents_policy_arn" {
  description = "The bucket policy eksctl attaches to the service's pod role."
  value       = aws_iam_policy.documents.arn
}

output "load_balancer_controller_policy_arn" {
  description = "The policy eksctl attaches to the load balancer controller's role."
  value       = aws_iam_policy.load_balancer_controller.arn
}

output "repository_urls" {
  description = "Where each image is pushed, by repository name."
  value       = { for name, repository in aws_ecr_repository.image : name => repository.repository_url }
}

output "database_host" {
  description = "For POSTGRES_HOST."
  value       = aws_db_instance.database.address
}

output "database_name" {
  description = "For POSTGRES_DB."
  value       = aws_db_instance.database.db_name
}

output "database_user" {
  description = "For POSTGRES_USER."
  value       = aws_db_instance.database.username
}

output "database_password" {
  description = "For POSTGRES_PASSWORD."
  value       = random_password.database.result
  sensitive   = true
}
