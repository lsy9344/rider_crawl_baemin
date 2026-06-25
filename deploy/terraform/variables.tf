variable "region" {
  description = "AWS 리전 (사양서: AWS Seoul)."
  type        = string
  default     = "ap-northeast-2"
}

variable "project" {
  description = "리소스 이름 prefix."
  type        = string
  default     = "rider-server"
}

variable "github_repository" {
  description = "GitHub Actions OIDC 배포를 허용할 owner/repo."
  type        = string
  default     = "lsy9344/rider_crawl_baemin"
}

variable "github_deploy_branch" {
  description = "프로덕션 배포를 허용할 GitHub branch."
  type        = string
  default     = "main"
}

variable "server_ecr_repository_name" {
  description = "서버 Docker image 를 저장할 ECR repository 이름."
  type        = string
  default     = "rider-server"
}

variable "instance_type" {
  description = "EC2 인스턴스 타입. 기본은 비용 최소 예시(t4g.micro), 운영 memory hardening은 tfvars에서 t4g.small로 고정."
  type        = string
  default     = "t4g.micro"
}

variable "root_volume_gb" {
  description = "EC2 루트 EBS(gp3) 크기(GB). 앱+PostgreSQL 데이터 포함."
  type        = number
  default     = 20
}

variable "vpc_cidr" {
  description = "전용 VPC CIDR."
  type        = string
  default     = "10.50.0.0/16"
}

variable "public_subnet_cidr" {
  description = "퍼블릭 서브넷 CIDR(NAT 없이 IGW 로 outbound)."
  type        = string
  default     = "10.50.1.0/24"
}

variable "ssh_ingress_cidr" {
  description = "SSH(22) 허용 source CIDR. 운영자 공인 IP 로 제한(fail-closed)."
  type        = string
  # apply 시 -var 또는 tfvars 로 운영자 IP 주입. 기본은 막아둠(빈 값이면 SG 규칙 미생성).
  default = ""
}

variable "app_ingress_cidr" {
  description = "앱 포트(8000) 허용 source CIDR. 빈 값이면 SG 규칙 미생성(fail-closed). 운영은 ALB/reverse proxy+TLS 또는 고정 Agent 출처 CIDR로 제한."
  type        = string
  default     = ""
}

variable "db_name" {
  description = "PostgreSQL 데이터베이스/유저 이름."
  type        = string
  default     = "rider"
}

variable "ebs_snapshot_retention_days" {
  description = "DLM EBS 스냅샷 보존 일수(사양서 RDS PITR≥7일 대체)."
  type        = number
  default     = 7
}
