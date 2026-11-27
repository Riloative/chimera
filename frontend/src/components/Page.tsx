import { Menu } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useUI } from "@/stores/ui";
import { IconButton } from "./ui";

export function PageShell({ children, wide }: { children: ReactNode; wide?: boolean }) {
  const { t } = useTranslation("sidebar");
  const setMobile = useUI((s) => s.setMobileSidebar);
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 shrink-0 items-center px-3 md:hidden">
        <IconButton label={t("open")} onClick={() => setMobile(true)}>
          <Menu className="size-[18px]" />
        </IconButton>
      </div>
      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className={wide ? "mx-auto w-full max-w-5xl px-4 pb-16 md:px-8 md:pt-10" : "mx-auto w-full max-w-3xl px-4 pb-16 md:px-8 md:pt-10"}>{children}</div>
      </main>
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-[1.5rem] font-medium tracking-[-0.02em] text-fg">{title}</h1>
        {description && <p className="mt-1.5 max-w-xl text-sm leading-relaxed text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Section({ title, description, children, actions }: { title: ReactNode; description?: ReactNode; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="mb-10">
      <div className="mb-3 flex items-end justify-between gap-4">
        <div>
          <h2 className="text-lead font-medium text-fg">{title}</h2>
          {description && <p className="mt-0.5 text-ui leading-relaxed text-muted">{description}</p>}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function Group({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <section className="mb-10 last:mb-0">
      <h2 className="text-lead font-medium text-fg">{title}</h2>
      {description && <p className="mt-0.5 text-ui leading-relaxed text-muted">{description}</p>}
      <div className="mt-4 space-y-5">{children}</div>
    </section>
  );
}
