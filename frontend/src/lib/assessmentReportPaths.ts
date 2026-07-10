/** DB/로컬 전체 경로에서 마지막 세그먼트(파일명)만 추출 */
export function assessmentReportBasename(fullPath: string): string {
  const normalized = fullPath.replace(/\\/g, '/');
  return normalized.split('/').pop() ?? normalized;
}

/** 확장자 제거한 파일명 (그룹 키). 예: assessment_20250101_120000.md → assessment_20250101_120000 */
export function assessmentReportStem(fullPath: string): string {
  const base = assessmentReportBasename(fullPath);
  const i = base.lastIndexOf('.');
  return i <= 0 ? base : base.slice(0, i);
}

export interface AssessmentReportFileRow {
  filename: string;
  date: string;
  size: number;
  source?: 'azure' | 'local';
}

export interface AssessmentReportStemGroup {
  stem: string;
  files: AssessmentReportFileRow[];
}

const REPORT_EXTENSION_ORDER: Record<string, number> = {
  md: 0,
  html: 1,
  json: 2,
};

function assessmentReportExtension(fullPath: string): string {
  const base = assessmentReportBasename(fullPath);
  const i = base.lastIndexOf('.');
  return i < 0 ? '' : base.slice(i + 1).toLowerCase();
}

/** stem 기준 그룹. 그룹은 최신 date 기준 내림차순, 파일은 md → html → json 순 */
export function groupAssessmentReportsByStem(
  files: AssessmentReportFileRow[],
): AssessmentReportStemGroup[] {
  const map = new Map<string, AssessmentReportFileRow[]>();
  for (const f of files) {
    const stem = assessmentReportStem(f.filename);
    if (!map.has(stem)) map.set(stem, []);
    map.get(stem)!.push(f);
  }
  for (const list of map.values()) {
    list.sort((a, b) => {
      const ao = REPORT_EXTENSION_ORDER[assessmentReportExtension(a.filename)] ?? 99;
      const bo = REPORT_EXTENSION_ORDER[assessmentReportExtension(b.filename)] ?? 99;
      if (ao !== bo) return ao - bo;
      return a.filename.localeCompare(b.filename);
    });
  }
  const groups: AssessmentReportStemGroup[] = [...map.entries()].map(([stem, gfiles]) => ({
    stem,
    files: gfiles,
  }));
  groups.sort((a, b) => {
    const ad = a.files[0]?.date ?? '';
    const bd = b.files[0]?.date ?? '';
    return bd.localeCompare(ad);
  });
  return groups;
}
