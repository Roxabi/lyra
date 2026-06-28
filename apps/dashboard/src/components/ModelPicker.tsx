import { PopoverSelect } from "@/components/ui/popover-select";
import { MODEL_CATALOG } from "@/lib/api";
import type { HarnessKind } from "@/lib/chats-storage";

interface ModelPickerProps {
  harness: HarnessKind;
  value: string;
  onChange: (model: string) => void;
  offline?: boolean;
}

export function ModelPicker({ harness, value, onChange, offline }: ModelPickerProps) {
  const models = MODEL_CATALOG[harness];
  return (
    <PopoverSelect
      label="Model"
      value={value}
      disabled={offline}
      options={models.map((m) => ({
        value: m,
        label: m,
        hint: offline ? "Hors ligne" : undefined,
      }))}
      onChange={onChange}
    />
  );
}
