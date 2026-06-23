# EC2 t4g.micro(ARM) — control plane 단일 호스트(앱+PostgreSQL via Docker Compose).
# AMI 는 하드코딩하지 않고 data 소스로 최신 Ubuntu 24.04 ARM64 를 조회한다.

data "aws_ami" "ubuntu_arm" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-arm64-server-*"]
  }
  filter {
    name   = "state"
    values = ["available"]
  }
}

resource "aws_instance" "app" {
  ami                    = data.aws_ami.ubuntu_arm.id
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.app.id]
  key_name               = aws_key_pair.ec2.key_name
  iam_instance_profile   = aws_iam_instance_profile.ec2.name

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    encrypted             = true
    delete_on_termination = true
    tags                  = { Name = "${var.project}-root", Backup = "true" }
  }

  # Docker + compose plugin + metric pusher deps 설치(부팅 1회).
  # 앱/DB 배포와 rider-metrics.service enable 은 다음 단계(compose/서비스 파일 업로드 후 수행).
  user_data = <<-EOF
    #!/bin/bash
    set -euxo pipefail
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y ca-certificates curl gnupg jq unzip
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=arm64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu noble stable" > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip" -o /tmp/awscliv2.zip
    unzip -q /tmp/awscliv2.zip -d /tmp
    /tmp/aws/install --update
    systemctl enable --now docker
    usermod -aG docker ubuntu
    mkdir -p /opt/rider-server
    chown ubuntu:ubuntu /opt/rider-server
  EOF

  tags = { Name = "${var.project}-app" }

  lifecycle {
    ignore_changes  = [ami] # AMI 갱신만으로 인스턴스 재생성되지 않도록(의도적 교체 시 -replace).
    prevent_destroy = true  # root volume local PostgreSQL data 보호: memory hardening 중 destroy/replacement 금지.
  }
}

# Elastic IP — 재부팅/재시작해도 고정 공인 IP 유지(Agent/webhook 등록 안정성).
resource "aws_eip" "app" {
  instance = aws_instance.app.id
  domain   = "vpc"
  tags     = { Name = "${var.project}-eip" }
}

# --- EBS 스냅샷 자동화(DLM) — 사양서 RDS PITR≥7일 의 저비용 대체. 매일 1회, N일 보존. ---
data "aws_iam_policy_document" "dlm_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["dlm.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "dlm" {
  name               = "${var.project}-dlm-role"
  assume_role_policy = data.aws_iam_policy_document.dlm_assume.json
}

resource "aws_iam_role_policy_attachment" "dlm" {
  role       = aws_iam_role.dlm.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "ebs" {
  description        = "${var.project} daily EBS snapshots"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"

  policy_details {
    resource_types = ["VOLUME"]
    target_tags    = { Backup = "true" }

    schedule {
      name = "daily"
      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["18:00"] # UTC 18:00 = KST 03:00
      }
      retain_rule {
        count = var.ebs_snapshot_retention_days
      }
      copy_tags = true
    }
  }
}
