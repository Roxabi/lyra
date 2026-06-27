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
    <select
      className="rounded border border-border bg-card px-2 py-1 text-xs"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value as HarnessKind)}
      aria-label="Harness"
    >
      {OPTIONS.map((o) => (
        <option key={o.id} value={o.id}>
          {o.label}
        </option>
      ))}
    </select>
  );
}
