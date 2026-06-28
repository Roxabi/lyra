import { SelectField } from "@/components/ui/select-field";
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
    <SelectField
      label="Model"
      value={value}
      disabled={offline}
      onChange={(e) => onChange(e.target.value)}
    >
      {models.map((m) => (
        <option key={m} value={m}>
          {offline ? `${m} (Hors ligne)` : m}
        </option>
      ))}
    </SelectField>
  );
}
