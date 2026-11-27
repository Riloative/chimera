import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/i18n";
import type { AppSettings, ThinkingCaps, ThinkingLevel } from "@/lib/types";
import { useSettings } from "@/stores/settings";
import { useUI } from "@/stores/ui";
import { Composer } from "./Composer";
import { ThinkingControl, effectiveThinking } from "./ThinkingControl";
import { TooltipProvider } from "./ui";

const LEVELS: ThinkingCaps = { shape: "levels", levels: ["off", "low", "medium", "high"], default: "medium", source: "table" };
const NO_OFF: ThinkingCaps = { shape: "levels", levels: ["low", "medium", "high", "max"], default: "medium", source: "detected" };
const TOGGLE: ThinkingCaps = { shape: "toggle", levels: ["off", "on"], default: "on", source: "detected" };

function setup(caps: ThinkingCaps | undefined, level: ThinkingLevel = "default") {
  const onChange = vi.fn();
  const view = render(
    <TooltipProvider>
      <ThinkingControl caps={caps} level={level} onChange={onChange} />
    </TooltipProvider>,
  );
  return { onChange, ...view };
}

afterEach(async () => {
  await i18n.changeLanguage("en");
});

describe("effectiveThinking (same rules as the server's clamp)", () => {
  it("picks the nearest supported level", () => {
    expect(effectiveThinking("off", NO_OFF)).toBe("low");
    expect(effectiveThinking("max", LEVELS)).toBe("high");
    expect(effectiveThinking("on", LEVELS)).toBe("medium");
    expect(effectiveThinking("high", TOGGLE)).toBe("on");
    expect(effectiveThinking("off", TOGGLE)).toBe("off");
    expect(effectiveThinking("medium", { shape: "levels", levels: ["off", "low", "high", "max"], default: null, source: "table" })).toBe("high");
    expect(effectiveThinking("high", { shape: "none", levels: [], default: null, source: "none" })).toBe("default");
    expect(effectiveThinking("high", undefined)).toBe("default");
  });
});

describe("ThinkingControl", () => {
  it("levels: a named menu button with arrow keys, and focus back on the trigger", async () => {
    const { onChange } = setup(LEVELS, "default");
    const trigger = screen.getByRole("button", { name: "Thinking: the model's default" });
    expect(trigger).toHaveTextContent("Thinking");
    expect(trigger.className).toMatch(/\bh-8\b/); // 32 px tall (≥ 24×24 target; measured in e2e)
    trigger.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByRole("menu")).toBeInTheDocument();
    expect(screen.getByText("Thinking in this conversation")).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: /^Default/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("The model's usual amount (medium).")).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: /^Default/ })).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}");
    expect(screen.getByRole("menuitemradio", { name: /^Off/ })).toHaveFocus();
    await userEvent.keyboard("{ArrowDown}{Enter}");
    expect(onChange).toHaveBeenCalledWith("low");
    expect(trigger).toHaveFocus();
  });

  it("levels: shows the stored level, or the one used when the model can't do it", async () => {
    setup(NO_OFF, "off");
    const trigger = screen.getByRole("button", { name: "Low thinking" });
    expect(trigger).toHaveAttribute("data-effective", "low");
    await userEvent.click(trigger);
    expect(screen.getByTestId("thinking-clamped")).toHaveTextContent("Off isn't available for this model, so low is used.");
    expect(screen.getByRole("menuitemradio", { name: /^Low/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByRole("menuitemradio", { name: /^Off/ })).toBeNull();
  });

  it("toggle: one pressed/unpressed button", async () => {
    const { onChange, rerender } = setup(TOGGLE, "default");
    const button = screen.getByRole("button", { name: "Thinking" });
    expect(button).toHaveAttribute("aria-pressed", "true"); // the model thinks by default
    await userEvent.click(button);
    expect(onChange).toHaveBeenCalledWith("off");
    rerender(
      <TooltipProvider>
        <ThinkingControl caps={TOGGLE} level="off" onChange={onChange} />
      </TooltipProvider>,
    );
    expect(screen.getByRole("button", { name: "Thinking" })).toHaveAttribute("aria-pressed", "false");
  });

  it("always thinks: a quiet note; unsupported: nothing at all", () => {
    const { unmount } = setup({ shape: "always", levels: [], default: "on", source: "table" });
    expect(screen.getByText("Always thinks")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
    unmount();
    const { container } = setup({ shape: "none", levels: [], default: null, source: "none" });
    expect(container).toBeEmptyDOMElement();
  });

  it("Lithuanian names start with the visible word", async () => {
    await i18n.changeLanguage("lt");
    setup(LEVELS, "high");
    const trigger = screen.getByRole("button", { name: "Išsamus mąstymas" });
    expect(trigger).toHaveTextContent("Išsamus");
    await userEvent.click(trigger);
    expect(screen.getByText("Mąstymas šiame pokalbyje")).toBeInTheDocument();
    expect(screen.getByRole("menuitemradio", { name: /^Išjungtas/ })).toBeInTheDocument();
    expect(screen.getByText("Atsako iš karto.")).toBeInTheDocument();
  });
});

describe("Composer drafts", () => {
  beforeEach(() => {
    useSettings.setState({ settings: { enter_to_send: true } as AppSettings });
    useUI.setState({ composerDrafts: {}, draftChars: { key: "", n: 0 } });
  });

  const composer = (key: string) => (
    <MemoryRouter>
      <TooltipProvider>
        <Composer onSend={vi.fn()} model={{ providerId: "p", model: "m" }} onModelChange={() => {}} visionSupported={false} conversationKey={key} />
      </TooltipProvider>
    </MemoryRouter>
  );

  it("regression: a half-written message stays with its conversation", async () => {
    const { rerender } = render(composer("a"));
    const input = screen.getByLabelText("Message") as HTMLTextAreaElement;
    await userEvent.type(input, "for chat A");
    rerender(composer("b"));
    expect(input.value).toBe("");
    await userEvent.type(input, "for B");
    rerender(composer("a"));
    expect(input.value).toBe("for chat A");
    rerender(composer("b"));
    expect(input.value).toBe("for B");
  });

  it("shows the thinking control next to the model picker", () => {
    render(
      <MemoryRouter>
        <TooltipProvider>
          <Composer
            onSend={vi.fn()}
            model={{ providerId: "p", model: "m" }}
            onModelChange={() => {}}
            visionSupported={false}
            thinking="high"
            thinkingCaps={LEVELS}
            onThinkingChange={() => {}}
          />
        </TooltipProvider>
      </MemoryRouter>,
    );
    const control = screen.getByTestId("thinking-control");
    expect(control.nextElementSibling).toBe(screen.getByRole("button", { name: /^Model: / }));
  });
});
