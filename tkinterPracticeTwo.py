import tkinter as tk
from tkinter import ttk, messagebox
import scriptFile as sf
import sv_ttk as svtk
import pywinstyles, sys, webbrowser, threading, queue
from monitor import SystemMonitor

# Opens the CloudWatch Metrics browser filtered to our namespace — always free
_CW_URL = (
    "https://us-east-1.console.aws.amazon.com/cloudwatch/home"
    "?region=us-east-1#metricsV2:graph=~();namespace=TaskViewer~2FSystemStats"
)

_monitor = None  # set after the tkinter root window is created

# One window per action — maps a key like "stats" to its open Toplevel
_open_windows = {}


# --- Helpers ---
def open_single_window(key):
    """Return a new Toplevel for `key`, or None if one is already open.

    If the window already exists it's restored and brought to the front instead,
    so pressing the same main-window button twice never opens a duplicate.
    """
    existing = _open_windows.get(key)
    # winfo_exists() is 0 once the user closes the window, so a stale entry is ignored
    if existing is not None and existing.winfo_exists():
        existing.deiconify()  # un-minimize if needed
        existing.lift()
        existing.focus_force()
        return None

    win = tk.Toplevel(root)
    _open_windows[key] = win
    return win


def apply_theme_to_titlebar(win):
    version = sys.getwindowsversion()
    if version.major == 10 and version.build >= 22000:
        pywinstyles.change_header_color(win, "#1c1c1c" if svtk.get_theme() == "dark" else "#fafafa")
    elif version.major == 10:
        pywinstyles.apply_style(win, "dark" if svtk.get_theme() == "dark" else "normal")

# --- Actions ---
def killTask():
    win = open_single_window("kill")
    if win is None:
        return
    win.title("Kill Task")

    ttk.Label(win, text="Enter exact process name (without .exe):").grid(row=0, column=0, padx=10, pady=(10, 4), sticky="w")
    e = ttk.Entry(win)
    e.grid(row=1, column=0, padx=10, pady=(0, 8), sticky="ew")

    def on_kill():
        name = e.get().strip()
        if not name:
            messagebox.showwarning("Missing", "Please enter a process name.")
            return
        if sf.killProccessByName(name):
            messagebox.showinfo("Done", f"Killed '{name}.exe'")
        else:
            messagebox.showwarning("Not found", f"No running process named '{name}.exe'")
        win.destroy()

    ttk.Button(win, text="Kill", command=on_kill).grid(row=2, column=0, padx=10, pady=(0, 10), sticky="ew")
    e.focus_set()

    win.columnconfigure(0, weight=1)
    apply_theme_to_titlebar(win)

