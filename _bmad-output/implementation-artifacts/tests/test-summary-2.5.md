# Test Automation Summary — Story 2.5 (핵심 도메인 모델과 상태 enum)

**워크플로:** `bmad-qa-generate-e2e-tests` · **역할:** QA 자동화(테스트 생성만 — 코드 리뷰/스토리 검증 아님)
**대상:** `src/rider_server/domain/` (8개 frozen dataclass + 3개 상태머신 + 6개 지원 enum + `SecretRef`)
**프레임워크:** pytest (`pyproject.toml` `pythonpath=["src"]`, `testpaths=["tests"]`) — 프로젝트 기존 컨벤션 사용
**실행:** `.venv/Scripts/python.exe -m pytest`

## 적용 범위 메모 (API/E2E 비해당)

본 스토리는 **순수 도메인 정의(dataclass + Enum)** 다 — HTTP/FastAPI 엔드포인트도 UI도 없다(둘 다 Epic 5 소유). 따라서 워크플로의 "API 테스트/E2E 테스트"는 **비해당**이고, 적절한 자동화는 **도메인 계약 단위 테스트**다. 기존 19개는 happy-path를 잘 덮고 있었고, 본 실행은 **AC 추적 가능한 커버리지 갭만 자동 보강**했다.

## 생성/보강한 테스트 (gap fill — 신규 9개)

### 상태/지원 enum — `tests/server/test_domain_states.py` (+4)
- [x] `test_baemin_auth_state_in_contract_order` — AC2: `BaeminAuthState` 7멤버 **계약 순서**(기존엔 집합만)
- [x] `test_subscription_status_in_contract_order` — AC2/5: `SubscriptionStatus` 4멤버 **계약 순서**
- [x] `test_suspended_is_distinct_across_lifecycle_and_subscription_enums` — AC5: `SUSPENDED` 동명 멤버가 두 enum에서 **별개 타입**임을 잠금(별도 enum 보장)
- [x] `test_every_enum_member_json_serializes_to_its_uppercase_name` — AC2: 전 enum·전 멤버 **대문자 JSON 직렬화**(2개 spot-check → 전수)

### 도메인 모델 — `tests/server/test_domain_models.py` (+5)
- [x] `test_package_all_reexports_eight_models_and_all_enums` — AC1: `domain/__init__.py` `__all__` **재노출 완전성·중복 없음·임포트 가능**
- [x] `test_optional_field_defaults_match_contract` — AC1/3: 선택 필드 **기본값 정본**(`external_id=""`/`url=""`/`interval_minutes=0`/`DeliveryRule.enabled=True`/`cdp_port=None`/`state=UNKNOWN`/`current_period_end=None`/`secret_kind=""`)
- [x] `test_subscription_quotas_default_not_shared_between_instances` — dev-note(d): `quotas` **default_factory 인스턴스 간 비공유**(가변 기본값 버그 가드)
- [x] `test_messenger_channel_kakao_variant` — AC1: **Kakao 변형**(텔레그램 식별자는 None — 한쪽만 채워짐)
- [x] `test_secret_ref_holds_only_opaque_handle_no_plaintext_leak` — NFR-8/ADD-15: 저장값·repr에 **평문 비누출**(핸들/분류/메타만)

## 커버리지 (AC 추적)

| AC | 요구 | 기존 | 보강 |
|---|---|---|---|
| AC1 | 8 모델·필드·frozen·임포트·`__all__` 재노출 | 임포트/필드/frozen | `__all__` 완전성·기본값·Kakao 변형 |
| AC2/5 | 대문자 `(str,Enum)`·멤버 구분·직렬화 | 멤버 집합·ACTIVE 구분·2 spot-check | 계약 **순서**·SUSPENDED 별개 enum·**전수** JSON |
| AC3/6/7 | soft delete(INACTIVE/enabled=False) 이력 보존 | target/channel/rule 전이·보존 | `enabled=True` 기본 활성 잠금 |
| NFR-8 | SecretRef 평문 비보유 | 필드 **이름** 검사 | 저장 **값**·repr 비누출 |

- 도메인 모델: 8/8 모델, 17/17 재노출 심볼 커버
- 상태머신: 3/3 (Customer 11 · Baemin 7 · Subscription 4) 멤버·순서·직렬화 잠금
- 서버 테스트: **19 → 28** (+9)

## 실행 결과

```
.venv/Scripts/python.exe -m pytest tests/server -q   →  28 passed
.venv/Scripts/python.exe -m pytest -q                →  698 passed
```

- 기준선 670 + 스토리 2.5 원본 19 + 본 보강 9 = **698**, **회귀 0**(NFR-20)
- 범위: `git diff -w --stat -- src/ pyproject.toml` = 빈 출력 → `src/rider_crawl/`·`pyproject.toml` **무변경**(순수 additive, 테스트만 추가)
- 누출 grep(`tests/server/`): 실제 토큰/`chat_id`/휴대폰 패턴 **0** — fixture는 가짜 ID/ref만(A1)

## 변경 파일

- `tests/server/test_domain_states.py` (+4 테스트)
- `tests/server/test_domain_models.py` (+5 테스트, `import rider_server.domain as domain_pkg` 추가)

## Next Steps

- CI에서 `tests/server/` 수집·실행(첫 rider_server 테스트 디렉터리)
- Story 2.6(게이트 평가)·2.7(wiring) 구현 시 본 enum/모델을 정본으로 소비
- DB/Pydantic 경계(Epic 5) 추가 시 enum↔DB 문자열·dataclass↔Pydantic 변환 계약 테스트 확장
