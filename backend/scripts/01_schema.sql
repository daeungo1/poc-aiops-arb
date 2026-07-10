-- ============================================================
-- AIOps PostgreSQL 통합 스키마
-- DB: PostgreSQL 16 · Docker entrypoint-initdb.d / 앱 db_init 공용
--   체크리스트 | 평가 리포트·원천(result_*) | Terraform 실행·파일
--   (구 01_schema + 03_schema_reports + 04_migrate_terraform_tracking 통합)
-- ============================================================

-- ============================================================
-- 1. CHECKLISTS
--    체크리스트 최상위 단위 (YAML 파일 1개 = 1 row)
-- ============================================================
CREATE TABLE IF NOT EXISTS checklists (
    id                        BIGSERIAL     NOT NULL,
    file_key                  VARCHAR(200)  NOT NULL,   -- YAML 파일 stem (예: database_common)
    name                      VARCHAR(500)  NOT NULL,
    version                   VARCHAR(50),
    description               TEXT,
    applicable_resource_types JSONB         NOT NULL DEFAULT '[]',
    login_id                  VARCHAR(100),             -- 등록자 로그인 아이디 (SSO email)
    user_name                 VARCHAR(200),             -- 등록자 이름
    sso_no                    VARCHAR(50),              -- 등록자 SSO 고유번호 (oid)
    raw_yaml                  TEXT,                     -- 원본 YAML 텍스트 (다운로드·편집용)
    created_at                TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_checklists        PRIMARY KEY (id),
    CONSTRAINT uq_checklists_key    UNIQUE      (file_key)
);

COMMENT ON TABLE  checklists                            IS '체크리스트 최상위 단위 (YAML 파일 1개 = 1 row)';
COMMENT ON COLUMN checklists.id                         IS '체크리스트 고유 ID (auto increment)';
COMMENT ON COLUMN checklists.file_key                   IS 'YAML 파일 stem 식별자 (예: database_common)';
COMMENT ON COLUMN checklists.name                       IS '체크리스트 명칭';
COMMENT ON COLUMN checklists.version                    IS '버전 정보';
COMMENT ON COLUMN checklists.description                IS '상세 설명';
COMMENT ON COLUMN checklists.applicable_resource_types  IS '적용 대상 Azure 리소스 타입 목록 (JSONB 배열)';
COMMENT ON COLUMN checklists.login_id                   IS '등록자 로그인 아이디 (SSO email)';
COMMENT ON COLUMN checklists.user_name                  IS '등록자 이름';
COMMENT ON COLUMN checklists.sso_no                     IS '등록자 SSO 고유번호 (Azure AD oid)';
COMMENT ON COLUMN checklists.raw_yaml                   IS '원본 YAML 텍스트 (다운로드·편집용)';


-- ============================================================
-- 2. CHECKLIST_ITEMS
--    categories → items → checks 3단 계층을 1개 테이블로 평탄화
-- ============================================================
CREATE TABLE IF NOT EXISTS checklist_items (
    id                   BIGSERIAL    NOT NULL,
    checklist_id         BIGINT       NOT NULL,

    -- 카테고리 정보
    category_id          VARCHAR(100),            -- YAML category.id
    category_name        VARCHAR(500),            -- YAML category.name
    category_order       INT          NOT NULL DEFAULT 0,   -- 카테고리 정렬 순서

    -- 점검 항목 정보
    item_id              VARCHAR(100),            -- YAML item.id
    item_name            VARCHAR(500),            -- YAML item.name

    -- 개별 확인 질문
    question             TEXT         NOT NULL,
    priority             VARCHAR(20)  NOT NULL DEFAULT 'Medium',  -- High | Medium | Low
    display_order        INT          NOT NULL DEFAULT 0,   -- 항목 전체 정렬 순서

    -- Azure 점검 상세
    check_type           VARCHAR(20)  NOT NULL DEFAULT 'manual',  -- manual | automated
    check_method         VARCHAR(200),            -- 자동화 점검 함수명
    resource_type        VARCHAR(300),            -- 대상 Azure 리소스 유형
    expected_value       VARCHAR(500),            -- 기대값 (단일)
    condition_field      VARCHAR(200),            -- 비교 기준 필드
    condition_equals     VARCHAR(500),            -- 비교 조건값
    policy_effect        VARCHAR(50),             -- 정책 위반 효과 (Audit / Deny 등)
    guidance             TEXT,                    -- 조치 가이드라인
    expected_versions    JSONB        NOT NULL DEFAULT '[]',  -- 필수 버전 목록
    recommended_versions JSONB        NOT NULL DEFAULT '[]',  -- 권장 버전 목록

    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_checklist_items           PRIMARY KEY (id),
    CONSTRAINT fk_checklist_items_checklist FOREIGN KEY (checklist_id)
        REFERENCES checklists (id)
        ON DELETE CASCADE,
    CONSTRAINT chk_checklist_items_priority
        CHECK (priority   IN ('High', 'Medium', 'Low')),
    CONSTRAINT chk_checklist_items_type
        CHECK (check_type IN ('manual', 'automated'))
);

