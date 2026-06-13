---
baseline_commit: bf6603f
---

# Story 3.7: Telegram 중앙 전송 도입

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a 운영자,
I want 3.1~3.6이 분리해 둔 전송 경계(`DispatchFanoutService.dispatch_all`(3.4)·`IdempotentDeliveryService.deliver_once`(3.5)·`DeliveryFailurePolicy.attempt_delivery`(3.6))에 꽂아 쓸 **중앙(central) Telegram 전송 어댑터**를 도입해, 기존 검증된 `rider_crawl.sender.send_telegram_text`(Bot API 호출·슈퍼그룹 migrate·ambiguous·retry-after 처리)를 **한 줄도 바꾸지 않고 재사용**하되 **Agent별 `getUpdates` polling을 만들지 않는 send-only 중앙 경로**로 두고(같은 bot token을 여러 프로세스가 동시에 polling하는 구조를 신규 경로에 도입하지 않음 — NFR-5/project-context 규칙), 전송 대상 scope에 `chat_id + topic_id(message_thread_id)` 조합을 포함시키고(라우팅 식별자 — secret 아님), Telegram 전송 실패를 **3.5/3.6 경계에 compose**해 채널별 `DeliveryLog(status, error_code=TELEGRAM_FAILURE)`로 남기며(직접 로깅 재구현 없음), **같은 `chat_id + topic_id` 조합을 둘 이상의 활성(ACTIVE) Telegram 채널이 공유하지 않도록 검출하는 순수 정책 함수**를 추가하고 싶다. 단, 이 모든 것은 **순수 additive·동기·결정적·런타임 미배선**(2.5/2.6/3.1~3.6 토대 제약 계승)으로, `src/rider_crawl/`·3.1~3.6 기존 코드·`deliver_once`/`build_dedup_key`(3.5)·`DispatchFanoutService`/`DispatchJob`(3.4)·`DeliveryFailurePolicy`/`attempt_delivery`(3.6)·기존 도메인 모델·enum(`Messenger`·`MessengerChannel`·`DeliveryStatus`·`FailureCategory`)은 **한 줄도 바꾸지 않고**(재사용·compose·import만), 신규 서비스 **`CentralTelegramSender`**(채널 라우팅 + 토큰 resolver 주입 + transport(`urlopen`) 주입 + 단일 시도 send-only 어댑터 → 3.4/3.6 `send` 콜백 seam 제공) + 신규 값 객체 **`TelegramRoute`**(전송 scope 식별 = `(chat_id, thread_id)`) + 신규 순수 정책 **`find_telegram_topic_collisions`/`assert_unique_telegram_topics`**(+ `TelegramTopicCollisionError`) + `services/__init__.py` 재노출 갱신 + 신규 테스트만 추가한다,

so that Telegram 전송이 **중앙 send-only 경로**로 처리되어 같은 bot token을 여러 프로세스가 동시에 polling하는 큐 경합 구조가 신규 경로에 생기지 않고(FR-24·ADD-11), `chat_id + topic_id`가 전송 scope에 포함되어 다른 토픽/계정으로 오발송되지 않으며(NFR-1), Telegram 실패가 채널별 `DeliveryLog`로 보이고(FR-26), ambiguous(전송 성공/실패 불명) 실패가 **재전송으로 이어지지 않으며**(오발송보다 미발송 — project-context 87·94), 활성 Telegram 채널 간 `chat_id + topic_id` 충돌이 사전 검출되고(project-context 91), 이 중앙 전송 seam 위에 **Story 3.8 dry-run/cutover**·**Epic 5 Telegram webhook(`/register`·secret header)·async dispatcher·실제 DeliveryLog 영속·런타임 배선**·**legacy poller 물리 제거(cutover)**가 additive로 빌드된다(FR-24, FR-26, NFR-1·5, ADD-11).

> **이 스토리의 성격 — "중앙 send-only Telegram 어댑터를 정의하고, 전송 scope(chat_id+topic)를 식별하고, 활성 토픽 충돌을 검출한다. 그것만."** 3.4가 한 Message를 채널별 `DispatchJob`으로 fan-out하며 `dispatch_all(send=...)`을 **주입 seam**으로 비워뒀고("중앙 Telegram(3.7)/Kakao Agent(Epic 4)/실 sender 배선은 호출부 책임이며 본 서비스는 기본 adapter를 두지 않는다" — `dispatch_fanout_service.py` 14-15·139-140행), 3.5가 단일 job 전송에 5필드 dedup key + insert-then-send(`deliver_once`)를 끼웠고, 3.6이 `deliver_once`를 try/except로 compose하는 `attempt_delivery(send=..., classify=...)`로 실패-분류·재시도·release를 얹으며 "Telegram 중앙 sendMessage/webhook=3.7. `attempt_delivery`의 `send`/`classify`는 **주입 콜백** — 중앙/per-Agent 경로 선택·실 sender는 호출부(3.7/Epic 5) 책임"이라 못 박았다. 본 스토리는 정확히 그 **`send` 콜백의 Telegram 실체(중앙 send-only)**·**전송 scope 식별자**·**활성 토픽 충돌 검출**을 채운다. [Source: epics.md Epic 3(511-513)·Story 3.7(649-669)·FR-24(64)·FR-26(66), architecture.md(192-195·433-434·507·525), implementation-contract.md(10), src/rider_server/services/dispatch_fanout_service.py(14-15·138-140), src/rider_server/services/delivery_failure_policy.py(`attempt_delivery`/3.6 위임)]
>
> **"중앙(central)"·"getUpdates polling 제거"의 정확한 의미(놓치기 쉬움 — AC1).** 에픽 AC1은 "Telegram 전송이 중앙 경로로 처리되고 Agent별 getUpdates polling이 제거되며 같은 bot token을 여러 프로세스에서 동시에 polling하는 **구조가 만들어지지 않는다**"이다. "구조가 만들어지지 않는다"가 핵심 — 본 스토리는 **(a) 신규 중앙 경로를 send-only로 도입**(어떤 `getUpdates`/polling 루프도 `rider_server`에 추가하지 않음)하고 **(b) 같은 bot token을 여러 프로세스에서 동시에 polling하는 신규 구조를 만들지 않음**을 **구조적으로 보장**한다. **legacy `rider_crawl` 수신 폴러(`TelegramUpdatePoller`·`get_telegram_updates`·`telegram_commands.py`)는 UI legacy 호환 경로라 보존·무변경**한다(물리 제거 금지) — 런타임에서 legacy 폴러를 실제로 끄는 **cutover는 Story 3.8(dry-run/승인)·Epic 5(webhook 배선) 소유**다. 인바운드 업데이트의 중앙 수신(webhook + secret header + `/register`)도 **Epic 5(FastAPI `api/telegram_webhook.py`, P4-06)** 다 — 본 스토리는 **outbound 전송(sendMessage)** 의 중앙 어댑터만 정의한다. [Source: epics.md AC(657-660)·FR-24 매핑(177 "채널 등록 UI/`/register`는 Epic 5"), architecture.md(192-193·438·478), implementation-contract.md(75 P4-06), project-context.md(48·90), src/rider_crawl/telegram_commands.py(327 `TelegramUpdatePoller`), src/rider_crawl/sender.py(119 `get_telegram_updates`)]
>
> **엄격한 범위 경계(스코프 크립 방지 — 가장 중요).** 본 스토리는 **순수 additive**다: 신규 `services/telegram_central_dispatch.py`(`CentralTelegramSender`·`TelegramRoute`·`find_telegram_topic_collisions`·`assert_unique_telegram_topics`·`TelegramTopicCollisionError`) + `services/__init__.py` 재노출 갱신 + 신규 테스트 `tests/server/test_telegram_central_dispatch.py`. 아래는 **다른 스토리/에픽 소유 — 절대 손대지 않는다:**
> - **`src/rider_crawl/` 전부 — 0줄 변경(가장 중요).** `sender.py`(`send_telegram_text`·`get_telegram_updates`·`_telegram_api_request`·error 헬퍼)·`telegram_commands.py`(`TelegramUpdatePoller`·`TelegramCommandProcessor`)·`messengers/telegram.py`·`config.py`·`redaction.py` 어떤 파일도 수정하지 않는다. **import해서 재사용만** 한다(`send_telegram_text`·`redact`·`AppConfig`). **이유 1(의존성 방향 — 절대 규칙):** `rider_server → rider_crawl` import만 허용, 역방향 금지(project-context.md 64, architecture.md 482·484, `test_run_once_split.py::test_rider_crawl_never_imports_rider_server`). **이유 2(legacy 폴러/명령 소유권):** legacy 폴러·명령 처리는 UI legacy 호환 경로로 계속 동작해야 하므로 본 스토리가 끄거나 갈아끼우지 않는다(cutover=3.8/Epic 5). [Source: project-context.md(48·64), src/rider_crawl/sender.py, src/rider_crawl/telegram_commands.py]
> - **`DispatchFanoutService`/`DispatchJob`/`FanoutOutcome`(3.4) 무변경.** `DispatchJob`을 import·소비만(채널 식별 `channel_id`·`messenger`·dedup 4차원 읽기). fan-out 채널 격리 루프(`dispatch_all`)는 3.4 소유 — 본 스토리는 **그 루프에 주입할 `send` 콜백의 Telegram 실체**만 제공한다(`dispatch_all`을 재구현/재정의하지 않음). [Source: src/rider_server/services/dispatch_fanout_service.py(48-80·127-152)]
> - **`IdempotentDeliveryService.deliver_once`/`build_dedup_key`(3.5)·`DeliveryFailurePolicy`/`attempt_delivery`(3.6) 본문·시그니처 무변경.** 본 스토리는 이들을 **import·compose(호출)** 만 한다 — `CentralTelegramSender.send`는 `attempt_delivery(send=...)`/`dispatch_all(send=...)`에 넘길 `(job, text) -> None` 콜백이다. dedup·release·재시도 결정·error_code 분류는 3.5/3.6이 그대로 처리한다(본 스토리는 그 위에 Telegram 전송 실체와 ambiguous-안전 신호만 얹음). [Source: src/rider_server/services/idempotency.py(83-142), src/rider_server/services/delivery_failure_policy.py(`attempt_delivery`)]
> - **`DeliveryLog`(3.5) 무변경 + 직접 로깅 재구현 금지.** Telegram 실패의 채널별 `DeliveryLog`(AC2)는 **3.6 `attempt_delivery`가 생성**한다(`status`/`error_code=TELEGRAM_FAILURE`). 본 스토리는 `DeliveryLog`를 직접 만들지 않고, `CentralTelegramSender.send`가 **실패 시 예외를 raise**해 3.6 경계가 분류·기록하게 한다(compose). [Source: src/rider_server/domain/delivery_log.py(29-37), src/rider_server/services/delivery_failure_policy.py]
> - **도메인 enum/모델 무변경(3.6보다 더 additive).** `Messenger.TELEGRAM`·`MessengerChannel`(`telegram_chat_id`/`thread_id`)·`DeliveryStatus`(5멤버)·`FailureCategory`(7멤버, `TELEGRAM_FAILURE` 포함)는 **이미 존재**(2.5·3.6) — 본 스토리는 enum/모델을 추가하지 않는다. 따라서 `domain/states.py`·`domain/__init__.py`·`test_domain_models.py`·`test_domain_states.py`의 **멤버-개수 lock을 건드리지 않는다**(3.6과 달리 lock 갱신 0). `TelegramRoute`는 domain 레코드가 아니라 **services-레이어 값 객체**(`DispatchJob`/`FanoutOutcome`/`GateDecision`/`RetryDecision` 선례)라 `domain/`에 추가하지 않는다. [Source: src/rider_server/domain/states.py(74-80·138-159), src/rider_server/domain/messenger_channel.py(16-24), src/rider_server/domain/__init__.py]
> - **Telegram webhook(인바운드)·`/register`·secret header 검증·async dispatcher** → **Epic 5(P4-06).** 본 스토리는 **outbound sendMessage 어댑터**만. `dispatch/telegram_dispatcher.py`(architecture 433-434, async 중앙 webhook/sendMessage)는 Epic 5 FastAPI 배선 시점의 집이고, 본 스토리는 동기 순수 코드라 3.1~3.6 선례대로 `services/`에 둔다(Project Structure Notes 참조). [Source: architecture.md(192-193·433-434·438·478), epics.md FR-24 매핑(177), implementation-contract.md(75·84)]
> - **dry-run 비교·cutover·rollback** → **Story 3.8(FR-3, NFR-24·25).** old/new 동시 실전송 방지·legacy 경로 물리 비활성화는 3.8. 본 스토리는 신규 중앙 어댑터 **정의**만(실발송 배선·legacy 폴러 종료 안 함). [Source: epics.md Story 3.8(671-693)]
> - **채널 등록/검증/활성화 UI·실제 token 영속·DB UNIQUE(chat_id+topic)·scheduler circuit breaker/jitter** → **Epic 5(FR-29·FR-33·5.4).** 본 스토리의 `assert_unique_telegram_topics`는 **순수 검출 함수**(런타임 enforcement·DB 제약 아님) — 실제 등록 시점 강제는 Epic 5. [Source: epics.md FR-29 매핑(182)·Story 5.4(980-1000)·Story 5.5(1002-1022)]
>
> **순수·결정적·동기·의존성 0(2.5/2.6/3.1~3.6 토대 제약 계승).** `CentralTelegramSender`·`TelegramRoute`·충돌 검출 함수는 FastAPI/SQLAlchemy/async 의존이 0인 순수 동기 파이썬이다. **내부에서 `datetime.now()`/`uuid4()`/`random`/`time.sleep` 실호출을 하지 않는다** — bot token은 **`resolve_token` 콜백 주입**(secret store에서), HTTP transport는 **`urlopen`(또는 동등 transport) 주입**(in-memory fake로 테스트), 재시도는 **단일 시도(`retry_attempts=1`)** 로 두고 backoff/재시도는 **3.6 `DeliveryFailurePolicy`가 소유**(이중 재시도 금지). [Source: project-context.md(35), src/rider_crawl/sender.py(80-116), src/rider_server/services/delivery_failure_policy.py]
>
> **secret/식별자 비노출(NFR-5, project-context 81).** bot token은 `resolve_token` 주입 seam으로만 들어오고 어떤 로그/예외/breadcrumb에도 평문으로 남기지 않는다. `telegram_chat_id`/`thread_id`는 **라우팅 식별자라 secret이 아니다**(2.4/2.5 결정 계승 — ref화 금지, `messenger_channel.py` 4-6행) — 하지만 NFR-5는 chat ID/topic ID도 redaction 대상으로 명시하므로, 본 스토리가 만드는 **로그/예외 breadcrumb**는 `redact()`를 통과한다(3.4 `FanoutOutcome.error_redacted` 선례). 테스트 fixture·예외 메시지에 실제 봇 토큰/chat_id 숫자/전화/이메일 원문을 넣지 않는다(가짜 token 문자열·가짜 chat_id만). [Source: project-context.md(81), epics.md NFR-5(90), src/rider_server/domain/messenger_channel.py(4-6), src/rider_crawl/redaction.py, src/rider_server/services/dispatch_fanout_service.py(35·150)]

