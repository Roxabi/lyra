import { useMediaQuery } from "@astryxdesign/core/hooks";
import {
  SegmentedControl as AstryxSegmentedControl,
  SegmentedControlItem,
} from "@astryxdesign/core/SegmentedControl";

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  icon?: import("@phosphor-icons/react").Icon;
}

export interface SegmentedControlProps<T extends string> {
  options: SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
  /** Accessible label for the radio group — required (Astryx renders it as aria-label). */
  ariaLabel: string;
  /** Icons only, or responsive (icons on narrow viewports, labels from `md`). */
  compact?: boolean | "responsive";
  className?: string;
}

/**
 * Astryx-native segmented control. Composes `@astryxdesign/core/SegmentedControl`
 * and adds the app's responsive icon-only behavior by toggling per-item
 * `isLabelHidden` from Astryx's `useMediaQuery` breakpoint. Collapse only
 * applies when every option has an icon, so items never render empty and the
 * group never mixes icon-only with text segments.
 */
export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  compact = false,
  className,
}: SegmentedControlProps<T>) {
  const isWide = useMediaQuery("(min-width: 768px)");
  const allHaveIcons = options.every((o) => o.icon);
  const iconOnly = allHaveIcons && (compact === true || (compact === "responsive" && !isWide));

  return (
    <AstryxSegmentedControl
      value={value}
      onChange={(v) => onChange(v as T)}
      label={ariaLabel}
      className={className}
    >
      {options.map((option) => {
        const Icon = option.icon;
        return (
          <SegmentedControlItem
            key={option.value}
            value={option.value}
            label={option.label}
            isLabelHidden={iconOnly}
            icon={Icon ? <Icon className="size-4" aria-hidden /> : undefined}
          />
        );
      })}
    </AstryxSegmentedControl>
  );
}