def searchAndDestroy():
    win = open_single_window("search")
    if win is None:
        return
    win.title("Search & Destroy")
    win.geometry("420x420")

    # Internal state to map listbox rows -> (pid, name)
    results = []

    # Query row
    ttk.Label(win, text="Enter part of the process name:").grid(row=0, column=0, padx=10, pady=(10, 4), sticky="w")
    q = ttk.Entry(win)
    q.grid(row=1, column=0, padx=10, pady=(0, 8), sticky="ew")

    # Listbox + scrollbar
    ttk.Label(win, text="Matching items:").grid(row=2, column=0, padx=10, sticky="w")
    list_frame = ttk.Frame(win)
    list_frame.grid(row=3, column=0, padx=10, pady=(0, 10), sticky="nsew")


    lst = tk.Listbox(list_frame, height=14, selectmode=tk.EXTENDED)  # allow multi-select
    vsb = ttk.Scrollbar(list_frame, orient="vertical", command=lst.yview)
    lst.configure(yscrollcommand=vsb.set)

    lst.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    list_frame.rowconfigure(0, weight=1)
    list_frame.columnconfigure(0, weight=1)

    # Actions
    def do_search(*_):
        nonlocal results
        lst.delete(0, tk.END)
        query = q.get().strip()
        results = sf.search(query) or []  # Expecting iterable of (pid, name) or similar
        for (pid, name) in results:
            lst.insert(tk.END, f"{pid}\t{name}")

    def do_kill_selected():
        sel_indices = lst.curselection()
        if not sel_indices:
            messagebox.showwarning("No selection", "Please select one or more items to kill.")
            return

        # Gather selected PIDs and names
        to_kill = []
        for idx in sel_indices:
            try:
                pid, name = results[idx][0], results[idx][1]
                to_kill.append((int(pid), name))
            except Exception:
                # Skip bad rows defensively
                continue

        if not to_kill:
            messagebox.showwarning("No valid selection", "Could not parse selected items.")
            return

        # Optional confirm
        names_preview = ", ".join(f"{pid}({name})" for pid, name in to_kill[:8])
        if len(to_kill) > 8:
            names_preview += ", ..."
        if not messagebox.askyesno("Confirm kill",
                                   f"Kill {len(to_kill)} process(es)?\n{names_preview}"):
            return

        successes, failures = 0, 0
        failed_items = []
        for pid, name in to_kill:
            try:
                if sf.kill_by_pid(pid):
                    successes += 1
                else:
                    failures += 1
                    failed_items.append(f"{pid}({name})")
            except Exception:
                failures += 1
                failed_items.append(f"{pid}({name})")

        # Refresh list after action
        do_search()

        if failures == 0:
            messagebox.showinfo("Done", f"Killed {successes} process(es).")
        else:
            messagebox.showwarning(
                "Partial",
                f"Killed {successes} process(es).\nFailed: {failures}\n{', '.join(failed_items[:10])}"
            )

    # Bottom buttons row
    btn_frame = ttk.Frame(win)
    btn_frame.grid(row=4, column=0, padx=10, pady=(0, 10), sticky="ew")

    ttk.Button(btn_frame, text="Search", command=do_search).grid(row=0, column=0, padx=5, sticky="ew")
    ttk.Button(btn_frame, text="Kill Selected", command=do_kill_selected).grid(row=0, column=1, padx=5, sticky="ew")

    # Stretch columns
    btn_frame.columnconfigure(0, weight=1)
    btn_frame.columnconfigure(1, weight=1)
    win.columnconfigure(0, weight=1)
    win.rowconfigure(3, weight=1)

    # Nice-to-have: Enter triggers search, Ctrl+A selects all
    q.bind("<Return>", do_search)
    win.bind("<Control-a>", lambda e: (lst.select_set(0, tk.END), "break"))
    q.focus_set()

    apply_theme_to_titlebar(win)

