import { useEffect, useState } from "react";
import {
  type PipelineRun,
  openPipelineStream,
  postPipelineStreamToken,
} from "@/lib/api";

export function usePipelineRuns(): {
  runs: PipelineRun[];
  isError: boolean;
  isLoading: boolean;
} {
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [isError, setIsError] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let source: EventSource | null = null;
    let cancelled = false;

    async function connect() {
      try {
        const { stream_token } = await postPipelineStreamToken();
        if (cancelled) return;
        source = openPipelineStream(stream_token, (ev) => {
          if (ev.type === "snapshot") {
            setRuns(ev.runs);
            setIsLoading(false);
            setIsError(false);
          } else if (ev.type === "error") {
            setIsError(true);
            setIsLoading(false);
          }
        });
        source.onerror = () => {
          if (!cancelled) {
            setIsError(true);
            setIsLoading(false);
          }
        };
      } catch {
        if (!cancelled) {
          setIsError(true);
          setIsLoading(false);
        }
      }
    }

    void connect();
    return () => {
      cancelled = true;
      source?.close();
    };
  }, []);

  return { runs, isError, isLoading };
}