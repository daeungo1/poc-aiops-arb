/**
 * MarkdownViewer — rich Markdown renderer for the chat sidebar.
 *
 * Features:
 * - GitHub Flavored Markdown (tables, task lists, strikethrough)
 * - Syntax-highlighted code blocks with copy button
 * - Styled inline code, blockquotes, links, and headings
 */

import { useState, useCallback, type ComponentPropsWithoutRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism';
import bash from 'react-syntax-highlighter/dist/esm/languages/prism/bash';
import json from 'react-syntax-highlighter/dist/esm/languages/prism/json';
import javascript from 'react-syntax-highlighter/dist/esm/languages/prism/javascript';
import typescript from 'react-syntax-highlighter/dist/esm/languages/prism/typescript';
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python';
import yaml from 'react-syntax-highlighter/dist/esm/languages/prism/yaml';
import hcl from 'react-syntax-highlighter/dist/esm/languages/prism/hcl';
import markup from 'react-syntax-highlighter/dist/esm/languages/prism/markup';
import sql from 'react-syntax-highlighter/dist/esm/languages/prism/sql';

SyntaxHighlighter.registerLanguage('bash', bash);
SyntaxHighlighter.registerLanguage('shell', bash);
SyntaxHighlighter.registerLanguage('json', json);
SyntaxHighlighter.registerLanguage('javascript', javascript);
SyntaxHighlighter.registerLanguage('js', javascript);
SyntaxHighlighter.registerLanguage('typescript', typescript);
SyntaxHighlighter.registerLanguage('ts', typescript);
SyntaxHighlighter.registerLanguage('python', python);
SyntaxHighlighter.registerLanguage('py', python);
SyntaxHighlighter.registerLanguage('yaml', yaml);
SyntaxHighlighter.registerLanguage('yml', yaml);
SyntaxHighlighter.registerLanguage('hcl', hcl);
SyntaxHighlighter.registerLanguage('terraform', hcl);
SyntaxHighlighter.registerLanguage('markup', markup);
SyntaxHighlighter.registerLanguage('html', markup);
SyntaxHighlighter.registerLanguage('xml', markup);
SyntaxHighlighter.registerLanguage('sql', sql);

// ── Copy button for code blocks ──────────────────────────────────

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard not available */
    }
  }, [text]);

  return (
    <button
      onClick={handleCopy}
      className="absolute top-2 right-2 text-[10px] px-2 py-1 rounded
                 bg-gray-200/80 hover:bg-gray-300 text-gray-600
                 transition-colors opacity-0 group-hover:opacity-100"
      title="복사"
    >
      {copied ? '✓ 복사됨' : '📋 복사'}
    </button>
  );
}

// ── Custom renderers ─────────────────────────────────────────────

type CodeProps = ComponentPropsWithoutRef<'code'> & {
  inline?: boolean;
};

function CodeBlock({ inline, className, children, ...props }: CodeProps) {
  const match = /language-(\w+)/.exec(className || '');
  const code = String(children).replace(/\n$/, '');

  if (!inline && match) {
    return (
      <div className="relative group my-2">
        <div className="flex items-center justify-between bg-gray-700 text-gray-300 text-[10px] px-3 py-1 rounded-t-lg">
          <span>{match[1]}</span>
        </div>
        <CopyButton text={code} />
        <SyntaxHighlighter
          style={oneLight}
          language={match[1]}
          PreTag="div"
          customStyle={{
            margin: 0,
            borderTopLeftRadius: 0,
            borderTopRightRadius: 0,
            borderBottomLeftRadius: '0.5rem',
            borderBottomRightRadius: '0.5rem',
            fontSize: '0.8rem',
            padding: '0.75rem',
          }}
        >
          {code}
        </SyntaxHighlighter>
      </div>
    );
  }

  // Multi-line code without language
  if (!inline && code.includes('\n')) {
    return (
      <div className="relative group my-2">
        <CopyButton text={code} />
        <pre className="bg-slate-800 text-slate-200 rounded-lg p-3 overflow-x-auto text-[0.8rem] leading-relaxed">
          <code {...props}>{children}</code>
        </pre>
      </div>
    );
  }

  // Inline code
  return (
    <code
      className="bg-blue-50 text-blue-800 px-1.5 py-0.5 rounded text-[0.8rem] font-mono"
      {...props}
    >
      {children}
    </code>
  );
}

