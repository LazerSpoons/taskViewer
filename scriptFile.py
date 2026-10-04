import psutil
import re


def _iter_processes(attrs):
    """Yield (proc, info) for every running process, like psutil.process_iter.

    psutil.process_iter keeps a module-level cache of Process objects and
    overwrites proc.info on them each time it runs. The monitor's background
    thread uses process_iter (it needs that cache for per-process CPU %), so if
    these UI-thread functions used it too, the two threads could overwrite each
    other's proc.info mid-scan. Fresh Process objects here belong only to the
    caller, so there's no shared state and nothing to lock.
    """
    for pid in psutil.pids():
        try:
            proc = psutil.Process(pid)
            info = proc.as_dict(attrs=attrs)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue  # Process exited or is protected — skip it
        yield proc, info


def killProccessByName(name: str) -> bool:
    if not name:
        return False

    target = (name + ".exe").casefold()
    found = False
    for proc, info in _iter_processes(['name']):
        try:
            pname = (info.get('name') or "").casefold()
            if pname == target:
                proc.kill()
                found = True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return found


def kill_by_pid(pid: int) -> bool:

    try:
        psutil.Process(int(pid)).kill()
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, ValueError):
        return False


def testThing():
    print("Test thing works")

def _compact(s: str) -> str:
    """Case-insensitive normalization that strips ALL non-alphanumerics."""
    return re.sub(r"[^0-9a-z]", "", (s or "").casefold())

def search(query: str):
    """
    Windows-friendly search across PROCESSES + SERVICES.

    Matches if EITHER:
      1) All space-separated tokens appear somewhere in process name/basename/exe path/cmdline, OR
      2) The compacted query (no spaces/punct) is a substring of those same fields;
         PLUS the same checks against Windows service name/display_name/binpath.

    Returns: list of (pid, name) sorted with exact/prefix matches first.
    """
    q_raw = (query or "").strip()
    q = q_raw.casefold()
    q_compact = _compact(q_raw)
    tokens = [t for t in q.split() if t]

    results = []
    seen_pids = set()

    # -------- 1) Processes --------
    for _, info in _iter_processes(['pid', 'name', 'exe', 'cmdline']):
        try:
            pid  = info['pid']
            name = info.get('name') or ""
            ncf  = name.casefold()
            bcf  = ncf[:-4] if ncf.endswith(".exe") else ncf
            ecf  = (info.get('exe') or "").casefold()
            ccf  = " ".join(info.get('cmdline') or []).casefold()

            ncp = _compact(name)
            bcp = _compact(name[:-4] if name.lower().endswith(".exe") else name)
            ecp = _compact(info.get('exe') or "")
            ccp = _compact(" ".join(info.get('cmdline') or []))

            haystacks         = (ncf, bcf, ecf, ccf)
            compact_haystacks = (ncp, bcp, ecp, ccp)

            token_match   = (not tokens) or all(any(t in h for h in haystacks) for t in tokens)
            compact_match = bool(q_compact) and any(q_compact in h for h in compact_haystacks)

            if token_match or compact_match:
                results.append((pid, name))
                seen_pids.add(pid)

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    # -------- 2) Windows Services --------
    try:
        for svc in psutil.win_service_iter():
            try:
                s = svc.as_dict()
                disp  = s.get('display_name') or ""
                sname = s.get('name') or ""
                binp  = s.get('binpath') or ""
                pid   = s.get('pid')

                svc_fields      = (disp.casefold(), sname.casefold(), binp.casefold())
                svc_compact_all = (_compact(disp), _compact(sname), _compact(binp))

                token_match   = (not tokens) or all(any(t in f for f in svc_fields) for t in tokens)
                compact_match = bool(q_compact) and any(q_compact in f for f in svc_compact_all)

                if (token_match or compact_match) and pid and pid not in seen_pids:
                    try:
                        pname = psutil.Process(pid).name()
                    except Exception:
                        pname = "unknown.exe"
                    results.append((pid, f"{pname} [svc: {disp or sname}]"))
                    seen_pids.add(pid)
            except Exception:
                continue
    except Exception:
        pass

    def sort_key(item):
        pid, name = item
        ncf = name.casefold()
        bcf = ncf[:-4] if ncf.endswith(".exe") else ncf
        ncp = _compact(name)
        bcp = _compact(name[:-4] if name.lower().endswith(".exe") else name)

        exact  = (q and (bcf == q or ncf == q)) or (q_compact and (bcp == q_compact or ncp == q_compact))
        prefix = (q and (bcf.startswith(q) or ncf.startswith(q))) or (q_compact and (bcp.startswith(q_compact) or ncp.startswith(q_compact)))
        return (not exact, not prefix, ncf, pid)

    results.sort(key=sort_key)
    return results
