import { Link, useRouterState } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { resolvePageTitle } from "@/app/nav";
import { useShellTitle } from "@/app/shell-title";
import { LocaleToggle } from "@/components/app-shell/locale-toggle";
import { useTheme } from "@/components/theme-provider";
import { Button, buttonVariants } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { useAuth } from "@/features/auth/auth-context";
import { OrgSwitcher } from "@/features/auth/components/org-switcher";
import { cn } from "@/lib/utils";

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const { t } = useTranslation("common");
  const next = theme === "dark" ? "light" : "dark";
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      aria-label={t("theme.toggle")}
      onClick={() => setTheme(next)}
    >
      {t(`theme.${next}`)}
    </Button>
  );
}

export function AppHeader() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { override } = useShellTitle();
  const { t } = useTranslation("common");
  const { t: ta } = useTranslation("auth");
  const { key } = resolvePageTitle(pathname);
  const label = override.literal ?? t(key);
  const { status, logout, session } = useAuth();

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
      <SidebarTrigger />
      <Separator orientation="vertical" className="mr-2 h-4" />
      <span className="min-w-0 truncate font-heading text-lg font-semibold tracking-tight">
        {label}
      </span>
      <div className="ml-auto flex items-center gap-2">
        <OrgSwitcher />
        {status === "authenticated" ? (
          <>
            <Link
              to="/account/links"
              className={cn(buttonVariants({ variant: "ghost", size: "sm" }))}
            >
              {ta("session.linkAccounts")}
            </Link>
            <span className="hidden max-w-[8rem] truncate text-xs text-muted-foreground sm:inline">
              {session?.user?.email ?? session?.principal.user_id}
            </span>
            <Button type="button" variant="outline" size="sm" onClick={() => void logout()}>
              {ta("session.signOut")}
            </Button>
          </>
        ) : status === "anonymous" ? (
          <Link
            to="/login"
            search={{ redirect: undefined }}
            className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
          >
            {ta("login.submit")}
          </Link>
        ) : null}
        <LocaleToggle />
        <ThemeToggle />
      </div>
    </header>
  );
}
