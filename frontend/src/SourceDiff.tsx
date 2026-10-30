import {useEffect, useLayoutEffect, useRef} from 'react';
import * as monaco from 'monaco-editor';

type Props = {
  projectId: string;
  path: string;
  language: string;
  original: string;
  modified: string;
};

/** Own the diff widget and its private models together, including teardown order. */
export default function SourceDiff({projectId, path, language, original, modified}: Props) {
  const host = useRef<HTMLDivElement>(null);
  const models = useRef<{original: monaco.editor.ITextModel; modified: monaco.editor.ITextModel} | null>(null);

  useLayoutEffect(() => {
    if (!host.current) return;
    const baseline = monaco.editor.createModel(original, language,
      monaco.Uri.from({scheme: 'codewarden-baseline', path: `/${projectId}/${path}`}));
    const draft = monaco.editor.createModel(modified, language,
      monaco.Uri.from({scheme: 'codewarden-diff', path: `/${projectId}/${path}`}));
    const widget = monaco.editor.createDiffEditor(host.current, {
      theme: 'vs-dark',
      automaticLayout: true,
      readOnly: true,
      // Avoid Monaco 0.52's unhandled word-highlighter Delayer cancellation on teardown.
      occurrencesHighlight: 'off',
      originalEditable: false,
      minimap: {enabled: false},
      fontSize: 14,
      renderSideBySide: true,
      scrollBeyondLastLine: false,
    });
    widget.setModel({original: baseline, modified: draft});
    models.current = {original: baseline, modified: draft};
    return () => {
      // A live DiffEditorWidget must release its listeners before either model dies.
      widget.setModel(null);
      widget.dispose();
      baseline.dispose();
      draft.dispose();
      models.current = null;
    };
  }, [projectId, path, language]);

  useEffect(() => {
    if (models.current && models.current.original.getValue() !== original)
      models.current.original.setValue(original);
  }, [original]);
  useEffect(() => {
    if (models.current && models.current.modified.getValue() !== modified)
      models.current.modified.setValue(modified);
  }, [modified]);

  return <div ref={host} style={{height: '100%', width: '100%'}} aria-label="导入基线与当前草稿差异"/>;
}
