import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation("chat");
  const override = dbDefault !== undefined && value !== dbDefault;
  return (
    <div className="flex flex-col gap-0.5">
      <PopoverSelect
        label={t("picker.harness")}
        value={value}
        disabled={disabled}
        options={OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
        onChange={(v) => onChange(v as HarnessKind)}
      />
      {override ? (
        <span className="text-[10px] text-status-closing">
          {t("picker.dbOverride", { default: dbDefault })}
        </span>
      ) : null}
    </div>
  );
}
