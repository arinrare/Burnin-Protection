"""
ml_pixel_shift.py
OLED burn-in prevention for Manor Lords (or any windowed/borderless game).

Every INTERVAL seconds the game window takes a bounded random walk: it moves
by MIN_SHIFT_PX-MAX_SHIFT_PX pixels in a random direction from wherever it
currently is, and dwells there for a full INTERVAL, bouncing off the edges of
a +/- MAX_DRIFT_PX box so it always lands on a new spot. It never returns to
the exact same spot, so wear is spread across a small region around the
original position instead of one pixel set. This moves every on-screen pixel
(world + UI), preventing static-element burn-in on OLED monitors - similar to
the WoW addon "Burnin Prevention!".

Works only when the game runs in WINDOWED or BORDERLESS WINDOWED mode.
Exclusive fullscreen ignores window positions, so nothing will move. When the
window fills the screen (e.g. via Borderless Gaming), each shift pushes one
edge off-screen and briefly shows a small strip of desktop - that is expected.

Usage:
    python ml_pixel_shift.py

Press Ctrl+C (or close the console) to stop. The window position is restored
if a shift is in progress when it exits.

Config - tweak below.
"""

import ctypes
import ctypes.wintypes as wt
import random
import sys
import time

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------
INTERVAL     = 15     # seconds each dwell position is held
MIN_SHIFT_PX = 20      # minimum pixels to shift
MAX_SHIFT_PX = 25     # maximum pixels to shift
MAX_DRIFT_PX = 100    # window top-left stays within +/- this of its original spot
DIRECTIONS   = ["up", "down", "left", "right",
                "up_left", "up_right", "down_left", "down_right"]

# Process name that owns the game window. "ManorLords-Win64-Shipping.exe" is the
# actual game; "ManorLords.exe" is the launcher. Matching "ManorLords" picks up
# either (whichever owns the largest visible window).
GAME_PROCESS = "Discord"

POLL_INTERVAL = 0.5   # scheduler granularity in seconds
# ----------------------------------------------------------------------------

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.SetProcessDPIAware()

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

SWP_NOSIZE     = 0x0001
SWP_NOZORDER   = 0x0004
SWP_NOACTIVATE = 0x0010


def process_name(hwnd):
    """Return the full path of the process that owns hwnd, or None."""
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wt.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
    finally:
        kernel32.CloseHandle(handle)
    return None


def is_visible_window(hwnd):
    """A window is interesting if it is visible and has non-zero size."""
    if not user32.IsWindowVisible(hwnd):
        return False
    r = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return False
    return (r.right - r.left) > 0 and (r.bottom - r.top) > 0


def find_game_window():
    """Return the hwnd of the largest visible ManorLords window, or None."""
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def callback(hwnd, lparam):
        if not is_visible_window(hwnd):
            return True
        name = process_name(hwnd)
        if name and GAME_PROCESS.lower() in name.lower():
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            area = (r.right - r.left) * (r.bottom - r.top)
            found.append((area, hwnd))
        return True

    user32.EnumWindows(callback, 0)
    if not found:
        return None
    found.sort(key=lambda item: item[0], reverse=True)
    return found[0][1]


def get_rect(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def set_position(hwnd, x, y):
    user32.SetWindowPos(hwnd, 0, x, y, 0, 0,
                        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)


def bounce_pos(x, y, dx, dy, base_rect):
    """Move (x,y) by (dx,dy), reflecting off the drift box so the window
    always lands somewhere new instead of clamping to an edge."""
    lo_x = base_rect.left - MAX_DRIFT_PX
    hi_x = base_rect.left + MAX_DRIFT_PX
    lo_y = base_rect.top - MAX_DRIFT_PX
    hi_y = base_rect.top + MAX_DRIFT_PX

    nx = x + dx
    ny = y + dy
    for _ in range(4):
        if nx < lo_x:
            nx = lo_x + (lo_x - nx)
        elif nx > hi_x:
            nx = hi_x - (nx - hi_x)
        else:
            break
    for _ in range(4):
        if ny < lo_y:
            ny = lo_y + (lo_y - ny)
        elif ny > hi_y:
            ny = hi_y - (ny - hi_y)
        else:
            break
    return nx, ny


def random_offset():
    """Return (direction_name, dx, dy) for this cycle's shift."""
    direction = random.choice(DIRECTIONS)
    d = random.randint(MIN_SHIFT_PX, MAX_SHIFT_PX)
    dx = dy = 0
    if "left" in direction:
        dx = -d
    elif "right" in direction:
        dx = d
    if "up" in direction:
        dy = -d
    elif "down" in direction:
        dy = d
    return direction, dx, dy


def wait_for_window():
    print("Waiting for the game window...")
    while True:
        hwnd = find_game_window()
        if hwnd:
            return hwnd
        time.sleep(1.0)


def main():
    hwnd = wait_for_window()
    print(f"Found game window (hwnd={hwnd}). "
          f"Wandering every {INTERVAL}s by {MIN_SHIFT_PX}-{MAX_SHIFT_PX}px. "
          f"Ctrl+C to stop.")

    base_rect = get_rect(hwnd)
    current = (base_rect.left, base_rect.top)
    next_shift = time.monotonic() + INTERVAL

    try:
        while True:
            time.sleep(POLL_INTERVAL)

            # The game closed or restarted -> re-acquire the window.
            if not user32.IsWindow(hwnd):
                print("Game window disappeared, waiting...")
                hwnd = wait_for_window()
                base_rect = get_rect(hwnd)
                current = (base_rect.left, base_rect.top)
                next_shift = time.monotonic() + INTERVAL
                print(f"Reacquired game window (hwnd={hwnd}).")
                continue

            if time.monotonic() < next_shift:
                continue

            # ---- move to a new dwell position ----
            while True:
                direction, dx, dy = random_offset()
                tx, ty = bounce_pos(current[0], current[1], dx, dy, base_rect)
                if (tx, ty) != current:
                    break
            print(f"[{time.strftime('%H:%M:%S')}] wandering {direction} "
                  f"by ({dx}, {dy}) px to ({tx}, {ty})")

            try:
                set_position(hwnd, tx, ty)
                dwell_until = time.monotonic() + INTERVAL
                while time.monotonic() < dwell_until:
                    time.sleep(POLL_INTERVAL)
                    if user32.IsWindow(hwnd):
                        # Re-apply in case the game fought the move.
                        cur = get_rect(hwnd)
                        if (cur.left, cur.top) != (tx, ty):
                            set_position(hwnd, tx, ty)
            except OSError:
                pass  # window vanished mid-shift; the loop re-acquires it

            current = (tx, ty)
            next_shift = time.monotonic() + INTERVAL

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        if hwnd and user32.IsWindow(hwnd):
            try:
                set_position(hwnd, base_rect.left, base_rect.top)
            except OSError:
                pass
            print("Restored window position.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)