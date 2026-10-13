import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { SearchDialog } from "@/components/SearchDialog";
import { Sidebar } from "@/components/Sidebar";
import { Toaster } from "@/components/Toaster";
import { Spinner, TooltipProvider } from "@/components/ui";
import { api, onUnauthorized } from "@/lib/api";
import { ChatPage } from "@/pages/ChatPage";
import { KnowledgePage } from "@/pages/KnowledgePage";
import { LoginPage } from "@/pages/LoginPage";
import { ModelsPage } from "@/pages/ModelsPage";
import { PromptsPage } from "@/pages/PromptsPage";
import { SettingsPage } from "@/pages/SettingsPage";
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

function Layout() {
  return (
    <div className="flex h-full overflow-hidden bg-bg">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[70] focus:rounded-lg focus:bg-elevated focus:px-3 focus:py-2">
        Skip to content
      </a>
      <Sidebar />
      <div id="main" className="min-w-0 flex-1">
        <Outlet />
      </div>
      <SearchDialog />
      <Shortcuts />
    </div>
  );
}

type AuthState = "checking" | "required" | "ok" | "offline";

export default function App() {
  const [auth, setAuth] = useState<AuthState>("checking");

  const boot = async () => {
    try {
      const status = await api.authStatus();
      if (status.auth_required && !status.authenticated) {
        setAuth("required");
        return;
      }
      setAuth("ok");
      await Promise.all([useSettings.getState().load(), useConversations.getState().load(), useModels.getState().load(false)]);
    } catch {
      setAuth("offline");
    }
  };

  useEffect(() => {
    void boot();
    return onUnauthorized(() => setAuth("required"));
  }, []);

  if (auth === "checking")
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  if (auth === "required") return <LoginPage onSuccess={boot} />;
  if (auth === "offline")
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
        <p className="text-fg">Cannot reach the Chimera server.</p>
        <p className="text-sm text-muted">Make sure it is running, then try again.</p>
        <button type="button" onClick={boot} className="h-9 rounded-lg bg-fg px-4 text-sm font-medium text-bg">
          Retry
        </button>
      </div>
    );

  return (
    <TooltipProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
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
