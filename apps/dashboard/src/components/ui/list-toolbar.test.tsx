import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { ListToolbarSearch } from "@/components/ui/list-toolbar";

function SearchHarness() {
  const [value, setValue] = useState("");
  return (
    <ListToolbarSearch
      value={value}
      onChange={setValue}
      placeholder="Rechercher…"
      aria-label="Search agents"
    />
  );
}

describe("ListToolbarSearch", () => {
  it("forwards typed text through the value-based TextInput onChange", async () => {
    // Guards the Input→TextInput migration: the onChange contract switched from
    // event-based (e.target.value) to value-based (next => onChange(next)).
    const user = userEvent.setup();
    render(<SearchHarness />);
    const input = screen.getByRole("textbox", { name: "Search agents" });
    await user.type(input, "lyra");
    expect((input as HTMLInputElement).value).toBe("lyra");
  });
});