COMMENT ON TABLE  checklist_items                       IS '체크리스트 세부 항목 (categories → items → checks 평탄화)';
COMMENT ON COLUMN checklist_items.id                    IS '항목 고유 ID (auto increment)';
COMMENT ON COLUMN checklist_items.checklist_id          IS '소속 체크리스트 ID';
COMMENT ON COLUMN checklist_items.category_id           IS '카테고리 식별자 (YAML id 그대로)';
COMMENT ON COLUMN checklist_items.category_name         IS '카테고리 명칭';
COMMENT ON COLUMN checklist_items.category_order        IS '카테고리 정렬 순서';
COMMENT ON COLUMN checklist_items.item_id               IS '점검 항목 식별자 (YAML id 그대로)';
COMMENT ON COLUMN checklist_items.item_name             IS '점검 항목 명칭';
COMMENT ON COLUMN checklist_items.question              IS '점검 질문 내용';
COMMENT ON COLUMN checklist_items.priority              IS '중요도 (High / Medium / Low)';
COMMENT ON COLUMN checklist_items.display_order         IS '항목 전체 정렬 순서';
COMMENT ON COLUMN checklist_items.check_type            IS '점검 유형 (manual / automated)';
COMMENT ON COLUMN checklist_items.check_method          IS '자동화 점검 함수명 (Python)';
COMMENT ON COLUMN checklist_items.resource_type         IS '대상 Azure 리소스 유형';
COMMENT ON COLUMN checklist_items.expected_value        IS '기대값 (단일)';
COMMENT ON COLUMN checklist_items.condition_field       IS '비교 기준 필드';
COMMENT ON COLUMN checklist_items.condition_equals      IS '비교 조건값';
COMMENT ON COLUMN checklist_items.policy_effect         IS '정책 위반 시 효과 (Audit / Deny 등)';
COMMENT ON COLUMN checklist_items.guidance              IS '조치 가이드라인';
COMMENT ON COLUMN checklist_items.expected_versions     IS '필수 버전 정보 (JSONB 배열)';
COMMENT ON COLUMN checklist_items.recommended_versions  IS '권장 버전 정보 (JSONB 배열)';

CREATE INDEX IF NOT EXISTS idx_checklist_items_checklist   ON checklist_items (checklist_id);
CREATE INDEX IF NOT EXISTS idx_checklist_items_category_id ON checklist_items (checklist_id, category_id);
CREATE INDEX IF NOT EXISTS idx_checklist_items_check_type  ON checklist_items (check_type);
CREATE INDEX IF NOT EXISTS idx_checklist_items_priority    ON checklist_items (priority);


-- ============================================================
-- updated_at 자동 갱신 트리거 (checklists)
-- ============================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_checklists_updated_at ON checklists;
CREATE TRIGGER trg_checklists_updated_at
    BEFORE UPDATE ON checklists
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ============================================================
-- 1. RESULT_REPORTS
--    평가 리포트 최상위 단위 (JSON 파일 1개 = 1 row)
-- ============================================================
CREATE TABLE IF NOT EXISTS result_reports (
    report_id               BIGSERIAL     NOT NULL,
    generated_at            TIMESTAMPTZ   NOT NULL,
    report_version          VARCHAR(20)   NOT NULL DEFAULT '1.0',
    total_resources         INT           NOT NULL DEFAULT 0,
    summary_total_checks    INT           NOT NULL DEFAULT 0,
    summary_total_passed    INT           NOT NULL DEFAULT 0,
    summary_total_failed    INT           NOT NULL DEFAULT 0,
    summary_total_warnings  INT           NOT NULL DEFAULT 0,
    summary_total_manual    INT           NOT NULL DEFAULT 0,
    summary_average_score   NUMERIC(5,2)  NOT NULL DEFAULT 0,
    summary_pass_rate       NUMERIC(5,2)  NOT NULL DEFAULT 0,
    summary_fail_rate       NUMERIC(5,2)  NOT NULL DEFAULT 0,
    summary_manual_rate     NUMERIC(5,2)  NOT NULL DEFAULT 0,
    report_md               TEXT,
    report_html             TEXT,

    CONSTRAINT pk_result_reports PRIMARY KEY (report_id)
);

