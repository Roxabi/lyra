import { Table, TableHeader, TableRow } from "@astryxdesign/core/Table";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SortableTableHeader } from "@/components/ui/sortable-table-header";

function renderHeader(props: Parameters<typeof SortableTableHeader>[0]) {
  return render(
    <Table>
      <TableHeader>
        <TableRow isHeaderRow>
          <SortableTableHeader {...props} />
        </TableRow>
      </TableHeader>
    </Table>,
  );
}

describe("SortableTableHeader", () => {
  it("renders an Astryx columnheader with aria-sort reflecting the active direction", () => {
    renderHeader({ label: "Name", active: true, direction: "asc", onClick: vi.fn() });
    expect(screen.getByRole("columnheader").getAttribute("aria-sort")).toBe("ascending");
  });

  it("reports aria-sort=none when not the active column", () => {
    renderHeader({ label: "Name", active: false, direction: "desc", onClick: vi.fn() });
    expect(screen.getByRole("columnheader").getAttribute("aria-sort")).toBe("none");
  });

  it("calls onClick when the header button is pressed", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    renderHeader({ label: "Started", active: false, direction: "asc", onClick });
    await user.click(screen.getByRole("button", { name: /Started/ }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