## Acceptance Criteria

**AC1 — 중앙 send-only Telegram 경로 + 신규 polling 구조 미생성 (FR-24, ADD-11, implementation-contract Reuse)**

1. **Given** 기존 Telegram sender(`rider_crawl.sender.send_telegram_text`)가 동작할 때 **When** 중앙(central) 전송 어댑터 `CentralTelegramSender`를 도입하면 **Then** Telegram outbound 전송이 **단일 중앙 경로**로 처리되고, 그 경로는 주입된 `MessengerChannel`(`telegram_chat_id`/`thread_id`) 라우팅 + 주입된 bot token + 주입된 HTTP transport(`urlopen`)로 `send_telegram_text`를 **재사용**해 `sendMessage`만 수행한다(legacy 호환 동작·Bot API quirk 보존). [Source: epics.md AC(657-659), implementation-contract.md(10), src/rider_crawl/sender.py(80-116)]
2. **And** 신규 중앙 경로에는 **어떤 `getUpdates`/수신 polling 루프도 추가되지 않는다**(send-only) — `CentralTelegramSender`는 `rider_crawl.sender.get_telegram_updates`나 `TelegramUpdatePoller`를 호출/생성하지 않으며, 같은 bot token을 여러 프로세스에서 동시에 polling하는 **신규 구조를 만들지 않는다**. legacy `rider_crawl` 폴러(`TelegramUpdatePoller`·`get_telegram_updates`·`telegram_commands.py`)는 **무변경·보존**(물리 제거·런타임 종료는 cutover=3.8/Epic 5). 인바운드 webhook/`/register`/secret header는 Epic 5(P4-06) — 본 스토리 범위 밖. [Source: epics.md AC(659-660), architecture.md(192-193·438), implementation-contract.md(75), project-context.md(48·90)]

**AC2 — 전송 scope = chat_id + topic_id, 채널별 DeliveryLog, ambiguous-안전 (FR-24, FR-26, NFR-1)**

3. **Given** 전송 대상 scope를 식별해야 할 때 **When** Telegram 전송을 라우팅/기록하면 **Then** `chat_id`와 `topic_id(message_thread_id = MessengerChannel.thread_id)` 조합이 전송 대상 scope에 포함된다 — `TelegramRoute(chat_id, thread_id)`(frozen 값 객체, `from_channel(channel)` 도출)가 그 scope 식별자이고, `CentralTelegramSender.send`는 이 route로만 `sendMessage`를 보낸다(다른 chat/topic 오발송 금지, fail-closed). dedup/scope 비축소는 3.5 dedup key의 `messenger_channel_id` 차원이 이미 (chat_id, thread_id)를 1:1로 묶어 보장한다(채널 = (chat_id, thread_id) 1쌍 — `MessengerChannel` 구조). [Source: epics.md AC(662-664)·FR-24(64), src/rider_server/domain/messenger_channel.py(16-24), src/rider_server/services/idempotency.py(build_dedup_key 5필드), project-context.md(92)]
4. **And** Telegram 전송 실패는 **채널별 `DeliveryLog`** 로 기록된다 — `CentralTelegramSender.send`는 실패 시 `TelegramSendError`(legacy)·또는 명시 예외를 **raise**하고, 이를 **3.6 `DeliveryFailurePolicy.attempt_delivery(send=CentralTelegramSender.send, classify=...)`** 에 compose하면 채널별 `DeliveryLog(channel_id=job.channel_id, status∈{FAILED,RETRYING}, error_code=FailureCategory.TELEGRAM_FAILURE.value)`가 생성된다(본 스토리는 `DeliveryLog`를 **직접 만들지 않고** 3.5/3.6 경계에 위임). `classify`는 3.6 `DeliveryFailurePolicy.channel_failure_category(Messenger.TELEGRAM)`(=`TELEGRAM_FAILURE`)를 재사용한다. [Source: epics.md AC(665)·FR-26(66), src/rider_server/services/delivery_failure_policy.py(`attempt_delivery`·`channel_failure_category`), src/rider_server/domain/delivery_log.py(29-37)]
5. **And** **ambiguous(전송 성공/실패 불명) 실패는 재전송으로 이어지지 않는다** — legacy `send_telegram_text`는 POST 후 응답을 못 읽거나 연결이 끊긴 경우 `TelegramSendError(ambiguous=True)`(메시지가 이미 전달됐을 수 있음)를 낸다. 이 경우 본 스토리는 **미발송 dedup key를 release하지 않도록**(따라서 재시도/재전송하지 않도록) 신호한다 — 오발송(중복 발송)보다 미발송이 안전하다는 원칙(project-context 87·94, NFR-1 fail-closed). 즉 ambiguous 실패는 3.5 insert-then-send로 선확보된 key를 풀지 않아 다음 라운드 reserve 충돌→`DUPLICATE_BLOCKED`로 중복 전송이 차단된다. (definite=명확한 HTTP 4xx/검증 실패는 재시도 가능 `TELEGRAM_FAILURE`로 release/재시도.) [Source: src/rider_crawl/sender.py(165-185 ambiguous), project-context.md(87·94), epics.md NFR-1, src/rider_server/services/idempotency.py(120-133), src/rider_server/services/delivery_failure_policy.py(release=should_retry일 때만)]

