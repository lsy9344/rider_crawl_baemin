# rider_server AWS 인프라 — Terraform 설정(Epic 5 / architecture.md "Infrastructure & Deployment").
#
# 비용 최소화 결정(사용자 승인): RDS 대신 EC2 1대에 Docker Compose 로 앱+PostgreSQL 을 함께
# 구동하고, NAT Gateway 없이 퍼블릭 서브넷+IGW 만 사용한다(NAT 시간당 과금 회피). 사양서의
# RDS/PITR 요구는 비용 우선으로 EC2 내 컨테이너 DB + EBS 스냅샷 백업으로 대체한다(README 명시).
#
# state 는 기존 공용 버킷(terraform-state-654654307503, ap-northeast-2, versioning on)에
# rider-server/ prefix 로 격리 저장한다(다른 프로젝트 naver-sms-automation/ 패턴 계승).

terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }

  backend "s3" {
    bucket  = "terraform-state-654654307503"
    key     = "rider-server/terraform.tfstate"
    region  = "ap-northeast-2"
    encrypt = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "rider-server"
      ManagedBy = "terraform"
      Epic      = "5"
    }
  }
}
