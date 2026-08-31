"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchHealth } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Activity,
  CheckCircle2,
  Database,
  Layers,
  Search,
  Server,
  UploadCloud,
  XCircle,
} from "lucide-react";
import { useDropzone } from "react-dropzone";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

const searchSchema = z.object({
  query: z.string().min(1, "Query cannot be empty"),
});

type SearchFormData = z.infer<typeof searchSchema>;

export default function Home() {
  const { data: health, isLoading, isError } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 10000,
  });

  const { register, handleSubmit } = useForm<SearchFormData>({
    resolver: zodResolver(searchSchema),
    defaultValues: { query: "" },
  });

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: () => {
      // Document upload handler placeholder
    },
  });

  const onSubmit = handleSubmit(() => {
    // Search query handler placeholder
  });

  return (
    <main className="min-h-screen bg-background text-foreground p-6 md:p-12 max-w-6xl mx-auto flex flex-col gap-8">
      {/* Header */}
      <header className="border-b border-border pb-6 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
            <Search className="w-8 h-8 text-primary" />
            Semantic Search & RAG Monorepo
          </h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Scalable document ingestion, vector retrieval, and background pipeline environment.
          </p>
        </div>

        {/* Backend Health Badge */}
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-full border border-border bg-card text-sm font-medium">
          <Activity className="w-4 h-4 text-primary animate-pulse" />
          <span>API Backend:</span>
          {isLoading ? (
            <span className="text-muted-foreground">Checking...</span>
          ) : isError ? (
            <span className="text-red-400 flex items-center gap-1">
              <XCircle className="w-4 h-4" /> Offline / Connecting
            </span>
          ) : (
            <span className="text-green-400 flex items-center gap-1">
              <CheckCircle2 className="w-4 h-4" /> {health?.status === "ok" ? "Healthy (v" + health.version + ")" : "Connected"}
            </span>
          )}
        </div>
      </header>

      {/* Services Grid */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="p-5 rounded-lg border border-border bg-card flex flex-col gap-2">
          <div className="flex items-center gap-2 text-primary font-semibold">
            <Server className="w-5 h-5" />
            FastAPI Backend & Worker
          </div>
          <p className="text-xs text-muted-foreground">
            Python 3.12, Uvicorn, Dramatiq worker with Redis broker, Docling, PyMuPDF, and LLM SDKs.
          </p>
          <div className="mt-auto pt-2 text-xs font-mono text-muted-foreground">
            Port: 8000 / Hot reload enabled
          </div>
        </div>

        <div className="p-5 rounded-lg border border-border bg-card flex flex-col gap-2">
          <div className="flex items-center gap-2 text-primary font-semibold">
            <Database className="w-5 h-5" />
            Vector & Relational Storage
          </div>
          <p className="text-xs text-muted-foreground">
            PostgreSQL (5432), Qdrant Vector DB (6333), Redis Cache & Broker (6379), MinIO (9000/9001).
          </p>
          <div className="mt-auto pt-2 text-xs font-mono text-muted-foreground">
            Persistent volumes mounted
          </div>
        </div>

        <div className="p-5 rounded-lg border border-border bg-card flex flex-col gap-2">
          <div className="flex items-center gap-2 text-primary font-semibold">
            <Layers className="w-5 h-5" />
            Local LLM & Embeddings
          </div>
          <p className="text-xs text-muted-foreground">
            Ollama running on port 11434 with persistent model volume for local inference (Qwen, etc.).
          </p>
          <div className="mt-auto pt-2 text-xs font-mono text-muted-foreground">
            Ollama host: 11434
          </div>
        </div>
      </section>

      {/* Interface Placeholders */}
      <section className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-2">
        {/* Search Box Form */}
        <div className="p-6 rounded-xl border border-border bg-card flex flex-col gap-4">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <Search className="w-5 h-5 text-primary" />
            Query Interface Placeholder
          </h2>
          <form onSubmit={onSubmit} className="flex gap-2">
            <input
              {...register("query")}
              placeholder="Enter search query..."
              className="flex-1 px-3 py-2 rounded-md bg-background border border-input text-sm focus:outline-none focus:ring-1 focus:ring-ring"
            />
            <Button type="submit">Search</Button>
          </form>
          <p className="text-xs text-muted-foreground">
            Ready for vector search query implementation.
          </p>
        </div>

        {/* Dropzone Component */}
        <div className="p-6 rounded-xl border border-border bg-card flex flex-col gap-4">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <UploadCloud className="w-5 h-5 text-primary" />
            Document Upload Dropzone Placeholder
          </h2>
          <div
            {...getRootProps()}
            className={`border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-colors ${
              isDragActive
                ? "border-primary bg-primary/10"
                : "border-border hover:border-primary/50"
            }`}
          >
            <input {...getInputProps()} />
            <UploadCloud className="w-8 h-8 mx-auto text-muted-foreground mb-2" />
            <p className="text-sm text-muted-foreground">
              Drag & drop documents (PDF, DOCX, etc.) here, or click to browse
            </p>
          </div>
        </div>
      </section>
    </main>
  );
}
