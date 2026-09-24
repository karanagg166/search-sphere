"use client";

import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useDropzone } from "react-dropzone";
import { UploadCloud, FileText, AlertCircle, CheckCircle, Loader2, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/context/auth-context";
import { uploadDocument } from "@/lib/api";

interface DocumentUploadProps {
  onRequireAuth?: () => void;
}

export function DocumentUpload({ onRequireAuth }: DocumentUploadProps) {
  const { isAuthenticated } = useAuth();
  const queryClient = useQueryClient();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadDocument(file),
    onSuccess: (data) => {
      setErrorMessage(null);
      setSuccessMessage(`Successfully uploaded "${data.filename}" (${(data.file_size / 1024).toFixed(1)} KB)`);
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      setTimeout(() => setSuccessMessage(null), 5000);
    },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    onError: (error: any) => {
      setSuccessMessage(null);
      const detail =
        error?.response?.data?.detail ||
        error?.message ||
        "Upload failed. Please ensure the file is a valid PDF under 20MB.";
      setErrorMessage(detail);
    },
  });

  const { getRootProps, getInputProps, isDragActive, isDragReject } = useDropzone({
    accept: {
      "application/pdf": [".pdf"],
    },
    maxFiles: 1,
    disabled: !isAuthenticated || uploadMutation.isPending,
    onDrop: (acceptedFiles) => {
      setErrorMessage(null);
      setSuccessMessage(null);

      if (!acceptedFiles || acceptedFiles.length === 0) {
        setErrorMessage("Please select a valid PDF file.");
        return;
      }

      const file = acceptedFiles[0];
      uploadMutation.mutate(file);
    },
  });

  return (
    <div className="p-6 rounded-xl border border-border bg-card flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <UploadCloud className="w-5 h-5 text-primary" />
          Upload Document (PDF)
        </h2>
        {isAuthenticated && (
          <span className="text-[11px] bg-primary/10 text-primary px-2 py-0.5 rounded-full font-medium">
            Ready to Ingest
          </span>
        )}
      </div>

      {!isAuthenticated ? (
        <div className="border-2 border-dashed border-border rounded-lg p-8 text-center flex flex-col items-center justify-center gap-3 bg-muted/20">
          <div className="p-3 rounded-full bg-muted text-muted-foreground">
            <Lock className="w-6 h-6" />
          </div>
          <div>
            <p className="text-sm font-medium">Sign in required</p>
            <p className="text-xs text-muted-foreground mt-0.5">
              You must be signed in to upload and manage documents.
            </p>
          </div>
          {onRequireAuth && (
            <Button
              variant="default"
              size="sm"
              onClick={onRequireAuth}
              className="mt-1 text-xs"
            >
              Sign In to Upload
            </Button>
          )}
        </div>
      ) : (
        <div
          {...getRootProps()}
          className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-all ${
            isDragActive
              ? "border-primary bg-primary/10 scale-[0.99]"
              : isDragReject
              ? "border-destructive bg-destructive/10"
              : "border-border hover:border-primary/50 hover:bg-muted/30"
          } ${uploadMutation.isPending ? "opacity-60 cursor-not-allowed" : ""}`}
        >
          <input {...getInputProps()} />

          <div className="flex flex-col items-center justify-center gap-2">
            {uploadMutation.isPending ? (
              <>
                <Loader2 className="w-9 h-9 text-primary animate-spin" />
                <p className="text-sm font-medium">Validating & Persisting to Storage...</p>
                <p className="text-xs text-muted-foreground">
                  Storing original PDF in object storage & metadata in PostgreSQL
                </p>
              </>
            ) : (
              <>
                <div className="p-3 rounded-full bg-primary/10 text-primary">
                  <FileText className="w-7 h-7" />
                </div>
                <div>
                  <p className="text-sm font-medium">
                    {isDragActive ? "Drop the PDF here..." : "Drag & drop a PDF document, or click to browse"}
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Accepts valid PDF files up to 20MB. Safe object storage persistence.
                  </p>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* Notifications */}
      {errorMessage && (
        <div className="p-3 rounded-lg bg-destructive/10 border border-destructive/30 text-destructive flex items-center gap-2 text-xs">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}

      {successMessage && (
        <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 flex items-center gap-2 text-xs">
          <CheckCircle className="w-4 h-4 shrink-0" />
          <span>{successMessage}</span>
        </div>
      )}
    </div>
  );
}
