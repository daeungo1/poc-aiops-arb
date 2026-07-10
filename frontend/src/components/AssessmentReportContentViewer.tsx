import { useMemo, useState } from 'react';
import { MarkdownViewer, CodeViewer } from './MarkdownViewer';
import { assessmentReportBasename } from '../lib/assessmentReportPaths';

export type AssessmentContentKind = 'markdown' | 'html' | 'javascript' | 'json' | 'plaintext';

export function assessmentContentKindFromFilename(filename: string): AssessmentContentKind {
  const base = assessmentReportBasename(filename).toLowerCase();
  if (base.endsWith('.md')) return 'markdown';
  if (base.endsWith('.html') || base.endsWith('.htm')) return 'html';
  if (base.endsWith('.json')) return 'json';
  if (base.endsWith('.js') || base.endsWith('.mjs') || base.endsWith('.cjs')) return 'javascript';
  return 'plaintext';
}

interface AssessmentReportContentViewerProps {
  content: string;
  filename: string;
  className?: string;
}

export function AssessmentReportContentViewer({
  content,
  filename,
  className = '',
}: AssessmentReportContentViewerProps) {
  const kind = useMemo(() => assessmentContentKindFromFilename(filename), [filename]);
  const basename = useMemo(() => assessmentReportBasename(filename), [filename]);
  const [htmlView, setHtmlView] = useState<'preview' | 'source'>('preview');

  const formattedJson = useMemo(() => {
    if (kind !== 'json') return content;
    try {
      const parsed = JSON.parse(content);
      return JSON.stringify(parsed, null, 2);
    } catch {
      return content;
    }
  }, [content, kind]);

  if (kind === 'markdown') {
    return (
      <div className={className}>
        <MarkdownViewer content={content} className="text-sm" />
      </div>
    );
  }

  if (kind === 'json') {
    return (
      <div className={className}>
        <CodeViewer code={formattedJson} language="json" filename={basename} />
      </div>
    );
  }

  if (kind === 'javascript') {
    return (
      <div className={className}>
        <CodeViewer code={content} language="javascript" filename={basename} />
      </div>
    );
  }

  if (kind === 'html') {
    return (
      <div className={className}>
        <div className="flex gap-0.5 mb-3 p-0.5 bg-gray-100 rounded-lg w-fit">
          <button
            type="button"
            onClick={() => setHtmlView('preview')}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
              htmlView === 'preview'
                ? 'bg-white text-gray-900 shadow-sm'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            미리보기
          </button>
          <button
            type="button"
            onClick={() => setHtmlView('source')}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
              htmlView === 'source'
                ? 'bg-white text-gray-900 shadow-sm'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            소스
          </button>
        </div>
        {htmlView === 'preview' ? (
          <div className="rounded-lg border border-gray-200 overflow-hidden bg-white shadow-sm">
            <iframe
              title={`HTML 미리보기: ${basename}`}
              srcDoc={content}
              sandbox="allow-scripts allow-same-origin"
              className="w-full min-h-[min(70vh,720px)] h-[70vh] border-0 block bg-white"
            />
          </div>
        ) : (
          <CodeViewer code={content} language="markup" filename={basename} />
        )}
      </div>
    );
  }

  return (
    <div className={className}>
      <CodeViewer code={content} language="text" filename={basename} />
    </div>
  );
}