COMMENT ON TABLE  result_reports                        IS '평가 리포트 최상위 단위 (JSON 파일 1개 = 1 row)';
COMMENT ON COLUMN result_reports.report_id              IS '리포트 고유 ID (auto increment)';
COMMENT ON COLUMN result_reports.generated_at           IS '리포트 생성 일시';
COMMENT ON COLUMN result_reports.report_version         IS '버전 (v1.0 등)';
COMMENT ON COLUMN result_reports.total_resources        IS '총 리소스 수';
COMMENT ON COLUMN result_reports.summary_total_checks   IS '총 점검 항목 수';
COMMENT ON COLUMN result_reports.summary_total_passed   IS '총 통과 수';
COMMENT ON COLUMN result_reports.summary_total_failed   IS '총 실패 수';
COMMENT ON COLUMN result_reports.summary_total_warnings IS '총 경고 수';
COMMENT ON COLUMN result_reports.summary_total_manual   IS '총 수동 점검 수';
COMMENT ON COLUMN result_reports.summary_average_score  IS '평균 점수';
COMMENT ON COLUMN result_reports.summary_pass_rate      IS '합격 비율 (%)';
COMMENT ON COLUMN result_reports.summary_fail_rate      IS '실패 비율 (%)';
COMMENT ON COLUMN result_reports.summary_manual_rate    IS '수동 점검 비율 (%)';
COMMENT ON COLUMN result_reports.report_md              IS 'Markdown 리포트 전문';
COMMENT ON COLUMN result_reports.report_html            IS 'HTML 리포트 전문';

CREATE INDEX IF NOT EXISTS idx_result_reports_generated_at ON result_reports (generated_at DESC);


-- ============================================================
-- 2. RESULT_RESOURCE_ASSESSMENTS
--    리포트 내 리소스별 평가 결과
-- ============================================================
CREATE TABLE IF NOT EXISTS result_resource_assessments (
    assessment_id         BIGSERIAL     NOT NULL,
    report_id             BIGINT        NOT NULL,
    subscription_id       VARCHAR(100),
    resource_id           VARCHAR(500)  NOT NULL,
    resource_name         VARCHAR(300)  NOT NULL,
    resource_type         VARCHAR(200)  NOT NULL,
    resource_group        VARCHAR(200),
    location              VARCHAR(100),
    assessment_time       TIMESTAMPTZ,
    overall_score         NUMERIC(5,2)  NOT NULL DEFAULT 0,
    summary_total_checks  INT           NOT NULL DEFAULT 0,
    summary_passed        INT           NOT NULL DEFAULT 0,
    summary_failed        INT           NOT NULL DEFAULT 0,
    summary_warnings      INT           NOT NULL DEFAULT 0,
    is_type_mismatch      BOOLEAN       NOT NULL DEFAULT FALSE,

    CONSTRAINT pk_result_resource_assessments PRIMARY KEY (assessment_id),
    CONSTRAINT fk_result_assessments_report   FOREIGN KEY (report_id)
        REFERENCES result_reports (report_id)
        ON DELETE CASCADE
);

