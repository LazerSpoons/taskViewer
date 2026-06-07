import collections
import logging
import socket
import threading
from datetime import datetime, timezone
from decimal import Decimal
from logging.handlers import RotatingFileHandler

import psutil

# How often (in seconds) the background thread collects stats and pushes to AWS
_INTERVAL = 60

# Maximum number of readings kept in memory for the UI to display
_HISTORY  = 100

# Module-level logger — errors from AWS calls go here, never to the UI
_log = logging.getLogger("taskviewer.monitor")
_log.setLevel(logging.ERROR)

# RotatingFileHandler caps the log at 500 KB then rolls over to a backup,
# so it never grows unbounded on a long-running machine
_log.addHandler(RotatingFileHandler(
    "monitor_errors.log", maxBytes=500_000, backupCount=2
))


class SystemMonitor:
    """Collects system stats in a background thread and pushes them to AWS."""

    def __init__(self, aws_region="us-east-1"):
        self._region   = aws_region
        # Identifies this machine in DynamoDB (partition key) and CloudWatch (dimension)
        self._hostname = socket.gethostname()

        # Mutex — both the background thread (writes) and UI thread (reads) touch
        # _history and _last_push_time, so we need a lock to prevent data races
        self._lock    = threading.Lock()

        # Event flag — calling stop() sets this, which wakes the sleeping thread early
        # and causes _run() to exit cleanly instead of waiting for the full 60s interval
        self._stop    = threading.Event()

        # Rolling buffer of the last 100 readings; oldest entry is dropped automatically
        # when maxlen is reached, so we never need to manually trim it
        self._history = collections.deque(maxlen=_HISTORY)

        # Shared state read by get_aws_status() — protected by _lock
        self._last_push_time = None  # ISO string of the last successful AWS push
        self._last_error     = None  # Error message string if the last push failed

        # boto3 clients are created inside the thread (_init_aws_clients),
        # not here — so the app still launches even if boto3 isn't installed yet
        self._cw_client = None  # CloudWatch client
        self._ddb_table = None  # DynamoDB Table resource

        # Recorded at construction time — used to tell apart records loaded from
        # DynamoDB (past sessions) from records collected in this run (current session)
        self._session_start = datetime.now(timezone.utc).isoformat()

        # daemon=True means this thread dies automatically when the main thread exits,
        # so closing the tkinter window doesn't leave a zombie process running
        self._thread = threading.Thread(
            target=self._run, name="SystemMonitor", daemon=True
        )

    # ---- Public API (all methods are safe to call from any thread) --------

    def start(self):
        """Start the background monitoring thread."""
        self._thread.start()

    def stop(self):
        """Signal the background thread to exit on its next wake-up."""
        self._stop.set()

    def get_latest(self):
        """Return the most recent reading as a dict, or None if no data yet."""
        with self._lock:
            # _history[-1] is the newest item; dict() makes a shallow copy so
            # the caller can't accidentally mutate the stored record
            return dict(self._history[-1]) if self._history else None

    def get_history(self):
        """Return a list of all stored readings (oldest first), each as a dict."""
        with self._lock:
            return [dict(r) for r in self._history]

    def get_aws_status(self):
        """Return the time of the last successful push and any current error message."""
        with self._lock:
            return {
                "last_push_time": self._last_push_time,
                "last_error":     self._last_error,
            }

    def clear_past_sessions(self):
        """Delete all past-session records from DynamoDB and the in-memory deque.

        Returns a human-readable status string for the UI to display.
        Runs any AWS calls synchronously — call this from a background thread
        so it doesn't block the UI.
        """
        with self._lock:
            past = [r for r in self._history if r.get("past_session")]

        if not past:
            return "No past session data to clear."

        if self._ddb_table is not None:
            try:
                # batch_writer handles the 25-items-per-request DynamoDB limit
                # automatically and retries any unprocessed items
                with self._ddb_table.batch_writer() as batch:
                    for r in past:
                        batch.delete_item(
                            Key={"hostname": r["hostname"], "timestamp": r["timestamp"]}
                        )
            except Exception as exc:
                _log.error("Clear past sessions failed: %s", exc)
                return f"Error deleting from DynamoDB: {exc}"

        with self._lock:
            current = [r for r in self._history if not r.get("past_session")]
            self._history.clear()
            self._history.extend(current)

        return f"Cleared {len(past)} past session record(s)."
    # ---- Private methods (only called from the background thread) ---------

    def _run(self):
        """Entry point for the background thread. Runs until stop() is called."""
        self._init_aws_clients()

        while not self._stop.is_set():
            record = self._collect()

            # Append to history under the lock so the UI thread sees a consistent state
            with self._lock:
                self._history.append(record)

            self._push_cloudwatch(record)
            self._push_dynamodb(record)

            # wait() sleeps for 60s BUT wakes immediately if stop() is called.
            # This is why we use Event.wait() instead of time.sleep() —
            # time.sleep(60) would make the app hang for up to 60s on close.
            self._stop.wait(timeout=_INTERVAL)

    def _init_aws_clients(self):
        """Create boto3 clients. Called once when the thread starts."""
        try:
            import boto3
            self._cw_client = boto3.client("cloudwatch", region_name=self._region)
            dynamodb        = boto3.resource("dynamodb", region_name=self._region)
            # Table() doesn't make a network call — it's just a reference to the table
            self._ddb_table = dynamodb.Table("SystemStats")
        except Exception as exc:
            # If boto3 isn't installed or credentials are missing, log it and continue.
            # _cw_client and _ddb_table stay None, and the push methods will no-op.
            _log.error("AWS client init failed: %s", exc)
        self._load_history_from_dynamodb()

    def _load_history_from_dynamodb(self):
        """Query DynamoDB for the last 100 readings and pre-populate the history deque.

        Called once on startup so the history table in the UI is immediately useful
        rather than empty until the first 60-second collection cycle completes.
        """
        if self._ddb_table is None:
            return
        try:
            from boto3.dynamodb.conditions import Key
            response = self._ddb_table.query(
                KeyConditionExpression=Key("hostname").eq(self._hostname),
                ScanIndexForward=False,  # newest first so Limit=100 gets the most recent
                Limit=_HISTORY,
            )
            items = response.get("Items", [])

            # Reverse so we insert oldest-first — the deque stores oldest at index 0
            records = []
            for item in reversed(items):
                records.append({
                    "hostname":       item["hostname"],
                    "timestamp":      item["timestamp"],
                    # DynamoDB returns numbers as Decimal — convert back to float/int
                    "cpu_pct":        float(item["cpu_pct"]),
                    "mem_pct":        float(item["mem_pct"]),
                    "disk_pct":       float(item["disk_pct"]),
                    "net_bytes_sent": int(item["net_bytes_sent"]),
                    "net_bytes_recv": int(item["net_bytes_recv"]),
                    # Older rows won't have these fields — fall back to "--"
                    "top_cpu":        item.get("top_cpu", "--"),
                    "top_ram":        item.get("top_ram", "--"),
                    "top_disk":       item.get("top_disk", "--"),
                    # Marks this record as from a previous run so the UI can style it differently
                    "past_session":   True,
                })

            with self._lock:
                self._history.extend(records)
        except Exception as exc:
            _log.error("DynamoDB history load failed: %s", exc)
    def _collect(self):
        """Read current system stats from psutil and return as a plain dict."""
        top_cpu, top_ram, top_disk = self._top_processes()
        return {
            "hostname":       self._hostname,
            "timestamp":      datetime.now(timezone.utc).isoformat(),
            "cpu_pct":        round(psutil.cpu_percent(interval=1), 2),
            "mem_pct":        round(psutil.virtual_memory().percent, 2),
            "disk_pct":       round(psutil.disk_usage("C:\\").percent, 2),
            "net_bytes_sent": psutil.net_io_counters().bytes_sent,
            "net_bytes_recv": psutil.net_io_counters().bytes_recv,
            "top_cpu":        top_cpu,
            "top_ram":        top_ram,
            "top_disk":       top_disk,
            "past_session":   False,
        }

    # Windows pseudo-processes that hold system memory but aren't real user apps.
    # Task Manager excludes these from its normal process list; we do the same
    # so they don't crowd out real applications like Firefox or Chrome.
    _SYSTEM_PROCS = {"MemCompression", "System", "Idle", "Registry"}

    def _top_processes(self):
        """Find the process using the most CPU, RAM, and disk I/O.

        Returns three strings like 'chrome.exe (45.2%)' — one per resource.
        Returns '--' for any resource where no process could be measured.

        Two important psutil behaviours to know:
        - cpu_percent(interval=None) returns CPU usage since the LAST call for
          that process. The very first call always returns 0.0, so the first
          collection cycle will show 0% for all processes. After that it works
          correctly, measuring usage over each 60-second window.
        - io_counters() counts cumulative bytes read+written since boot, not a
          rate. So 'top disk' means the process that has done the most total
          disk work since the machine started, not necessarily right now.
        """
        top_cpu_name,  top_cpu_val  = "--", -1.0
        top_ram_name,  top_ram_val  = "--", -1.0
        top_disk_name, top_disk_val = "--", -1

        # process_iter is efficient — it fetches these fields in one shot per process
        # rather than making a separate syscall for each attribute
        for proc in psutil.process_iter(["name", "cpu_percent", "memory_percent"]):
            try:
                name = proc.info["name"] or "unknown"

                # Skip Windows pseudo-processes — they hold system memory that
                # Task Manager also excludes from the normal process list
                if name in self._SYSTEM_PROCS:
                    continue

                cpu  = proc.info["cpu_percent"] or 0.0
                ram  = proc.info["memory_percent"] or 0.0

                if cpu > top_cpu_val:
                    top_cpu_val  = cpu
                    top_cpu_name = f"{name} ({cpu:.1f}%)"

                if ram > top_ram_val:
                    top_ram_val  = ram
                    top_ram_name = f"{name} ({ram:.1f}%)"

                try:
                    io = proc.io_counters()
                    total_io = io.read_bytes + io.write_bytes
                    if total_io > top_disk_val:
                        top_disk_val  = total_io
                        top_disk_name = f"{name} ({total_io / 1024 / 1024:.0f} MB)"
                except (psutil.AccessDenied, AttributeError):
                    # Some system processes deny access to io_counters — skip them
                    pass

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                # Process exited or is protected — skip and move on
                continue

        return top_cpu_name, top_ram_name, top_disk_name

    def _push_cloudwatch(self, record):
        """Send CPU, RAM, and Disk as 3 custom metrics in a single API call."""
        if self._cw_client is None:
            return

        try:
            dims = [{"Name": "Host", "Value": self._hostname}]
            ts   = datetime.fromisoformat(record["timestamp"])
            self._cw_client.put_metric_data(
                Namespace="TaskViewer/SystemStats",
                MetricData=[
                    {"MetricName": "CPUUsage",    "Dimensions": dims,
                     "Timestamp": ts, "Value": record["cpu_pct"],  "Unit": "Percent"},
                    {"MetricName": "MemoryUsage", "Dimensions": dims,
                     "Timestamp": ts, "Value": record["mem_pct"],  "Unit": "Percent"},
                    {"MetricName": "DiskUsage",   "Dimensions": dims,
                     "Timestamp": ts, "Value": record["disk_pct"], "Unit": "Percent"},
                ],
            )
            with self._lock:
                self._last_push_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                self._last_error     = None
        except Exception as exc:
            _log.error("CloudWatch push failed: %s", exc)
            with self._lock:
                self._last_error = f"CloudWatch: {exc}"

    def _push_dynamodb(self, record):
        """Write the full record to DynamoDB."""
        if self._ddb_table is None:
            return

        try:
            self._ddb_table.put_item(Item={
                "hostname":       record["hostname"],
                "timestamp":      record["timestamp"],
                # Decimal(str(x)) avoids float precision errors in DynamoDB
                "cpu_pct":        Decimal(str(record["cpu_pct"])),
                "mem_pct":        Decimal(str(record["mem_pct"])),
                "disk_pct":       Decimal(str(record["disk_pct"])),
                "net_bytes_sent": record["net_bytes_sent"],
                "net_bytes_recv": record["net_bytes_recv"],
                "top_cpu":        record["top_cpu"],
                "top_ram":        record["top_ram"],
                "top_disk":       record["top_disk"],
            })
        except Exception as exc:
            _log.error("DynamoDB push failed: %s", exc)
            with self._lock:
                self._last_error = f"DynamoDB: {exc}"
