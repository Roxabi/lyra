import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ChatComposer } from "@/components/chat/ChatComposer";
import "@/i18n";

describe("ChatComposer", () => {
  it("forwards typed input to onChange as a string (Astryx value-first contract)", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<ChatComposer value="" disabled={false} onChange={onChange} onSend={vi.fn()} />);

    await user.type(screen.getByRole("textbox"), "h");

    // Astryx TextArea's onChange passes the value string, not the DOM event —
    // ChatComposer forwards it straight through, so a regression to `e.target.value`
    // wiring would surface here.
    expect(onChange).toHaveBeenCalled();
    expect(typeof onChange.mock.calls[0][0]).toBe("string");
  });

  it("sends on Enter but not on Shift+Enter", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatComposer value="hello" disabled={false} onChange={vi.fn()} onSend={onSend} />);
    const textarea = screen.getByRole("textbox");

    await user.type(textarea, "{Enter}");
    expect(onSend).toHaveBeenCalledTimes(1);

    await user.type(textarea, "{Shift>}{Enter}{/Shift}");
    expect(onSend).toHaveBeenCalledTimes(1); // Shift+Enter is a newline, not a send
  });

  it("does not send on Enter while the agent is offline (disabled)", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<ChatComposer value="hello" disabled={true} onChange={vi.fn()} onSend={onSend} />);

    await user.type(screen.getByRole("textbox"), "{Enter}");
    // A disabled textarea receives no key events; Enter must be inert.
    expect(onSend).not.toHaveBeenCalled();
  });
});