def showSystemStats():
    win = open_single_window("stats")
    if win is None:
        return
    win.title("System Stats")
    win.geometry("580x640")
    win.resizable(True, True)
    apply_theme_to_titlebar(win)

    # The top-process scan is the costly part of monitoring, so the monitor only
    # runs it often while this window is open to show the results
    _monitor.set_live_view(True)

    def _on_stats_close():
        _monitor.set_live_view(False)
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", _on_stats_close)

    # -- Live Readings --
    live_frame = ttk.LabelFrame(win, text="Live Readings", padding=8)
    live_frame.grid(row=0, column=0, padx=10, pady=(10, 4), sticky="ew")

    cpu_var  = tk.StringVar(value="CPU: --")
    mem_var  = tk.StringVar(value="RAM: --")
    disk_var = tk.StringVar(value="Disk: --")
    net_var  = tk.StringVar(value="Net Sent: --   Net Recv: --")

    ttk.Label(live_frame, textvariable=cpu_var).grid(row=0, column=0, padx=8, sticky="w")
    ttk.Label(live_frame, textvariable=mem_var).grid(row=0, column=1, padx=8, sticky="w")
    ttk.Label(live_frame, textvariable=disk_var).grid(row=0, column=2, padx=8, sticky="w")
    ttk.Label(live_frame, textvariable=net_var).grid(row=1, column=0, columnspan=3, padx=8, pady=(4,0), sticky="w")

    # -- Top Processes --
    top_frame = ttk.LabelFrame(win, text="Top Processes", padding=8)
    top_frame.grid(row=1, column=0, padx=10, pady=4, sticky="ew")

    top_cpu_var  = tk.StringVar(value="CPU:  --")
    top_ram_var  = tk.StringVar(value="RAM:  --")
    top_disk_var = tk.StringVar(value="Disk: --")

    ttk.Label(top_frame, text="CPU: ",  width=5, anchor="w").grid(row=0, column=0, padx=(8,0), pady=2, sticky="w")
    ttk.Label(top_frame, textvariable=top_cpu_var).grid(row=0, column=1, padx=4, pady=2, sticky="w")
    ttk.Label(top_frame, text="RAM: ",  width=5, anchor="w").grid(row=1, column=0, padx=(8,0), pady=2, sticky="w")
    ttk.Label(top_frame, textvariable=top_ram_var).grid(row=1, column=1, padx=4, pady=2, sticky="w")
    ttk.Label(top_frame, text="Disk: ", width=5, anchor="w").grid(row=2, column=0, padx=(8,0), pady=2, sticky="w")
    ttk.Label(top_frame, textvariable=top_disk_var).grid(row=2, column=1, padx=4, pady=2, sticky="w")

    # -- History Table --
    hist_frame = ttk.LabelFrame(win, text="History (last 100 readings)", padding=8)
    hist_frame.grid(row=2, column=0, padx=10, pady=4, sticky="nsew")

    cols = ("timestamp", "cpu_pct", "mem_pct", "disk_pct", "net_sent_mb", "net_recv_mb")
    tv = ttk.Treeview(hist_frame, columns=cols, show="headings", height=12)
    for col, heading, width in [
        ("timestamp",   "Timestamp (UTC)", 160),
        ("cpu_pct",     "CPU %",            60),
        ("mem_pct",     "RAM %",            60),
        ("disk_pct",    "Disk %",           60),
        ("net_sent_mb", "Net Sent MB",      90),
        ("net_recv_mb", "Net Recv MB",      90),
    ]:
        tv.heading(col, text=heading)
        tv.column(col, width=width, anchor="center")

    vsb = ttk.Scrollbar(hist_frame, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=vsb.set)
    tv.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hist_frame.rowconfigure(0, weight=1)
    hist_frame.columnconfigure(0, weight=1)

    # Past-session rows are dimmed so they're visually distinct from the current session
    tv.tag_configure("past", foreground="#888888")

    # -- AWS Sync Status --
    aws_frame = ttk.LabelFrame(win, text="AWS Sync Status", padding=8)
    aws_frame.grid(row=3, column=0, padx=10, pady=(4, 10), sticky="ew")

    push_var  = tk.StringVar(value="Last push: waiting for first cycle...")
    error_var = tk.StringVar(value="")
    ttk.Label(aws_frame, textvariable=push_var).grid(row=0, column=0, padx=8, sticky="w")
    ttk.Label(aws_frame, textvariable=error_var, foreground="red").grid(row=1, column=0, padx=8, sticky="w")

    # Worker threads must never touch tkinter (it isn't thread-safe), so the clear
    # worker drops its result message here and _refresh shows it on the UI thread
    clear_results = queue.Queue()

    def _do_clear():
        if not messagebox.askyesno("Clear Past Sessions",
                                   "Delete all past-session records from DynamoDB and history?"):
            return
        # Run in a daemon thread — DynamoDB batch deletes can take a moment
        def _run():
            clear_results.put(_monitor.clear_past_sessions())
        threading.Thread(target=_run, daemon=True).start()

    btn_frame = ttk.Frame(aws_frame)
    btn_frame.grid(row=2, column=0, padx=4, pady=(4, 0), sticky="w")
    ttk.Button(btn_frame, text="View Metrics",        command=lambda: webbrowser.open(_CW_URL)).grid(row=0, column=0, padx=4)
    ttk.Button(btn_frame, text="Clear Past Sessions", command=_do_clear).grid(row=0, column=1, padx=4)

    win.columnconfigure(0, weight=1)
    win.rowconfigure(2, weight=1)  # history table stretches when window is resized

    # (row count, newest timestamp) of the history last drawn — lets _refresh skip
    # rebuilding the table when nothing new was pushed, so scrolling isn't reset
    shown_history = None

    def _refresh():
        nonlocal shown_history
        # Guard against the window being closed while a pending after() is queued
        if not win.winfo_exists():
            return

        if _monitor is not None:
            rec = _monitor.get_latest()
            if rec:
                cpu_var.set(f"CPU: {rec['cpu_pct']}%")
                mem_var.set(f"RAM: {rec['mem_pct']}%")
                disk_var.set(f"Disk: {rec['disk_pct']}%")
                sent_mb = rec['net_bytes_sent'] / 1024 / 1024
                recv_mb = rec['net_bytes_recv'] / 1024 / 1024
                net_var.set(f"Net Sent: {sent_mb:.1f} MB   Net Recv: {recv_mb:.1f} MB")

                if rec.get("top_cpu"):
                    top_cpu_var.set(rec["top_cpu"])
                    top_ram_var.set(rec["top_ram"])
                    top_disk_var.set(rec["top_disk"])

            # Rebuild history table from in-memory data — no AWS call made here
            history = _monitor.get_history()
            key = (len(history), history[-1]["timestamp"] if history else None)
            if key != shown_history:
                shown_history = key
                _draw_history(history)

            status = _monitor.get_aws_status()
            if status["last_push_time"]:
                push_var.set(f"Last push: {status['last_push_time']}")
            error_var.set(f"Error: {status['last_error']}" if status["last_error"] else "")

        win.after(1000, _refresh)  # poll every second — cheap, it only reads memory

        # Show any finished "Clear Past Sessions" result. Done after scheduling the
        # next refresh, because showinfo blocks until dismissed and the live
        # readings should keep updating behind it
        try:
            msg = clear_results.get_nowait()
        except queue.Empty:
            pass
        else:
            messagebox.showinfo("Done", msg)

    def _draw_history(history):
        tv.delete(*tv.get_children())
        for r in reversed(history):  # newest first
            sent_mb = r['net_bytes_sent'] / 1024 / 1024
            recv_mb = r['net_bytes_recv'] / 1024 / 1024
            # Apply "past" tag to dim records from previous sessions
            tag = ("past",) if r.get("past_session") else ()
            tv.insert("", "end", values=(
                r["timestamp"][:19].replace("T", " "),
                f"{r['cpu_pct']:.1f}",
                f"{r['mem_pct']:.1f}",
                f"{r['disk_pct']:.1f}",
                f"{sent_mb:.1f}",
                f"{recv_mb:.1f}",
            ), tags=tag)

    win.after(100, _refresh)  # first call after window renders


