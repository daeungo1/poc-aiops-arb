# Azure Storage Coverage Spike

이 실험은 Azure Storage용 6개 control을 합성 evidence로만 평가하여 결정론 evaluator와 source mapping의
coverage를 검증한다. Azure API 또는 기타 외부 서비스는 호출하지 않는다.

## 실행

현재 작업 디렉터리: 저장소 루트 (`aiops-agent-main/`)

```powershell
uv run --project backend python backend/scripts/run_coverage_spike.py
```

현재 작업 디렉터리: `backend/`

```powershell
uv run python scripts/run_coverage_spike.py
```

스크립트는 JSON과 Markdown 내용을 정규화하고 두 content hash에서 결정론적 `generation_id`를 계산한다.
두 파일은 다음 immutable generation directory에 완전히 기록하고 fsync/close한다.

- `reports/generations/<generation_id>/coverage-summary.json`
- `reports/generations/<generation_id>/coverage-summary.md`

기존 generation의 hash가 같으면 그대로 재사용하고, 같은 `generation_id` 경로에 다른 내용이 있으면
오류로 처리한다. 두 파일이 준비된 뒤 `reports/current.json` 하나만 atomic replacement한다. 이 current manifest가
두 파일의 정확한 상대 경로와 SHA-256 hash를 담는 유일한 canonical publication boundary다. 따라서 reader는
current manifest 한 개를 읽은 뒤 해당 immutable bundle을 검증하며, 이전 JSON과 새 Markdown을 혼합하지 않는다.
각 generation 파일 자체 또는 과거 top-level report 파일 두 개가 atomic pair라고 주장하지 않는다.

`current.json`을 atomic replacement한 직후 publisher는 destination `reports` directory도 durability sync한다.
POSIX에서는 directory descriptor에 `fsync`를 호출하고, Windows에서는 directory를 `CreateFileW`의
`GENERIC_WRITE` 및 `FILE_FLAG_BACKUP_SEMANTICS`로 열어 `FlushFileBuffers`를 호출한다. platform 또는
filesystem이 이 기능을 명시적으로 지원하지 않으면 `DirectoryFsyncUnsupportedError`로 publication을 실패시킨다.
지원 여부를 조용히 무시하거나 atomic replacement만으로 crash durability까지 보장됐다고 보고하지 않는다.

Atomic replacement가 끝난 뒤 destination directory sync가 실패하면 caller는
`ReportPublicationDurabilityError`를 받는다. 이 시점에는 rename이 이미 발생했을 수 있으므로 recovery 후
`current.json`은 old 또는 new complete immutable bundle 중 하나를 가리킬 수 있다. 두 bundle 모두 완전히 기록되고
hash가 고정되어 있으므로 `read_current_report_bundle()`로 현재 bundle을 검증할 수 있다. caller는 이 오류를 성공으로
처리하지 말고 publication을 재시도하여 directory durability barrier가 완료됐음을 확인해야 한다. 예외가 전파될 때도
publish lock은 해제되고, replacement로 이동한 manifest staging file은 `.staging`에 남지 않는다.

Manifest 임시 파일은 `reports/current.json` 옆이 아닌 전용 `reports/.staging` directory에서만 생성한다.
모든 publish와 staging scavenging은 `filelock`이 관리하는 cross-process advisory lock인
`reports/.publish.lock`을 bounded timeout으로 획득한 뒤에만 수행한다. timeout이면 cleanup, generation publish,
current manifest replacement를 하나도 실행하지 않고 fail closed한다. lock을 획득한 publisher만 stale staging을 정리하고,
그 다음 generation을 기록하고 `reports/current.json`을 교체한 뒤 lock을 해제한다.

staging root는 application-owned real directory여야 하며 symlink/reparse point이면 거부한다. scavenger는
`current.<uuid>.tmp` exact pattern과 일치하는 direct child single-link regular file만 `os.scandir`와 lstat로
재검증한 후 unlink한다. symlink, reparse point, directory, hardlink, 예상 밖 이름은 unlink하지 않고 fail closed하며
어떤 경우에도 재귀 삭제하지 않는다. write, flush, fsync, close 또는 replacement가 실패하면 current manifest를
변경하기 전에 publication을 중단한다. 실패 직후 cleanup은 best effort다. Windows가 닫히지 않은 handle을 계속
점유하면 exact-pattern 임시 파일을 남길 수 있고, handle이 닫힌 뒤 다음 locked publisher가 이를 정리한다.

