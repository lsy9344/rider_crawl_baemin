#!/usr/bin/env bash
# rider_server 운영 7지표 → CloudWatch custom metric 푸셔.
#
# /metrics/operational(JSON)을 60초마다 긁어 numeric 지표를 namespace "RiderServer",
# dimension Environment=production 으로 PutMetricData 한다. 비식별 fleet 집계만 다룬다.
#
# 비용: custom metric 소수 + 표준 해상도(60s) → 저비용. CloudWatch agent(유료 로그수집) 미사용.
# 권한: EC2 인스턴스 역할(rider-server-ec2-role)에 cloudwatch:PutMetricData(namespace=RiderServer 제한).
# 의존: aws-cli v2, jq, curl(Terraform user-data가 기본 설치).
#
# 환경변수(systemd unit 에서 주입, 기본값 내장):
#   METRICS_URL   기본 http://localhost:8000/metrics/operational
#   CW_NAMESPACE  기본 RiderServer
#   CW_ENV        기본 production  (dimension Environment 값)
#   AWS_REGION    미설정 시 IMDS placement/region 으로 자동 해석

set -uo pipefail

METRICS_URL="${METRICS_URL:-http://localhost:8000/metrics/operational}"
CW_NAMESPACE="${CW_NAMESPACE:-RiderServer}"
CW_ENV="${CW_ENV:-production}"

log() { printf '%s push_metrics: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# 리전 해석: 명시 env > IMDSv2 placement/region.
resolve_region() {
  if [[ -n "${AWS_REGION:-}" ]]; then echo "$AWS_REGION"; return; fi
  if [[ -n "${AWS_DEFAULT_REGION:-}" ]]; then echo "$AWS_DEFAULT_REGION"; return; fi
  local token region
  token="$(curl -s --max-time 3 -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 300" 2>/dev/null)"
  region="$(curl -s --max-time 3 -H "X-aws-ec2-metadata-token: $token" \
    "http://169.254.169.254/latest/meta-data/placement/region" 2>/dev/null)"
  echo "${region:-ap-northeast-2}"
}

REGION="$(resolve_region)"
export AWS_DEFAULT_REGION="$REGION"

push_once() {
  local body
  body="$(curl -s --max-time 10 "$METRICS_URL")" || { log "curl failed"; return 1; }
  if [[ -z "$body" ]]; then log "empty response"; return 1; fi

  # jq 로 (이름, 값) 쌍을 생성한다:
  #   - 스칼라 numeric 7지표(null/비numeric 은 제외 → null 인 oldest_heartbeat 는 Agent 0대 시 스킵)
  #   - 플랫폼 dict(crawl_error_rate/_samples)는 "이름_PLATFORM" 으로 평탄화
  # 출력 한 줄당 "MetricName<TAB>Value".
  local pairs
  pairs="$(printf '%s' "$body" | jq -r '
    .metrics
    | to_entries[]
    | if (.value | type) == "number" then
        "\(.key)\t\(.value)"
      elif (.value | type) == "object" then
        (.key) as $k | (.value | to_entries[] | "\($k)_\(.key)\t\(.value)")
      else
        empty
      end
  ' 2>/dev/null)"

  if [[ -z "$pairs" ]]; then log "no numeric metrics parsed (body: ${body:0:200})"; return 1; fi

  # PutMetricData 를 배치(최대 20개/요청 — 여긴 한 번에 다 들어감)로 호출.
  local md=() name value count=0
  while IFS=$'\t' read -r name value; do
    [[ -z "$name" ]] && continue
    md+=("MetricName=${name},Value=${value},Unit=None,Dimensions=[{Name=Environment,Value=${CW_ENV}}]")
    count=$((count + 1))
  done <<< "$pairs"

  if [[ "$count" -eq 0 ]]; then log "no metric-data entries"; return 1; fi

  if aws cloudwatch put-metric-data \
      --namespace "$CW_NAMESPACE" \
      --metric-data "${md[@]}" 2>/tmp/push_metrics.err; then
    log "pushed $count metrics to $CW_NAMESPACE (region=$REGION)"
    return 0
  else
    log "put-metric-data failed: $(tr '\n' ' ' < /tmp/push_metrics.err)"
    return 1
  fi
}

# RUN_ONCE=1 이면 1회만(테스트/디버그). 기본은 60초 루프(systemd Type=simple).
if [[ "${RUN_ONCE:-0}" == "1" ]]; then
  push_once
  exit $?
fi

log "starting loop (url=$METRICS_URL ns=$CW_NAMESPACE env=$CW_ENV region=$REGION interval=60s)"
while true; do
  push_once || true
  sleep 60
done
