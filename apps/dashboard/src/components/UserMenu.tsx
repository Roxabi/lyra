import { CaretUpDown, Moon, Palette, Sun, Users } from "@phosphor-icons/react";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import i18n, { persistLocale } from "@/i18n";
import { readOperatorProfile } from "@/lib/operator-profile";
import { applyTheme, type Theme } from "@/lib/theme";
import { useTheme } from "@/lib/use-theme";
import { userDisplayName, userInitials } from "@/lib/user";
import { cn } from "@/lib/utils";

const THEME_OPTIONS: { value: Theme; Icon: typeof Sun; labelKey: `theme.${Theme}` }[] = [
  { value: "light", Icon: Sun, labelKey: "theme.light" },
  { value: "dark", Icon: Moon, labelKey: "theme.dark" },
];

const LOCALE_OPTIONS = [
  { value: "fr", labelKey: "userMenu.localeFr" },
  { value: "en", labelKey: "userMenu.localeEn" },
] as const;

interface UserMenuProps {
  variant?: "compact" | "sidebar";
  collapsed?: boolean;
  className?: string;
}

function ThemePicker({ className }: { className?: string }) {
  const { t } = useTranslation();
  const theme = useTheme();

  return (
    <div className={cn("px-2 py-1.5", className)}>
      <fieldset className="m-0 min-w-0 border-0 p-0">
        <legend className="mb-2 text-xs font-medium text-muted-foreground">
          {t("userMenu.theme")}
        </legend>
        <div className="grid grid-cols-2 gap-1 rounded-lg bg-muted p-1">
          {THEME_OPTIONS.map(({ value, Icon, labelKey }) => {
            const active = theme === value;
            return (
              <button
                key={value}
                type="button"
                aria-pressed={active}
                title={t(labelKey)}
                onClick={() => applyTheme(value)}
                className={cn(
                  "inline-flex min-h-10 flex-col items-center justify-center gap-0.5 rounded-md px-1 text-[10px] font-medium transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-popover",
                  active
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon className="size-4" aria-hidden />
                <span className="truncate">{t(labelKey)}</span>
              </button>
            );
          })}
        </div>
      </fieldset>
    </div>
  );
}

function LocalePicker({ className }: { className?: string }) {
  const { t, i18n: i18nInstance } = useTranslation();
  const [locale, setLocale] = useState(i18nInstance.language);

  return (
    <div className={cn("px-2 py-1.5", className)}>
      <fieldset className="m-0 min-w-0 border-0 p-0">
        <legend className="mb-2 text-xs font-medium text-muted-foreground">
          {t("userMenu.language")}
        </legend>
        <div className="grid grid-cols-2 gap-1 rounded-lg bg-muted p-1">
          {LOCALE_OPTIONS.map(({ value, labelKey }) => {
            const active = locale === value;
            return (
              <button
                key={value}
                type="button"
                aria-pressed={active}
                onClick={() => {
                  void i18n.changeLanguage(value);
                  persistLocale(value);
                  setLocale(value);
                }}
                className={cn(
                  "inline-flex min-h-10 items-center justify-center rounded-md px-2 text-xs font-medium transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-popover",
                  active
                    ? "bg-background text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {t(labelKey)}
              </button>
            );
          })}
        </div>
      </fieldset>
    </div>
  );
}

export function UserMenu({ variant = "compact", collapsed = false, className }: UserMenuProps) {
  const { t } = useTranslation();
  const user = readOperatorProfile();
  const displayName = userDisplayName(user);
  const initials = userInitials(user);
  const menuSide = variant === "sidebar" ? "right" : "bottom";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          "rounded-lg outline-none transition-colors",
          "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          "data-[state=open]:bg-accent",
          variant === "compact"
            ? "inline-flex size-11 items-center justify-center hover:bg-accent"
            : cn(
                "flex w-full items-center hover:bg-accent",
                collapsed ? "justify-center px-0 py-2" : "gap-3 px-2 py-2 text-left",
              ),
          className,
        )}
        aria-label={t("userMenu.open", { name: displayName })}
      >
        <Avatar className={cn("rounded-lg", variant === "compact" ? "size-9" : "size-10")}>
          <AvatarFallback className="rounded-lg bg-brand/15 text-sm font-semibold text-brand-foreground">
            {initials}
          </AvatarFallback>
        </Avatar>

        {variant === "sidebar" && !collapsed && (
          <>
            <span className="grid min-w-0 flex-1 text-left text-sm leading-tight">
              <span className="truncate font-medium text-foreground">{displayName}</span>
              <span className="truncate text-xs text-muted-foreground">{user.email}</span>
            </span>
            <CaretUpDown className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          </>
        )}
      </DropdownMenuTrigger>

      <DropdownMenuContent
        align="end"
        side={menuSide}
        sideOffset={8}
        className="w-56 min-w-56 rounded-lg"
      >
        <DropdownMenuLabel className="p-0 font-normal">
          <div className="flex items-center gap-2 px-2 py-2 text-left text-sm">
            <Avatar className="size-10 rounded-lg">
              <AvatarFallback className="rounded-lg bg-brand/15 text-sm font-semibold text-brand-foreground">
                {initials}
              </AvatarFallback>
            </Avatar>
            <div className="grid min-w-0 flex-1 leading-tight">
              <span className="truncate font-medium text-foreground">{displayName}</span>
              <span className="truncate text-xs text-muted-foreground">{user.email}</span>
            </div>
          </div>
        </DropdownMenuLabel>

        <DropdownMenuSeparator />

        <DropdownMenuGroup>
          <DropdownMenuItem asChild>
            <Link to="/users" className="cursor-pointer">
              <Users className="size-4" aria-hidden />
              {t("nav.users")}
            </Link>
          </DropdownMenuItem>
          <DropdownMenuItem asChild>
            <Link to="/design-system" className="cursor-pointer">
              <Palette className="size-4" aria-hidden />
              {t("nav.designSystem")}
            </Link>
          </DropdownMenuItem>
        </DropdownMenuGroup>

        <DropdownMenuSeparator />

        <ThemePicker />
        <LocalePicker />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
