import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Group } from "@/components/Page";
import { Field, Input, Select } from "@/components/ui";
import { errorText } from "@/lib/errors";
import type { AppSettings, ContextSettings } from "@/lib/types";
import { useModels } from "@/stores/models";
import { useSettings } from "@/stores/settings";
import { toast } from "@/stores/ui";

const clamp = (v: number, min: number, max: number, fallback: number) => (Number.isFinite(v) && v > 0 ? Math.min(max, Math.max(min, v)) : fallback);

/** Settings → Chat → Context window: the user's defaults for context management. */
export function ContextSettingsGroup({ settings }: { settings: AppSettings }) {
  const { t } = useTranslation("context");
  const update = useSettings((s) => s.update);
  const entries = useModels((s) => s.entries);
  const c = settings.context;
  const save = (patch: Partial<ContextSettings>) => update({ context: { ...c, ...patch } }).catch((e) => toast(errorText(e), "error"));
  const [num, setNum] = useState({ fallback: c.fallback_window, hysteresis: Math.round(c.hysteresis * 100), margin: c.safety_margin_pct, reserve: c.reserve_tokens });

  useEffect(() => {
    setNum({ fallback: c.fallback_window, hysteresis: Math.round(c.hysteresis * 100), margin: c.safety_margin_pct, reserve: c.reserve_tokens });
  }, [c.fallback_window, c.hysteresis, c.safety_margin_pct, c.reserve_tokens]);

  const summaryValue = c.summary_provider_id && c.summary_model ? `${c.summary_provider_id}\u0000${c.summary_model}` : "";

  return (
    <Group title={t("settings.title")} description={t("settings.help")}>
      <Field label={t("settings.strategy")} htmlFor="ctx-strategy" hint={t(`strategy.${c.strategy}Help`)}>
        <Select id="ctx-strategy" value={c.strategy} onChange={(e) => save({ strategy: e.target.value as ContextSettings["strategy"] })}>
          <option value="sliding">{t("strategy.sliding")}</option>
          <option value="summarize">{t("strategy.summarize")}</option>
          <option value="off">{t("strategy.off")}</option>
        </Select>
      </Field>
      <Field label={t("settings.summaryModel")} htmlFor="ctx-summary" hint={t("settings.summaryModelHelp")}>
        <Select
          id="ctx-summary"
          value={summaryValue}
          onChange={(e) => {
            const [pid, model] = e.target.value ? e.target.value.split("\u0000") : [null, null];
            void save({ summary_provider_id: pid, summary_model: model });
          }}
        >
          <option value="">{t("settings.summaryModelDefault")}</option>
          {entries.map((entry) =>
            entry.models.length ? (
              <optgroup key={entry.provider.id} label={entry.provider.name}>
                {entry.models.map((m) => (
                  <option key={m.id} value={`${entry.provider.id}\u0000${m.id}`}>
                    {m.name ?? m.id}
                  </option>
                ))}
              </optgroup>
            ) : null,
          )}
        </Select>
      </Field>
      <Field label={t("settings.fallback")} htmlFor="ctx-fallback" hint={t("settings.fallbackHelp")}>
        <Input
          id="ctx-fallback"
          type="number"
          min={512}
          max={2000000}
          step={512}
          value={num.fallback}
          onChange={(e) => setNum({ ...num, fallback: Number(e.target.value) })}
          onBlur={() => {
            const v = clamp(num.fallback, 512, 2_000_000, 4096);
            if (v !== c.fallback_window) void save({ fallback_window: v });
            else setNum({ ...num, fallback: v });
          }}
        />
      </Field>
      <details className="group rounded-lg border border-line px-3 py-2">
        <summary className="cursor-pointer text-sm font-medium text-fg select-none">{t("settings.advanced")}</summary>
        <div className="mt-3 grid gap-4">
          <Field label={t("settings.hysteresis")} htmlFor="ctx-hysteresis" hint={t("settings.hysteresisHelp")}>
            <Input
              id="ctx-hysteresis"
              type="number"
              min={30}
              max={95}
              value={num.hysteresis}
              onChange={(e) => setNum({ ...num, hysteresis: Number(e.target.value) })}
              onBlur={() => {
                const v = clamp(num.hysteresis, 30, 95, 75) / 100;
                if (v !== c.hysteresis) void save({ hysteresis: v });
                else setNum({ ...num, hysteresis: Math.round(v * 100) });
              }}
            />
          </Field>
          <Field label={t("settings.margin")} htmlFor="ctx-margin" hint={t("settings.marginHelp")}>
            <Input
              id="ctx-margin"
              type="number"
              min={0}
              max={30}
              value={num.margin}
              onChange={(e) => setNum({ ...num, margin: Number(e.target.value) })}
              onBlur={() => {
                const v = Number.isFinite(num.margin) ? Math.min(30, Math.max(0, num.margin)) : 5;
                if (v !== c.safety_margin_pct) void save({ safety_margin_pct: v });
                else setNum({ ...num, margin: v });
              }}
            />
          </Field>
          <Field label={t("settings.reserve")} htmlFor="ctx-reserve" hint={t("settings.reserveHelp")}>
            <Input
              id="ctx-reserve"
              type="number"
              min={64}
              max={65536}
              step={64}
              value={num.reserve}
              onChange={(e) => setNum({ ...num, reserve: Number(e.target.value) })}
              onBlur={() => {
                const v = clamp(num.reserve, 64, 65_536, 1024);
                if (v !== c.reserve_tokens) void save({ reserve_tokens: v });
                else setNum({ ...num, reserve: v });
              }}
            />
          </Field>
        </div>
      </details>
    </Group>
  );
}
