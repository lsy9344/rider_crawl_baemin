# CloudWatch 운영 알람 + SNS 토픽 — rider_server 7지표 모니터링(Story 5.9 운영 연동).
#
# 비용 결정: CloudAgent(유료 로그 수집) 미사용. EC2 의 경량 푸셔(deploy/cloudwatch/push_metrics.sh)가
# /metrics/operational 을 60초마다 긁어 custom metric(namespace "RiderServer", Environment=production)
# 으로 PutMetricData 한다. 여기선 그 metric 위에 소수의 actionable 알람만 건다.
# - custom metric: 적은 수(7개) + 표준 해상도 → 사실상 프리티어/저비용.
# - alarm: 표준 알람 소수 → 개당 월 $0.10 수준(프리티어 10개). SNS 미구독 시 발신 비용 0.
#
# IAM(cloudwatch:PutMetricData)은 storage_secrets.tf 의 ec2_perms 에 namespace 제한으로 추가됨.

# ── 알람 임계(운영자 튜닝 가능). 기본값은 policy.py 정본 의미를 따른다(drift 0). ──
variable "alarm_namespace" {
  description = "푸셔가 적재하는 CloudWatch custom metric 네임스페이스(IAM 조건과 일치해야 함)."
  type        = string
  default     = "RiderServer"
}

variable "alarm_email" {
  description = "알람 수신 이메일. 비우면(기본) SNS 구독을 만들지 않는다 — 운영자가 나중에 직접 구독."
  type        = string
  default     = ""
}

variable "heartbeat_stale_seconds" {
  description = "oldest_heartbeat_age_seconds 알람 임계(초). Agent 전원 침묵 감지. 기본 900s(15분)."
  type        = number
  default     = 900
}

variable "telegram_error_alarm_threshold" {
  description = "telegram_error_count(10분 윈도) 알람 임계. policy 의 fail-loud(>=1)보다 완화한 운영 임계."
  type        = number
  default     = 5
}

variable "queue_lag_alarm_seconds" {
  description = "kakao_queue_lag_seconds 알람 임계(초). policy.py QUEUE_LAG_ALERT_SECONDS 와 동일한 120초."
  type        = number
  default     = 120
}

variable "auth_required_alarm_threshold" {
  description = "auth_required_count/gmail_reauth_required_count 알람 임계. policy.py 의 >=1 의미와 동일."
  type        = number
  default     = 1
}

variable "crawl_error_rate_alarm_threshold" {
  description = "crawl_error_rate_by_platform_* 알람 임계. scheduler breaker threshold 와 동일한 0.30."
  type        = number
  default     = 0.30
}

variable "crawl_error_min_samples" {
  description = "crawl_error_rate_by_platform_* 알람 표본 가드. scheduler breaker min_samples 와 동일한 5."
  type        = number
  default     = 5
}

variable "host_mem_available_alarm_percent" {
  description = "HostMemAvailablePercent low-memory 알람 임계. t4g.small 전환 후에도 15% 미만 지속 시 경고."
  type        = number
  default     = 15
}

variable "host_swap_used_alarm_percent" {
  description = "HostSwapUsedPercent high-swap 알람 임계. 40% 초과 지속 시 메모리 압박으로 본다."
  type        = number
  default     = 40
}

# ── SNS 토픽(알람 액션 대상). 구독은 alarm_email 지정 시에만 생성(승인 없는 실 이메일 구독 금지). ──
resource "aws_sns_topic" "alarms" {
  name = "${var.project}-alarms"
  tags = { Name = "${var.project}-alarms" }
}

