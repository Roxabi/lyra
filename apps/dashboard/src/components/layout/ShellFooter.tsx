import { ThemeToggle } from "@/components/ThemeToggle";
import { cn } from "@/lib/utils";

interface ShellFooterProps {
  collapsed?: boolean;
}

export function ShellFooter({ collapsed = false }: ShellFooterProps) {
  return (
    <div
      className={cn(
        "flex items-center p-2",
        collapsed ? "justify-center" : "justify-end gap-2 px-3",
      )}
    >
      <ThemeToggle />
    </div>
  );
}