**AC3 — 활성 Telegram 채널 간 chat_id + topic_id 충돌 검출 (project-context 91)**

6. **Given** 활성 Telegram 대상끼리 충돌하면 안 될 때 **When** `find_telegram_topic_collisions(channels)`/`assert_unique_telegram_topics(channels)`로 채널 집합을 검사하면 **Then** 둘 이상의 **활성(`MessengerChannelState.ACTIVE`) Telegram** `MessengerChannel`이 같은 `(telegram_chat_id, thread_id)`(= `TelegramRoute`) 조합을 공유하면 충돌로 검출되고(`find_...`는 충돌 그룹 반환, `assert_...`는 `TelegramTopicCollisionError` raise, fail-closed), 비활성(PENDING/VERIFIED/INACTIVE)·Kakao 채널·서로 다른 (chat,topic) 조합은 충돌로 오검출되지 않는다(`thread_id=None`도 정상 키로 취급 — `None` vs `""` 정규화 일관). 본 스토리는 **순수 검출 함수**까지이며, 등록 시점 실제 강제·DB UNIQUE는 Epic 5(FR-29) 소유다. [Source: epics.md AC(667-669), project-context.md(91), src/rider_server/domain/states.py(105-111 `MessengerChannelState`), src/rider_crawl/telegram_commands.py(155-157 `locks_by_target` (chat_id,thread_id) 키 선례)]

**AC4 — legacy sender 재사용(Bot API quirk 보존)·단일 시도·결정성·비노출 (FR-2, NFR-5, 토대 제약)**

7. **And** `CentralTelegramSender`는 **`rider_crawl.sender.send_telegram_text`를 재사용**해 Bot API 의미론(슈퍼그룹 `migrate_to_chat_id` 전환·`retry-after`·`ok!=true` 에러·JSON 검증·ambiguous 표시)을 **재구현하지 않는다**(implementation-contract "Keep Telegram sender; move to central flow"). 중앙 경로는 **단일 시도**(`retry_attempts=1`)로 호출해 legacy 내부 재시도와 3.6 backoff 재시도의 **이중 재시도를 피한다**(재시도/backoff 소유권 = 3.6). 내부 `now()`/`uuid4()`/`random`/실제 `time.sleep` 미호출(transport·token resolver 주입·결정적). bot token은 `resolve_token` 주입으로만, 어떤 로그/예외 breadcrumb도 `redact()` 통과(평문 token·chat_id 숫자 비노출). [Source: implementation-contract.md(10), src/rider_crawl/sender.py(80-116·227-241·266-285), project-context.md(35·81), src/rider_crawl/redaction.py]

**AC5 — 순수 additive·무회귀·런타임 미배선 (FR-2, NFR-20, 토대 제약)**

8. **And** `src/rider_crawl/`·`pyproject.toml`·3.1 `dispatch_service.py`/`crawl_service.py`·3.2 `snapshot_normalizer.py`·3.3 `message_render_service.py`·3.4 `dispatch_fanout_service.py`·3.5 `idempotency.py`·3.6 `delivery_failure_policy.py`·2.6 `subscription_gate.py`·기존 도메인 모델·enum(`states.py` 포함 — 멤버 무변경) **0줄 변경**(`git diff -w --stat`; 신규 `services/telegram_central_dispatch.py`·`tests/server/test_telegram_central_dispatch.py` + `services/__init__.py` 재노출 additive만 예외)으로 기존 회귀 그물(`tests/server/test_*`·`tests/test_app.py`·`tests/test_sender.py`·`tests/test_telegram_sender.py`·`tests/test_telegram_commands.py`)이 **전부 통과**한다. 의존성은 **단방향**(`rider_server → rider_crawl`, `test_rider_crawl_never_imports_rider_server` 통과)이고, 신규 코드·테스트에 평문 secret 0건이다. 3.6과 달리 **도메인 enum/모델 lock 갱신 0**(추가 enum/모델 없음). [Source: project-context.md(58·64·82), src/rider_server/services/__init__.py, tests/server/test_run_once_split.py(431)]

## Tasks / Subtasks

- [x] **Task 1 — `TelegramRoute` 값 객체 + `CentralTelegramSender` 어댑터: 신규 `services/telegram_central_dispatch.py` (AC: 1, 2, 4, 5)** — 순수·결정적·동기 서비스(`DispatchFanoutService`(3.4)·`DeliveryFailurePolicy`(3.6) 패턴). import는 단방향만: `from rider_crawl.config import AppConfig`, `from rider_crawl.sender import send_telegram_text, TelegramSendError`, `from rider_crawl.redaction import redact`, `from rider_server.domain import Messenger, MessengerChannel, MessengerChannelState`, `from rider_server.services.dispatch_fanout_service import DispatchJob`, 표준 `dataclasses`/`typing`. 역방향 import 0:
  - [x] **`@dataclass(frozen=True) class TelegramRoute`**: `chat_id: str`, `thread_id: str | None = None`. `@classmethod from_channel(cls, channel: MessengerChannel) -> TelegramRoute`(channel.messenger가 TELEGRAM 아니면 `ValueError` fail-closed; `telegram_chat_id` 비어 있으면 fail-closed). 전송 scope 식별자 = (chat_id, thread_id). `GateDecision`(2.6)·`DispatchJob`(3.4) 선례처럼 services 소속 값 객체(domain 추가 아님). [Source: src/rider_server/domain/messenger_channel.py(16-24), src/rider_server/services/dispatch_fanout_service.py(48-66)]
  - [x] **`class CentralTelegramSender`**(frozen dataclass 또는 정적 구성 — 주입 의존만 보유): 필드/주입 = `channels: Mapping[str, MessengerChannel]`(channel_id → 조회), `resolve_token: Callable[[MessengerChannel], str]`(secret store seam — bot token), `urlopen`(transport seam — `send_telegram_text(urlopen=...)`에 위임), 선택 `timeout_seconds`. **`send(self, job: DispatchJob, text: str) -> None`**: (1) `channel = self.channels[job.channel_id]`(없으면 fail-closed — `DispatchFanoutService.UnknownChannelError` 재사용 또는 동등 KeyError 계열), (2) `channel.messenger != Messenger.TELEGRAM`이면 fail-closed(`ValueError`), (3) `route = TelegramRoute.from_channel(channel)`, (4) `token = self.resolve_token(channel)`, (5) **per-call `AppConfig` carrier 구성**(token/chat_id만 의미값, 나머지 send 무관 필드는 안전 placeholder) — Task 2 헬퍼, (6) `send_telegram_text(config, text, message_thread_id=int(route.thread_id) if route.thread_id else None, urlopen=self.urlopen, retry_attempts=1, sleep=lambda *_: None)` 호출. 실패 시 `TelegramSendError` 전파(분류·로깅은 3.6). **getUpdates/polling 미호출(send-only)**. 내부 `now()`/`uuid4()`/`random`/실 sleep 미호출. [Source: src/rider_crawl/sender.py(80-116), src/rider_server/services/dispatch_fanout_service.py(39-45·127-152), AC1·AC2·AC4]
  - [x] **`as_send_callback(self) -> Callable[[DispatchJob, str], None]`**: `self.send` 바운드 메서드(또는 클로저)를 반환해 `DispatchFanoutService.dispatch_all(send=...)`·`DeliveryFailurePolicy.attempt_delivery(send=...)` seam에 그대로 꽂히게 한다(`(job, text) -> None` 시그니처 일치). [Source: src/rider_server/services/dispatch_fanout_service.py(132·146), src/rider_server/services/delivery_failure_policy.py(`attempt_delivery` send 주입)]
- [x] **Task 2 — legacy `send_telegram_text` 재사용 어댑팅: per-channel `AppConfig` carrier 헬퍼 (AC: 1, 4, 5)** — `rider_crawl` 무변경 재사용:
  - [x] **`_app_config_for(channel, token) -> AppConfig` (모듈-프라이빗 헬퍼)**: `AppConfig`의 send 관련 필드(`telegram_bot_token=token`, `telegram_chat_id=channel.telegram_chat_id`, `telegram_message_thread_id`는 `send_telegram_text(message_thread_id=...)` 인자로 넘기므로 빈 문자열 가능)만 의미값으로, 나머지 12개 required 필드(`coupang_eats_url`·`baemin_center_name`·`baemin_center_id`·`browser_mode`·`cdp_url`·`headless`·`kakao_chat_name`·`send_enabled`·`send_only_on_change`·`timezone`·`run_lock_timeout_seconds`·`page_timeout_seconds`)는 **send에 무관한 안전 placeholder**(빈 문자열/False/0)로 채운다. `send_enabled`는 send_telegram_text가 참조하지 않으므로(전송 게이트는 호출부 책임) 무관. **재사용 anchor:** `tests/test_sender.py:676`·`tests/test_app.py:328`의 AppConfig 생성 패턴을 참고하되 제품 코드용 헬퍼로 둔다. [Source: src/rider_crawl/config.py(36-54), src/rider_crawl/sender.py(90-100), tests/test_sender.py(676)]
  - [x] **재사용 경계 주석**: "Bot API quirk(슈퍼그룹 migrate·retry-after·ambiguous·ok!=true)는 `send_telegram_text`가 소유 — 여기서 재구현 금지. `retry_attempts=1`로 단일 시도(재시도/backoff=3.6). transport(`urlopen`)·token(`resolve_token`) 주입으로 결정적·secret 비노출." 1단락. [Source: implementation-contract.md(10), src/rider_crawl/sender.py(102-116·227-241·266-285)]
