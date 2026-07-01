import { ToggleButton } from "@astryxdesign/core/ToggleButton";

/**
 * Astryx-native filter chip. Composes `@astryxdesign/core/ToggleButton`
 * (pressed/toggle semantics) behind the app's `{ active, onClick, children }`
 * API so the four call-sites (status/agent filters) change minimally. The chip
 * label is the string child.
 */
export interface FilterChipProps {
  active?: boolean;
  children: string;
  onClick?: () => void;
  disabled?: boolean;
  className?: string;
}

export function FilterChip({
  active = false,
  children,
  onClick,
  disabled,
  className,
}: FilterChipProps) {
  return (
    <ToggleButton
      label={children}
      isPressed={active}
      onPressedChange={() => onClick?.()}
      isDisabled={disabled}
      className={className}
    />
  );
}
