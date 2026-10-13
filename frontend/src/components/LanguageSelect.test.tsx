import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useTranslation } from "react-i18next";
import i18n, { detectLanguage, STORAGE_KEY } from "@/i18n";
import { useAuth } from "@/stores/auth";
import { LanguageSelect } from "./LanguageSelect";

function Probe() {
  const { t } = useTranslation("sidebar");
  return <p>{t("newChat")}</p>;
}

afterEach(async () => {
  localStorage.clear();
  await i18n.changeLanguage("en");
});

describe("LanguageSelect", () => {
  it("switches instantly, sets <html lang> and remembers the choice when signed out", async () => {
    useAuth.setState({ status: "anonymous", user: null });
    render(
      <>
        <LanguageSelect />
        <Probe />
      </>,
    );
    expect(screen.getByText("New chat")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Language" }), "lt");
    expect(await screen.findByText("Naujas pokalbis")).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("lt");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("lt");
    expect(screen.getByRole("option", { name: "English" })).toBeInTheDocument(); // native names
    expect(screen.getByRole("option", { name: "Lietuvių" })).toBeInTheDocument();
  });

  it("saves the choice to the account when signed in", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ id: "u", username: "a", email: "a@x.io", role: "user", language: "lt", active: true, must_change_password: false, last_login_at: null, created_at: "" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    useAuth.setState({ status: "authenticated", user: { id: "u", username: "a", email: "a@x.io", role: "user", language: "en", active: true, must_change_password: false, last_login_at: null, created_at: "" } });
    render(<LanguageSelect />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Language" }), "lt");
    expect(fetchMock).toHaveBeenCalledWith("/api/account", expect.objectContaining({ method: "PATCH", body: JSON.stringify({ language: "lt" }) }));
    expect(useAuth.getState().user?.language).toBe("lt");
    vi.unstubAllGlobals();
  });
});

describe("detectLanguage", () => {
  it("prefers a saved choice, then the browser languages, then English", () => {
    const original = Object.getOwnPropertyDescriptor(window.navigator, "languages");
    Object.defineProperty(window.navigator, "languages", { value: ["lt-LT", "en-US"], configurable: true });
    expect(detectLanguage()).toBe("lt");
    localStorage.setItem(STORAGE_KEY, "en");
    expect(detectLanguage()).toBe("en");
    localStorage.clear();
    Object.defineProperty(window.navigator, "languages", { value: ["de-DE"], configurable: true });
    expect(detectLanguage()).toBe("en");
    if (original) Object.defineProperty(window.navigator, "languages", original);
  });
});
