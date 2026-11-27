import { ChevronDown, Globe, Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import type { SearchMode } from "@/lib/types";
import { cn } from "@/lib/utils";
import { effectiveSearch } from "@/stores/web";
import { MenuContent, MenuItem, MenuLabel, MenuRadioGroup, MenuRadioItem, MenuRoot, MenuSeparator, MenuTrigger, Tooltip } from "./ui";

/**
 * Web search for a conversation: the button switches the conversation between
 * off and on (stored on the server); the menu beside it also offers "Auto" (the
 * model decides) and a one-off choice for the next message only. A one-off
 * choice is drawn with a dashed outline and explained under the composer.
 */
export function SearchControl({
  mode,
  override,
  available,
  autoAvailable,
  isAdmin,
  onModeChange,
  onOverrideChange,
}: {
  mode: SearchMode;
  override: boolean | undefined;
  available: boolean;
  autoAvailable: boolean;
  isAdmin: boolean;
  onModeChange: (mode: SearchMode) => void;
  onOverrideChange: (value: boolean | null) => void;
}) {
  const { t } = useTranslation("web");
  const navigate = useNavigate();
  if (!available) {
    return (
      <Tooltip label={isAdmin ? t("composer.searchUnavailableAdmin") : t("composer.searchUnavailableUser")}>
        <button
          type="button"
          aria-disabled
          aria-label={t("composer.searchName.off")}
          onClick={() => isAdmin && navigate("/settings/web")}
          data-testid="search-toggle"
          className="flex h-8 cursor-not-allowed items-center gap-1.5 rounded-lg px-2 text-ui text-faint opacity-60"
        >
          <Globe className="size-[18px] shrink-0" />
          <span className="hidden sm:inline">{t("composer.search")}</span>
        </button>
      </Tooltip>
    );
  }
  // Auto falls back to off when an administrator has turned it off.
  const stored: SearchMode = mode === "auto" && !autoAvailable ? "off" : mode;
  const now = effectiveSearch(stored, override);
  const oneOff = override !== undefined;
  const active = now !== "off";
  const label = t(`composer.searchName.${stored}`);
  const tip = oneOff ? (override ? t("composer.oneOffOn") : t("composer.oneOffOff")) : t(`composer.searchTip.${stored}`);
  return (
    <div
      className={cn(
        "flex h-8 items-center rounded-lg border transition-colors",
        oneOff ? "border-dashed border-line-strong" : "border-transparent",
        active ? "bg-accent-soft text-accent-text" : "text-muted",
      )}
      data-testid="search-control"
      data-mode={stored}
      data-effective={now}
    >
      <Tooltip label={tip}>
        <button
          type="button"
          aria-pressed={stored !== "off"}
          aria-label={label}
          onClick={() => onModeChange(stored === "off" ? "on" : "off")}
          data-testid="search-toggle"
          className={cn("flex h-full items-center gap-1.5 rounded-l-lg pr-1 pl-2 text-ui", active ? "font-medium" : "hover:bg-hover hover:text-fg")}
        >
          {stored === "auto" ? <Sparkles className="size-[17px] shrink-0" /> : <Globe className="size-[18px] shrink-0" />}
          {/* The visible word starts the accessible name (WCAG 2.5.3). */}
          <span className={cn(active || stored === "auto" ? "inline" : "hidden sm:inline")}>{stored === "auto" ? t("composer.auto") : t("composer.search")}</span>
        </button>
      </Tooltip>
      <MenuRoot>
        <MenuTrigger asChild>
          <button
            type="button"
            aria-label={t("composer.searchOptions")}
            data-testid="search-options"
            className={cn("flex h-full w-6 items-center justify-center rounded-r-lg", !active && "hover:bg-hover hover:text-fg")}
          >
            <ChevronDown className="size-3.5 opacity-80" />
          </button>
        </MenuTrigger>
        <MenuContent align="start" side="top" className="w-72">
          <MenuLabel>{t("composer.menu.conversation")}</MenuLabel>
          <MenuRadioGroup value={stored} onValueChange={(v) => onModeChange(v as SearchMode)}>
            <MenuRadioItem value="off" hint={t("composer.menu.offHint")} testId="search-mode-off">
              {t("composer.menu.off")}
            </MenuRadioItem>
            <MenuRadioItem value="on" hint={t("composer.menu.onHint")} testId="search-mode-on">
              {t("composer.menu.on")}
            </MenuRadioItem>
            {autoAvailable && (
              <MenuRadioItem value="auto" hint={t("composer.menu.autoHint")} testId="search-mode-auto">
                {t("composer.menu.auto")}
              </MenuRadioItem>
            )}
          </MenuRadioGroup>
          <MenuSeparator />
          <MenuLabel>{t("composer.menu.nextOnly")}</MenuLabel>
          {oneOff ? (
            <MenuItem onSelect={() => onOverrideChange(null)}>{t("composer.menu.undoOneOff")}</MenuItem>
          ) : stored === "off" ? (
            <MenuItem onSelect={() => onOverrideChange(true)}>{t("composer.menu.searchNext")}</MenuItem>
          ) : (
            <MenuItem onSelect={() => onOverrideChange(false)}>{t("composer.menu.skipNext")}</MenuItem>
          )}
        </MenuContent>
      </MenuRoot>
    </div>
  );
}
