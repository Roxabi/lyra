import { Avatar } from "@astryxdesign/core/Avatar";
import { Item } from "@astryxdesign/core/Item";
import { Popover } from "@astryxdesign/core/Popover";
import { SegmentedControl, SegmentedControlItem } from "@astryxdesign/core/SegmentedControl";
import { CaretUpDown, Moon, Palette, Sun, Users } from "@phosphor-icons/react";
import { useNavigate } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n, { persistLocale } from "@/i18n";
import { type OperatorProfile, readOperatorProfile } from "@/lib/operator-profile";
import { applyTheme } from "@/lib/theme";
import { useTheme } from "@/lib/use-theme";
import { userDisplayName } from "@/lib/user";
import { cn } from "@/lib/utils";

// Typed literals so `navigate({ to })` keeps TanStack's route checking.
const NAV_LINKS = [
  { to: "/users", labelKey: "nav.users", Icon: Users, testId: "user-menu-users" },
  {
    to: "/design-system",
    labelKey: "nav.designSystem",
    Icon: Palette,
    testId: "user-menu-design-system",
  },
] as const;

type NavTarget = (typeof NAV_LINKS)[number]["to"];

interface UserMenuProps {
  variant?: "compact" | "sidebar";
  collapsed?: boolean;
  className?: string;
}

/**
 * Popover content: profile header, navigation links, and the theme/locale
 * pickers. Rendered inside Astryx's `Popover` (a dialog layer) rather than a
 * `DropdownMenu`: this menu mixes navigation with in-place settings toggles,
 * which Astryx's action-item `DropdownMenu` (Button trigger, closes on select)
 * explicitly advises against. Popover hosts the arbitrary content plus the rich
 * avatar trigger without regressing the shell UX built in slice 2.
 */
function MenuContent({
  user,
  displayName,
  onNavigate,
}: {
  user: OperatorProfile;
  displayName: string;
  onNavigate: (to: NavTarget) => void;
}) {
  const { t } = useTranslation();
  const theme = useTheme();
  const [locale, setLocale] = useState(i18n.language);

  const changeLocale = (value: string) => {
    void i18n.changeLanguage(value);
    persistLocale(value);
    setLocale(value);
  };

  return (
    <div className="grid w-60 gap-1">
      <div className="flex items-center gap-2 px-1 py-1">
        <Avatar name={displayName} size={40} />
        <div className="grid min-w-0 flex-1 leading-tight">
          <span className="truncate text-sm font-medium text-foreground">{displayName}</span>
          <span className="truncate text-xs text-muted-foreground">{user.email}</span>
        </div>
      </div>

      <div className="grid gap-0.5">
        {NAV_LINKS.map(({ to, labelKey, Icon, testId }) => (
          <Item
            key={to}
            label={t(labelKey)}
            startContent={<Icon className="size-4" aria-hidden />}
            onClick={() => onNavigate(to)}
            data-testid={testId}
          />
        ))}
      </div>

      <div className="grid gap-2 px-1 py-1">
        <div className="grid gap-1">
          <span aria-hidden className="text-xs font-medium text-muted-foreground">
            {t("userMenu.theme")}
          </span>
          <SegmentedControl
            label={t("userMenu.theme")}
            value={theme}
            onChange={(value) => {
              // SegmentedControl's onChange is typed `(value: string)`; narrow
              // back to Theme so a stray item value can't persist a bad theme.
              if (value === "light" || value === "dark") applyTheme(value);
            }}
            layout="fill"
            size="sm"
          >
            <SegmentedControlItem
              value="light"
              label={t("theme.light")}
              icon={<Sun className="size-4" aria-hidden />}
            />
            <SegmentedControlItem
              value="dark"
              label={t("theme.dark")}
              icon={<Moon className="size-4" aria-hidden />}
            />
          </SegmentedControl>
        </div>

        <div className="grid gap-1">
          <span aria-hidden className="text-xs font-medium text-muted-foreground">
            {t("userMenu.language")}
          </span>
          <SegmentedControl
            label={t("userMenu.language")}
            value={locale}
            onChange={changeLocale}
            layout="fill"
            size="sm"
          >
            <SegmentedControlItem value="fr" label={t("userMenu.localeFr")} />
            <SegmentedControlItem value="en" label={t("userMenu.localeEn")} />
          </SegmentedControl>
        </div>
      </div>
    </div>
  );
}

export function UserMenu({ variant = "compact", collapsed = false, className }: UserMenuProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const user = readOperatorProfile();
  const displayName = userDisplayName(user);

  const handleNavigate = (to: NavTarget) => {
    setOpen(false);
    void navigate({ to });
  };

  return (
    <Popover
      isOpen={open}
      onOpenChange={(next) => {
        setOpen(next);
        // Restore focus to the trigger on dismiss (Escape / light-dismiss) so
        // keyboard users don't drop to <body>; the old Radix menu did this for
        // free. Navigation closes via handleNavigate (focus moves to the page),
        // so this only fires for real dismissals.
        if (!next) triggerRef.current?.focus();
      }}
      placement={variant === "sidebar" ? "end" : "below"}
      alignment="end"
      label={t("userMenu.open", { name: displayName })}
      closeButtonLabel={t("userMenu.closePopover")}
      content={<MenuContent user={user} displayName={displayName} onNavigate={handleNavigate} />}
    >
      <button
        ref={triggerRef}
        type="button"
        aria-label={t("userMenu.open", { name: displayName })}
        className={cn(
          "rounded-lg outline-none transition-colors hover:bg-accent",
          "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          variant === "compact"
            ? "inline-flex size-11 items-center justify-center"
            : cn(
                "flex w-full items-center",
                collapsed ? "justify-center px-0 py-2" : "gap-3 px-2 py-2 text-left",
              ),
          className,
        )}
      >
        <Avatar name={displayName} size={variant === "compact" ? 36 : 40} />
        {variant === "sidebar" && !collapsed && (
          <>
            <span className="grid min-w-0 flex-1 text-left text-sm leading-tight">
              <span className="truncate font-medium text-foreground">{displayName}</span>
              <span className="truncate text-xs text-muted-foreground">{user.email}</span>
            </span>
            <CaretUpDown className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          </>
        )}
      </button>
    </Popover>
  );
}
