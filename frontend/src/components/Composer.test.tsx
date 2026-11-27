import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { Composer } from "./Composer";
import { TooltipProvider } from "./ui";
import { useSettings } from "@/stores/settings";
import { useUI } from "@/stores/ui";
import type { AppSettings } from "@/lib/types";

function setup(props: Partial<React.ComponentProps<typeof Composer>> = {}) {
  const onSend = vi.fn().mockResolvedValue(undefined);
  render(
    <MemoryRouter>
      <TooltipProvider>
        <Composer onSend={onSend} model={{ providerId: "p", model: "m" }} onModelChange={() => {}} visionSupported={false} {...props} />
      </TooltipProvider>
    </MemoryRouter>,
  );
  return { onSend, input: screen.getByLabelText("Message") as HTMLTextAreaElement };
}

beforeEach(() => {
  useSettings.setState({ settings: { enter_to_send: true } as AppSettings });
  useUI.setState({ composerDrafts: {} });
});

describe("Composer", () => {
  it("sends on Enter and inserts a newline on Shift+Enter", async () => {
    const { onSend, input } = setup();
    await userEvent.type(input, "line one{Shift>}{Enter}{/Shift}line two");
    expect(input.value).toBe("line one\nline two");
    await userEvent.keyboard("{Enter}");
    expect(onSend).toHaveBeenCalledWith("line one\nline two", [], { urls: null, web_search: null });
    expect(input.value).toBe("");
  });

  it("does not send empty messages", async () => {
    const { onSend, input } = setup();
    await userEvent.type(input, "   {Enter}");
    expect(onSend).not.toHaveBeenCalled();
  });

  it("requires a model", async () => {
    const { onSend, input } = setup({ model: null });
    await userEvent.type(input, "hello{Enter}");
    expect(onSend).not.toHaveBeenCalled();
    expect(screen.getByText("Select a model to start chatting.")).toBeInTheDocument();
  });

  it("uses Ctrl+Enter when enter-to-send is off", async () => {
    useSettings.setState({ settings: { enter_to_send: false } as AppSettings });
    const { onSend, input } = setup();
    await userEvent.type(input, "hi{Enter}");
    expect(onSend).not.toHaveBeenCalled();
    await userEvent.keyboard("{Control>}{Enter}{/Control}");
    expect(onSend).toHaveBeenCalledWith("hi\n", [], { urls: null, web_search: null });
  });

  it("shows a stop button while streaming and Escape stops", async () => {
    const onStop = vi.fn();
    const { input } = setup({ streaming: true, onStop });
    expect(screen.getByRole("button", { name: "Stop generating" })).toBeInTheDocument();
    input.focus();
    await userEvent.keyboard("{Escape}");
    expect(onStop).toHaveBeenCalled();
  });

  it("restores the text if sending fails", async () => {
    const onSend = vi.fn().mockRejectedValue(new Error("nope"));
    const { input } = setup({ onSend });
    await userEvent.type(input, "keep me{Enter}");
    await vi.waitFor(() => expect(input.value).toBe("keep me"));
  });
});
