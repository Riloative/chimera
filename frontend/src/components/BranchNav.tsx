import { ChevronLeft, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

/** Inline `‹ 2 / 3 ›` navigator between sibling versions of a message. */
export function BranchNav({
  index,
  total,
  kind,
  onSelect,
  className,
}: {
  /** 0-based position among siblings. */
  index: number;
  total: number;
  kind: "user" | "assistant";
  onSelect: (index: number) => void;
  className?: string;
}) {
  const { t } = useTranslation("chat");
  if (total < 2) return null;
  const label = kind === "assistant" ? t("branch.responseOf", { n: index + 1, total }) : t("branch.versionOf", { n: index + 1, total });
  const button = "flex size-6 items-center justify-center rounded-md text-faint transition-colors hover:bg-hover hover:text-fg disabled:opacity-35 disabled:hover:bg-transparent";
  return (
    <div role="group" aria-label={label} className={cn("flex items-center gap-0.5 text-caption text-muted tabular-nums", className)} data-testid="branch-nav">
      <button
        type="button"
        className={button}
        disabled={index === 0}
        onClick={() => onSelect(index - 1)}
        aria-label={kind === "assistant" ? t("branch.previousResponse") : t("branch.previousVersion")}
      >
        <ChevronLeft className="size-3.5" />
      </button>
      <span aria-live="polite" aria-atomic="true">
        <span className="sr-only">{label}</span>
        <span aria-hidden>
          {index + 1} / {total}
        </span>
      </span>
      <button
        type="button"
        className={button}
        disabled={index >= total - 1}
        onClick={() => onSelect(index + 1)}
        aria-label={kind === "assistant" ? t("branch.nextResponse") : t("branch.nextVersion")}
      >
        <ChevronRight className="size-3.5" />
      </button>
    </div>
  );
}