- [x] **Task 3 — ambiguous-안전 분류 헬퍼: `services/telegram_central_dispatch.py` (AC: 2.5)** — 오발송보다 미발송:
  - [x] **`is_ambiguous_send_failure(exc: Exception) -> bool` (staticmethod/함수)**: `isinstance(exc, TelegramSendError) and getattr(exc, "ambiguous", False)` → True. ambiguous 실패는 메시지가 이미 전달됐을 수 있어 **재전송하면 중복 발송 위험**이므로, 호출부(또는 3.6 compose 시 release 결정)가 이 실패에 대해 **dedup key를 release하지 않도록**(재시도 안 함) 한다. definite 실패(명확한 HTTP 4xx/검증)는 재시도 가능. **본 스토리는 ambiguity 판정 헬퍼 + 안전 기본값(release 안 함) 제공**까지이고, 실제 release/재시도 wiring은 3.6 `attempt_delivery`(release=should_retry일 때만)와 Epic 5가 한다. 테스트로 "ambiguous → 재전송/ release 없음" 경로를 in-memory seam으로 단언. [Source: src/rider_crawl/sender.py(165-185), project-context.md(87·94), src/rider_server/services/idempotency.py(120-133), src/rider_server/services/delivery_failure_policy.py]
  - [x] **classify 재사용 주석**: Telegram 전송 예외 → `FailureCategory`는 **3.6 `DeliveryFailurePolicy.channel_failure_category(Messenger.TELEGRAM)`** (=`TELEGRAM_FAILURE`)를 재사용(신규 매핑 미추가). ambiguous는 "재시도 안 함"이라는 release-결정 레이어에서 처리(category는 여전히 TELEGRAM_FAILURE이되 release하지 않음). [Source: src/rider_server/services/delivery_failure_policy.py(`channel_failure_category`)]
- [x] **Task 4 — 활성 토픽 충돌 검출: `services/telegram_central_dispatch.py` (AC: 3)** — 순수 정책 함수:
  - [x] **`class TelegramTopicCollisionError(ValueError)`**: 같은 `(chat_id, thread_id)`를 여러 활성 채널이 공유할 때(설정 무결성 버그 → surface, fail-closed). [Source: src/rider_server/services/dispatch_fanout_service.py(39-45 `UnknownChannelError` 선례)]
  - [x] **`find_telegram_topic_collisions(channels: Iterable[MessengerChannel]) -> list[tuple[TelegramRoute, list[MessengerChannel]]]`**: `messenger==TELEGRAM and state==MessengerChannelState.ACTIVE`만 대상, `TelegramRoute(chat_id, normalized thread_id)`로 그룹핑해 **2개 이상** 묶인 그룹만 반환(결정적·입력 순서 보존). `thread_id`는 `None`↔`""` 정규화 일관(legacy `_normalize_thread_id` 의미 — None/빈문자 동일 취급)으로 오검출 방지. [Source: project-context.md(91), src/rider_crawl/telegram_commands.py(155-157), src/rider_server/domain/states.py(105-111)]
  - [x] **`assert_unique_telegram_topics(channels) -> None`**: `find_...` 결과가 비어 있지 않으면 `TelegramTopicCollisionError`(메시지는 `redact()` 통과 — chat_id 숫자 비노출). [Source: project-context.md(81·91)]
- [x] **Task 5 — 서비스 재노출 갱신: `services/__init__.py` (AC: 1~5)** — additive only:
  - [x] `from .telegram_central_dispatch import (CentralTelegramSender, TelegramRoute, TelegramTopicCollisionError, find_telegram_topic_collisions, assert_unique_telegram_topics)` 추가, `__all__`에 다섯 심볼 additive(기존 2.6/3.1~3.6 심볼 무삭제). docstring에 "Story 3.7(FR-24·FR-26, ADD-11)이 `CentralTelegramSender`(중앙 send-only Telegram 어댑터 — legacy `send_telegram_text` 재사용·transport/token 주입·`dispatch_all`/`attempt_delivery` send seam 제공)·`TelegramRoute`(전송 scope=(chat_id,thread_id))·활성 토픽 충돌 검출을 additive로 추가 — 인바운드 webhook/`/register`·async dispatcher·실제 영속은 Epic 5" 1단락 보강. [Source: src/rider_server/services/__init__.py(1-59)]
- [x] **Task 6 — 테스트 추가: 신규 `tests/server/test_telegram_central_dispatch.py` (AC: 1~8)** — 외부 호출 없음(fake urlopen/in-memory seam), 가짜 token·chat_id만. 평면 `tests/server/`(`__init__.py` 미추가). `tests/test_telegram_sender.py`(urlopen fake)·`tests/server/test_idempotency.py`(`_Seam` reserve/send/release)·`tests/server/test_dispatch_fanout.py`(DispatchJob fixture) 패턴 재사용:
  - [x] **(AC1 — 중앙 send-only):** fake `urlopen`(`sendMessage` 호출 캡처)·fake `resolve_token`으로 `CentralTelegramSender.send(job, text)` → 정확히 `sendMessage` 1회·올바른 chat_id+message_thread_id payload. **`get_telegram_updates`/`TelegramUpdatePoller` 미호출**(send-only) 단언(예: poller import 없이 동작·getUpdates fake가 안 불림). [Source: tests/test_telegram_sender.py, AC1]
  - [x] **(AC2 — scope + 채널별 DeliveryLog compose):** `TelegramRoute.from_channel`이 (chat_id, thread_id) 도출(thread_id None/값 둘 다). `CentralTelegramSender.send`를 **`DeliveryFailurePolicy.attempt_delivery(send=..., classify=channel_failure_category(TELEGRAM))`** 에 compose 시 실패 → 채널별 `DeliveryLog(channel_id=job.channel_id, status∈{FAILED,RETRYING}, error_code="TELEGRAM_FAILURE")`. 성공 시 `deliver_once`로 `SENT`(직접 로깅 재구현 없음). [Source: src/rider_server/services/delivery_failure_policy.py, AC2]
  - [x] **(AC2.5 — ambiguous 미재전송):** fake `urlopen`이 `TelegramSendError(ambiguous=True)` 유발 → `is_ambiguous_send_failure` True, 안전 경로에서 **release 미호출·재전송 0**(in-memory `_Seam`으로 2라운드 재처리 시 reserve 충돌→`DUPLICATE_BLOCKED`·send 미호출). definite 실패(ambiguous=False)는 release/재시도 경로. [Source: src/rider_crawl/sender.py(165-185), src/rider_server/services/idempotency.py(120-133), AC2.5]
  - [x] **(AC3 — 활성 토픽 충돌):** 같은 (chat_id, thread_id) 활성 Telegram 채널 2개 → `find_...` 1그룹·`assert_...` raises. 비활성(INACTIVE/PENDING)·Kakao·다른 topic·다른 chat → 충돌 아님. `thread_id=None` vs `""` 동일 취급. [Source: project-context.md(91), AC3]
  - [x] **(AC4 — 재사용·단일시도·결정·비노출):** `send_telegram_text`가 `retry_attempts=1`로 호출됨(이중 재시도 없음 — fake urlopen 호출 횟수로 단언). Bot API quirk(예: 4xx → TelegramSendError 전파)는 legacy가 처리(본 코드 재구현 없음). 같은 입력 2회 호출 동일 동작(결정적). breadcrumb/예외에 평문 token·chat_id 숫자 0(redact 통과). [Source: src/rider_crawl/sender.py(102-116), AC4]
  - [x] **(재노출·방향·frozen):** `from rider_server.services import CentralTelegramSender, TelegramRoute, find_telegram_topic_collisions, assert_unique_telegram_topics, TelegramTopicCollisionError` 동작·`__all__` 포함. `TelegramRoute` frozen(`FrozenInstanceError`). `telegram_central_dispatch.py`에 `rider_crawl→rider_server` 역import 0. [Source: project-context.md(64), src/rider_server/services/__init__.py]
- [x] **Task 7 — 회귀·범위·누출 검증 및 마무리 (AC: 1~8)**
  - [x] 운영 venv로 전체 스위트 1회: `.venv/Scripts/python.exe -m pytest -q`(WSL `python3` 금지 — pytest 미설치). 기준선(참고값 **923** — HEAD `bf6603f`(3.6 종료) 기준, **복사 금지·본인 재측정**) 대비 기존 통과가 **하나도** 안 깨지고(특히 `tests/test_telegram_sender.py`·`tests/test_telegram_commands.py`·`tests/server/test_dispatch_fanout.py`·`test_idempotency*.py`·`test_delivery_failure_policy.py`), 신규 케이스만큼만 증가가 정상(순수 additive — enum/모델 lock 갱신 0). [Source: 3-6 스토리(186·197), memory/dev-env-quirks, memory/stale-test-count-a2]
  - [x] 범위 점검: `git diff -w --stat`에 **신규 `services/telegram_central_dispatch.py`·`tests/server/test_telegram_central_dispatch.py` + `services/__init__.py`(재노출)만** 보이고 **`src/rider_crawl/`·`pyproject.toml`·3.1~3.6 services 본문·2.6 게이트·기존 도메인 모델·enum(`states.py`) 변경 0줄**임을 확인. 특히 `sender.py`/`telegram_commands.py`/`idempotency.py`/`dispatch_fanout_service.py`/`delivery_failure_policy.py` 무변경. CRLF/LF 노이즈·무관 파일은 되돌리지 않는다(`git diff -w`). [Source: project-context.md(82), memory/dev-env-quirks]
  - [x] 누출 grep + 의존성 방향 grep: 신규 코드·테스트에 평문 secret 0건(봇토큰/`chat_id=digits`/한국휴대폰/이메일), `src/rider_crawl/`에 `rider_server` import가 **새로 생기지 않았음**(`test_rider_crawl_never_imports_rider_server` 통과·`grep -rn "import rider_server" src/rider_crawl/` = 0건) 확인. [Source: project-context.md(64·81), tests/server/test_run_once_split.py(431)]
  - [x] 변경 파일을 File List에 기록하고, **리뷰 시점 재측정 pass 수치 1개만** Dev Agent Record에 적는다(A2′ — dev 노트에 잠정 수치 박지 말 것). [Source: memory/stale-test-count-a2, 3-6 스토리(91·144)]

