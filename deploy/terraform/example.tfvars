# 복사해서 terraform.tfvars 로 사용. 운영자 공인 IP 로 SSH 를 제한한다.
# 빈 값으로 두면 SSH(22) ingress 규칙 자체가 생성되지 않는다(완전 차단).
ssh_ingress_cidr = "203.0.113.10/32"

# 앱 포트(8000) 허용 출처를 열려면(예: 특정 Agent IP) 지정한다.
# 빈 값이면 앱 포트 ingress 규칙이 생성되지 않는다(기본 fail-closed).
# app_ingress_cidr = "0.0.0.0/0"

# 인스턴스/볼륨 조정. 운영 memory hardening 기준은 t4g.small 이다.
instance_type = "t4g.small"
# root_volume_gb = 20
