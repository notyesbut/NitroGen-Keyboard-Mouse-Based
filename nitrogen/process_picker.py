from __future__ import annotations

import os
import sys
from typing import Dict, List, Tuple

import psutil

import win32gui
import win32process


def _normalize_process_name(value: str) -> str:
    name = value.strip().strip('"').strip("'")
    if "\\" in name or "/" in name:
        name = os.path.basename(name)
    return name


def _name_variants(name: str) -> set[str]:
    base = _normalize_process_name(name).lower()
    variants = {base}
    if base.endswith(".exe"):
        variants.add(base[:-4])
    else:
        variants.add(base + ".exe")
    return variants


def _process_name_matches(query: str, candidate: str) -> bool:
    return bool(_name_variants(query) & _name_variants(candidate))


def process_name_matches(query: str, candidate: str) -> bool:
    return _process_name_matches(query, candidate)


def parse_process_spec(value: str) -> Tuple[int | None, str]:
    raw = value.strip()
    if raw.lower().startswith("pid:"):
        try:
            return int(raw.split(":", 1)[1].strip()), raw
        except Exception:
            return None, raw
    if raw.isdigit():
        return int(raw), raw
    return None, _normalize_process_name(raw)


def process_exists(value: str) -> bool:
    pid, name = parse_process_spec(value)
    if pid is not None:
        return psutil.pid_exists(pid)
    for proc in psutil.process_iter(["name"]):
        try:
            pname = proc.info["name"]
            if pname and _process_name_matches(name, pname):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def process_has_window(value: str) -> bool:
    pid, name = parse_process_spec(value)
    windows = _collect_windows()
    if pid is not None:
        return pid in windows
    for pid in windows.keys():
        try:
            proc_name = psutil.Process(pid).name()
        except Exception:
            continue
        if proc_name and _process_name_matches(name, proc_name):
            return True
    return False


def _collect_windows() -> Dict[int, List[str]]:
    windows: Dict[int, List[str]] = {}

    def enum_window_callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid:
            if not title:
                title = "<untitled>"
            titles = windows.setdefault(pid, [])
            if title not in titles:
                titles.append(title)
        return True

    try:
        win32gui.EnumWindows(enum_window_callback, None)
    except Exception:
        pass
    return windows

