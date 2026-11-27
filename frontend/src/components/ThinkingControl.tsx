import { Brain, ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ThinkingCaps, ThinkingLevel } from "@/lib/types";
import { cn } from "@/lib/utils";
import { MenuContent, MenuLabel, MenuRadioGroup, MenuRadioItem, MenuRoot, MenuTrigger, Tooltip } from "./ui";

const ORDER: ThinkingLevel[] = ["off", "low", "medium", "high", "max"];

/** The level a request will use: the nearest one the model supports
 * (mirrors providers/reasoning.py `clamp`; the server decides in the end). */
export function effectiveThinking(level: ThinkingLevel, caps: ThinkingCaps | undefined): ThinkingLevel {
  if (!caps || level === "default" || caps.shape === "none" || caps.shape === "always") return "default";
  if (caps.levels.includes(level)) return level;
  if (caps.shape === "toggle") return level === "off" ? "off" : "on";
  let wanted: ThinkingLevel = level;
  if (wanted === "on") {
    wanted = caps.default && caps.default !== "off" && ORDER.includes(caps.default) ? caps.default : "medium";
    if (caps.levels.includes(wanted)) return wanted;
  }
  const i = ORDER.indexOf(wanted);
  if (i < 0 || !caps.levels.length) return "default";
  return [...caps.levels].sort((a, b) => Math.abs(ORDER.indexOf(a) - i) - Math.abs(ORDER.indexOf(b) - i) || ORDER.indexOf(b) - ORDER.indexOf(a))[0];
}

/**
 * Thinking for this conversation, next to the model picker. Adapts to what
 * the model can do: a menu of levels, a single on/off button, a quiet
 * "Always thinks" note, or nothing when the model has no thinking control.
 * A level the model doesn't support is shown as the one actually used, with
 * a one-line note in the menu.
 */
export function ThinkingControl({
  caps,
  level,
  onChange,
}: {
  caps: ThinkingCaps | undefined;
  level: ThinkingLevel;
  onChange: (level: ThinkingLevel) => void;
}) {
  const { t } = useTranslation("chat");
  if (!caps || caps.shape === "none") return null;

  if (caps.shape === "always") {
    return (
      <Tooltip label={t("thinking.alwaysTip")}>
        <span className="flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-2 text-ui text-muted" data-testid="thinking-control" data-shape="always">
          <Brain className="size-[17px] shrink-0" aria-hidden />
          <span className="max-sm:sr-only">{t("thinking.always")}</span>
        </span>
      </Tooltip>
    );
  }

  const now = effectiveThinking(level, caps);

  if (caps.shape === "toggle") {
    const on = (now === "default" ? (caps.default ?? "on") : now) !== "off";
    return (
      <Tooltip label={on ? t("thinking.toggleOn") : t("thinking.toggleOff")}>
        <button
          type="button"
          aria-pressed={on}
          aria-label={t("thinking.name")}
          onClick={() => onChange(on ? "off" : "on")}
          data-testid="thinking-control"
          data-shape="toggle"
          data-effective={on ? "on" : "off"}
          className={cn(
            "flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-2 text-ui transition-colors",
            on ? "bg-accent-soft font-medium text-accent-text" : "text-muted hover:bg-hover hover:text-fg",
          )}
        >
          <Brain className="size-[17px] shrink-0" aria-hidden />
          <span className="hidden sm:inline">{t("thinking.name")}</span>
        </button>
      </Tooltip>
    );
  }

  const shown = now === "default" ? "default" : now;
  const visible = shown === "default" ? t("thinking.name") : t(`thinking.levels.${shown}`);
  const clamped = level !== "default" && now !== level;
  const choices: ThinkingLevel[] = ["default", ...caps.levels.filter((l) => l !== "on")];
  const defaultHint =
    caps.default && caps.default !== "default" && caps.levels.includes(caps.default)
      ? t("thinking.hints.defaultKnown", { level: t(`thinking.levels.${caps.default}`).toLocaleLowerCase() })
      : t("thinking.hints.default");
  return (
    <MenuRoot>
      <Tooltip label={t("thinking.levelsTip")}>
        <MenuTrigger asChild>
          <button
            type="button"
            // The visible word starts the accessible name (WCAG 2.5.3).
            aria-label={t(`thinking.buttonName.${shown}`)}
            data-testid="thinking-control"
            data-shape="levels"
            data-level={level}
            data-effective={now}
            className="flex h-8 shrink-0 items-center gap-1 rounded-lg pr-1.5 pl-2 text-ui text-muted transition-colors hover:bg-hover hover:text-fg data-[state=open]:bg-hover data-[state=open]:text-fg"
          >
            <Brain className="size-[17px] shrink-0" aria-hidden />
            <span className="hidden sm:inline">{visible}</span>
            <ChevronDown className="size-3.5 shrink-0 opacity-70" aria-hidden />
          </button>
        </MenuTrigger>
      </Tooltip>
      <MenuContent align="end" side="top" className="w-72">
        <MenuLabel>{t("thinking.menuTitle")}</MenuLabel>
        {clamped && (
          <p className="px-2.5 pb-1.5 text-caption leading-snug text-muted" data-testid="thinking-clamped">
            {t("thinking.clamped", { wanted: t(`thinking.levels.${level}`), used: t(`thinking.levels.${now}`).toLocaleLowerCase() })}
          </p>
        )}
        <MenuRadioGroup value={clamped ? now : level} onValueChange={(v) => onChange(v as ThinkingLevel)}>
          {choices.map((c) => (
            <MenuRadioItem key={c} value={c} hint={c === "default" ? defaultHint : t(`thinking.hints.${c}`)} testId={`thinking-${c}`}>
              {t(`thinking.levels.${c}`)}
            </MenuRadioItem>
          ))}
        </MenuRadioGroup>
      </MenuContent>
    </MenuRoot>
  );
}