// ── MarkdownViewer component ─────────────────────────────────────

interface MarkdownViewerProps {
  content: string;
  className?: string;
}

export function MarkdownViewer({ content, className = '' }: MarkdownViewerProps) {
  return (
    <div className={`markdown-viewer ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Code blocks
          code: CodeBlock as any,

          // Tables
          table: ({ children }) => (
            <div className="overflow-x-auto my-2 rounded-lg border border-gray-200">
              <table className="min-w-full text-xs">{children}</table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="bg-gray-50 text-gray-600 font-semibold">{children}</thead>
          ),
          th: ({ children }) => (
            <th className="px-3 py-1.5 text-left border-b border-gray-200">{children}</th>
          ),
          td: ({ children }) => (
            <td className="px-3 py-1.5 border-b border-gray-100">{children}</td>
          ),

          // Links
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-azure-blue hover:underline"
            >
              {children}
            </a>
          ),

          // Blockquotes
          blockquote: ({ children }) => (
            <blockquote className="border-l-3 border-azure-blue/40 bg-blue-50/50 pl-3 py-1 my-2 text-gray-600 italic rounded-r">
              {children}
            </blockquote>
          ),

          // Headings
          h1: ({ children }) => (
            <h1 className="text-base font-bold mt-3 mb-1.5 text-gray-900 border-b border-gray-200 pb-1">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="text-sm font-bold mt-2.5 mb-1 text-gray-800">{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 className="text-sm font-semibold mt-2 mb-1 text-gray-700">{children}</h3>
          ),

          // Lists
          ul: ({ children }) => (
            <ul className="list-disc pl-5 my-1 space-y-0.5">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="list-decimal pl-5 my-1 space-y-0.5">{children}</ol>
          ),
          li: ({ children }) => (
            <li className="text-gray-700 leading-relaxed">{children}</li>
          ),

          // Paragraphs
          p: ({ children }) => (
            <p className="my-1 leading-relaxed">{children}</p>
          ),

          // Horizontal rule
          hr: () => <hr className="my-3 border-gray-200" />,

          // Task list (input checkbox from GFM)
          input: ({ checked, ...props }) => (
            <input
              type="checkbox"
              checked={checked}
              readOnly
              className="mr-1.5 accent-azure-blue"
              {...props}
            />
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

// ── CodeViewer — syntax-highlighted raw code viewer ───────────────

interface CodeViewerProps {
  code: string;
  language?: string;
  filename?: string;
  className?: string;
}

export function CodeViewer({ code, language = 'text', filename, className = '' }: CodeViewerProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard not available */
    }
  }, [code]);

  return (
    <div className={`relative group ${className}`}>
      {/* Header bar */}
      <div className="flex items-center justify-between bg-gray-700 text-gray-300 text-[11px] px-4 py-1.5 rounded-t-lg">
        <div className="flex items-center gap-2">
          {filename && <span className="font-medium text-gray-200">{filename}</span>}
          <span className="bg-gray-600 px-1.5 py-0.5 rounded text-[10px]">{language}</span>
        </div>
        <button
          onClick={handleCopy}
          className="text-[10px] px-2 py-0.5 rounded hover:bg-gray-600 transition-colors"
        >
          {copied ? '✓ 복사됨' : '📋 복사'}
        </button>
      </div>
      <SyntaxHighlighter
        style={oneLight}
        language={language}
        showLineNumbers
        PreTag="div"
        customStyle={{
          margin: 0,
          borderTopLeftRadius: 0,
          borderTopRightRadius: 0,
          borderBottomLeftRadius: '0.5rem',
          borderBottomRightRadius: '0.5rem',
          fontSize: '0.8rem',
          padding: '1rem',
        }}
        lineNumberStyle={{ color: '#94a3b8', fontSize: '0.7rem', paddingRight: '1rem' }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}
