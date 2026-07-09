import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { jobStatusClass } from "@/shared/lib/job-status";

export function JobStatusBadge({ status, className }: { status: string; className?: string }) {
  return (
    <Badge variant="outline" className={cn(jobStatusClass(status), className)}>
      {status}
    </Badge>
  );
}
