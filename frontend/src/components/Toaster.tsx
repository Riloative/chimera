import { AlertTriangle, CheckCircle2, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useUI } from "@/stores/ui";

export function Toaster() {
  const toasts = useUI((s) => s.toasts);
  const dismiss = useUI((s) => s.dismissToast);
  return (
    <div aria-live="polite" className="pointer-events-none fixed bottom-4 left-1/2 z-[60] flex w-[min(26rem,calc(100vw-2rem))] -translate-x-1/2 flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          role={t.tone === "error" ? "alert" : "status"}
          className={cn(
            "animate-pop-in pointer-events-auto flex items-start gap-2.5 rounded-xl border bg-elevated px-3.5 py-2.5 text-[0.8125rem] shadow-pop",
            t.tone === "error" ? "border-danger/30" : "border-line",
          )}
        >
          {t.tone === "error" ? (
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" aria-label="Error" />
          ) : t.tone === "success" ? (
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" aria-label="Success" />
          ) : null}
          <span className="min-w-0 flex-1 leading-relaxed break-words text-fg">{t.message}</span>
          {t.action && (
            <button type="button" className="font-medium text-accent-text hover:underline" onClick={t.action.onClick}>
              {t.action.label}
            </button>
          )}
          <button type="button" aria-label="Dismiss" onClick={() => dismiss(t.id)} className="text-faint hover:text-fg">
            <X className="size-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
