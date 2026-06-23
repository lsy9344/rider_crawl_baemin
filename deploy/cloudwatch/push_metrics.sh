#!/usr/bin/env bash
# rider_server 운영 7지표 + host memory/swap → CloudWatch custom metric 푸셔.
#
# /metrics/operational(JSON)과 /proc/meminfo를 60초마다 긁어 numeric 지표를 namespace
# "RiderServer", dimension Environment=production 으로 PutMetricData 한다. 비식별 fleet/host
# 집계만 다루며 process command line 또는 secret 값을 수집하지 않는다.
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

host_metric_pairs() {
  awk '
    /^MemTotal:/ { mem_total = $2 * 1024 }
    /^MemAvailable:/ { mem_available = $2 * 1024 }
    /^SwapTotal:/ { swap_total = $2 * 1024 }
    /^SwapFree:/ { swap_free = $2 * 1024 }
    END {
      if (mem_total > 0) {
        printf "HostMemAvailableBytes\t%.0f\tBytes\n", mem_available
        printf "HostMemAvailablePercent\t%.6f\tPercent\n", (mem_available / mem_total) * 100
      }
      if (swap_total > 0) {
        swap_used = swap_total - swap_free
      } else {
        swap_used = 0
      }
      printf "HostSwapUsedBytes\t%.0f\tBytes\n", swap_used
      if (swap_total > 0) {
        printf "HostSwapUsedPercent\t%.6f\tPercent\n", (swap_used / swap_total) * 100
      } else {
        printf "HostSwapUsedPercent\t0\tPercent\n"
      }
    }
  ' /proc/meminfo
}

push_once() {
  local app_pairs="" body="" host_pairs pairs jq_filter
  # app 운영 7지표를 실제로 얻었는가. host metric publish 성공이 app 손실을 가리지 않도록
  # 별도로 추적한다 — app 실패(curl 실패/빈 응답/jq parse 실패)면 host 를 올리고도 non-zero
  # 로 끝낸다(검토 Medium: 운영 7지표 손실을 health check 가 잡을 수 있게).
  local app_ok=0

  host_pairs="$(host_metric_pairs)" || { log "host meminfo parse failed"; host_pairs=""; }

  if body="$(curl -s --max-time 10 "$METRICS_URL")"; then
    if [[ -n "$body" ]]; then
      # jq 로 (이름, 값) 쌍을 생성한다:
      #   - 스칼라 numeric 7지표(null/비numeric 은 제외 → null 인 oldest_heartbeat 는 Agent 0대 시 스킵)
      #   - 플랫폼 dict(crawl_error_rate/_samples)는 numeric 값만 "이름_PLATFORM" 으로 평탄화
      # 출력 한 줄당 "MetricName<TAB>Value<TAB>Unit".
      jq_filter='.metrics | to_entries[] | if (.value | type) == "number" then "\(.key)\t\(.value)\tNone" elif (.value | type) == "object" then (.key) as $k | (.value | to_entries[] | select(.value | type == "number") | "\($k)_\(.key)\t\(.value)\tNone") else empty end'
      if app_pairs="$(printf '%s' "$body" | jq -r "$jq_filter" 2>/dev/null)" && [[ -n "$app_pairs" ]]; then
        app_ok=1
      else
        # jq 실패 또는 numeric 지표 0개 — app metric 손실(빈 app_pairs 로 진행하되 실패로 표시).
        app_pairs=""
        log "no numeric app metrics parsed (body: ${body:0:200})"
      fi
    else
      log "empty app metrics response"
    fi
  else
    log "curl failed; publishing host metrics only"
  fi

  pairs="$(printf '%s\n%s\n' "$app_pairs" "$host_pairs")"

  # PutMetricData 를 저카디널리티 단일 요청으로 호출.
  local md=() name value unit count=0
  while IFS=$'\t' read -r name value unit; do
    [[ -z "$name" ]] && continue
    md+=("MetricName=${name},Value=${value},Unit=${unit:-None},Dimensions=[{Name=Environment,Value=${CW_ENV}}]")
    count=$((count + 1))
  done <<< "$pairs"

  if [[ "$count" -eq 0 ]]; then log "no metric-data entries"; return 1; fi

  if aws cloudwatch put-metric-data \
      --namespace "$CW_NAMESPACE" \
      --metric-data "${md[@]}" 2>/tmp/push_metrics.err; then
    if [[ "$app_ok" -eq 1 ]]; then
      log "pushed $count metrics to $CW_NAMESPACE (region=$REGION)"
      return 0
    fi
    # host 는 올렸지만 app 운영지표는 손실 — 성공으로 끝내면 health check 가 못 잡는다(검토 Medium).
    log "pushed $count host-only metrics to $CW_NAMESPACE (region=$REGION); app operational metrics MISSING"
    return 2
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
