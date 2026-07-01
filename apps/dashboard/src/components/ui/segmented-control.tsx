import {
  SegmentedControl as AstryxSegmentedControl,
  SegmentedControlItem,
} from "@astryxdesign/core/SegmentedControl";
import { useMediaQuery } from "@/lib/use-media-query";

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  icon?: import("@phosphor-icons/react").Icon;
}

export interface SegmentedControlProps<T extends string> {
  options: SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
  ariaLabel?: string;
  /** Icons only, or responsive (icons on narrow viewports, labels from `md`). */
  compact?: boolean | "responsive";
  className?: string;
}

/**
 * Astryx-native segmented control. Composes `@astryxdesign/core/SegmentedControl`
 * and adds the app's responsive icon-only behavior (absent from Astryx) by
 * toggling per-item `isLabelHidden` from a `useMediaQuery` breakpoint.
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
  const iconOnly = compact === true || (compact === "responsive" && !isWide);

  return (
    <AstryxSegmentedControl
      value={value}
      onChange={(v) => onChange(v as T)}
      label={ariaLabel ?? ""}
      className={className}
    >
      {options.map((option) => {
        const Icon = option.icon;
        return (
          <SegmentedControlItem
            key={option.value}
            value={option.value}
            label={option.label}
            isLabelHidden={iconOnly && Boolean(Icon)}
            icon={Icon ? <Icon className="size-4" aria-hidden /> : undefined}
          />
        );
      })}
    </AstryxSegmentedControl>
  );
}
