import { SelectField } from "@/components/ui/select-field";
import type { HarnessKind } from "@/lib/chats-storage";

interface HarnessPickerProps {
  value: HarnessKind;
  onChange: (h: HarnessKind) => void;
  disabled?: boolean;
}

const OPTIONS: { id: HarnessKind; label: string }[] = [
  { id: "claude-cli", label: "Clipool" },
  { id: "omp-rpc", label: "OMP" },
];

export function HarnessPicker({ value, onChange, disabled }: HarnessPickerProps) {
  return (
    <SelectField
      label="Harness"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value as HarnessKind)}
    >
      {OPTIONS.map((o) => (
        <option key={o.id} value={o.id}>
          {o.label}
        </option>
      ))}
    </SelectField>
  );
}