COMMENT ON TABLE  result_resource_assessments                      IS '리포트 내 리소스별 평가 결과';
COMMENT ON COLUMN result_resource_assessments.assessment_id        IS '평가 고유 ID (auto increment)';
COMMENT ON COLUMN result_resource_assessments.report_id            IS '리포트 참조 ID';
COMMENT ON COLUMN result_resource_assessments.subscription_id      IS 'Azure 구독 ID';
COMMENT ON COLUMN result_resource_assessments.resource_id          IS 'Azure 리소스 전체 경로 (ARM ID)';
COMMENT ON COLUMN result_resource_assessments.resource_name        IS '리소스명';
COMMENT ON COLUMN result_resource_assessments.resource_type        IS '유형 (예: StorageAccount)';
COMMENT ON COLUMN result_resource_assessments.resource_group       IS '리소스 그룹';
COMMENT ON COLUMN result_resource_assessments.location             IS '리전 (예: koreasouth)';
COMMENT ON COLUMN result_resource_assessments.assessment_time      IS '평가 일시';
COMMENT ON COLUMN result_resource_assessments.overall_score        IS '리소스 보안 점수';
COMMENT ON COLUMN result_resource_assessments.summary_total_checks IS '총 점검 수';
COMMENT ON COLUMN result_resource_assessments.summary_passed       IS '통과 수';
COMMENT ON COLUMN result_resource_assessments.summary_failed       IS '실패 수';
COMMENT ON COLUMN result_resource_assessments.summary_warnings     IS '경고 수';
COMMENT ON COLUMN result_resource_assessments.is_type_mismatch     IS '선택 체크리스트가 리소스 타입과 불일치하여 평가 미적용된 경우 TRUE';

CREATE INDEX IF NOT EXISTS idx_result_assessments_report         ON result_resource_assessments (report_id);
CREATE INDEX IF NOT EXISTS idx_result_assessments_subscription   ON result_resource_assessments (subscription_id);
CREATE INDEX IF NOT EXISTS idx_result_assessments_resource_type  ON result_resource_assessments (resource_type);
CREATE INDEX IF NOT EXISTS idx_result_assessments_resource_group ON result_resource_assessments (resource_group);
CREATE INDEX IF NOT EXISTS idx_result_assessments_type_mismatch  ON result_resource_assessments (is_type_mismatch) WHERE is_type_mismatch = TRUE;
-- 복합 인덱스: DISTINCT ON (resource_name) + 시간 정렬 최적화
CREATE INDEX IF NOT EXISTS idx_result_assessments_name_time
    ON result_resource_assessments (resource_name, assessment_time DESC NULLS LAST);
-- 복합 인덱스: 점수 구간 조회 최적화
CREATE INDEX IF NOT EXISTS idx_result_assessments_score_range
    ON result_resource_assessments (overall_score, is_type_mismatch, summary_total_checks);
-- result_reports generated_at 정렬 인덱스
CREATE INDEX IF NOT EXISTS idx_result_reports_generated_at
    ON result_reports (generated_at DESC);


-- ============================================================
-- 3. RESULT_CHECK_RESULTS
--    리소스 평가 내 개별 점검 결과
-- ============================================================
CREATE TABLE IF NOT EXISTS result_check_results (
    result_id           BIGSERIAL    NOT NULL,
    assessment_id       BIGINT       NOT NULL,
    status              VARCHAR(20)  NOT NULL,
    severity            VARCHAR(20),
    question            TEXT         NOT NULL,
    finding             TEXT,
    recommendation      TEXT,
    evidence_property   VARCHAR(300),
    evidence_actual     TEXT,
    evidence_expected   TEXT,
    checklist_name      VARCHAR(200),

    CONSTRAINT pk_result_check_results        PRIMARY KEY (result_id),
    CONSTRAINT fk_result_check_results_assess FOREIGN KEY (assessment_id)
        REFERENCES result_resource_assessments (assessment_id)
        ON DELETE CASCADE,
    CONSTRAINT chk_result_check_results_status
        CHECK (status   IN ('pass', 'fail', 'warning', 'manual_review', 'n_a')),
    CONSTRAINT chk_result_check_results_severity
        CHECK (severity IN ('critical', 'high', 'medium', 'low'))
);

COMMENT ON TABLE  result_check_results                   IS '리소스 평가 내 개별 점검 결과';
COMMENT ON COLUMN result_check_results.result_id         IS '결과 고유 ID (auto increment)';
COMMENT ON COLUMN result_check_results.assessment_id     IS '평가 참조 ID';
COMMENT ON COLUMN result_check_results.status            IS '결과 상태 (pass/fail/warning/manual_review/n_a)';
COMMENT ON COLUMN result_check_results.severity          IS '위험도 (critical/high/medium/low)';
COMMENT ON COLUMN result_check_results.question          IS '점검 항목 (질문)';
COMMENT ON COLUMN result_check_results.finding           IS '발견 사항 (진단 결과)';
COMMENT ON COLUMN result_check_results.recommendation    IS '조치 권고 사항';
COMMENT ON COLUMN result_check_results.evidence_property IS '점검 속성 (property_checked)';
COMMENT ON COLUMN result_check_results.evidence_actual   IS '실제 값 (actual_value)';
COMMENT ON COLUMN result_check_results.evidence_expected IS '기대 값 (expected_value)';
COMMENT ON COLUMN result_check_results.checklist_name    IS '출처 체크리스트 이름';

