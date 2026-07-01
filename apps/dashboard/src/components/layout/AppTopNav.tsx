import { TopNav } from "@astryxdesign/core/TopNav";
import { useRouterState } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { UserMenu } from "@/components/UserMenu";
import { resolvePageTitle } from "@/lib/nav";
import { useShellTitleContext } from "@/lib/shell-title";

export function AppTopNav() {
  const { t } = useTranslation();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const { override } = useShellTitleContext();
  const title = resolvePageTitle(pathname);
  const label = override.literal ?? t(title.key);

  return (
    <TopNav
      label={t("nav.main")}
      className="bg-background/80 backdrop-blur"
      heading={
        // A plain <span>, not <h1>: Astryx mounts the `heading` slot twice below
        // the md breakpoint (mobile top-bar + drawer header), so a semantic
        // heading would produce duplicate <h1> nodes. Pages own their own <h1>.
        <span className="block min-w-0 truncate font-[family-name:var(--font-head)] text-lg font-semibold tracking-tight">
          {label}
        </span>
      }
      endContent={
        <div className="shrink-0 md:hidden">
          <UserMenu variant="compact" />
        </div>
      }
    />
  );
}
