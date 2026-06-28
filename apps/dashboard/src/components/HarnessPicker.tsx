import { PopoverSelect } from "@/components/ui/popover-select";
import type { HarnessKind } from "@/lib/chats-storage";

interface HarnessPickerProps {
  value: HarnessKind;
  onChange: (h: HarnessKind) => void;
  disabled?: boolean;
}

const OPTIONS = [
  { value: "claude-cli" as const, label: "Clipool" },
  { value: "omp-rpc" as const, label: "OMP" },
];

export function HarnessPicker({ value, onChange, disabled }: HarnessPickerProps) {
  return (
    <PopoverSelect
      label="Harness"
      value={value}
      disabled={disabled}
      options={OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
      onChange={(v) => onChange(v as HarnessKind)}
    />
  );
}