CREATE INDEX IF NOT EXISTS idx_result_check_results_assessment ON result_check_results (assessment_id);
CREATE INDEX IF NOT EXISTS idx_result_check_results_status     ON result_check_results (status);
CREATE INDEX IF NOT EXISTS idx_result_check_results_severity   ON result_check_results (severity);


-- ============================================================
-- 4. RESULT_FILE
--    진단 결과 원천 데이터 (ResourceAssessment 전체 저장)
-- ============================================================
CREATE TABLE IF NOT EXISTS result_file (
    id              BIGSERIAL     NOT NULL,
    report_id       BIGINT,
    scope_id        VARCHAR(200),
    resource_id     VARCHAR(1000),
    resource_name   VARCHAR(500),
    resource_type   VARCHAR(300),
    resource_group  VARCHAR(300),
    result_status   VARCHAR(50),
    overall_score   NUMERIC(5,2),
    details         JSONB         NOT NULL DEFAULT '{}',
    report_md       TEXT,
    report_html     TEXT,
    trace_id        UUID          NOT NULL DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_result_file PRIMARY KEY (id),
    CONSTRAINT fk_result_file_report FOREIGN KEY (report_id)
        REFERENCES result_reports (report_id)
        ON DELETE SET NULL
);

COMMENT ON TABLE  result_file               IS '진단 결과 원천 데이터 (ResourceAssessment 전체)';
COMMENT ON COLUMN result_file.id            IS '고유 ID (auto increment)';
COMMENT ON COLUMN result_file.report_id     IS '리포트 참조 ID (result_reports.report_id)';
COMMENT ON COLUMN result_file.scope_id      IS 'Azure 구독 ID (subscription_id)';
COMMENT ON COLUMN result_file.resource_id   IS '리소스 식별자';
COMMENT ON COLUMN result_file.resource_name IS '리소스명';
COMMENT ON COLUMN result_file.resource_type IS '리소스 유형';
COMMENT ON COLUMN result_file.resource_group IS '리소스 그룹';
COMMENT ON COLUMN result_file.result_status IS '결과 상태 (pass/fail/warning/n_a/manual_review)';
COMMENT ON COLUMN result_file.overall_score IS '리소스 보안 점수';
COMMENT ON COLUMN result_file.details       IS 'ResourceAssessment 전체 JSON';
COMMENT ON COLUMN result_file.report_md     IS 'Markdown 리포트 전문';
COMMENT ON COLUMN result_file.report_html   IS 'HTML 리포트 전문';
COMMENT ON COLUMN result_file.trace_id      IS '추적 고유 ID';
COMMENT ON COLUMN result_file.created_at    IS '생성 일시';

