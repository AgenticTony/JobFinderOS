'use client';

// CV upload with drag & drop — adapted from TalentHive's FileUpload/BulkUpload.

import { useEffect, useRef, useState } from 'react';
import { FileUp, Loader2, RefreshCw } from 'lucide-react';
import { cn } from '@/lib/utils';
import { apiErrorMessage, waitForAiJob } from '@/lib/api';
import { isPending, queueDetail } from '@/lib/aiQueue';
import { useT } from '@/i18n/LocaleContext';
import type { AiJob, Profile } from '@/types';

interface Props {
  // Resolves once the CV is SAVED. WO-31: when it resolves with a
  // profile whose extraction is queued, this component follows the job
  // and calls onExtracted when the fields have landed.
  onUploaded: (file: File) => Promise<unknown>;
  onExtracted?: () => Promise<void> | void;
  label?: string;
  hasExistingCv?: boolean;
}

export default function CvUpload({ onUploaded, onExtracted, label, hasExistingCv }: Props) {
  const tc = useT().console;
  const t = tc.cvUpload;
  const [reading, setReading] = useState<AiJob | null>(null);
  const unmounted = useRef(false);
  useEffect(() => {
    unmounted.current = false;
    return () => {
      unmounted.current = true;
    };
  }, []);
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
    if (!/\.(pdf|docx)$/i.test(file.name)) {
      setError(t.wrongType);
      return;
    }
    setError(null);
    setUploading(true);
    try {
      const result = await onUploaded(file);
      const extraction = (result as Profile | undefined)?.extraction;
      if (isPending(extraction)) {
        // Not awaited: the upload is done — the wizard may move on while
        // the queued reading of the CV finishes.
        void followExtraction(extraction);
      }
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setUploading(false);
    }
  };

  const followExtraction = async (job: AiJob) => {
    setReading(job);
    const finished = await waitForAiJob(job, setReading, 2000, () => unmounted.current);
    if (unmounted.current) return;
    setReading(null);
    if (finished.status === 'failed' && finished.error) setError(finished.error);
    await onExtracted?.();
  };

  const readingLine = (() => {
    if (!reading) return null;
    const detail = queueDetail(tc.queue, reading);
    const main = reading.status === 'running' ? tc.queue.cvRunning : tc.queue.cvQueued(detail.about);
    return { main, extra: detail.extra };
  })();

  const statusLine = (() => {
    if (!uploading) return undefined;
    if (elapsed < 12) return t.extracting;
    if (elapsed < 45) return t.talking(elapsed);
    return t.stillWorking(elapsed);
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
            ? 'border-signal bg-signal/5'
            : 'border-line-2 hover:border-signal/50 hover:bg-surface/40'
        )}
      >
        {uploading ? (
          <Loader2 className="h-8 w-8 animate-spin text-signal" />
        ) : hasExistingCv ? (
          <RefreshCw className="h-8 w-8 text-mid" />
        ) : (
          <FileUp className="h-8 w-8 text-mid" />
        )}
        <div>
          <p className="text-sm font-medium text-hi">
            {uploading
              ? statusLine
              : label ?? (hasExistingCv ? t.replace : t.drop)}
          </p>
          {!uploading && (
            <p className="mt-1 text-xs text-low">
              {t.hint}
            </p>
          )}
        </div>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
      </div>
      {readingLine && (
        <div className="mt-2 flex items-start gap-2 text-sm text-mid" role="status">
          <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-signal" />
          <div>
            <p>{readingLine.main}</p>
            {readingLine.extra && <p className="mt-0.5 text-xs text-low">{readingLine.extra}</p>}
          </div>
        </div>
      )}
      {error && <p className="mt-2 text-sm text-bad">{error}</p>}
      {/* OPS-6 disclosure REMOVED for beta (owner decision 2026-09-01):
          both point-of-collection panels (account box + this one) came out
          for tester-facing simplicity; /privacy remains the disclosure of
          record (linked from the login box footer). Restore <PrivacyNotice
          context="cv" /> here when beta ends — see CLAUDE.md open items. */}
    </div>
  );
}
