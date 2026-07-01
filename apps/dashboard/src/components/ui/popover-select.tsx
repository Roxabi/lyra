import { Selector } from "@astryxdesign/core/Selector";
import { Text } from "@astryxdesign/core/Text";

/**
 * Thin Astryx-native adapter over `@astryxdesign/core/Selector`.
 *
 * Astryx `SelectorOptionData` has no `hint` field, so the optional per-option
 * hint (e.g. online/offline status, "offline") is rendered as a second line via
 * `renderOption`. Retained as a shared component because six call-sites depend
 * on the `{ value, label, hint, disabled }` option shape — the internals are
 * 100% Astryx (no shadcn/Radix).
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
    <Selector
      label={label}
      isLabelHidden
      aria-label={label}
      placeholder={label}
      value={value}
      isDisabled={disabled}
      className={className}
      options={options.map((o) => ({ value: o.value, label: o.label, disabled: o.disabled }))}
      onChange={onChange}
      renderOption={
        hintByValue.size > 0
          ? (option) => {
              const hint = hintByValue.get(option.value);
              return (
                <span className="flex flex-col">
                  <span>{option.label ?? option.value}</span>
                  {hint ? (
                    <Text type="supporting" as="span">
                      {hint}
                    </Text>
                  ) : null}
                </span>
              );
            }
          : undefined
      }
    />
  );
}
