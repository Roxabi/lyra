import { useRouterState } from "@tanstack/react-router";
import { resolvePageLabel } from "@/app/nav";
import { useShellTitle } from "@/app/shell-title";
import { useTheme } from "@/components/theme-provider";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <Button type="button" variant="outline" size="sm" onClick={() => setTheme(next)}>
      {next}
    </Button>
  );
}

export function AppHeader() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { override } = useShellTitle();
  const label = override.literal ?? resolvePageLabel(pathname);

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
      <SidebarTrigger />
      <Separator orientation="vertical" className="mr-2 h-4" />
      <span className="min-w-0 truncate font-heading text-lg font-semibold tracking-tight">
        {label}
      </span>
      <div className="ml-auto flex items-center gap-2">
        <ThemeToggle />
      </div>
    </header>
  );
}
