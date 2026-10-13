import { RotateCcw } from "lucide-react";
import type { GenerationParams } from "@/lib/types";
import { cn } from "@/lib/utils";

interface ParamSpec {
  key: keyof GenerationParams;
  label: string;
  hint: string;
  min: number;
  max: number;
  step: number;
  integer?: boolean;
}

export const PARAM_SPECS: ParamSpec[] = [
  { key: "temperature", label: "Temperature", hint: "Randomness. Lower is more focused.", min: 0, max: 2, step: 0.05 },
  { key: "top_p", label: "Top-p", hint: "Nucleus sampling probability mass.", min: 0, max: 1, step: 0.01 },
  { key: "top_k", label: "Top-k", hint: "Sample from the k most likely tokens.", min: 0, max: 200, step: 1, integer: true },
  { key: "min_p", label: "Min-p", hint: "Drop tokens below this relative probability.", min: 0, max: 1, step: 0.01 },
  { key: "max_tokens", label: "Max output tokens", hint: "Upper bound on response length.", min: 1, max: 131072, step: 1, integer: true },
  { key: "repeat_penalty", label: "Repeat penalty", hint: "Penalize repeated tokens (1 = off).", min: 0, max: 2, step: 0.01 },
  { key: "presence_penalty", label: "Presence penalty", hint: "Encourage new topics.", min: -2, max: 2, step: 0.05 },
  { key: "frequency_penalty", label: "Frequency penalty", hint: "Discourage frequent tokens.", min: -2, max: 2, step: 0.05 },
  { key: "seed", label: "Seed", hint: "Fixed seed for reproducible output (-1 = random).", min: -1, max: 2 ** 31, step: 1, integer: true },
];

/**
 * Editor for sampling parameters. Empty means "use the server default".
 * Parameters the selected connection does not forward are disabled.
 */
export function ParamsEditor({
  value,
  onChange,
  supported,
  inherited,
}: {
  value: GenerationParams;
  onChange: (v: GenerationParams) => void;
  supported?: string[];
  inherited?: GenerationParams;
}) {
  const set = (key: keyof GenerationParams, v: unknown) => onChange({ ...value, [key]: v });

  return (
    <div className="space-y-1">
      {PARAM_SPECS.map((spec) => {
        const isSupported = !supported || supported.includes(spec.key);
        const current = value[spec.key] as number | null | undefined;
        const fallback = inherited?.[spec.key] as number | null | undefined;
        const showSlider = spec.key !== "seed" && spec.key !== "max_tokens";
        return (
          <div key={spec.key} className={cn("grid grid-cols-[1fr_auto] items-center gap-x-4 gap-y-1 rounded-lg py-2", !isSupported && "opacity-45")}>
            <div className="min-w-0">
              <label htmlFor={`param-${spec.key}`} className="text-[0.8125rem] font-medium text-fg">
                {spec.label}
              </label>
              <p className="text-[0.75rem] text-muted">{isSupported ? spec.hint : "Not supported by this connection."}</p>
            </div>
            <div className="flex items-center gap-1.5">
              <input
                id={`param-${spec.key}`}
                type="number"
                inputMode="decimal"
                disabled={!isSupported}
                min={spec.min}
                max={spec.max}
                step={spec.step}
                value={current ?? ""}
                placeholder={fallback != null ? String(fallback) : "default"}
                onChange={(e) => {
                  const raw = e.target.value;
                  if (raw === "") return set(spec.key, null);
                  const n = spec.integer ? parseInt(raw, 10) : parseFloat(raw);
                  if (!Number.isNaN(n)) set(spec.key, Math.min(spec.max, Math.max(spec.min, n)));
                }}
                className="h-8 w-24 rounded-md border border-line-strong bg-elevated px-2 text-right font-mono text-[0.8125rem] text-fg placeholder:font-sans placeholder:text-faint focus:border-accent focus:outline-none"
              />
              <button
                type="button"
                aria-label={`Reset ${spec.label}`}
                title="Use default"
                disabled={current == null}
                onClick={() => set(spec.key, null)}
                className="flex size-7 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-fg disabled:invisible"
              >
                <RotateCcw className="size-3.5" />
              </button>
            </div>
            {showSlider && isSupported && (
              <input
                type="range"
                aria-label={`${spec.label} slider`}
                min={spec.min}
                max={spec.max}
                step={spec.step}
                value={current ?? fallback ?? spec.min}
                onChange={(e) => set(spec.key, spec.integer ? parseInt(e.target.value, 10) : parseFloat(e.target.value))}
                className={cn("col-span-2 h-1 w-full cursor-pointer accent-[var(--accent)]", current == null && "opacity-40")}
              />
            )}
          </div>
        );
      })}
      <div className="py-2">
        <label htmlFor="param-stop" className="text-[0.8125rem] font-medium text-fg">
          Stop sequences
        </label>
        <p className="mb-1.5 text-[0.75rem] text-muted">One per line (up to 8). Generation stops when any appears.</p>
        <textarea
          id="param-stop"
          rows={2}
          value={(value.stop ?? []).join("\n")}
          disabled={!!supported && !supported.includes("stop")}
          onChange={(e) => {
            const lines = e.target.value.split("\n").slice(0, 8);
            set("stop", lines.some((l) => l.length) ? lines : null);
          }}
          className="w-full rounded-md border border-line-strong bg-elevated px-2 py-1.5 font-mono text-[0.8125rem] text-fg focus:border-accent focus:outline-none"
        />
      </div>
    </div>
  );
}

/** Strip empty values before sending to the server. */
export function cleanParams(p: GenerationParams): GenerationParams {
  const out: GenerationParams = {};
  for (const [k, v] of Object.entries(p)) {
    if (v === null || v === undefined) continue;
    if (k === "stop") {
      const stops = (v as string[]).filter((s) => s.length);
      if (stops.length) out.stop = stops;
      continue;
    }
    (out as Record<string, unknown>)[k] = v;
  }
  return out;
}
