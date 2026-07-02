import { useEffect, useState } from "react";
import { type DashboardJob, openJobsStream, postJobsStreamToken } from "@/lib/api";

export function useJobsLive(): {
  jobs: DashboardJob[];
  isError: boolean;
  isLoading: boolean;
} {
  const [jobs, setJobs] = useState<DashboardJob[]>([]);
  const [isError, setIsError] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let source: EventSource | null = null;
    let cancelled = false;

    async function connect() {
      try {
        const { stream_token } = await postJobsStreamToken();
        if (cancelled) return;
        source = openJobsStream(stream_token, (ev) => {
          if (ev.type === "snapshot") {
            setJobs(ev.jobs);
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

  return { jobs, isError, isLoading };
}
