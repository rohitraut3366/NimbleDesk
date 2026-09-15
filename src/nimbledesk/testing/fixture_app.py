from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict


class FixtureState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready: bool = False
    counter: int = 0
    submitted_text: str = ""
    checkbox_enabled: bool = False
    selected_item: str | None = None
    dialog_count: int = 0
    drag_completed: bool = False
    password_length: int = 0
    closed: bool = False


class FixtureRecorder:
    def __init__(self, state_path: Path, events_path: Path) -> None:
        self.state_path = state_path
        self.events_path = events_path
        self.state = FixtureState()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_state()

    def update(self, event: str, **changes: Any) -> None:
        for name, value in changes.items():
            if name not in FixtureState.model_fields:
                raise ValueError(f"unknown fixture state field: {name}")
            setattr(self.state, name, value)
        self._write_state()
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {"timestamp": time.time(), "event": event, "state": self.state.model_dump()},
                    separators=(",", ":"),
                )
                + "\n"
            )

    def _write_state(self) -> None:
        temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temporary.write_text(self.state.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.state_path)


def run_fixture(state_path: Path, events_path: Path) -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    recorder = FixtureRecorder(state_path, events_path)
    root = tk.Tk()
    root.title("NimbleDesk Backend Fixture")
    root.geometry("720x700")
    root.minsize(640, 600)
    container = ttk.Frame(root, padding=18)
    container.pack(fill="both", expand=True)
    ttk.Label(
        container,
        text="NimbleDesk Backend Fixture",
        font=("TkDefaultFont", 18, "bold"),
    ).pack(anchor="w")
    ttk.Label(
        container,
        text=(
            "Known controls for native accessibility, pointer, keyboard, scroll, "
            "and dialog tests."
        ),
    ).pack(anchor="w", pady=(2, 14))

    status = tk.StringVar(value="Ready")
    counter_text = tk.StringVar(value="Counter: 0")

    def increment() -> None:
        next_value = recorder.state.counter + 1
        counter_text.set(f"Counter: {next_value}")
        status.set("Counter incremented")
        recorder.update("counter_incremented", counter=next_value)

    ttk.Button(container, text="Increment counter", command=increment).pack(anchor="w")
    ttk.Label(container, textvariable=counter_text).pack(anchor="w", pady=(4, 14))

    ttk.Label(container, text="Public text").pack(anchor="w")
    public_text = ttk.Entry(container, name="public_text")
    public_text.pack(fill="x", pady=(2, 5))

    def submit_text() -> None:
        value = public_text.get()
        status.set(f"Submitted {len(value)} characters")
        recorder.update("text_submitted", submitted_text=value)

    ttk.Button(container, text="Submit public text", command=submit_text).pack(anchor="w")

    ttk.Label(container, text="Password field (must be redacted)").pack(anchor="w", pady=(14, 0))
    password = ttk.Entry(container, name="password_field", show="•")
    password.pack(fill="x", pady=(2, 5))

    def submit_password() -> None:
        length = len(password.get())
        password.delete(0, "end")
        status.set("Password length recorded without storing its value")
        recorder.update("password_submitted", password_length=length)

    ttk.Button(container, text="Submit password length", command=submit_password).pack(anchor="w")

    checkbox_value = tk.BooleanVar(value=False)

    def toggle_checkbox() -> None:
        recorder.update("checkbox_toggled", checkbox_enabled=checkbox_value.get())

    ttk.Checkbutton(
        container,
        text="Enable fixture option",
        variable=checkbox_value,
        command=toggle_checkbox,
    ).pack(anchor="w", pady=(14, 10))

    ttk.Label(container, text="Scrollable choices").pack(anchor="w")
    choices_frame = ttk.Frame(container)
    choices_frame.pack(fill="x", pady=(2, 10))
    choices = tk.Listbox(choices_frame, height=5, exportselection=False)
    scrollbar = ttk.Scrollbar(choices_frame, orient="vertical", command=choices.yview)
    choices.configure(yscrollcommand=scrollbar.set)
    choices.pack(side="left", fill="x", expand=True)
    scrollbar.pack(side="right", fill="y")
    for index in range(1, 101):
        choices.insert("end", f"Choice {index:03d}")

    def select_choice(_event: object) -> None:
        selection = choices.curselection()  # type: ignore[no-untyped-call]
        if selection:
            recorder.update("choice_selected", selected_item=str(choices.get(selection[0])))

    choices.bind("<<ListboxSelect>>", select_choice)

    def show_dialog() -> None:
        count = recorder.state.dialog_count + 1
        recorder.update("dialog_opened", dialog_count=count)
        messagebox.showinfo("Fixture dialog", "Dialog opened successfully", parent=root)
        recorder.update("dialog_closed")

    ttk.Button(container, text="Open fixture dialog", command=show_dialog).pack(anchor="w")

    ttk.Label(container, text="Drag the blue square into the green target").pack(
        anchor="w", pady=(14, 2)
    )
    canvas = tk.Canvas(container, width=600, height=110, background="#f4f5f7", name="drag_area")
    canvas.pack(fill="x")
    draggable = canvas.create_rectangle(20, 30, 70, 80, fill="#1976d2", tags=("draggable",))
    canvas.create_rectangle(500, 20, 580, 90, outline="#278a46", width=4, tags=("target",))
    drag_offset = [0.0, 0.0]

    def drag_start(event: tk.Event[tk.Misc]) -> None:
        left, top, _right, _bottom = canvas.coords(draggable)
        drag_offset[:] = [event.x - left, event.y - top]

    def drag_move(event: tk.Event[tk.Misc]) -> None:
        left = event.x - drag_offset[0]
        top = event.y - drag_offset[1]
        canvas.coords(draggable, left, top, left + 50, top + 50)

    def drag_end(_event: tk.Event[tk.Misc]) -> None:
        left, top, right, bottom = canvas.coords(draggable)
        completed = left >= 500 and top >= 20 and right <= 580 and bottom <= 90
        status.set("Drag completed" if completed else "Drag target missed")
        recorder.update("drag_finished", drag_completed=completed)

    canvas.tag_bind("draggable", "<ButtonPress-1>", drag_start)
    canvas.tag_bind("draggable", "<B1-Motion>", drag_move)
    canvas.tag_bind("draggable", "<ButtonRelease-1>", drag_end)
    ttk.Label(container, textvariable=status, name="fixture_status").pack(anchor="w", pady=(14, 0))

    def close() -> None:
        recorder.update("closed", closed=True)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    recorder.update("ready", ready=True)
    public_text.focus_set()
    root.mainloop()


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the NimbleDesk native-backend fixture app")
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> None:
    parsed = parse_args(arguments)
    run_fixture(parsed.state, parsed.events)


if __name__ == "__main__":
    main()