reader는 `.staging`을 검사하거나 정리하지 않는다. reader는 publish lock도 획득하지 않고 atomic하게 읽은
current manifest가 가리키는 immutable bundle의 hash만 검증하므로 진행 중인 publisher를 기다리지 않는다.

이 보호는 report directory가 trusted filesystem/ACL boundary 안에 있다는 전제다. 같은 권한의 공격자가 경로를
바꾸는 same-privilege malicious replacement는 process scope 밖이지만, 검증 실패가 재귀 삭제나 외부 경로 추적으로
이어지지는 않는다.

## Coverage 정의

모든 control coverage 비율의 denominator는 전체 control 수 6이다.

- `machine_verifiable`: evaluator kind가 `managed` 또는 `custom`인 control
- `managed_source`: source 목록에 `aprl`, `advisor`, `defender`, `azure_policy` 중 하나 이상이 있는 control
- `custom_evaluator`: `EvaluatorKind.CUSTOM`인 control
- `custom_assertion`: required primary source가 `arm`, `arg`, `storage_service` 중 하나이고 결정론
	assertion을 로컬에서 실행하는 control
- `agent_assisted`, `manual`: 해당 evaluator kind의 control
- `evaluator_kind_counts`: 서로 배타적인 네 evaluator kind이며 합계가 전체 control 수와 같아야 한다.

`managed_source`와 `custom_assertion`은 evaluator kind와 별도의 축이다. 같은 control이 로컬 custom assertion을
실행하면서 corroborating managed source mapping도 가져 두 metric에 겹쳐 포함될 수 있다. 현재 결과는
`custom_assertion` 6/6, `managed_source` 4/6이다.

## Fixture Gate

세 fixture는 control마다 하나의 실제 verdict state와 reason code를 생성하므로 verdict denominator는 18이다.
`unknown`은 verdict state 중 하나이며, `conflicts`는 `evidence_conflict` 또는 `evidence_scope_conflict` reason을
가진 verdict의 별도 집계다. 따라서 conflict는 verdict state와 상호 배타적인 category가 아니다.

각 fixture는 `metadata.classification`과 `metadata.comparable`을 명시한다. compliant 및 noncompliant fixture는
`oracle`/`true`이고 expected output과 총 12개 verdict를 비교한다. partial fixture는
`exploratory`/`false`이며 unknown 처리와 reason code만 보고한다. comparable fixture 집합과 expected 파일 집합,
각 expected의 6개 control key, fixture와 expected의 `fixture_id`가 모두 정확히 일치해야 한다.

`managed_source_conflicts`는 corroborating managed source status가 primary assertion과 모순되어
`managed_source_conflict` reason code를 낸 verdict만 집계한다. `all_conflicts`는 여기에 primary evidence conflict와
scope/resource conflict를 더한 별도 metric이다.

## Gate 의미

report의 `validation_mode`는 항상 `synthetic_fixture`다. `implementation_gate`는 oracle completeness,
내부 합계, fixture mismatch 0건만 판정하며 CLI exit code도 이 gate만 따른다. 합성 fixture는 live Azure adapter나
API를 검증하지 않는다.

`deployment_readiness`는 다음 조건이 충족되기 전까지 항상 `blocked`다.

1. live Azure adapter/API validation
2. required RBAC/API limitations 문서화 및 검증
3. human mapping/verdict review
4. UI contract approval

스크립트는 다음 조건을 모두 만족할 때만 exit code `0`을 반환한다.

1. expected oracle이 완전하다.
2. fixture mismatch가 0개다.
3. evaluator kind, machine-verifiable, fixture verdict state 등 내부 합계가 유효하다.

Expected verdict가 다르거나 checklist, mapping, fixture, expected 문서가 malformed이면 exit code `1`을 반환한다.