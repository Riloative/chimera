import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { LegacyNotice } from "@/components/LegacyNotice";
import { SearchDialog } from "@/components/SearchDialog";
import { Sidebar } from "@/components/Sidebar";
import { Toaster } from "@/components/Toaster";
import { Spinner, TooltipProvider } from "@/components/ui";
import { onAuthEvent } from "@/lib/api";
import { ChangePasswordScreen, LoginPage, RegisterPage } from "@/pages/AuthPages";
import { ChatPage } from "@/pages/ChatPage";
import { KnowledgePage } from "@/pages/KnowledgePage";
import { ModelsPage } from "@/pages/ModelsPage";
import { PromptsPage } from "@/pages/PromptsPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { useAuth } from "@/stores/auth";
import { useConversations } from "@/stores/conversations";
import { useModels } from "@/stores/models";
import { useSettings } from "@/stores/settings";
import { useUI } from "@/stores/ui";

function Shortcuts() {
  const navigate = useNavigate();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod) return;
      const key = e.key.toLowerCase();
      if (key === "k" && !e.shiftKey) {
        e.preventDefault();
        useUI.getState().setSearchOpen(true);
      } else if (key === "o" && e.shiftKey) {
        e.preventDefault();
        navigate("/");
      } else if (key === "b" && !e.shiftKey) {
        const s = useSettings.getState();
        if (!s.settings) return;
        e.preventDefault();
        void s.update({ sidebar_collapsed: !s.settings.sidebar_collapsed }).catch(() => undefined);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);
  return null;
}

/** The signed-in application. Data loads only after authentication. */
function AppLayout() {
  const { t } = useTranslation();
  const loaded = useRef(false);
  useEffect(() => {
    if (loaded.current) return;
    loaded.current = true;
    void Promise.all([useSettings.getState().load(), useConversations.getState().load(), useModels.getState().load(false)]);
  }, []);
  return (
    <div className="flex h-full overflow-hidden bg-bg">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[70] focus:rounded-lg focus:bg-elevated focus:px-3 focus:py-2">
        {t("skipToContent")}
      </a>
      <Sidebar />
      <div id="main" className="flex min-w-0 flex-1 flex-col">
        <LegacyNotice />
        <div className="min-h-0 flex-1">
          <Outlet />
        </div>
      </div>
      <SearchDialog />
      <Shortcuts />
    </div>
  );
}

function RequireAuth() {
  const status = useAuth((s) => s.status);
  const mustChange = useAuth((s) => s.user?.must_change_password);
  const location = useLocation();
  if (status !== "authenticated") {
    const next = location.pathname + location.search;
    return <Navigate to={next === "/" ? "/login" : `/login?next=${encodeURIComponent(next)}`} replace />;
  }
  if (mustChange) return <ChangePasswordScreen />;
  return <AppLayout />;
}

function GuestOnly({ children }: { children: React.ReactNode }) {
  const status = useAuth((s) => s.status);
  const location = useLocation();
  if (status === "authenticated") {
    const next = new URLSearchParams(location.search).get("next");
    return <Navigate to={next && next.startsWith("/") && !next.startsWith("//") ? next : "/"} replace />;
  }
  return <>{children}</>;
}

export default function App() {
  const { t } = useTranslation();
  const { status, refresh, expire } = useAuth();

  useEffect(() => {
    void refresh();
    return onAuthEvent((event) => {
      if (event === "unauthorized") expire();
      else void refresh();
    });
  }, [refresh, expire]);

  if (status === "loading")
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  if (status === "offline")
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
        <p className="text-fg">{t("offline.title")}</p>
        <p className="text-sm text-muted">{t("offline.body")}</p>
        <button type="button" onClick={() => refresh()} className="h-9 rounded-lg bg-fg px-4 text-sm font-medium text-bg">
          {t("retry")}
        </button>
      </div>
    );

  return (
    <TooltipProvider>
      <BrowserRouter>
        <Routes>
          <Route path="login" element={<GuestOnly><LoginPage /></GuestOnly>} />
          <Route path="register" element={<GuestOnly><RegisterPage /></GuestOnly>} />
          <Route element={<RequireAuth />}>
            <Route index element={<ChatPage />} />
            <Route path="c/:id" element={<ChatPage />} />
            <Route path="models" element={<ModelsPage />} />
            <Route path="knowledge" element={<KnowledgePage />} />
            <Route path="knowledge/:id" element={<KnowledgePage />} />
            <Route path="prompts" element={<PromptsPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="settings/:section" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
      <Toaster />
    </TooltipProvider>
  );
}
