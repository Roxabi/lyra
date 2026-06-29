import { PopoverSelect } from "@/components/ui/popover-select";
import type { HarnessKind } from "@/lib/chats-storage";

interface HarnessPickerProps {
  value: HarnessKind;
  onChange: (h: HarnessKind) => void;
  disabled?: boolean;
  /** Agent DB default — shows override hint when tab value differs. */
  dbDefault?: HarnessKind;
}

const OPTIONS = [
  { value: "claude-cli" as const, label: "Clipool" },
  { value: "omp-rpc" as const, label: "OMP" },
];

export function HarnessPicker({ value, onChange, disabled, dbDefault }: HarnessPickerProps) {
  const override = dbDefault !== undefined && value !== dbDefault;
  return (
    <div className="flex flex-col gap-0.5">
      <PopoverSelect
        label="Harness"
        value={value}
        disabled={disabled}
        options={OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
        onChange={(v) => onChange(v as HarnessKind)}
      />
      {override ? (
        <span className="text-[10px] text-amber-600 dark:text-amber-400">
          ≠ DB default ({dbDefault})
        </span>
      ) : null}
    </div>
  );
}
