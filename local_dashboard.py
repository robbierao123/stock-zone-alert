import os
import tkinter as tk
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

VIEW_FILE = "dashboard_view.txt"

CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", 5))

class DashboardApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Zone Dashboard Mirror")
        self.root.geometry("1000x620")
        self.root.configure(bg="black")

        self.last_content = None
        self.last_file_mtime = None

        # top info bar
        self.info_frame = tk.Frame(root, bg="#111111")
        self.info_frame.pack(fill="x")

        self.status_label = tk.Label(
            self.info_frame,
            text="Status: Waiting for dashboard data...",
            bg="#111111",
            fg="white",
            font=("Consolas", 11, "bold"),
            anchor="w",
            padx=10,
            pady=6,
        )
        self.status_label.pack(fill="x")

        self.meta_label = tk.Label(
            self.info_frame,
            text="Tickers: 0 | Active Hits: 0 | Last Refresh: --",
            bg="#111111",
            fg="#cccccc",
            font=("Consolas", 10),
            anchor="w",
            padx=10,
            pady=4,
        )
        self.meta_label.pack(fill="x")

        # main text area
        self.text = tk.Text(
            root,
            bg="black",
            fg="white",
            insertbackground="white",
            font=("Consolas", 12),
            padx=12,
            pady=12,
            borderwidth=0,
            wrap="none",
        )
        self.text.pack(fill="both", expand=True)

        # optional scrollbar
        self.scrollbar = tk.Scrollbar(self.text)
        self.scrollbar.pack(side="right", fill="y")
        self.text.config(yscrollcommand=self.scrollbar.set)
        self.scrollbar.config(command=self.text.yview)

        self.update_loop()

    def load_content(self) -> str:
        path = Path(VIEW_FILE)
        if not path.exists():
            return "Waiting for dashboard data..."
        return path.read_text(encoding="utf-8")

    def get_file_mtime(self) -> str:
        path = Path(VIEW_FILE)
        if not path.exists():
            return "--"
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")

    def parse_dashboard_stats(self, content: str) -> tuple[int, int]:
        """
        Returns:
            total_tickers_shown, active_hits
        """
        lines = content.splitlines()
        ticker_count = 0
        active_hits = 0

        for line in lines:
            stripped = line.strip()

            if (
                not stripped
                or stripped.startswith("```")
                or stripped.startswith("LIVE ZONE HITS")
                or stripped.startswith("Last Updated:")
                or stripped.startswith("Ticker  Price")
                or stripped.startswith("------")
                or stripped.startswith("None")
            ):
                continue

            # dashboard rows
            parts = stripped.split()
            if len(parts) >= 2:
                ticker_count += 1

                if "HIT" in stripped:
                    active_hits += 1

        return ticker_count, active_hits

    def refresh_display(self, content: str) -> None:
        self.text.config(state="normal")
        self.text.delete("1.0", tk.END)

        clean_content = content.strip() + "\n"
        self.text.insert("1.0", clean_content)

        self.text.config(state="disabled")

    def update_info_bar(self, content: str) -> None:
        ticker_count, active_hits = self.parse_dashboard_stats(content)
        file_refresh_time = self.get_file_mtime()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if Path(VIEW_FILE).exists():
            self.status_label.config(
                text=f"Status: Live auto-refresh running every {CHECK_INTERVAL_SECONDS}s"
            )
        else:
            self.status_label.config(text="Status: Waiting for dashboard_view.txt...")

        self.meta_label.config(
            text=(
                f"Tickers Shown: {ticker_count} | "
                f"Active Hits: {active_hits} | "
                f"Dashboard Updated: {file_refresh_time} | "
                f"Local Refresh: {now_str}"
            )
        )

    def update_loop(self) -> None:
        content = self.load_content()

        if content != self.last_content:
            self.refresh_display(content)
            self.last_content = content

        self.update_info_bar(content)
        self.root.after(CHECK_INTERVAL_SECONDS , self.update_loop)


if __name__ == "__main__":
    root = tk.Tk()
    app = DashboardApp(root)
    root.mainloop()