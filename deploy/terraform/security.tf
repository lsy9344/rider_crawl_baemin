# 보안 그룹 + 전용 키페어.
#
# SSH(22)는 운영자 IP(var.ssh_ingress_cidr)로만 — 빈 값이면 규칙 자체를 만들지 않는다(fail-closed).
# 앱(8000)은 Agent outbound HTTPS 출처용. 빈 값이면 규칙을 만들지 않는다(fail-closed).
# 운영 전 도메인/TLS 종료(80/443)+IP 제한으로 좁힐 것.
# egress 는 전체 허용(Agent→배민/쿠팡 웹, AWS API, 패키지 설치).

resource "aws_security_group" "app" {
  name        = "${var.project}-app-sg"
  description = "rider-server control plane (app + postgres on same host)"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${var.project}-app-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "ssh" {
  count             = var.ssh_ingress_cidr == "" ? 0 : 1
  security_group_id = aws_security_group.app.id
  description       = "SSH from operator IP"
  cidr_ipv4         = var.ssh_ingress_cidr
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "app_http" {
  count             = var.app_ingress_cidr == "" ? 0 : 1
  security_group_id = aws_security_group.app.id
  description       = "backend-api (uvicorn)"
  cidr_ipv4         = var.app_ingress_cidr
  from_port         = 8000
  to_port           = 8000
  ip_protocol       = "tcp"
}

# PostgreSQL(5432)은 호스트 내부 Docker 네트워크에서만 접근 — 외부 ingress 규칙을 만들지 않는다.

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.app.id
  description       = "all outbound"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# 전용 키페어 — Terraform 이 생성하고 private key 를 로컬에 저장(0600). git 추적 금지(.gitignore).
resource "tls_private_key" "ec2" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "aws_key_pair" "ec2" {
  key_name   = "${var.project}-keypair"
  public_key = tls_private_key.ec2.public_key_openssh
  tags       = { Name = "${var.project}-keypair" }
}

resource "local_sensitive_file" "private_key" {
  content         = tls_private_key.ec2.private_key_pem
  filename        = "${path.module}/.secrets/${var.project}-keypair.pem"
  file_permission = "0600"
}
