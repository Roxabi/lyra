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
    <select
      className="rounded border border-border bg-card px-2 py-1 text-xs"
      value={value}
      disabled={offline}
      onChange={(e) => onChange(e.target.value)}
      aria-label="Model"
    >
      {models.map((m) => (
        <option key={m} value={m}>
          {offline ? `${m} (Hors ligne)` : m}
        </option>
      ))}
    </select>
  );
}
