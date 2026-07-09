import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface PresenceBadgeProps {
  platform: string;
  present: boolean;
  className?: string;
}

const PLATFORM_LABEL: Record<string, string> = {
  telegram: "TG",
  discord: "DC",
  web: "Web",
};

export function PresenceBadge({ platform, present, className }: PresenceBadgeProps) {
  const label = PLATFORM_LABEL[platform] ?? platform;
  return (
    <Badge
      variant={present ? "default" : "outline"}
      className={cn("text-[10px]", !present && "opacity-50", className)}
    >
      {label}
    </Badge>
  );
}
