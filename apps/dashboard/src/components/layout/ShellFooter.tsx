import { UserMenu } from "@/components/UserMenu";
import { cn } from "@/lib/utils";

interface ShellFooterProps {
  collapsed?: boolean;
}

export function ShellFooter({ collapsed = false }: ShellFooterProps) {
  return (
    <div className={cn("border-t border-border/50 p-2")}>
      <UserMenu variant="sidebar" collapsed={collapsed} />
    </div>
  );
}
