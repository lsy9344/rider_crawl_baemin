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

variable "instance_type" {
  description = "EC2 인스턴스 타입 (비용 최소화: ARM t4g.micro)."
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
  description = "앱 포트(8000) 허용 source CIDR. Agent outbound HTTPS 출처. 기본 전체(0.0.0.0/0) — 운영 전 도메인/TLS/IP 제한 권장."
  type        = string
  default     = "0.0.0.0/0"
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
