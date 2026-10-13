import { AlertTriangle, CheckCircle2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { useUI } from "@/stores/ui";

export function Toaster() {
  const { t } = useTranslation();
  const toasts = useUI((s) => s.toasts);
  const dismiss = useUI((s) => s.dismissToast);
  return (
    <div aria-live="polite" className="pointer-events-none fixed bottom-4 left-1/2 z-[60] flex w-[min(26rem,calc(100vw-2rem))] -translate-x-1/2 flex-col gap-2">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          role={toast.tone === "error" ? "alert" : "status"}
          className={cn(
            "animate-pop-in pointer-events-auto flex items-start gap-2.5 rounded-xl border bg-elevated px-3.5 py-2.5 text-[0.8125rem] shadow-pop",
            toast.tone === "error" ? "border-danger/30" : "border-line",
          )}
        >
          {toast.tone === "error" ? (
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" aria-label={t("error")} />
          ) : toast.tone === "success" ? (
            <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" aria-label={t("success")} />
          ) : null}
          <span className="min-w-0 flex-1 leading-relaxed break-words text-fg">{toast.message}</span>
          {toast.action && (
            <button type="button" className="font-medium text-accent-text hover:underline" onClick={toast.action.onClick}>
              {toast.action.label}
            </button>
          )}
          <button type="button" aria-label={t("dismiss")} onClick={() => dismiss(toast.id)} className="text-faint hover:text-fg">
            <X className="size-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}