## Dev Notes

### 범위 경계 (스코프 크립 방지 — 가장 중요)

- 본 스토리는 **순수 additive**다: 신규 `services/telegram_central_dispatch.py`·`tests/server/test_telegram_central_dispatch.py` + additive 수정 `services/__init__.py`(재노출). **`src/rider_crawl/`·`pyproject.toml` 무변경, 3.1~3.6 services 본문 무변경, 기존 도메인 모델·enum(`states.py`·`messenger_channel.py`...) 무변경.** 3.6과 달리 **enum/모델 추가가 없어** `domain/states.py`·`domain/__init__.py`·`test_domain_models.py`·`test_domain_states.py`의 lock을 **건드리지 않는다**(가장 비침습적인 Epic 3 스토리).
- **건드리지 않는다:** `rider_crawl` 전부(`sender.py`·`telegram_commands.py`·`messengers/telegram.py`·`config.py`·`redaction.py` — import·재사용만), `DispatchFanoutService`/`DispatchJob`/`FanoutOutcome`(3.4 — `dispatch_all`에 꽂을 send 콜백만 제공), `deliver_once`/`build_dedup_key`(3.5 — compose만), `DeliveryFailurePolicy`/`attempt_delivery`(3.6 — compose·`channel_failure_category` 재사용만), `DeliveryLog`(3.5 — 직접 생성 안 함, 3.6이 생성), `Messenger`/`MessengerChannel`/`DeliveryStatus`/`FailureCategory`(2.5/3.6 — 그대로 사용), legacy 폴러(`TelegramUpdatePoller`·`get_telegram_updates`), 인바운드 webhook/`/register`/secret header/async dispatcher(Epic 5·P4-06), dry-run·cutover·rollback(3.8), 채널 등록 UI·DB UNIQUE·scheduler breaker/jitter(Epic 5·5.4). [Source: epics.md Story 3.8(671-693)·FR-24 매핑(177)·FR-29 매핑(182), architecture.md(433-434·438), implementation-contract.md(75)]

### 위치 결정 — 왜 `services/telegram_central_dispatch.py`인가 (architecture `dispatch/`와의 변이 — 반드시 읽을 것)

- **architecture는 `rider_server/dispatch/telegram_dispatcher.py`(중앙 webhook/sendMessage)를 명시한다(433-434·507)**. 그러나 그 항목은 **async FastAPI 배선 시점의 집**이다 — webhook(인바운드)·secret header·`/register`는 Epic 5(P4-06)이고 async다.
- **본 스토리는 동기·순수 코드**(outbound sendMessage 어댑터)다. 3.1~3.6이 architecture의 `services/` 영역(`CrawlService`/`MessageRenderService`/`DispatchService`/`idempotency`/`SubscriptionGate`)뿐 아니라 **`dispatch/`로 매핑될 수도 있던 `DispatchFanoutService`(3.4)까지 일관되게 `services/`에 둔 선례**가 있다(architecture가 폴더를 못 박았어도 동기 서비스는 `services/`로 수렴). 따라서 본 스토리도 **`services/telegram_central_dispatch.py`** 로 두어 Epic 3 동기 서비스 레이어 일관성을 지키고, `dispatch/` 패키지(async 중앙 webhook/dispatcher)는 **Epic 5가 신설**한다(반쪽짜리 빈 패키지 선점 방지). 파일명 `telegram_central_dispatch.py`는 미래 async `dispatch/telegram_dispatcher.py`와 이름 충돌·혼동을 피한다. [Source: architecture.md(425-434·507), src/rider_server/services/dispatch_fanout_service.py(3.4가 services/에 배치), src/rider_server/services/__init__.py]
- **`TelegramRoute`/`CentralTelegramSender`/충돌 함수는 services 소속 값 객체·정책**이다 — `DispatchJob`/`FanoutOutcome`(3.4)·`GateDecision`/`RetryDecision`(2.6/3.6)이 domain이 아니라 services 값 객체였던 선례와 동형(독립 계약 테이블/도메인 레코드 아님). 그래서 `domain/__init__.py`·model-count lock 무변경. [Source: src/rider_server/services/dispatch_fanout_service.py(48-80), src/rider_server/services/subscription_gate.py(33-43)]

### "중앙 send-only" + legacy 폴러 보존의 정합 (AC1 — 가장 헷갈리는 지점)

- **"중앙(central)" = 단일 outbound 전송 경로 + 인바운드는 webhook(Epic 5).** 본 스토리는 **outbound sendMessage**의 중앙 어댑터(`CentralTelegramSender`)만 정의한다. 인바운드(사용자 명령·`/register`)의 중앙 수신 = webhook + secret header(Epic 5 `api/telegram_webhook.py`, P4-06). [Source: architecture.md(192-193·438·478), implementation-contract.md(75·84)]
- **"getUpdates polling 제거" = 신규 경로에 polling 구조를 만들지 않음 + cutover 시 legacy 폴러 종료.** legacy `rider_crawl` 폴러(`TelegramUpdatePoller`·`get_telegram_updates`·`telegram_commands.py`)는 **UI legacy 호환 경로**라 보존한다(project-context 48: "텔레그램 수신 폴러는 봇 토큰별 단일 큐라는 제약 — 같은 봇 토큰을 여러 프로세스에서 동시에 쓰는 설계를 추가하지 않는다"). 본 스토리는 그 폴러를 **삭제·수정하지 않고**, **신규 중앙 경로에 어떤 polling도 넣지 않음**으로써 "같은 bot token 다중 프로세스 polling 구조 미생성"을 보장한다. 런타임에서 legacy 폴러를 실제로 끄는 것은 **cutover(3.8 dry-run/승인 → Epic 5 webhook 전환)** 다. 즉 본 스토리의 AC1은 **"신규 경로는 send-only이고 polling을 안 만든다"** 로 충족되며, legacy 폴러 물리 제거를 요구하지 않는다. [Source: epics.md AC(657-660), project-context.md(48·90), src/rider_crawl/telegram_commands.py(327), src/rider_crawl/sender.py(119)]

### legacy `send_telegram_text` 재사용 정본 (AC4 — 무엇을 재사용하고 무엇을 재구현하지 않나)

- **재사용(그대로 위임):** `send_telegram_text(config, text, *, message_thread_id, urlopen, timeout_seconds, retry_attempts, sleep)`. 이 함수가 이미 보유한 **Bot API quirk를 절대 재구현하지 않는다** — 슈퍼그룹 전환 시 `migrate_to_chat_id` 안내(227-241), `retry-after` 백오프(266-285), `ok!=true` 에러 매핑(192-194·204-245), JSON 검증(187-194), **ambiguous 표시**(165-185: POST 후 응답 못 읽음/연결 끊김 → 메시지 전달됐을 수 있음 → fast-retry 금지 신호). [Source: src/rider_crawl/sender.py(80-245·266-285)]
- **주입으로 결정성·secret 비노출 확보:** `urlopen`=transport seam(테스트 fake), `sleep=lambda *_: None`(실 sleep 금지 — 어차피 단일 시도라 안 불림), `retry_attempts=1`(**이중 재시도 금지** — 재시도/backoff는 3.6 `DeliveryFailurePolicy` 소유). bot token은 `resolve_token(channel)` 주입(secret store seam) — `AppConfig.telegram_bot_token`에 담겨 `send_telegram_text` 내부에서만 쓰이고 로그/예외엔 안 남는다(legacy도 token을 메시지에 안 박음). [Source: src/rider_crawl/sender.py(85-88·102-116·288-310), project-context.md(35·81)]
- **per-call `AppConfig` carrier:** `AppConfig`는 12개 required 필드를 가진 frozen dataclass다(`config.py` 36-54). `send_telegram_text`는 그중 `telegram_bot_token`/`telegram_chat_id`(+옵션 `telegram_message_thread_id`)만 읽으므로(90-100), carrier는 그 셋만 의미값으로 채우고 나머지는 send 무관 placeholder로 둔다. 생성 패턴은 `tests/test_sender.py:676`/`tests/test_app.py:328` 참고(단, 제품 코드 헬퍼). thread_id는 `send_telegram_text(message_thread_id=int(...))` 인자로 넘기는 편이 명확하다(`MessengerChannel.thread_id`는 str). [Source: src/rider_crawl/config.py(36-54), src/rider_crawl/sender.py(90-100·302-310), tests/test_sender.py(676)]

### ambiguous 전송 실패와 중복 발송 방지 (AC2.5 — 놓치기 쉬운 disaster 방지)

