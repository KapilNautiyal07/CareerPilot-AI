"use client";

import { useQueryClient } from "@tanstack/react-query";
import { FileUp, FlaskConical } from "lucide-react";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";
import { keys, useMeta } from "@/lib/queries";
import { cn } from "@/lib/utils";
import type { Resume } from "@/types/api";

const ACCEPT = ".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export function ResumeUploader({ onUploaded, compact }: { onUploaded?: (r: Resume) => void; compact?: boolean }) {
  const qc = useQueryClient();
  const meta = useMeta();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<"file" | "sample" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const maxMb = meta.data?.max_upload_mb ?? 5;

  async function done(r: Resume) {
    await Promise.all([keys.resumes, keys.profile].map((k) => qc.invalidateQueries({ queryKey: k })));
    qc.setQueryData(keys.resume(r.id), r);
    onUploaded?.(r);
  }

  async function upload(file: File) {
    setError(null);
    const ext = file.name.toLowerCase().split(".").pop();
    if (!["pdf", "docx"].includes(ext ?? "")) return setError("Only PDF and DOCX files are supported.");
    if (file.size > maxMb * 1024 * 1024) return setError(`The file is larger than ${maxMb} MB.`);
    const form = new FormData();
    form.append("file", file);
    setBusy("file");
    try {
      await done(await api<Resume>("/resumes", { form }));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
      if (input.current) input.current.value = "";
    }
  }

  async function sample() {
    setError(null);
    setBusy("sample");
    try {
      await done(await api<Resume>("/resumes/sample", { method: "POST" }));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const f = e.dataTransfer.files?.[0];
          if (f) void upload(f);
        }}
        className={cn(
          "flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed bg-card px-6 text-center transition-colors",
          compact ? "py-8" : "py-12",
          dragging && "border-primary bg-primary-soft/50",
        )}
      >
        <FileUp className="size-6 text-muted-foreground" aria-hidden />
        <div>
          <p className="text-sm font-medium">Drop your resume here or choose a file</p>
          <p className="text-xs text-muted-foreground">PDF or DOCX, up to {maxMb} MB. Text-based files only (no scans).</p>
        </div>
        <div className="flex flex-wrap justify-center gap-2">
          <Button type="button" onClick={() => input.current?.click()} loading={busy === "file"} disabled={!!busy}>
            Choose file
          </Button>
          <Button type="button" variant="outline" onClick={sample} loading={busy === "sample"} disabled={!!busy}>
            <FlaskConical /> Use demo resume
          </Button>
        </div>
        <input ref={input} type="file" accept={ACCEPT} className="sr-only" aria-label="Resume file" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
      </div>
      {busy && <p className="text-xs text-muted-foreground" aria-live="polite">Extracting text and analyzing skills…</p>}
      {error && <Alert tone="error">{error}</Alert>}
    </div>
  );
}
