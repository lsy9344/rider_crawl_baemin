output "instance_id" {
  description = "EC2 인스턴스 ID."
  value       = aws_instance.app.id
}

output "public_ip" {
  description = "고정 공인 IP(EIP) — Agent/webhook 등록 대상."
  value       = aws_eip.app.public_ip
}

output "ssh_command" {
  description = "EC2 SSH 접속 명령."
  value       = "ssh -i ${abspath("${path.module}/.secrets/${var.project}-keypair.pem")} ubuntu@${aws_eip.app.public_ip}"
}

output "artifacts_bucket" {
  description = "sanitized 아티팩트 S3 버킷."
  value       = aws_s3_bucket.artifacts.bucket
}

output "db_secret_name" {
  description = "DB 자격증명 Secrets Manager 이름."
  value       = aws_secretsmanager_secret.db.name
}

output "app_secret_name" {
  description = "앱 secret Secrets Manager 이름(운영자가 값 채움)."
  value       = aws_secretsmanager_secret.app.name
}

output "vpc_id" {
  value       = aws_vpc.main.id
  description = "전용 VPC ID."
}