- **위험:** Telegram POST가 나갔는데 응답을 못 받으면(네트워크 끊김) 메시지가 **이미 전달됐을 수 있다**(legacy가 `ambiguous=True`로 표시). 이때 3.5/3.6 경계가 "실패 → release(key) → 재시도"로 처리하면 **같은 실적 메시지가 두 번 발송**된다(disaster). 운영 원칙은 **오발송(중복)보다 미발송**(project-context 87·94, NFR-1 fail-closed)이다.
- **안전 처리:** ambiguous 실패는 **dedup key를 release하지 않는다** → 3.5 insert-then-send로 선확보된 key가 유지되어, 다음 라운드 reserve 충돌→`DUPLICATE_BLOCKED`(send 미호출)로 **재전송이 구조적으로 차단**된다. 운영자는 `DeliveryLog`에서 미확정 상태를 보고 수동 판단한다. 본 스토리는 `is_ambiguous_send_failure(exc)` 판정 + "ambiguous → release 안 함" 안전 기본값을 제공한다 — 실제 release 결정은 3.6 `attempt_delivery`(`release`는 `decision.should_retry`일 때만)에 ambiguity를 반영해 wiring하거나(호출부), 가장 보수적으로 ambiguous를 비-release 경로로 둔다. definite 실패(명확한 4xx/검증)는 재시도 가능(`TELEGRAM_FAILURE` release/재시도). [Source: src/rider_crawl/sender.py(165-185), src/rider_server/services/idempotency.py(96-107·120-133), src/rider_server/services/delivery_failure_policy.py(release=should_retry), project-context.md(87·94)]

### 전송 scope·토픽 충돌과 3.5 dedup의 관계 (AC2·AC3)

- **scope = (chat_id, thread_id)는 채널 식별자에 이미 1:1.** `MessengerChannel`은 Telegram이면 `(telegram_chat_id, thread_id)` 1쌍을 표현한다(`messenger_channel.py` 16-24). 3.5 dedup key의 `messenger_channel_id` 차원이 이 (chat,topic)을 묶으므로 **scope 비축소**(다른 토픽/탭 중복 판단 혼선 방지)는 3.5가 이미 보장한다(project-context 92 "scope key를 줄이면 다른 탭/계정의 중복 판단이 섞일 수 있다"). 본 스토리 `TelegramRoute`는 그 scope를 **명시적 값 객체**로 표면화해 라우팅·충돌 검출에 쓴다(dedup key 자체는 3.5 소유 — 재조립·비교 안 함). [Source: src/rider_server/domain/messenger_channel.py(16-24), src/rider_server/services/idempotency.py(build_dedup_key), project-context.md(92)]
- **충돌 검출은 활성(ACTIVE)만·정규화 일관.** legacy가 라우팅 lock을 `(normalize_chat_id, normalize_thread_id)` 키로 잡은 선례(`telegram_commands.py` 155-157)와 동형 — 활성 채널만 대상으로 `(chat_id, thread_id)` 그룹핑, `thread_id` None↔"" 동일 취급(오검출 방지). 비활성·Kakao·다른 조합은 충돌 아님. 등록 시점 실제 강제는 Epic 5(FR-29). [Source: src/rider_crawl/telegram_commands.py(155-157), src/rider_server/domain/states.py(105-111), project-context.md(91), epics.md FR-29 매핑(182)]

### 3.8/Epic 5/Epic 4와의 경계 — 본 스토리가 하지 않는 것

- **3.8(dry-run/cutover — 안 함):** 실발송 없는 dry-run, old/new 동시 실전송 방지, legacy 경로 물리 비활성화·rollback은 3.8(FR-3·NFR-24·25). 본 스토리는 중앙 어댑터 **정의**만(실발송 배선·legacy 폴러 종료 안 함). [Source: epics.md Story 3.8(671-693)]
- **Epic 5(webhook·`/register`·영속·async·등록 UI — 안 함):** 인바운드 webhook + secret header(`api/telegram_webhook.py`, P4-06), `/register <code>` 자동 등록(chat_id+thread 저장), async dispatcher(`dispatch/telegram_dispatcher.py`), 실제 `DeliveryLog` 영속·DB UNIQUE(chat_id+topic), scheduler 연동·`telegram_send_error_rate` 지표는 Epic 5. `resolve_token`/`urlopen`/`channels`/충돌 enforcement는 전부 주입 seam·정의-only. [Source: architecture.md(192-193·216·433-438), implementation-contract.md(75·84), epics.md Story 5.5(1002-1022)·FR-29 매핑(182)]
- **Epic 4(Kakao 실전송 — 안 함):** Kakao PC 자동화 실전송은 Agent(4.6). 본 스토리는 Telegram outbound만(Kakao 채널은 충돌 검출에서 제외). [Source: epics.md Story 4.6(810)]

### 보존해야 할 공개 동작 / 핵심 원칙 (깨면 regression)

- (a) **`rider_crawl` 전부·`pyproject.toml`·3.1~3.6 services 본문·2.6 게이트·기존 도메인 모델·enum 무변경** — `git diff -w` = 신규 `services/telegram_central_dispatch.py`·`test_telegram_central_dispatch.py` + additive `services/__init__.py`(재노출)만. (b) **legacy `send_telegram_text` compose(재사용)만** — Bot API quirk 재구현 0, `retry_attempts=1` 단일 시도(이중 재시도 금지). (c) **send-only** — 신규 경로에 `getUpdates`/polling 0(legacy 폴러 보존·무변경). (d) **의존성 단방향** — `rider_server → rider_crawl`, 역방향 0(ast 가드). (e) **순수·결정적·동기** — `now()`/`uuid4()`/`random`/실 sleep 금지(transport·token 주입). (f) **frozen 불변** — `TelegramRoute`는 `@dataclass(frozen=True)`. (g) **ambiguous → 미재전송**(release 안 함, fail-closed: 오발송보다 미발송). (h) **충돌 검출은 활성 Telegram만**·정규화 일관. (i) **비노출** — 로그/예외 breadcrumb에 평문 token·chat_id 숫자 0(redact 통과). (j) **enum/모델 lock 무변경**(추가 enum/모델 없음 — 3.6과 다름). [Source: project-context.md(35·48·64·81·82·87·91·92·94), src/rider_crawl/sender.py(80-185), src/rider_server/services/]

### 이전 스토리 인텔리전스 (Epic 2 → 3.1 → … → 3.6 → 3.7 이월 교훈)

- **3.4가 본 스토리에 남긴 명시 위임:** `dispatch_fanout_service.py`(14-15·138-140행)가 "Telegram 중앙 sendMessage/webhook = Story 3.7. `dispatch_all`은 **주입된 sender 콜백**만 호출한다(중앙/per-Agent 경로 선택은 호출부 책임)… 본 서비스는 기본 adapter를 두지 않는다(채널별 라우팅이 Epic 5 config 배선 전이므로)"라 못 박았다. 본 스토리는 정확히 그 **Telegram send 콜백 실체**를 채운다(`as_send_callback()`). [Source: src/rider_server/services/dispatch_fanout_service.py(14-15·138-140)]
- **3.6이 본 스토리에 남긴 명시 위임:** `delivery_failure_policy.py`/3.6 스토리(129행)가 "3.7(Telegram 중앙 — 안 함): `attempt_delivery`의 `send`/`classify`는 주입 콜백 — 중앙 webhook/sendMessage 경로·getUpdates 제거는 3.7"이라 했다. 본 스토리는 `send`=`CentralTelegramSender`, `classify`=`channel_failure_category(TELEGRAM)`(=`TELEGRAM_FAILURE`)로 그 seam을 채운다(3.6 `attempt_delivery` 본문 무변경 compose). [Source: 3-6 스토리(129), src/rider_server/services/delivery_failure_policy.py]
- **무회귀 비결 = "새 필드가 아니라 새 어댑터/정책"**(epic-2-retro 64-67·149, 3.1~3.6 공통): 3.2 Snapshot·3.3 Message·3.4 DispatchJob·3.5 DeliveryLog·3.6 DeliveryFailurePolicy·3.7 CentralTelegramSender 모두 **기존 코드를 갈아엎지 않고 옆에 레코드/서비스/정책/어댑터를 추가**(재사용·compose·wrapping). 본 스토리는 특히 enum/모델 추가가 0이라 가장 비침습적이다. [Source: 3-6 스토리(143), epic-2-retro-2026-06-13.md(64-69·149)]
- **A2′(테스트 수치 단일 정본):** dev-story 노트에 **잠정 pass 수치를 박지 말 것** — 리뷰 시점 재측정값 1개만 정본. Epic 2/3.1~3.6 모두 stale 수치로 MEDIUM 재발(qa-generate-e2e가 dev 노트 뒤 케이스 추가). 기준선 923(3.6 종료, HEAD `bf6603f`)은 **참고값**(본인 재측정). [Source: memory/stale-test-count-a2, 3-6 스토리(144·197), epic-2-retro-2026-06-13.md(49·115)]
- **A1′(secret 스캔 게이트):** retro가 Epic 3 선행으로 권고. dev는 신규 코드·테스트 평문 secret 0건을 **수동 grep**으로 확인(봇토큰/`chat_id=digits`/한국휴대폰/이메일). Telegram 도메인이라 특히 token/chat_id fixture에 가짜 값만(`bot_token="FAKE-TELEGRAM-TOKEN"`, `chat_id="-100test"` 등 비실값). [Source: epic-2-retro-2026-06-13.md(114·129), project-context.md(81)]
- **dev-env:** pytest는 `.venv/Scripts/python.exe -m pytest`(WSL `python3` 아님 — 미설치). 범위 확인 `git diff -w`(CRLF/LF 노이즈). [Source: memory/dev-env-quirks]
- **enum 멤버-개수 lock 주의(이번엔 해당 없음이지만 경계):** memory/enum-member-count-locks — enum 멤버 추가 시 여러 테스트의 "exactly N members" lock이 깨진다. **본 스토리는 enum을 추가하지 않으므로 해당 없음** — 만약 구현 중 enum 추가가 필요해지면(예: ambiguous 전용 category) 그건 **스코프 크립 신호**다(3.6이 정의한 7 카테고리로 충분, ambiguity는 release-결정 레이어에서 처리). [Source: memory/enum-member-count-locks]

