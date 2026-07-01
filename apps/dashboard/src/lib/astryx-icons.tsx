import type { IconRegistry } from "@astryxdesign/core/Icon";
import { registerIcons } from "@astryxdesign/core/Icon";
import {
  ArrowDown,
  ArrowSquareOut,
  ArrowsDownUp,
  ArrowUp,
  Calendar,
  CaretDown,
  CaretLeft,
  CaretRight,
  Check,
  CheckCircle,
  Checks,
  Clock,
  Columns,
  Copy,
  DotsThree,
  EyeSlash,
  Funnel,
  Info,
  List,
  MagnifyingGlass,
  Microphone,
  Stop,
  Warning,
  Wrench,
  X,
  XCircle,
} from "@phosphor-icons/react";

/**
 * astryx-icons.tsx — Phosphor glyphs for Astryx's global icon registry (slice 3, #2091).
 *
 * Astryx components (SideNav, Dialog, Select, …) resolve their internal glyphs
 * (chevrons, close, check, search, …) through `@astryxdesign/core/Icon`'s
 * global registry — a semantic name → `ReactNode` map, independent of any
 * theme. `registerAppIcons()` populates that registry with Phosphor icons
 * (the icon set used everywhere else in the dashboard) so Astryx's built-in
 * chrome matches the rest of the app instead of falling back to its
 * minimal built-in SVGs or a different icon library.
 *
 * Call once at boot, before the app renders (see `src/main.tsx`).
 */
const iconProps = {
  size: "1em",
  "aria-hidden": true as const,
};

export const phosphorIconRegistry: IconRegistry = {
  close: <X {...iconProps} />,
  chevronDown: <CaretDown {...iconProps} />,
  chevronLeft: <CaretLeft {...iconProps} />,
  chevronRight: <CaretRight {...iconProps} />,
  check: <Check {...iconProps} />,
  success: <CheckCircle {...iconProps} />,
  error: <XCircle {...iconProps} />,
  warning: <Warning {...iconProps} />,
  info: <Info {...iconProps} />,
  calendar: <Calendar {...iconProps} />,
  clock: <Clock {...iconProps} />,
  externalLink: <ArrowSquareOut {...iconProps} />,
  menu: <List {...iconProps} />,
  moreHorizontal: <DotsThree {...iconProps} />,
  search: <MagnifyingGlass {...iconProps} />,
  arrowUp: <ArrowUp {...iconProps} />,
  arrowDown: <ArrowDown {...iconProps} />,
  arrowsUpDown: <ArrowsDownUp {...iconProps} />,
  funnel: <Funnel {...iconProps} />,
  eyeSlash: <EyeSlash {...iconProps} />,
  viewColumns: <Columns {...iconProps} />,
  copy: <Copy {...iconProps} />,
  checkDouble: <Checks {...iconProps} />,
  wrench: <Wrench {...iconProps} />,
  stop: <Stop {...iconProps} />,
  microphone: <Microphone {...iconProps} />,
};

export function registerAppIcons(): void {
  registerIcons(phosphorIconRegistry);
}
