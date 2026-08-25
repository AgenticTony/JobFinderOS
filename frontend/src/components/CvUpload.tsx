'use client';

// CV upload with drag & drop — adapted from TalentHive's FileUpload/BulkUpload.

import { useEffect, useRef, useState } from 'react';
import { FileUp, Loader2, RefreshCw } from 'lucide-react';
import { cn } from '@/lib/utils';

interface Props {
  onUploaded: (file: File) => Promise<void>;
  label?: string;
  hasExistingCv?: boolean;
}

export default function CvUpload({ onUploaded, label, hasExistingCv }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);

  // Show elapsed time while the AI works so it never looks frozen
  useEffect(() => {
    if (!uploading) {
      setElapsed(0);
      return;
    }
    const timer = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, [uploading]);

  const handleFiles = async (files: FileList | null) => {
    const file = files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setError('Please upload a PDF file');
      return;
    }
    setError(null);
    setUploading(true);
    try {
      await onUploaded(file);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const statusLine = (() => {
    if (!uploading) return undefined;
    if (elapsed < 12) return 'Extracting & analyzing your CV…';
    if (elapsed < 45) return `Talking to GLM (${elapsed}s) — first-time analysis can take a minute…`;
    return `Still working (${elapsed}s) — Z.ai can be slow; the app stays usable, this will finish on its own.`;
  })();

  return (
    <div>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          handleFiles(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        className={cn(
          'flex cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed p-8 text-center transition-colors',
          dragging
            ? 'border-sky-500 bg-sky-500/5'
            : 'border-white/15 hover:border-white/30 hover:bg-white/[0.02]'
        )}
      >
        {uploading ? (
          <Loader2 className="h-8 w-8 animate-spin text-sky-400" />
        ) : hasExistingCv ? (
          <RefreshCw className="h-8 w-8 text-zinc-400" />
        ) : (
          <FileUp className="h-8 w-8 text-zinc-400" />
        )}
        <div>
          <p className="text-sm font-medium text-zinc-200">
            {uploading
              ? statusLine
              : label ?? (hasExistingCv ? 'Replace your CV' : 'Drop your CV (PDF) here')}
          </p>
          {!uploading && (
            <p className="mt-1 text-xs text-zinc-500">
              PDF up to 5MB — text is extracted and profiled by AI
            </p>
          )}
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,application/pdf"
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </div>
      {error && <p className="mt-2 text-sm text-rose-400">{error}</p>}
    </div>
  );
}