### Project Structure Notes

- 신규: `src/rider_server/services/telegram_central_dispatch.py`, `tests/server/test_telegram_central_dispatch.py`. 수정(additive): `src/rider_server/services/__init__.py`(재노출). `.agents/`·`.claude/`·`_bmad/`·`src/rider_crawl/`는 대상 아님(`rider_crawl`은 import 재사용만). [Source: project-context.md(64), architecture.md(425-434)]
- **`services/` 채움:** `telegram_central_dispatch.py` 추가 — `dispatch_fanout_service.py`(3.4)·`idempotency.py`(3.5)·`delivery_failure_policy.py`(3.6) 옆. architecture의 `dispatch/telegram_dispatcher.py`(async 중앙 webhook/sendMessage, 433-434)는 **Epic 5 신설** 집이므로 본 스토리(동기 outbound 어댑터)는 3.4~3.6 선례대로 `services/`에 둔다(상단 "위치 결정" 참조). [Source: architecture.md(425-434·507), src/rider_server/services/]
- **`domain/` 무변경:** 새 도메인 레코드·enum **없다**(11모델·기존 enum 유지) — `Messenger`/`MessengerChannel`/`DeliveryStatus`/`FailureCategory`는 그대로 사용. 따라서 `domain/__init__.py`·`test_domain_models.py`·`test_domain_states.py` **무변경**(3.6과 다른 점). `TelegramRoute`는 services 값 객체. [Source: src/rider_server/domain/states.py(74-80·138-159), src/rider_server/domain/messenger_channel.py(16-24)]
- **테스트 위치:** 평면 `tests/server/`에 `test_telegram_central_dispatch.py` 추가. `__init__.py` 미추가(평면 컨벤션, basename 고유). urlopen fake는 `tests/test_telegram_sender.py` 패턴, in-memory reserve/send/release `_Seam`은 `tests/server/test_idempotency.py`(60-75) 패턴, DispatchJob fixture는 `tests/server/test_dispatch_fanout.py` 패턴 재사용(공유 conftest 없이 자급자족). [Source: tests/server/, tests/test_telegram_sender.py]
- **`pyproject.toml` 무변경:** `pythonpath=["src"]`로 `rider_server.services.telegram_central_dispatch` import 동작(서버 패키징·async/ORM·webhook은 Epic 5). [Source: pyproject.toml(pythonpath), 3-6 스토리(154)]

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic-3(511-513)·#Story-3.7(649-669)·#Story-3.8(671-693)] — Epic 3 의도(한 번 수집 → 정규화 → 여러 채널 fan-out, 중복 없이 채널별 추적; Telegram 중앙 전송도 이 단계 도입), Story 3.7 user story·3 AC 원문(중앙 webhook/sendMessage 전환·getUpdates polling 제거·다중 프로세스 polling 구조 미생성; chat_id+topic_id scope 포함·채널별 DeliveryLog; 활성 chat_id+topic_id 충돌 금지), 다운스트림 3.8(dry-run/cutover).
- [Source: _bmad-output/planning-artifacts/epics.md#FR-24(64)·#FR-26(66)·#FR-29(72·182)·#NFR-1·#NFR-5(90)·#ADD-11(139)·#FR-24-매핑(177)] — FR-24(Telegram 중앙 전송: 동일 bot token 다중 프로세스 polling 금지·chat ID+topic ID scope 포함·실패 채널별 DeliveryLog; 채널 등록 UI/`/register`는 Epic 5), FR-26(채널별 전송 이력·실패 채널만 재시도), FR-29(채널 등록/검증/활성화=Epic 5), NFR-5(token/chat ID/topic ID redaction), ADD-11(중앙 webhook+secret header+`/register`·getUpdates 제거·chat_id+optional message_thread_id 자동 저장).
- [Source: _bmad-output/planning-artifacts/architecture.md(192-195·216·309·324-330·433-438·478·507·520·525)] — Telegram 중앙 webhook+secret header·Agent별 getUpdates 제거(token 큐 경합 방지)·에러 분류 7카테고리(telegram_failure)·`dispatch/telegram_dispatcher.py`(중앙 webhook/sendMessage)·`api/telegram_webhook.py`(secret header+`/register`)·Telegram webhook 외부 인바운드 단일 진입·⑦ 메신저 정책→`rider_server/dispatch/`·Telegram Bot API(중앙)·data flow(DispatchJob Telegram=중앙).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/implementation-contract.md(10·75·84)] — **Reuse: "Telegram sender → Move to central webhook/sendMessage flow; remove per-Agent getUpdates polling."**, P4-06(Telegram webhook+secret header, `/register` works without getUpdates polling=Epic 5), Telegram registration `/register <code>`로 chat_id+optional message_thread_id 자동 저장(Epic 5).
- [Source: _bmad-output/specs/spec-riderbot-refactoring/data-api-contract.md(12·30·34·146-152)] — `MessengerChannel`(Telegram chat/topic or Kakao room), `messenger_channels`(id, tenant_id, messenger, telegram_chat_id, thread_id, kakao_room_name, state), `delivery_logs`(id, message_id, channel_id, status, dedup_key, error_code, sent_at), Dedup Key(messenger_channel_id 포함=scope).
- [Source: src/rider_crawl/sender.py(80-116·119-142·145-194·227-245·266-285·288-310)] — 재사용 대상 `send_telegram_text`(urlopen·message_thread_id·retry_attempts·sleep 주입 지점), `get_telegram_updates`(=신규 경로에서 호출 금지), Bot API quirk(`migrate_to_chat_id`·retry-after·ok!=true·ambiguous 165-185), token/chat_id 읽기.
- [Source: src/rider_crawl/telegram_commands.py(155-157·327-...)] — legacy `TelegramUpdatePoller`(보존·무변경)·`locks_by_target` (chat_id,thread_id) 키 정규화 선례(충돌 검출 동형).
- [Source: src/rider_crawl/config.py(36-54·99-101)] — `AppConfig`(12 required 필드·telegram_bot_token/chat_id/message_thread_id) — per-call carrier 구성 대상.
- [Source: src/rider_server/services/dispatch_fanout_service.py(14-15·39-45·48-80·127-152)] — `dispatch_all(send=...)` 주입 seam(본 스토리 send 콜백 꽂는 곳)·3.7 위임 명시(14-15)·`UnknownChannelError`(fail-closed 선례)·`DispatchJob`(channel_id/messenger).
- [Source: src/rider_server/services/delivery_failure_policy.py(`attempt_delivery`·`channel_failure_category`·release=should_retry)] — compose 대상(send/classify 주입)·`channel_failure_category(TELEGRAM)`=`TELEGRAM_FAILURE` 재사용·release는 should_retry일 때만(ambiguous 미release 근거).
- [Source: src/rider_server/services/idempotency.py(96-107·120-133)] — insert-then-send·reserve 충돌→`DUPLICATE_BLOCKED`(ambiguous 미release 시 중복 차단 메커니즘)·`build_dedup_key`(messenger_channel_id scope).
- [Source: src/rider_server/domain/messenger_channel.py(16-24)·states.py(74-80·105-111·138-159)] — `MessengerChannel`(telegram_chat_id/thread_id=라우팅 식별자, secret 아님)·`Messenger.TELEGRAM`·`MessengerChannelState`(ACTIVE)·`DeliveryStatus`(5)·`FailureCategory`(7, TELEGRAM_FAILURE 존재).
- [Source: src/rider_server/services/__init__.py(1-59)] — services 재노출(본 스토리가 `CentralTelegramSender`/`TelegramRoute`/`find_telegram_topic_collisions`/`assert_unique_telegram_topics`/`TelegramTopicCollisionError` additive 추가).
- [Source: src/rider_crawl/redaction.py] — `redact(...)`(P0-04 재사용, breadcrumb/예외 통과).
- [Source: tests/server/test_run_once_split.py(431)·tests/test_telegram_sender.py·tests/server/test_idempotency.py(60-75)·tests/server/test_dispatch_fanout.py·tests/test_sender.py(676)] — 의존성 방향 가드, urlopen fake·`_Seam`(reserve/send/release)·DispatchJob fixture·AppConfig factory 재사용 anchor.
- [Source: _bmad-output/implementation-artifacts/3-6-수집-실패와-전송-실패-분리-재시도-실패-상태-관리.md(15·26·71·129·144·197)] — 3.6 위임 정밀(Telegram 중앙=3.7 `send`/`classify` 주입), `attempt_delivery` compose 패턴, A2′(923 기준선·수치 단일 정본), enum lock 갱신 선례(본 스토리는 해당 없음).
- [Source: _bmad-output/project-context.md(35·48·64·81·82·87·91·92·94)] — 순수·결정성, 텔레그램 봇 토큰별 단일 큐(다중 프로세스 polling 금지), 단방향 의존, secret/식별자 redaction, 범위 규율, 배민 미발송 우선, 활성 텔레그램 chat_id+topic_id 충돌 금지, send_only_on_change scope 비축소, Kakao 미발송 우선.
- [Source: memory/dev-env-quirks·stale-test-count-a2·enum-member-count-locks] — pytest `.venv/Scripts/python.exe`·`git diff -w`, 리뷰 시점 수치 재측정, enum 멤버 lock(본 스토리 enum 미추가).
- 요구사항 추적: FR-24(Telegram 중앙 전송)·FR-26(채널별 전송 이력)·NFR-1(fail-closed·오발송 방지)·NFR-5(token/chat ID/topic ID redaction)·NFR-20(기존 동작 무회귀)·ADD-11(중앙 webhook·getUpdates 제거)·FR-2(기존 자산 재사용·무변경). 인바운드 webhook/`/register`/secret header/async dispatcher/영속/등록 UI=Epic 5(P4-06·FR-29·5.5), dry-run/cutover/rollback=3.8, Kakao 실전송=Epic 4, scheduler breaker/jitter=5.4.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8 (Claude Opus 4.8)

