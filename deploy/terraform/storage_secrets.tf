# S3(sanitized 아티팩트) + Secrets Manager(DB 비번/앱 secret) + IAM(인스턴스 역할).

# --- S3: sanitized 스크린샷/HTML fixture/export. raw 민감 HTML 저장 금지(사양서). ---
resource "aws_s3_bucket" "artifacts" {
  bucket = "${var.project}-artifacts-${data.aws_caller_identity.current.account_id}"
  tags   = { Name = "${var.project}-artifacts" }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

data "aws_caller_identity" "current" {}

# --- Secrets Manager: DB 비밀번호(랜덤 생성) + 앱 secret 묶음. DB 엔 *_ref 만(사양서). ---
resource "random_password" "db" {
  length  = 32
  special = false # URL/연결 문자열 안전을 위해 특수문자 제외
}

resource "aws_secretsmanager_secret" "db" {
  name                    = "${var.project}/db-credentials"
  description             = "rider-server PostgreSQL credentials (managed by terraform)"
  recovery_window_in_days = 0 # 개발/재생성 편의 — 운영은 7~30 권장
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    username = var.db_name
    password = random_password.db.result
    dbname   = var.db_name
    # 앱이 그대로 쓸 수 있는 완성형 연결 문자열(컨테이너 내부 db 호스트).
    database_url = "postgresql+asyncpg://${var.db_name}:${random_password.db.result}@db:5432/${var.db_name}"
  })
}

# 앱 런타임 secret(텔레그램 webhook secret/봇 토큰 등) placeholder — 운영자가 값 채움.
resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.project}/app-secrets"
  description             = "rider-server app secrets (telegram webhook/bot token refs). Fill values post-apply."
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    RIDER_TELEGRAM_WEBHOOK_SECRET = ""
    RIDER_TELEGRAM_BOT_TOKEN      = ""
  })

  lifecycle {
    # 운영자가 콘솔/CLI 로 실제 값을 채운 뒤 terraform 이 빈 값으로 덮어쓰지 않도록.
    ignore_changes = [secret_string]
  }
}

# --- IAM: EC2 인스턴스 역할 — secret read + S3 artifact 버킷 read/write. ---
data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ec2" {
  name               = "${var.project}-ec2-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

data "aws_iam_policy_document" "ec2_perms" {
  statement {
    sid       = "ReadSecrets"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.db.arn, aws_secretsmanager_secret.app.arn]
  }
  statement {
    sid       = "S3Artifacts"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"]
    resources = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
  }
  statement {
    sid       = "CloudWatchLogs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/${var.project}/*"]
  }
  # 운영 7지표 푸셔(deploy/cloudwatch/push_metrics.sh)가 custom metric 을 적재한다.
  # PutMetricData 는 리소스 단위 제한이 불가하므로 cloudwatch:namespace 조건으로 네임스페이스를
  # RiderServer 로만 제한한다(다른 네임스페이스 오염/오과금 방지).
  statement {
    sid       = "CloudWatchPutCustomMetrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["RiderServer"]
    }
  }
}

resource "aws_iam_role_policy" "ec2" {
  name   = "${var.project}-ec2-policy"
  role   = aws_iam_role.ec2.id
  policy = data.aws_iam_policy_document.ec2_perms.json
}

resource "aws_iam_instance_profile" "ec2" {
  name = "${var.project}-ec2-profile"
  role = aws_iam_role.ec2.name
}
