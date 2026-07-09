import { Link, useRouterState } from "@tanstack/react-router";
import {
  APP_NAME,
  APP_TAGLINE,
  type AppNavItem,
  adminNavItems,
  homeNavItems,
  isNavItemActive,
  observeNavItems,
  operateNavItems,
} from "@/app/nav";
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";

function NavSection({ title, items }: { title: string; items: AppNavItem[] }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  return (
    <SidebarGroup>
      <SidebarGroupLabel>{title}</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {items.map((item) => {
            const active = isNavItemActive(pathname, item);
            const Icon = item.Icon;

            if (item.status === "planned") {
              return (
                <SidebarMenuItem key={item.to}>
                  <SidebarMenuButton
                    isActive={false}
                    disabled
                    tooltip={`${item.label} — coming soon`}
                    className="opacity-50"
                  >
                    <Icon />
                    <span>{item.label}</span>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              );
            }

            return (
              <SidebarMenuItem key={item.to}>
                <SidebarMenuButton
                  isActive={active}
                  tooltip={item.label}
                  render={<Link to={item.to} />}
                >
                  <Icon />
                  <span>{item.label}</span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            );
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}

export function AppSidebar() {
  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="border-b border-sidebar-border">
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton size="lg" render={<Link to="/" />}>
              <img src="/factory-mark.svg" alt="" className="size-8" aria-hidden />
              <div className="grid flex-1 text-left text-sm leading-tight">
                <span className="truncate font-semibold">{APP_NAME}</span>
                <span className="truncate text-xs text-muted-foreground">{APP_TAGLINE}</span>
              </div>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>

      <SidebarContent>
        <NavSection title="Home" items={homeNavItems} />
        <NavSection title="Operate" items={operateNavItems} />
        <NavSection title="Observe" items={observeNavItems} />
        <NavSection title="Admin" items={adminNavItems} />
      </SidebarContent>

      <SidebarFooter className="border-t border-sidebar-border p-2">
        <span className="px-2 font-mono text-[10px] font-bold uppercase tracking-widest text-muted-foreground group-data-[collapsible=icon]:hidden">
          v2 greenfield
        </span>
      </SidebarFooter>
      <SidebarRail />
    </Sidebar>
  );
}