### Debug Log References

- 기준선(HEAD `bf6603f`, 3.6 종료) 전체 스위트 재측정: **923 passed** (참고값 923과 일치).
- 신규 send-only 가드 테스트(`test_module_is_send_only_no_getupdates_or_poller`)가 초기엔
  docstring의 "안 한다" 언급(`get_telegram_updates`/`TelegramUpdatePoller`/`getUpdates`)을
  raw-text로 잡아 실패 → AST import-엣지 검사로 정밀화(주석/문서 언급 무시, 실제 import만 검사).

### Completion Notes List

- **순수 additive 구현 완료.** 신규 `services/telegram_central_dispatch.py`(`TelegramRoute`·
  `CentralTelegramSender`·`is_ambiguous_send_failure`·`find_telegram_topic_collisions`·
  `assert_unique_telegram_topics`·`TelegramTopicCollisionError`) + `services/__init__.py`
  재노출(additive) + 신규 테스트만 추가. `src/rider_crawl/`·3.1~3.6 services 본문·도메인
  모델·enum(`states.py`)·`pyproject.toml` **0줄 변경**(enum/모델 lock 갱신 0 — 3.6과 다름).
- **AC1 — 중앙 send-only.** `CentralTelegramSender.send`가 legacy `send_telegram_text`를
  재사용해 `sendMessage`만 1회 수행(올바른 chat_id+message_thread_id payload). `getUpdates`/
  `TelegramUpdatePoller`는 import조차 하지 않음(AST 가드로 단언). legacy 폴러 무변경·보존.
- **AC2 — scope + 채널별 DeliveryLog compose.** `TelegramRoute(chat_id, thread_id)`가 전송
  scope 식별자. 3.6 `attempt_delivery(send=CentralTelegramSender.send, classify=
  channel_failure_category(TELEGRAM))`에 compose 시 성공→`SENT`, 실패→채널별
  `DeliveryLog(error_code="TELEGRAM_FAILURE", status∈{FAILED,RETRYING})`. `DeliveryLog`
  직접 생성 없음(3.5/3.6 위임).
- **AC2.5 — ambiguous 미재전송.** `is_ambiguous_send_failure` 헬퍼 + "ambiguous → release
  안 함" 안전 기본값. in-memory seam으로 2라운드 재처리 시 reserve 충돌→`DUPLICATE_BLOCKED`·
  send 미호출(재전송 0)을 단언. definite 실패는 release/재시도 경로로 대비 단언.
- **AC3 — 활성 토픽 충돌 검출.** 활성(ACTIVE) Telegram만 대상, `thread_id` None↔"" 정규화
  일관. 비활성/Kakao/다른 조합은 충돌 아님. `assert_...`는 `TelegramTopicCollisionError`
  raise, 메시지는 `redact()` 통과(chat_id 숫자 비노출, 채널 id만 진단용 보존).
- **AC4 — 재사용·단일 시도·결정성·비노출.** `retry_attempts=1` 단일 시도(legacy 내부 재시도
  미발동 — 호출 횟수로 단언, 재시도/backoff=3.6). token은 `resolve_token` 주입 seam으로만.
  transport(`urlopen`)·token 주입으로 결정적. 신규 코드·테스트 평문 secret 0건.
- **AC5 — 무회귀·런타임 미배선.** 의존성 단방향(`rider_server → rider_crawl`, 역import 0건).
  `resolve_token`/`urlopen`/`channels`/충돌 enforcement는 전부 주입 seam·정의-only(실발송
  배선·webhook·영속·cutover는 Epic 5/3.8).
- **테스트 결과(리뷰 시점 재측정 단일 정본):** 전체 `949 passed`(기준선 923 + 신규 26).
  회귀 0(특히 `test_telegram_sender.py`·`test_telegram_commands.py`·`test_dispatch_fanout.py`·
  `test_idempotency*.py`·`test_delivery_failure_policy.py` 전부 통과). dev 노트의 잠정
  수치(943 = 923 + dev 20)는 qa-generate-e2e 보강(+6)으로 stale이었음 — 리뷰에서 재측정해
  정정(A2′, memory/stale-test-count-a2). 신규 테스트 = dev 20 + QA 6 = 26케이스.

### File List

- `src/rider_server/services/telegram_central_dispatch.py` (신규) — `TelegramRoute`·
  `CentralTelegramSender`·`is_ambiguous_send_failure`·`find_telegram_topic_collisions`·
  `assert_unique_telegram_topics`·`TelegramTopicCollisionError`.
- `src/rider_server/services/__init__.py` (수정·additive) — 다섯 심볼 재노출 + docstring 보강.
- `tests/server/test_telegram_central_dispatch.py` (신규) — AC1~AC8 테스트 26케이스(dev 20 + qa-e2e 보강 6).

## Change Log

| Date       | Version | Description                                                                 | Author     |
| ---------- | ------- | --------------------------------------------------------------------------- | ---------- |
| 2026-06-13 | 0.1     | Story 3.7 구현: 중앙 send-only Telegram 어댑터(`CentralTelegramSender`)·전송 scope(`TelegramRoute`)·활성 토픽 충돌 검출·ambiguous-안전 헬퍼 additive 추가. 전체 943 passed(기준선 923+20). | Amelia (Dev) |
| 2026-06-13 | 0.2     | Senior Developer Review(AI): 전체  AC/Task 검증 통과, CRITICAL 0. 테스트 수치 재측정 정정(943/+20 → 949/+26, A2′) 후 Status → done, sprint-status sync. | Noah Lee (Review) |

## Senior Developer Review (AI)

**Reviewer:** Noah Lee · **Date:** 2026-06-13 · **Outcome: Approve (done)** — CRITICAL 0, HIGH 0.

### 검증 방법
- 신규 `telegram_central_dispatch.py`·`test_telegram_central_dispatch.py`·`services/__init__.py`(재노출)와 compose 대상(`sender.py`/`config.py`/`dispatch_fanout_service.py`/`delivery_failure_policy.py`/`idempotency.py`/`messenger_channel.py`/`states.py`/`redaction.py`) 전량 정독.
- `git diff -w --stat`(제품 코드 = `__init__.py` 재노출 + 신규 2파일만, `src/rider_crawl/`·3.1~3.6 본문·enum 0줄), `grep -rn "import rider_server" src/rider_crawl/`=0, 신규 코드/테스트 평문 secret grep=0.
- 운영 venv 전체 스위트 재측정: **949 passed**(신규 파일 26 passed), 회귀 0.

### AC ↔ 구현 (전부 IMPLEMENTED)
- **AC1(중앙 send-only):** `CentralTelegramSender.send`가 `send_telegram_text`로 `sendMessage`만 1회. `get_telegram_updates`/`TelegramUpdatePoller` **import 0**(AST 가드 테스트로 고정). legacy 폴러 무변경.
- **AC2(scope + 채널별 DeliveryLog):** `TelegramRoute(chat_id, thread_id)` scope, 3.6 `attempt_delivery(classify=channel_failure_category(TELEGRAM))` compose → `DeliveryLog`. `DeliveryLog` 직접 생성 없음.
- **AC2.5(ambiguous 미재전송):** `is_ambiguous_send_failure` + 안전 기본값. 헬퍼 기반 wiring으로 2라운드 reserve 충돌→`DUPLICATE_BLOCKED` 고정.
- **AC3(충돌 검출):** 활성 Telegram만, `None`↔`""` 정규화 일관, 예외 메시지 `redact()` 통과(chat_id/thread_id 마스킹·채널 id 보존).
- **AC4(재사용·단일 시도·결정·비노출):** `retry_attempts=1`(이중 재시도 0), token=`resolve_token` 주입, transport=`urlopen` 주입.
- **AC5(additive·무회귀):** enum/모델 lock 갱신 0, 의존성 단방향, 949 passed·회귀 0.

### Findings
- **[MEDIUM · 정정 완료] 테스트 수치 stale (A2′).** Dev Agent Record가 `943 passed(+20)`·"20케이스"로 기록했으나 qa-generate-e2e가 6케이스를 뒤에 추가 → 실제 단일 정본 **949 passed / 26케이스**. Completion Notes·File List·Change Log를 재측정값으로 정정함. (memory/stale-test-count-a2 예측대로 재발.)
- **[LOW · Epic 5 인계, 코드 변경 없음] ambiguous-resend 런타임 enforcement는 미배선.** AC2가 예시한 `attempt_delivery(classify=TELEGRAM_FAILURE)` 경로는 `decide()`가 `should_retry=True`를 내 release→재전송한다 — ambiguous 보호는 `is_ambiguous_send_failure`를 release 결정에 끼워야 동작하며, 이는 본 스토리가 명시적으로 Epic 5/3.8에 위임한 wiring이다(헬퍼·docstring로 계약 제공). 신규 enum/정책 변경은 스코프 크립이라 코드 수정하지 않음. **Epic 5 주의 항목.**
- **[LOW · 문서] seam 표현.** `as_send_callback()`은 `(job, text) -> None`이라 `dispatch_all`에는 직접, `attempt_delivery`에는 `text` 클로저로 꽂힌다(테스트가 올바르게 그렇게 함). docstring의 "dispatch_all/attempt_delivery send seam 제공" 표현은 후자에 클로저가 필요함을 함축만 함 — 동작 영향 없음.

### 결론
순수 additive·무회귀·전 AC 충족. CRITICAL/HIGH 없음 → **done**. 잔여 LOW 2건은 Epic 5/3.8 소유의 정의·문서 항목으로 본 스토리 차단 사유 아님.
