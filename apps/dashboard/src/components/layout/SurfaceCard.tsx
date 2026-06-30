import type { ReactNode } from "react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface SurfaceCardProps {
  title?: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  contentClassName?: string;
  padding?: boolean;
}

/** Standard dashboard card — single border, no shadow. */
export function SurfaceCard({
  title,
  description,
  action,
  children,
  className,
  contentClassName,
  padding = true,
}: SurfaceCardProps) {
  const hasHeader = title || description || action;

  return (
    <Card className={cn("dashboard-surface border-border/60 shadow-none", className)}>
      {hasHeader ? (
        <CardHeader className="flex flex-row items-start justify-between space-y-0 pb-3">
          <div className="space-y-1">
            {title ? <CardTitle className="text-sm font-semibold">{title}</CardTitle> : null}
            {description ? <CardDescription>{description}</CardDescription> : null}
          </div>
          {action}
        </CardHeader>
      ) : null}
      <CardContent className={cn(!padding && "p-0", contentClassName)}>{children}</CardContent>
    </Card>
  );
}
