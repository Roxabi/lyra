/**
 * Sidebar footer account menu — consolidates top-bar chrome:
 * org switch, link accounts, theme, locale, change password, sign out.
 * Avoid nested menus (Base UI #31 / nativeButton mismatches).
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import {
  Building2,
  ChevronsUpDown,
  KeyRound,
  Languages,
  Link2,
  LogOut,
  Moon,
  Sun,
  UserRound,
} from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useTheme } from "@/components/theme-provider";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  sidebarMenuButtonVariants,
  useSidebar,
} from "@/components/ui/sidebar";
import { createOrg, fetchOrgs } from "@/features/auth/api";
import { useAuth } from "@/features/auth/auth-context";
import { ChangePasswordDialog } from "@/features/auth/change-password-dialog";
import i18n, { persistLocale } from "@/i18n";
import { cn } from "@/lib/utils";

function userLabel(session: ReturnType<typeof useAuth>["session"]): {
  primary: string;
  secondary: string;
  initials: string;
} {
  const email = session?.user?.email?.trim() || null;
  const name = session?.user?.display_name?.trim() || null;
  const uid = session?.principal.user_id ?? "";
  const primary = name || email || uid || "—";
  const secondary = email && name ? email : email || uid;
  const seed = (name || email || uid || "?").trim();
  const initials = seed
    .split(/[\s@._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("")
    .slice(0, 2);
  return { primary, secondary, initials: initials || "?" };
}

export function NavUser() {
  const { t } = useTranslation("common");
  const { t: ta } = useTranslation("auth");
  const { isMobile } = useSidebar();
  const navigate = useNavigate();
  const { status, session, logout, activeOrgId, setOrg } = useAuth();
  const { theme, setTheme } = useTheme();
  const qc = useQueryClient();
  const [orgName, setOrgName] = useState("");
  const [passwordOpen, setPasswordOpen] = useState(false);

  const orgsQ = useQuery({
    queryKey: ["auth", "orgs"],
    queryFn: fetchOrgs,
    enabled: status === "authenticated",
    // Soft-fail: empty org list if hub/BFF briefly unavailable (no toast storm).
    retry: 1,
  });
  const create = useMutation({
    mutationFn: () => createOrg(orgName.trim()),
    onSuccess: async (data) => {
      setOrgName("");
      setOrg(data.org.id);
      await qc.invalidateQueries({ queryKey: ["auth", "orgs"] });
    },
  });

  if (status === "loading") return null;

  if (status === "anonymous") {
    return (
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton
            size="lg"
            tooltip={ta("login.submit")}
            render={<Link to="/login" search={{ redirect: undefined }} />}
          >
            <UserRound />
            <span>{ta("login.submit")}</span>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    );
  }

  const { primary, secondary, initials } = userLabel(session);
  const orgs = orgsQ.data?.orgs ?? [];
  const currentOrg = orgs.find((o) => o.id === activeOrgId);
  const locale = i18n.language === "en" ? "en" : "fr";
  const nextLocale = locale === "fr" ? "en" : "fr";
  const nextTheme = theme === "dark" ? "light" : "dark";

  return (
    <>
      <SidebarMenu>
        <SidebarMenuItem>
          <DropdownMenu>
            {/*
              Native <button> trigger only — NEVER render={<SidebarMenuButton />}.
              Menu.Trigger is nativeButton=true; composing with SidebarMenuButton
              (useRender host) still trips Base UI production error #31 on M1.
            */}
            <DropdownMenuTrigger
              type="button"
              aria-label={t("userMenu.open", { name: primary })}
              className={cn(
                sidebarMenuButtonVariants({ size: "lg" }),
                "data-open:bg-sidebar-accent data-open:text-sidebar-accent-foreground",
              )}
            >
              <span
                className={cn(
                  "flex size-8 shrink-0 items-center justify-center rounded-lg bg-sidebar-primary text-xs font-semibold text-sidebar-primary-foreground",
                )}
                aria-hidden
              >
                {initials}
              </span>
              <div className="grid min-w-0 flex-1 text-left text-sm leading-tight">
                <span className="truncate font-semibold">{primary}</span>
                <span className="truncate text-xs text-muted-foreground">
                  {currentOrg?.name ?? ta("org.personal")}
                  {secondary && secondary !== primary ? ` · ${secondary}` : ""}
                </span>
              </div>
              <ChevronsUpDown className="ml-auto size-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent
              className="w-64 min-w-56 rounded-lg"
              side={isMobile ? "bottom" : "right"}
              align="end"
              sideOffset={4}
            >
              {/* Plain header — Menu.GroupLabel must sit inside Menu.Group. */}
              <div className="flex items-center gap-2 px-1 py-1.5 text-left text-sm">
                <span className="flex size-8 items-center justify-center rounded-lg bg-sidebar-primary text-xs font-semibold text-sidebar-primary-foreground">
                  {initials}
                </span>
                <div className="grid min-w-0 flex-1 text-left text-sm leading-tight">
                  <span className="truncate font-semibold">{primary}</span>
                  <span className="truncate text-xs text-muted-foreground">{secondary}</span>
                </div>
              </div>

              <DropdownMenuSeparator />

              <DropdownMenuGroup>
                <DropdownMenuLabel>{ta("org.switch")}</DropdownMenuLabel>
                <DropdownMenuItem onClick={() => setOrg(null)}>
                  <UserRound />
                  {ta("org.personal")}
                  {!activeOrgId ? (
                    <span className="ml-auto text-xs text-muted-foreground">✓</span>
                  ) : null}
                </DropdownMenuItem>
                {orgs.map((o) => (
                  <DropdownMenuItem key={o.id} onClick={() => setOrg(o.id)}>
                    <Building2 />
                    <span className="truncate">{o.name}</span>
                    {activeOrgId === o.id ? (
                      <span className="ml-auto text-xs text-muted-foreground">✓</span>
                    ) : null}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuGroup>

              <div className="flex flex-col gap-1.5 p-2">
                <Input
                  placeholder={ta("org.newName")}
                  value={orgName}
                  onChange={(e) => setOrgName(e.target.value)}
                  onKeyDown={(e) => {
                    // Keep typing from selecting menu items / closing.
                    e.stopPropagation();
                    if (e.key === "Enter" && orgName.trim() && !create.isPending) {
                      e.preventDefault();
                      create.mutate();
                    }
                  }}
                  // Prevent Base UI menu typeahead / focus traps from stealing keys.
                  onClick={(e) => e.stopPropagation()}
                />
                <button
                  type="button"
                  className="inline-flex h-7 items-center justify-center rounded-md bg-primary px-2 text-xs font-medium text-primary-foreground disabled:opacity-50"
                  disabled={!orgName.trim() || create.isPending}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    create.mutate();
                  }}
                >
                  {ta("org.create")}
                </button>
              </div>

              <DropdownMenuSeparator />

              <DropdownMenuGroup>
                <DropdownMenuItem
                  onClick={() => {
                    void navigate({ to: "/account/links" });
                  }}
                >
                  <Link2 />
                  {ta("session.linkAccounts")}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => {
                    setPasswordOpen(true);
                  }}
                >
                  <KeyRound />
                  {ta("password.change")}
                </DropdownMenuItem>
              </DropdownMenuGroup>

              <DropdownMenuSeparator />

              <DropdownMenuGroup>
                <DropdownMenuItem
                  onClick={() => {
                    setTheme(nextTheme);
                  }}
                >
                  {nextTheme === "light" ? <Sun /> : <Moon />}
                  {t(`theme.${nextTheme}`)}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => {
                    void i18n.changeLanguage(nextLocale);
                    persistLocale(nextLocale);
                  }}
                >
                  <Languages />
                  {nextLocale === "fr" ? t("userMenu.localeFr") : t("userMenu.localeEn")}
                </DropdownMenuItem>
              </DropdownMenuGroup>

              <DropdownMenuSeparator />

              <DropdownMenuItem variant="destructive" onClick={() => void logout()}>
                <LogOut />
                {ta("session.signOut")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </SidebarMenuItem>
      </SidebarMenu>

      <ChangePasswordDialog open={passwordOpen} onOpenChange={setPasswordOpen} />
    </>
  );
}
