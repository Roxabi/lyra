import { useTranslation } from "react-i18next";
import { MODEL_CATALOG } from "@/shared/api/bff-types";
import { SelectField } from "@/shared/components/select-field";
import type { HarnessKind } from "@/shared/lib/chats-storage";

interface ModelPickerProps {
  harness: HarnessKind;
  value: string;
  onChange: (model: string) => void;
  offline?: boolean;
}

export function ModelPicker({ harness, value, onChange, offline }: ModelPickerProps) {
  const { t } = useTranslation("chat");
  const models = MODEL_CATALOG[harness];
  return (
    <SelectField
      label={t("picker.model")}
      value={value}
      disabled={offline}
      options={models.map((m) => ({
        value: m,
        label: m,
        hint: offline ? t("picker.offline") : undefined,
      }))}
      onChange={onChange}
    />
  );
}