def list_processes(show_all: bool = False) -> List[Dict[str, object]]:
    windows = _collect_windows()
    processes: List[Dict[str, object]] = []

    if show_all:
        seen = set()
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                pid = int(proc.info["pid"])
                name = proc.info.get("name") or f"pid_{pid}"
                titles = windows.get(pid, [])
                processes.append({"pid": pid, "name": name, "titles": titles})
                seen.add(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        for pid, titles in windows.items():
            if pid in seen:
                continue
            processes.append({"pid": pid, "name": f"pid_{pid}", "titles": titles})
    else:
        for pid, titles in windows.items():
            try:
                name = psutil.Process(pid).name()
            except Exception:
                name = f"pid_{pid}"
            processes.append({"pid": pid, "name": name, "titles": titles})

    processes.sort(
        key=lambda p: (
            0 if p.get("titles") else 1,
            str(p["name"]).lower(),
            int(p["pid"]),
        )
    )
    return processes


def list_visible_processes() -> List[Dict[str, object]]:
    return list_processes(show_all=False)


def _compact_titles(titles: List[str], max_len: int = 80) -> str:
    joined = " | ".join(titles)
    if len(joined) <= max_len:
        return joined
    return joined[: max_len - 3] + "..."


def _match_processes(processes: List[Dict[str, object]], query: str) -> List[Dict[str, object]]:
    q = _normalize_process_name(query).lower()
    if not q:
        return processes
    matches: List[Dict[str, object]] = []
    for proc in processes:
        name = str(proc.get("name", ""))
        if _process_name_matches(q, name):
            matches.append(proc)
            continue
        if q and q in name.lower():
            matches.append(proc)
            continue
        titles = [str(t) for t in proc.get("titles", [])]
        if any(q in t.lower() for t in titles):
            matches.append(proc)
    return matches


def _describe_list_mode(show_all: bool) -> str:
    return "all processes" if show_all else "windowed only"


def _has_window(proc: Dict[str, object]) -> bool:
    titles = proc.get("titles", [])
    return bool(titles)


def _confirm_no_window(proc: Dict[str, object]) -> bool:
    name = proc.get("name", "unknown")
    pid = proc.get("pid", "unknown")
    print(f"Process {name} (pid:{pid}) has no visible window.")
    reply = input("Use anyway? (y/N): ").strip().lower()
    return reply in {"y", "yes"}


def _supports_live_search() -> bool:
    if os.name != "nt":
        return False
    try:
        import msvcrt  # noqa: F401
    except Exception:
        return False
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _clear_screen() -> None:
    try:
        os.system("cls")
    except Exception:
        print("\n" * 5)


def _format_process_line(proc: Dict[str, object], idx: int) -> str:
    titles = _compact_titles([str(t) for t in proc["titles"]]) if proc.get("titles") else "<no window>"
    return f"{idx:2d}) {proc['name']} (pid:{proc['pid']}): {titles}"


def _derive_filter_text(buffer: str, current_filter: str) -> str:
    if not buffer:
        return ""
    if buffer.startswith("/"):
        return buffer[1:].strip()
    if buffer.startswith("#"):
        return current_filter
    if buffer.isdigit():
        return current_filter
    return buffer


def _select_from_proc(proc: Dict[str, object]) -> str | None:
    if not _has_window(proc) and not _confirm_no_window(proc):
        return None
    return f"pid:{proc['pid']}"


def _resolve_live_selection(
    buffer: str,
    visible: List[Dict[str, object]],
    default_name: str | None,
    default_ok: bool,
) -> tuple[str | None, str]:
    buf = buffer.strip()

    if not buf:
        if default_name and default_ok:
            return default_name, ""
        if len(visible) == 1:
            selection = _select_from_proc(visible[0])
            if selection:
                return selection, ""
            return None, "Selection cancelled."
        return None, "Type to filter or use #n to select."

    if buf.startswith("#"):
        idx_raw = buf[1:].strip()
        if not idx_raw.isdigit():
            return None, "Invalid index format. Use #n."
        index = int(idx_raw)
        if 1 <= index <= len(visible):
            selection = _select_from_proc(visible[index - 1])
            if selection:
                return selection, ""
            return None, "Selection cancelled."
        return None, "Index out of range."

    if buf.isdigit():
        index = int(buf)
        if 1 <= index <= len(visible):
            selection = _select_from_proc(visible[index - 1])
            if selection:
                return selection, ""
            return None, "Selection cancelled."
        if psutil.pid_exists(index):
            return f"pid:{index}", ""
        return None, "Invalid selection."

    pid, name = parse_process_spec(buf)
    if pid is not None:
        if psutil.pid_exists(pid):
            return f"pid:{pid}", ""
        return None, f"PID not found: {pid}"

    if len(visible) == 1:
        selection = _select_from_proc(visible[0])
        if selection:
            return selection, ""
        return None, "Selection cancelled."

    if len(visible) > 1:
        return None, "Multiple matches. Keep typing or use #n."

    if process_exists(name):
        return name, ""

    return None, "No matching process found."


def _choose_process_name_prompt(default_name: str | None = None, show_all_default: bool = False) -> str:
    show_all = bool(show_all_default)
    filter_text = ""
    while True:
        default_ok = process_has_window(default_name) if default_name else False
        processes = list_processes(show_all=show_all)
        visible = _match_processes(processes, filter_text)
        if processes:
            print(f"Active processes ({_describe_list_mode(show_all)}):")
            if filter_text:
                print(f"Filter: {filter_text}")
            if not visible:
                print("No matches for current filter.")
            for idx, proc in enumerate(visible, 1):
                titles = _compact_titles([str(t) for t in proc["titles"]]) if proc.get("titles") else "<no window>"
                print(f"{idx:2d}) {proc['name']} (pid:{proc['pid']}): {titles}")
        else:
            print("No processes found.")

        prompt = "Enter number, 'r' refresh, 'all' toggle view, '/text' filter, '/clear' reset: "
        if default_name:
            prompt = f"{prompt}(default: {default_name}) "
        choice = input(prompt).strip()

        if choice.lower() in {"r", "refresh"}:
            continue
        if choice.lower() in {"all", "windowed"}:
            show_all = not show_all if choice.lower() == "all" else False
            continue
        if choice.startswith("/"):
            if choice.strip() in {"/", "/clear"}:
                filter_text = ""
            else:
                filter_text = choice[1:].strip()
            continue
        if choice == "" and default_name:
            if not default_ok:
                print("Default process not found. Pick from the list or refresh.")
                continue
            return default_name
        if choice.isdigit():
            index = int(choice)
            if 1 <= index <= len(visible):
                proc = visible[index - 1]
                if not _has_window(proc) and not _confirm_no_window(proc):
                    continue
                pid = int(proc["pid"])
                return f"pid:{pid}"
            if psutil.pid_exists(index):
                return f"pid:{index}"
            print("Invalid selection.")
            continue
        if choice:
            pid, name = parse_process_spec(choice)
            if pid is not None:
                if psutil.pid_exists(pid):
                    return f"pid:{pid}"
                print(f"PID not found: {pid}")
                continue
            matches = _match_processes(processes, name)
            if len(matches) == 1:
                if not _has_window(matches[0]) and not _confirm_no_window(matches[0]):
                    continue
                return f"pid:{matches[0]['pid']}"
            if len(matches) > 1:
                filter_text = name
                print(f"Filter set to: {filter_text}")
                continue
            if process_exists(name):
                return name
            print("No matching process found. Use 'r' to refresh or enter pid:####.")
            continue


def _choose_process_name_live(
    default_name: str | None = None,
    show_all_default: bool = False,
    max_rows: int = 30,
) -> str:
    import msvcrt

    show_all = bool(show_all_default)
    filter_text = ""
    buffer = ""
    message = ""

    while True:
        default_ok = process_has_window(default_name) if default_name else False
        processes = list_processes(show_all=show_all)
        filter_text = _derive_filter_text(buffer, filter_text)
        visible = _match_processes(processes, filter_text)
        total_visible = len(visible)
        if max_rows > 0:
            visible = visible[:max_rows]

        _clear_screen()
        print("Process picker (live search)")
        print("Type to filter | Enter select | #n pick index | Tab toggle all | Esc clear | F5 refresh")
        if default_name:
            status = "ok" if default_ok else "missing"
            print(f"Default: {default_name} ({status})")
        print(
            f"Mode: {_describe_list_mode(show_all)} | "
            f"Filter: {filter_text or '<none>'} | "
            f"Matches: {total_visible} | "
            f"Input: {buffer}"
        )
        if message:
            print(f"Note: {message}")
        if not processes:
            print("No processes found.")
        elif not visible:
            print("No matches for current filter.")
        else:
            for idx, proc in enumerate(visible, 1):
                print(_format_process_line(proc, idx))
            if total_visible > len(visible):
                print(f"... showing first {len(visible)} of {total_visible} matches")

        message = ""
        ch = msvcrt.getwch()

        if ch in ("\r", "\n"):
            if buffer.strip().startswith("/"):
                cmd = buffer.strip().lower()
                if cmd in {"/", "/clear"}:
                    buffer = ""
                    filter_text = ""
                    message = "Filter cleared."
                else:
                    buffer = buffer.strip()[1:]
                continue

            selection, msg = _resolve_live_selection(buffer, visible, default_name, default_ok)
            if selection:
                return selection
            message = msg
            continue

        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x08":
            buffer = buffer[:-1]
            continue
        if ch == "\x1b":
            buffer = ""
            filter_text = ""
            continue
        if ch == "\t":
            show_all = not show_all
            continue
        if ch in ("\x00", "\xe0"):
            key = msvcrt.getwch()
            if key == "?":
                continue
            continue
        if ch.isprintable():
            buffer += ch


def choose_process_name(
    default_name: str | None = None,
    show_all_default: bool = False,
    live_search: bool = True,
    max_rows: int = 30,
) -> str:
    if live_search and _supports_live_search():
        return _choose_process_name_live(
            default_name=default_name,
            show_all_default=show_all_default,
            max_rows=max_rows,
        )
    return _choose_process_name_prompt(default_name=default_name, show_all_default=show_all_default)
