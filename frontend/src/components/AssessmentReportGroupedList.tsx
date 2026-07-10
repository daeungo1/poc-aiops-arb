import { Fragment, useEffect, useMemo, useState } from 'react';
import {
  assessmentReportBasename,
  assessmentReportStem,
  groupAssessmentReportsByStem,
  type AssessmentReportFileRow,
} from '../lib/assessmentReportPaths';

export interface AssessmentReportGroupedListProps {
  files: AssessmentReportFileRow[];
  selectedFilename: string | null;
  onSelectFile: (filename: string) => void;
  /** 대시보드 최근 결과 등 좁은 레이아웃 */
  compact?: boolean;
  /** true면 열 헤더(리포트 그룹/파일 등)를 숨기고 스크린 리더용으로만 남김 */
  hideGroupFileHeader?: boolean;
}

function formatAssessmentDateTime(value: string): { date: string; time: string } {
  const normalized = (value || '').replace('T', ' ');
  const date = normalized.slice(0, 10);
  const time = normalized.slice(11, 19);
  return {
    date: date || '-',
    time: time || '',
  };
}

function formatAssessmentDateTimeLine(value: string): string {
  const dt = formatAssessmentDateTime(value);
  return dt.time ? `${dt.date} ${dt.time}` : dt.date;
}

function formatRelativeTime(value: string, now = new Date()): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return formatAssessmentDateTimeLine(value);

  const diffSeconds = Math.max(0, Math.floor((now.getTime() - parsed.getTime()) / 1000));
  if (diffSeconds < 60) return `${diffSeconds}초 전`;

  const diffMinutes = Math.floor(diffSeconds / 60);
  if (diffMinutes < 60) return `${diffMinutes}분 전`;

  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}시간 전`;

  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 30) return `${diffDays}일 전`;

  const diffMonths = Math.floor(diffDays / 30);
  if (diffMonths < 12) return `${diffMonths}달 전`;

  const diffYears = Math.floor(diffMonths / 12);
  return `${diffYears}년 전`;
}

function assessmentReportExtensionLabel(filename: string): string {
  const base = assessmentReportBasename(filename);
  const i = base.lastIndexOf('.');
  return i < 0 ? base : base.slice(i + 1).toLowerCase();
}

function assessmentReportGroupLabel(stem: string): string {
  const match = /^Report_(\d+)(?:_|$)/i.exec(stem);
  return match ? `Report_${match[1]}` : stem;
}

export function AssessmentReportGroupedList({
  files,
  selectedFilename,
  onSelectFile,
  compact = false,
  hideGroupFileHeader = false,
}: AssessmentReportGroupedListProps) {
  const groups = useMemo(() => groupAssessmentReportsByStem(files), [files]);
  const now = useMemo(() => new Date(), [files]);
  const [expandedStem, setExpandedStem] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    if (!selectedFilename) return;
    const stem = assessmentReportStem(selectedFilename);
    setExpandedStem((prev) => {
      const n = new Set(prev);
      n.add(stem);
      return n;
    });
  }, [selectedFilename]);

  const thPad = compact ? 'py-2 pl-2 pr-1' : 'py-2.5 pl-2 pr-1';
  const groupRowPad = compact ? 'py-1.5' : 'py-2';
  const childRowPad = compact ? 'py-1.5' : 'py-2';
  const indentChild = compact ? 'pl-9' : 'pl-11';

  if (files.length === 0) {
    return null;
  }

  return (
    <div
      className={
        compact
          ? 'bg-white rounded-lg border border-gray-200 overflow-hidden'
          : 'bg-white rounded-xl border border-gray-200 overflow-hidden flex flex-col min-h-0'
      }
    >
      <div className={compact ? 'max-h-72 overflow-auto' : 'flex-1 overflow-auto min-h-0'}>
        <table className="w-full text-sm border-collapse table-fixed">
          <colgroup>
            <col className="w-[2.25rem]" />
            <col />
          </colgroup>
          <thead className={hideGroupFileHeader ? 'sr-only' : undefined}>
            <tr
              className={
                hideGroupFileHeader
                  ? ''
                  : 'bg-gray-50 border-b border-gray-200 text-xs text-gray-600 sticky top-0 z-10'
              }
            >
              <th
                scope="col"
                className={`${compact ? 'py-2 pl-1 pr-0' : 'py-2.5 pl-1 pr-0'} text-left font-semibold w-px`}
                aria-hidden
              />
              <th scope="col" className={`${thPad} px-3 text-left font-semibold`}>
                {compact ? '평가 리포트' : '리포트 그룹 / 파일'}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {groups.map((g) => {
              const open = expandedStem.has(g.stem);
              const groupDateTime = formatRelativeTime(g.files[0]?.date ?? '', now);
              return (
                <Fragment key={g.stem}>
                  <tr className="border-b border-gray-100 transition-colors">
                    <td colSpan={2} className={`${groupRowPad} px-3 align-middle`}>
                      <button
                        type="button"
                        aria-expanded={open}
                        aria-label={open ? `${g.stem} 접기` : `${g.stem} 펼치기`}
                        onClick={() =>
                          setExpandedStem((prev) => {
                            const n = new Set(prev);
                            if (n.has(g.stem)) n.delete(g.stem);
                            else {
                              n.add(g.stem);
                              if (g.files[0]) onSelectFile(g.files[0].filename);
                            }
                            return n;
                          })
                        }
                        className="w-full text-left flex min-h-[44px] items-center justify-between gap-2 text-xs font-semibold text-gray-700 hover:text-gray-900"
                      >
                        <span className="min-w-0 flex-1">
                          <span className="block truncate">📂 {assessmentReportGroupLabel(g.stem)}</span>
                          <span className="block text-right text-[11px] font-normal text-gray-400">
                            {groupDateTime}
                          </span>
                        </span>
                        <span className="shrink-0 text-gray-500">{open ? '▴' : '▾'}</span>
                      </button>
                    </td>
                  </tr>
                  {open &&
                    g.files.map((f) => {
                      const sel = selectedFilename === f.filename;
                      const label = assessmentReportExtensionLabel(f.filename);
                      return (
                        <tr
                          key={f.filename}
                          className={`cursor-pointer hover:bg-gray-50/60 bg-white ${
                            sel ? 'bg-azure-light/80' : ''
                          }`}
                          onClick={() => onSelectFile(f.filename)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              onSelectFile(f.filename);
                            }
                          }}
                          role="button"
                          tabIndex={0}
                        >
                          <td className={`${childRowPad} pl-1 pr-0 align-top w-px`} aria-hidden />
                          <td className={`${childRowPad} pr-3 align-top`}>
                            <div
                              className={`border-l-2 pl-3 ml-1 ${indentChild} ${
                                sel ? 'border-l-azure-blue' : 'border-gray-200'
                              }`}
                            >
                              <div className="min-w-0">
                                <div className="font-medium text-gray-800 uppercase truncate">{label}</div>
                              </div>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