resource "aws_sns_topic_subscription" "alarms_email" {
  count     = var.alarm_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# 공통 dimension — 푸셔가 PutMetricData 시 붙이는 값과 정확히 일치해야 알람이 metric 을 찾는다.
locals {
  metric_dimensions = { Environment = "production" }
  # 알람 액션: SNS 토픽으로 알린다(미구독이어도 토픽 발행은 동작 — 추후 구독만 추가).
  alarm_actions = [aws_sns_topic.alarms.arn]
}

# (host-a) HostMemAvailablePercent < 15% (5분) — EC2 host memory pressure.
resource "aws_cloudwatch_metric_alarm" "host_mem_available_low" {
  alarm_name          = "${var.project}-host-mem-available-low"
  alarm_description   = "HostMemAvailablePercent < ${var.host_mem_available_alarm_percent}% (5분 지속). EC2 host memory pressure."
  namespace           = var.alarm_namespace
  metric_name         = "HostMemAvailablePercent"
  dimensions          = local.metric_dimensions
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 5
  threshold           = var.host_mem_available_alarm_percent
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  tags                = { Name = "${var.project}-host-mem-available-low" }
}

# (host-b) HostSwapUsedPercent > 40% (5분) — swap 사용 급증.
resource "aws_cloudwatch_metric_alarm" "host_swap_used_high" {
  alarm_name          = "${var.project}-host-swap-used-high"
  alarm_description   = "HostSwapUsedPercent > ${var.host_swap_used_alarm_percent}% (5분 지속). EC2 host swap pressure."
  namespace           = var.alarm_namespace
  metric_name         = "HostSwapUsedPercent"
  dimensions          = local.metric_dimensions
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  threshold           = var.host_swap_used_alarm_percent
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  tags                = { Name = "${var.project}-host-swap-used-high" }
}

# (a) agents_offline >= 1 (5분) — Agent 오프라인. CRITICAL.
resource "aws_cloudwatch_metric_alarm" "agents_offline" {
  alarm_name          = "${var.project}-agents-offline"
  alarm_description   = "오프라인 Agent >= 1 (5분 지속). policy.py: agents_offline>=1 -> CRITICAL."
  namespace           = var.alarm_namespace
  metric_name         = "agents_offline"
  dimensions          = local.metric_dimensions
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  # 푸셔 미가동/데이터 결손도 관측 장애로 본다.
  treat_missing_data = "breaching"
  alarm_actions      = local.alarm_actions
  ok_actions         = local.alarm_actions
  tags               = { Name = "${var.project}-agents-offline" }
}

# (b) targets_critical >= 1 (10분) — 갱신 정체 critical 대상. CRITICAL.
resource "aws_cloudwatch_metric_alarm" "targets_critical" {
  alarm_name          = "${var.project}-targets-critical"
  alarm_description   = "갱신 정체 critical 대상 >= 1 (10분 지속)."
  namespace           = var.alarm_namespace
  metric_name         = "targets_critical"
  dimensions          = local.metric_dimensions
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 10
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  tags                = { Name = "${var.project}-targets-critical" }
}

# (c) telegram_error_count 급증 (10분 윈도 카운트가 임계 초과 5분 지속) — 알림 전달 장애.
resource "aws_cloudwatch_metric_alarm" "telegram_errors" {
  alarm_name          = "${var.project}-telegram-errors"
  alarm_description   = "Telegram 전송 오류(10분 윈도) >= ${var.telegram_error_alarm_threshold} (5분 지속). 알림 전달 장애 신호."
  namespace           = var.alarm_namespace
  metric_name         = "telegram_error_count"
  dimensions          = local.metric_dimensions
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  threshold           = var.telegram_error_alarm_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  tags                = { Name = "${var.project}-telegram-errors" }
}

# (d) oldest_heartbeat_age_seconds 과도 — Agent 전원 침묵(heartbeat 노화). CRITICAL.
# null(Agent 0대) 또는 푸셔 미가동으로 metric 이 빠지면 관측 장애로 본다.
resource "aws_cloudwatch_metric_alarm" "heartbeat_stale" {
  alarm_name          = "${var.project}-heartbeat-stale"
  alarm_description   = "가장 오래된 heartbeat 가 ${var.heartbeat_stale_seconds}s 초과 (5분 지속). Agent 침묵."
  namespace           = var.alarm_namespace
  metric_name         = "oldest_heartbeat_age_seconds"
  dimensions          = local.metric_dimensions
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  threshold           = var.heartbeat_stale_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  tags                = { Name = "${var.project}-heartbeat-stale" }
}

# (e) kakao_queue_lag_seconds > 120초 — Kakao FIFO 적체. WARNING.
resource "aws_cloudwatch_metric_alarm" "queue_lag" {
  alarm_name          = "${var.project}-queue-lag"
  alarm_description   = "Kakao queue lag > ${var.queue_lag_alarm_seconds}s (5분 지속). runbook: queue_lag."
  namespace           = var.alarm_namespace
  metric_name         = "kakao_queue_lag_seconds"
  dimensions          = local.metric_dimensions
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  threshold           = var.queue_lag_alarm_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  tags                = { Name = "${var.project}-queue-lag" }
}

# (f) auth_required_count >= 1 — 사람 개입 재인증 필요. WARNING.
resource "aws_cloudwatch_metric_alarm" "auth_required" {
  alarm_name          = "${var.project}-auth-required"
  alarm_description   = "auth_required_count >= ${var.auth_required_alarm_threshold}. runbook: auth_required."
  namespace           = var.alarm_namespace
  metric_name         = "auth_required_count"
  dimensions          = local.metric_dimensions
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = var.auth_required_alarm_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  tags                = { Name = "${var.project}-auth-required" }
}

# (g) gmail_reauth_required_count >= 1 — 쿠팡 인증 이메일 재확인 근사. WARNING.
resource "aws_cloudwatch_metric_alarm" "gmail_reauth_required" {
  alarm_name          = "${var.project}-gmail-reauth-required"
  alarm_description   = "gmail_reauth_required_count >= ${var.auth_required_alarm_threshold}. runbook: auth_required."
  namespace           = var.alarm_namespace
  metric_name         = "gmail_reauth_required_count"
  dimensions          = local.metric_dimensions
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = var.auth_required_alarm_threshold
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  tags                = { Name = "${var.project}-gmail-reauth-required" }
}

# (h) platform crawl 실패율 — 표본 가드 후 30% 초과. CRITICAL.
resource "aws_cloudwatch_metric_alarm" "api_error_rate_baemin" {
  alarm_name          = "${var.project}-api-error-rate-baemin"
  alarm_description   = "BAEMIN crawl error rate > ${var.crawl_error_rate_alarm_threshold} with samples >= ${var.crawl_error_min_samples}. runbook: api_error_rate."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 5
  threshold           = var.crawl_error_rate_alarm_threshold
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  tags                = { Name = "${var.project}-api-error-rate-baemin" }

  metric_query {
    id          = "rate"
    return_data = false
    metric {
      namespace   = var.alarm_namespace
      metric_name = "crawl_error_rate_by_platform_BAEMIN"
      dimensions  = local.metric_dimensions
      period      = 60
      stat        = "Maximum"
    }
  }

  metric_query {
    id          = "samples"
    return_data = false
    metric {
      namespace   = var.alarm_namespace
      metric_name = "crawl_samples_by_platform_BAEMIN"
      dimensions  = local.metric_dimensions
      period      = 60
      stat        = "Maximum"
    }
  }

  metric_query {
    id          = "guarded"
    expression  = "IF(FILL(samples, 0) >= ${var.crawl_error_min_samples}, FILL(rate, 0), 0)"
    label       = "BAEMIN crawl error rate sample guarded"
    return_data = true
  }
}

resource "aws_cloudwatch_metric_alarm" "api_error_rate_coupang" {
  alarm_name          = "${var.project}-api-error-rate-coupang"
  alarm_description   = "COUPANG crawl error rate > ${var.crawl_error_rate_alarm_threshold} with samples >= ${var.crawl_error_min_samples}. runbook: api_error_rate."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 5
  threshold           = var.crawl_error_rate_alarm_threshold
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
  tags                = { Name = "${var.project}-api-error-rate-coupang" }

  metric_query {
    id          = "rate"
    return_data = false
    metric {
      namespace   = var.alarm_namespace
      metric_name = "crawl_error_rate_by_platform_COUPANG"
      dimensions  = local.metric_dimensions
      period      = 60
      stat        = "Maximum"
    }
  }

  metric_query {
    id          = "samples"
    return_data = false
    metric {
      namespace   = var.alarm_namespace
      metric_name = "crawl_samples_by_platform_COUPANG"
      dimensions  = local.metric_dimensions
      period      = 60
      stat        = "Maximum"
    }
  }

  metric_query {
    id          = "guarded"
    expression  = "IF(FILL(samples, 0) >= ${var.crawl_error_min_samples}, FILL(rate, 0), 0)"
    label       = "COUPANG crawl error rate sample guarded"
    return_data = true
  }
}

# ── outputs ──
output "alarm_sns_topic_arn" {
  description = "알람 SNS 토픽 ARN. 운영자가 이메일/슬랙 등을 직접 구독한다(미구독 시 발신 비용 0)."
  value       = aws_sns_topic.alarms.arn
}

output "cloudwatch_metrics_namespace" {
  description = "운영 7지표 custom metric 네임스페이스."
  value       = var.alarm_namespace
}

output "cloudwatch_alarm_names" {
  description = "생성된 CloudWatch 알람 이름 목록."
  value = [
    aws_cloudwatch_metric_alarm.host_mem_available_low.alarm_name,
    aws_cloudwatch_metric_alarm.host_swap_used_high.alarm_name,
    aws_cloudwatch_metric_alarm.agents_offline.alarm_name,
    aws_cloudwatch_metric_alarm.targets_critical.alarm_name,
    aws_cloudwatch_metric_alarm.telegram_errors.alarm_name,
    aws_cloudwatch_metric_alarm.heartbeat_stale.alarm_name,
    aws_cloudwatch_metric_alarm.queue_lag.alarm_name,
    aws_cloudwatch_metric_alarm.auth_required.alarm_name,
    aws_cloudwatch_metric_alarm.gmail_reauth_required.alarm_name,
    aws_cloudwatch_metric_alarm.api_error_rate_baemin.alarm_name,
    aws_cloudwatch_metric_alarm.api_error_rate_coupang.alarm_name,
  ]
}
