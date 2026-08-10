import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { VoiceEngineInfo, VoiceIdInfo } from "@/features/agents/api";
import { SelectField } from "@/shared/components/select-field";

interface VoicePickerProps {
  engines: VoiceEngineInfo[];
  voices: VoiceIdInfo[];
  engine: string;
  voice: string;
  onEngineChange: (engine: string) => void;
  onVoiceChange: (voice: string) => void;
  offline?: boolean;
  error?: boolean;
}

const XAI_ENGINE_ALIASES = new Set(["xai", "grok", "grok-tts", "xai-tts"]);

function isXaiEngine(name: string): boolean {
  return XAI_ENGINE_ALIASES.has(name.toLowerCase());
}

export function VoicePicker({
  engines,
  voices,
  engine,
  voice,
  onEngineChange,
  onVoiceChange,
  offline,
  error,
}: VoicePickerProps) {
  const { t } = useTranslation("agents");

  const engineOptions = useMemo(() => {
    const names = engines.map((e) => e.name);
    // Prefer canonical "xai" first when present among aliases
    const sorted = [...names].sort((a, b) => {
      if (a === "xai") return -1;
      if (b === "xai") return 1;
      return a.localeCompare(b);
    });
    // De-dupe display: hide grok/xai-tts aliases if "xai" exists
    const hasXai = sorted.includes("xai");
    return sorted
      .filter((n) => !(hasXai && isXaiEngine(n) && n !== "xai"))
      .map((n) => {
        const info = engines.find((e) => e.name === n);
        const hintParts: string[] = [];
        if (info?.supports_voice) hintParts.push(t("voiceSupportsId"));
        if (info?.supports_clone) hintParts.push(t("voiceSupportsClone"));
        return {
          value: n,
          label: n,
          hint: hintParts.join(" · ") || undefined,
        };
      });
  }, [engines, t]);

  const showVoiceIds = isXaiEngine(engine) || voices.length > 0;

  const voiceOptions = useMemo(() => {
    if (!voices.length) {
      return voice ? [{ value: voice, label: voice, hint: t("voiceUnknown") }] : [];
    }
    const opts = voices.map((v) => {
      const bits = [v.gender, v.language].filter(Boolean);
      return {
        value: v.voice_id,
        label: v.name || v.voice_id,
        hint: bits.length ? bits.join(" · ") : undefined,
      };
    });
    // Keep currently saved id visible even if legacy (e.g. Ono_Anna) or stale
    if (voice && !opts.some((o) => o.value === voice)) {
      opts.unshift({ value: voice, label: voice, hint: t("voiceUnknown") });
    }
    return opts;
  }, [voices, voice, t]);

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <SelectField
        label={t("ttsEngine")}
        value={engine || engineOptions[0]?.value || "xai"}
        disabled={offline || error || engineOptions.length === 0}
        options={
          engineOptions.length
            ? engineOptions
            : [{ value: engine || "xai", label: engine || "xai" }]
        }
        onChange={onEngineChange}
      />
      {showVoiceIds ? (
        <SelectField
          label={t("ttsVoice")}
          value={voice || voiceOptions[0]?.value || "eve"}
          disabled={offline || error || voiceOptions.length === 0}
          options={
            voiceOptions.length ? voiceOptions : [{ value: voice || "eve", label: voice || "eve" }]
          }
          onChange={onVoiceChange}
        />
      ) : null}
      {error ? (
        <p className="text-xs text-destructive sm:col-span-2">{t("voiceCapsError")}</p>
      ) : null}
      {!error && offline ? (
        <p className="text-xs text-muted-foreground sm:col-span-2">{t("voiceCapsLoading")}</p>
      ) : null}
    </div>
  );
}
