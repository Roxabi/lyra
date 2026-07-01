import { Selector, SelectorOption } from "@astryxdesign/core/Selector";

/**
 * Thin Astryx-native adapter over `@astryxdesign/core/Selector`.
 *
 * Astryx `SelectorOptionData` has no `hint` field, so the optional per-option
 * hint (e.g. online/offline status, "offline") is rendered as a second line via
 * `renderOption` + Astryx's own `SelectorOption`. Retained as a shared component
 * because six call-sites depend on the `{ value, label, hint, disabled }` option
 * shape — the internals are 100% Astryx (no shadcn/Radix).
 *
 * The consumer `className` is applied to an outer wrapper (not to `Selector`,
 * whose `className` lands on the inner trigger and would not size the flex item),
 * and the Selector fills that wrapper via `width="100%"` so `flex-1` call-sites
 * (e.g. ChatSidebar) stretch as expected.
 */
export interface PopoverOption {
  value: string;
  label: string;
  hint?: string;
  disabled?: boolean;
}

interface PopoverSelectProps {
  label: string;
  value: string;
  options: PopoverOption[];
  disabled?: boolean;
  onChange: (value: string) => void;
  className?: string;
}

export function PopoverSelect({
  label,
  value,
  options,
  disabled,
  onChange,
  className,
}: PopoverSelectProps) {
  const hintByValue = new Map(options.filter((o) => o.hint).map((o) => [o.value, o.hint]));

  return (
    <div className={className}>
      <Selector
        label={label}
        isLabelHidden
        aria-label={label}
        placeholder={label}
        value={value}
        isDisabled={disabled}
        width="100%"
        options={options.map((o) => ({ value: o.value, label: o.label, disabled: o.disabled }))}
        onChange={onChange}
        renderOption={
          hintByValue.size > 0
            ? (option) => (
                <SelectorOption
                  label={option.label ?? option.value}
                  description={hintByValue.get(option.value)}
                />
              )
            : undefined
        }
      />
    </div>
  );
}