CREATE INDEX IF NOT EXISTS idx_result_file_report_id     ON result_file (report_id);
CREATE INDEX IF NOT EXISTS idx_result_file_scope_id      ON result_file (scope_id);
CREATE INDEX IF NOT EXISTS idx_result_file_result_status ON result_file (result_status);
CREATE INDEX IF NOT EXISTS idx_result_file_created_at    ON result_file (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_result_file_trace_id      ON result_file (trace_id);


-- ============================================================
-- 5. TERRAFORM_RUNS
--    Terraform 실행 이력 (1 실행 = 1 row)
-- ============================================================
CREATE TABLE IF NOT EXISTS terraform_runs (
    id                     BIGSERIAL     NOT NULL,
    scope_id               VARCHAR(200),
    run_timestamp          VARCHAR(50),
    source_diagnosis_ids   JSONB         NOT NULL DEFAULT '[]',
    resources_count        INT           NOT NULL DEFAULT 0,
    recommendations_count  INT           NOT NULL DEFAULT 0,
    source_report_ids      JSONB         NOT NULL DEFAULT '[]',
    source_resource_names  JSONB         NOT NULL DEFAULT '[]',
    created_at             TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_terraform_runs PRIMARY KEY (id)
);

COMMENT ON TABLE  terraform_runs                        IS 'Terraform 실행 이력 (1 실행 = 1 row)';
COMMENT ON COLUMN terraform_runs.id                     IS '고유 ID (auto increment)';
COMMENT ON COLUMN terraform_runs.scope_id               IS 'Azure 구독 ID';
COMMENT ON COLUMN terraform_runs.run_timestamp          IS '실행 타임스탬프';
COMMENT ON COLUMN terraform_runs.source_diagnosis_ids   IS '연관 trace_id 목록 (JSONB 배열)';
COMMENT ON COLUMN terraform_runs.resources_count        IS '코드 생성 대상 리소스 수';
COMMENT ON COLUMN terraform_runs.recommendations_count  IS '적용된 권고 사항 건수';
COMMENT ON COLUMN terraform_runs.source_report_ids      IS '사용된 진단 리포트 ID 목록 (result_reports.report_id 배열)';
COMMENT ON COLUMN terraform_runs.source_resource_names  IS '대상 리소스명 목록 (JSONB 배열)';
COMMENT ON COLUMN terraform_runs.created_at             IS '생성 일시';

CREATE INDEX IF NOT EXISTS idx_terraform_runs_scope_id      ON terraform_runs (scope_id);
CREATE INDEX IF NOT EXISTS idx_terraform_runs_created_at    ON terraform_runs (created_at DESC);


-- ============================================================
-- 6. TERRAFORM_RUN_FILES
--    Terraform 실행별 생성 파일 (tf N개 + md 2개 등)
-- ============================================================
CREATE TABLE IF NOT EXISTS terraform_run_files (
    id          BIGSERIAL     NOT NULL,
    run_id      BIGINT        NOT NULL,
    file_name   VARCHAR(500)  NOT NULL,
    file_type   VARCHAR(20)   NOT NULL,
    content     TEXT          NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_terraform_run_files      PRIMARY KEY (id),
    CONSTRAINT fk_terraform_run_files_run  FOREIGN KEY (run_id)
        REFERENCES terraform_runs (id)
        ON DELETE CASCADE,
    CONSTRAINT chk_terraform_run_files_type
        CHECK (file_type IN ('tf', 'md', 'other'))
);

COMMENT ON TABLE  terraform_run_files           IS 'Terraform 실행별 생성 파일 목록';
COMMENT ON COLUMN terraform_run_files.id        IS '고유 ID (auto increment)';
COMMENT ON COLUMN terraform_run_files.run_id    IS 'terraform_runs 참조 ID';
COMMENT ON COLUMN terraform_run_files.file_name IS '파일명 (예: main.tf, README.md)';
COMMENT ON COLUMN terraform_run_files.file_type IS '파일 유형 (tf / md / other)';
COMMENT ON COLUMN terraform_run_files.content   IS '파일 전체 내용';
COMMENT ON COLUMN terraform_run_files.created_at IS '생성 일시';

CREATE INDEX IF NOT EXISTS idx_terraform_run_files_run_id   ON terraform_run_files (run_id);
CREATE INDEX IF NOT EXISTS idx_terraform_run_files_type     ON terraform_run_files (run_id, file_type);


-- ============================================================
-- Idempotent: 예전 DB에 terraform_runs만 옛 정의로 있을 때 컬럼 보강
-- ============================================================
ALTER TABLE terraform_runs
    ADD COLUMN IF NOT EXISTS resources_count        INT   NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS recommendations_count  INT   NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS source_report_ids      JSONB NOT NULL DEFAULT '[]',
    ADD COLUMN IF NOT EXISTS source_resource_names  JSONB NOT NULL DEFAULT '[]';

COMMENT ON COLUMN terraform_runs.resources_count       IS '코드 생성 대상 리소스 수';
COMMENT ON COLUMN terraform_runs.recommendations_count IS '적용된 권고 사항 건수';
COMMENT ON COLUMN terraform_runs.source_report_ids     IS '사용된 진단 리포트 ID 목록 (result_reports.report_id 배열)';
COMMENT ON COLUMN terraform_runs.source_resource_names IS '대상 리소스명 목록 (JSONB 배열)';

-- ============================================================
-- Idempotent: result_resource_assessments 구독 이름 컬럼 보강
-- ============================================================
ALTER TABLE result_resource_assessments
    ADD COLUMN IF NOT EXISTS subscription_name VARCHAR(300);

COMMENT ON COLUMN result_resource_assessments.subscription_name IS 'Azure 구독 이름 (표시 이름, 평가 시점 캡처)';