# --- Main Window ---
root = tk.Tk()
root.title("Main Window")
root.geometry("300x240")

svtk.use_dark_theme()

ttk.Label(root, text="Select an action:", font=("Arial", 14)).grid(row=0, column=0, pady=10, padx=10)
ttk.Button(root, text="Close Program (Kill by name)", command=killTask).grid(row=1, column=0, padx=10, pady=5, sticky="ew")
ttk.Button(root, text="Search & Destroy", command=searchAndDestroy).grid(row=2, column=0, padx=10, pady=5, sticky="ew")

ttk.Button(root, text="System Stats", command=showSystemStats).grid(row=3, column=0, padx=10, pady=5, sticky="ew")

root.columnconfigure(0, weight=1)

apply_theme_to_titlebar(root)

_monitor = SystemMonitor()
_monitor.start()


def on_close():
    """Stop the monitor thread cleanly before exiting, so an in-flight AWS push can finish."""
    root.withdraw()            # hide immediately so closing feels instant
    _monitor.stop()            # wakes the thread from its wait() right away
    _monitor.join(timeout=2)   # give a scan or push in progress a moment to finish
    root.destroy()             # ends mainloop; the daemon flag covers anything still running


root.protocol("WM_DELETE_WINDOW", on_close)

root.mainloop()
