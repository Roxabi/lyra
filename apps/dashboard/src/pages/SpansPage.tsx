import { Card } from "@astryxdesign/core/Card";
import { Stack } from "@astryxdesign/core/Stack";
import { Text } from "@astryxdesign/core/Text";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { PageIntro } from "@/components/layout/PageIntro";
import { Input } from "@/components/ui/input";
import { fetchSpans, type SpanRow } from "@/lib/api";

export function SpansPage() {
  const [poolId, setPoolId] = useState("");
  const [jobId, setJobId] = useState("");
  const [component, setComponent] = useState("");
  const [selected, setSelected] = useState<SpanRow | null>(null);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["bff-spans", poolId, jobId, component],
    queryFn: () =>
      fetchSpans({
        pool_id: poolId || undefined,
        job_id: jobId || undefined,
        component: component || undefined,
      }),
    refetchInterval: 30_000,
  });

  return (
    <div className="space-y-6">
      <PageIntro>Raw OTel spans from otel-raw store (JSONL + SQLite).</PageIntro>

      <div className="grid gap-3 sm:grid-cols-3">
        <Input placeholder="pool_id" value={poolId} onChange={(e) => setPoolId(e.target.value)} />
        <Input placeholder="job_id" value={jobId} onChange={(e) => setJobId(e.target.value)} />
        <Input
          placeholder="component"
          value={component}
          onChange={(e) => setComponent(e.target.value)}
        />
      </div>

      <Card>
        <Stack gap={4}>
          <Text type="label">Spans {data ? `(${data.total})` : ""}</Text>
          {isLoading && <p className="text-sm text-muted-foreground">Loading…</p>}
          {isError && <p className="text-sm text-destructive">Failed to load spans.</p>}
          {data && data.items.length === 0 && (
            <p className="text-sm text-muted-foreground">No spans match filters.</p>
          )}
          {data && data.items.length > 0 && (
            <ul className="divide-y text-sm">
              {data.items.map((row) => (
                <li key={`${row.trace_id}:${row.span_id}`}>
                  <button
                    type="button"
                    className="w-full py-2 text-left hover:bg-muted/40"
                    onClick={() => setSelected(row)}
                  >
                    <span className="font-mono">{row.job_id ?? "—"}</span>
                    <span className="mx-2 text-muted-foreground">·</span>
                    <span>{row.component ?? row.name}</span>
                    <span className="mx-2 text-muted-foreground">·</span>
                    <span>{row.duration_ms.toFixed(1)} ms</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Stack>
      </Card>

      {selected && (
        <Card>
          <Stack gap={4}>
            <Text type="label">Raw JSON</Text>
            <pre className="max-h-96 overflow-auto rounded bg-muted p-3 text-xs">
              {JSON.stringify(selected, null, 2)}
            </pre>
          </Stack>
        </Card>
      )}
    </div>
  );
}
