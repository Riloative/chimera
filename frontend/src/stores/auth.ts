import { create } from "zustand";
import { currentLanguage, isLanguage, setLanguage, STORAGE_KEY } from "@/i18n";
import { api, ApiError } from "@/lib/api";
import type { User } from "@/lib/types";

type Status = "loading" | "anonymous" | "authenticated" | "offline";

interface AuthState {
  status: Status;
  user: User | null;
  needsSetup: boolean;
  registrationOpen: boolean;
  setupTokenRequired: boolean;
  refresh: () => Promise<void>;
  login: (identifier: string, password: string, remember: boolean) => Promise<User>;
  register: (data: { email: string; username: string; password: string; password_confirm: string; setup_token?: string; remember: boolean }) => Promise<User>;
  logout: () => Promise<void>;
  setUser: (user: User) => void;
  expire: () => void;
}

/** At login the account's language wins; a new account adopts the screen's choice. */
async function reconcileLanguage(user: User) {
  if (isLanguage(user.language)) {
    if (user.language !== currentLanguage()) await setLanguage(user.language);
    return;
  }
  let local: string | null = null;
  try {
    local = localStorage.getItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
  const lng = isLanguage(local) ? local : currentLanguage();
  try {
    await api.updateAccount({ language: lng });
  } catch {
    /* not critical */
  }
}

export const useAuth = create<AuthState>((set, get) => ({
  status: "loading",
  user: null,
  needsSetup: false,
  registrationOpen: false,
  setupTokenRequired: false,
  refresh: async () => {
    try {
      const s = await api.authStatus();
      set({
        status: s.authenticated ? "authenticated" : "anonymous",
        user: s.user,
        needsSetup: s.needs_setup,
        registrationOpen: s.registration_open,
        setupTokenRequired: s.setup_token_required,
      });
      if (s.user) await reconcileLanguage(s.user);
    } catch (err) {
      set({ status: err instanceof ApiError && err.status === 401 ? "anonymous" : "offline" });
    }
  },
  login: async (identifier, password, remember) => {
    const user = await api.login({ identifier, password, remember });
    await reconcileLanguage(user);
    set({ status: "authenticated", user });
    return user;
  },
  register: async (data) => {
    const user = await api.register({ ...data, language: currentLanguage() });
    set({ status: "authenticated", user, needsSetup: false });
    return user;
  },
  logout: async () => {
    try {
      await api.logout();
    } finally {
      // A full reload guarantees no other user's data lingers in memory.
      window.location.assign("/login");
    }
  },
  setUser: (user) => set({ user }),
  expire: () => {
    if (get().status === "authenticated") set({ status: "anonymous", user: null });
  },
}));
