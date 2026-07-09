import { X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface FilterChipProps {
  label: string;
  active: boolean;
  onToggle: () => void;
  className?: string;
}

export function FilterChip({ label, active, onToggle, className }: FilterChipProps) {
  return (
    <Button
      type="button"
      variant={active ? "default" : "outline"}
      size="sm"
      className={cn("h-7 gap-1 rounded-full px-2.5 text-xs", className)}
      onClick={onToggle}
      aria-pressed={active}
    >
      {label}
      {active ? <X className="size-3" aria-hidden /> : null}
    </Button>
  );
}

export function StatusBadge({
  status,
  variant,
}: {
  status: string;
  variant?: "default" | "secondary" | "destructive" | "outline";
}) {
  return <Badge variant={variant ?? "outline"}>{status}</Badge>;
}
