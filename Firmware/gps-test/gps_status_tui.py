#!/usr/bin/env python3
"""
Live GNSS status TUI for antenna placement and RF debugging.

Renders the module's unsolicited stream (10 Hz NAV-PVT / NAV-SAT, 1 Hz
MON-RF per the SheepRTK config), falling back to polling any message
type that goes quiet:
  - fix state, sat count, position, accuracy
  - per-RF-block antenna status, jamming indicator, noise, AGC
  - per-satellite CN0 bar chart (the main thing to watch while
    adjusting the antenna: healthy outdoor CN0 is 35-50 dBHz,
    ephemeris decode needs roughly 25+)

Run on the Pi:
  uv run --with pyserial --with pyubx2 python gps_status_tui.py
Quit with q or Ctrl+C.
"""

import argparse
import curses
import threading
import time

import serial
from pyubx2 import UBXMessage, UBXReader

FIX_TYPES = {
    0: ('NO FIX', 'bad'),
    1: ('DEAD RECKONING', 'warn'),
    2: ('2D FIX', 'warn'),
    3: ('3D FIX', 'good'),
    4: ('GNSS+DR', 'good'),
    5: ('TIME ONLY', 'warn'),
}
CARR_SOLN = {1: ' + RTK FLOAT', 2: ' + RTK FIXED'}
ANT_STATUS = {0: 'INIT', 1: 'UNKNOWN', 2: 'OK', 3: 'SHORT!', 4: 'OPEN!'}
ANT_POWER = {0: 'OFF', 1: 'ON', 2: 'UNKNOWN'}
GNSS_NAMES = {0: 'GPS', 1: 'SBAS', 2: 'GAL', 3: 'BDS', 5: 'QZSS', 6: 'GLO', 7: 'NAVIC'}

CN0_BAR_MAX = 55  # dBHz at full bar width
BLOCKS = ' ▏▎▍▌▋▊▉█'


class GnssState:
    def __init__(self):
        self.lock = threading.Lock()
        self.updated = threading.Event()
        self.pvt = None          # last NAV-PVT
        self.sats = []           # [(cno, gnss_name, svid, used)] from NAV-SAT
        self.rf_blocks = []      # [dict] from MON-RF
        self.msg_count = 0
        self.msg_rate = 0.0
        self.parse_errors = 0
        self.last_pvt_time = 0.0
        self.last_sat_time = 0.0
        self.last_rf_time = 0.0
        self.serial_error = None


def reader_thread(ser, state, stop):
    ubr = UBXReader(ser)
    rate_window_start = time.monotonic()
    rate_window_count = 0
    while not stop.is_set():
        try:
            raw, msg = ubr.read()
        except serial.SerialException as e:
            with state.lock:
                state.serial_error = str(e)
            return
        except Exception:
            with state.lock:
                state.parse_errors += 1
            continue
        if msg is None:
            continue
        now = time.monotonic()
        rate_window_count += 1
        if now - rate_window_start >= 1.0:
            with state.lock:
                state.msg_rate = rate_window_count / (now - rate_window_start)
            rate_window_start = now
            rate_window_count = 0
        with state.lock:
            state.msg_count += 1
            if msg.identity == 'NAV-PVT':
                state.pvt = msg
                state.last_pvt_time = now
            elif msg.identity == 'NAV-SAT':
                sats = []
                for i in range(1, msg.numSvs + 1):
                    sfx = f'_{i:02d}'
                    sats.append((
                        getattr(msg, 'cno' + sfx),
                        GNSS_NAMES.get(getattr(msg, 'gnssId' + sfx), '?'),
                        getattr(msg, 'svId' + sfx),
                        getattr(msg, 'svUsed' + sfx),
                    ))
                sats.sort(reverse=True)
                state.sats = sats
                state.last_sat_time = now
            elif msg.identity == 'MON-RF':
                blocks = []
                for i in range(1, msg.nBlocks + 1):
                    sfx = f'_{i:02d}'
                    blocks.append({
                        'id': getattr(msg, 'blockId' + sfx),
                        'antStatus': getattr(msg, 'antStatus' + sfx),
                        'antPower': getattr(msg, 'antPower' + sfx),
                        'jamInd': getattr(msg, 'jamInd' + sfx),
                        'noisePerMS': getattr(msg, 'noisePerMS' + sfx),
                        'agcCnt': getattr(msg, 'agcCnt' + sfx),
                    })
                state.rf_blocks = blocks
                state.last_rf_time = now
            else:
                continue
        state.updated.set()


def poller_thread(ser, state, stop):
    # Fallback only: the module normally streams these unsolicited
    # (10 Hz NAV-PVT/NAV-SAT, 1 Hz MON-RF per SheepRTK config). Poll just
    # the ones that have gone quiet, e.g. after a factory reset.
    polls = [
        ('last_pvt_time', 1.0, UBXMessage('NAV', 'NAV-PVT', 0).serialize()),
        ('last_sat_time', 1.0, UBXMessage('NAV', 'NAV-SAT', 0).serialize()),
        ('last_rf_time', 2.5, UBXMessage('MON', 'MON-RF', 0).serialize()),
    ]
    while not stop.is_set():
        now = time.monotonic()
        with state.lock:
            stale = b''.join(
                msg for attr, max_age, msg in polls
                if now - getattr(state, attr) > max_age
            )
        if stale:
            try:
                ser.write(stale)
            except serial.SerialException:
                return
        stop.wait(1.0)


def meter(value, vmax, width):
    frac = max(0.0, min(value, vmax)) / vmax * width
    full = int(frac)
    bar = '█' * full
    if full < width:
        bar += BLOCKS[min(round((frac - full) * 8), 8)]
    return bar.ljust(width, '·')[:width]


def cn0_color(cno, colors):
    if cno >= 35:
        return colors['good']
    if cno >= 25:
        return colors['warn']
    return colors['bad']


def draw(scr, state, colors, t0):
    scr.erase()
    h, w = scr.getmaxyx()
    now = time.monotonic()

    def put(y, x, text, attr=0):
        if 0 <= y < h and x < w - 1:
            scr.addnstr(y, x, text, max(0, w - x - 1), attr)

    def section(y, title, extra=''):
        put(y, 0, '─' * (w - 1), colors['title'] | curses.A_DIM)
        put(y, 2, f' {title} ', colors['title'] | curses.A_BOLD)
        if extra:
            put(y, 4 + len(title), f' {extra} ', colors['title'])

    with state.lock:
        pvt = state.pvt
        sats = list(state.sats)
        rf = list(state.rf_blocks)
        msg_rate = state.msg_rate
        parse_errors = state.parse_errors
        pvt_age = now - state.last_pvt_time if state.last_pvt_time else None
        sat_age = now - state.last_sat_time if state.last_sat_time else None
        rf_age = now - state.last_rf_time if state.last_rf_time else None
        serial_error = state.serial_error

    # --- Header bar ---
    age_str = f'{pvt_age:4.2f}s' if pvt_age is not None else '  ---'
    left = '  GNSS LIVE STATUS'
    right = (f'elapsed {now - t0:5.0f}s │ {msg_rate:4.1f} msg/s │ '
             f'data age {age_str} │ errs {parse_errors} │ [q] quit  ')
    pad = max(1, w - 1 - len(left) - len(right))
    put(0, 0, (left + ' ' * pad + right)[:w - 1], curses.A_REVERSE | curses.A_BOLD)

    if serial_error:
        put(2, 2, f'SERIAL ERROR: {serial_error}', colors['bad'] | curses.A_BOLD)
        scr.refresh()
        return

    # --- Fix / position ---
    y = 2
    section(y, 'FIX')
    y += 1
    if pvt is None or pvt_age is None or pvt_age > 5:
        stale = f' (last seen {pvt_age:.0f}s ago)' if pvt_age else ''
        put(y, 2, f'NAV-PVT: no data{stale}', colors['bad'] | curses.A_BOLD)
        y += 2
    else:
        name, sev = FIX_TYPES.get(pvt.fixType, (f'fixType={pvt.fixType}', 'warn'))
        name += CARR_SOLN.get(getattr(pvt, 'carrSoln', 0), '')
        put(y, 2, '●', colors[sev] | curses.A_BOLD)
        put(y, 4, f'{name:<22}', colors[sev] | curses.A_BOLD)
        put(y, 28, f'sats used {pvt.numSV:2d}')
        if pvt.fixType >= 2:
            put(y, 44, f'hAcc {pvt.hAcc / 1000:8.3f} m')
        y += 1
        put(y, 2, f'lat {pvt.lat:12.7f}    lon {pvt.lon:12.7f}    '
                  f'alt {pvt.hMSL / 1000:8.2f} m')
        y += 1
        valid = pvt.validDate and pvt.validTime
        put(y, 2, f'GPS time {pvt.year}-{pvt.month:02d}-{pvt.day:02d} '
                  f'{pvt.hour:02d}:{pvt.min:02d}:{pvt.second:02d}')
        put(y, 33, '✓ valid' if valid else '⚠ NOT VALID (default epoch)',
            colors['good'] if valid else colors['warn'])
        y += 2

    # --- RF blocks ---
    extra = f'stale ({rf_age:.0f}s)' if rf_age is not None and rf_age > 5 else ''
    section(y, 'RF / ANTENNA', extra)
    y += 1
    if not rf:
        put(y, 2, 'no data yet', colors['warn'])
        y += 1
    jam_w = 12
    for b in rf:
        ant = ANT_STATUS.get(b['antStatus'], '?')
        antc = {'OK': 'good', 'SHORT!': 'bad', 'OPEN!': 'bad'}.get(ant, 'warn')
        jam = b['jamInd']
        jamc = 'good' if jam < 50 else ('warn' if jam < 150 else 'bad')
        put(y, 2, f"block {b['id']}")
        put(y, 11, 'ant', curses.A_DIM)
        put(y, 15, f'{ant:<8}', colors[antc] | curses.A_BOLD)
        put(y, 24, 'pwr', curses.A_DIM)
        put(y, 28, f"{ANT_POWER.get(b['antPower'], '?'):<8}")
        put(y, 37, 'jam', curses.A_DIM)
        put(y, 41, meter(jam, 255, jam_w), colors[jamc])
        put(y, 42 + jam_w, f'{jam:3d}', colors[jamc] | curses.A_BOLD)
        put(y, 47 + jam_w, f"noise {b['noisePerMS']:3d}", curses.A_DIM)
        put(y, 58 + jam_w, f"agc {b['agcCnt']}", curses.A_DIM)
        y += 1
    put(y, 2, 'jam = CW interference at the AGC: 0 quiet → 255 saturated'
              ' (green <50 · yellow <150 · red ≥150)', curses.A_DIM)
    y += 1
    put(y, 2, 'high jam with few/weak sats usually means an unpowered or'
              ' disconnected antenna', curses.A_DIM)
    y += 2

    # --- Satellites ---
    n_decent = sum(1 for s in sats if s[0] >= 25)
    n_good = sum(1 for s in sats if s[0] >= 35)
    extra = f'tracked {len(sats)} │ ≥25 dBHz: {n_decent} │ ≥35 dBHz: {n_good}'
    if sat_age is not None and sat_age > 5:
        extra += f' │ stale ({sat_age:.0f}s)'
    section(y, 'SATELLITES', extra)
    y += 1

    bar_x = 18
    bar_w = max(10, w - bar_x - 2)
    ruler = [' '] * bar_w
    for v in (25, 35, 45):
        pos = min(bar_w - 1, round(v / CN0_BAR_MAX * (bar_w - 1)))
        for i, c in enumerate(str(v)):
            if pos + i < bar_w:
                ruler[pos + i] = c
    put(y, 2, 'sig   sv  dBHz', curses.A_DIM)
    put(y, bar_x, ''.join(ruler), curses.A_DIM)
    y += 1

    if not sats:
        put(y, 2, '(none tracked — check antenna / sky view)', colors['bad'])
        y += 1
    max_rows = max(0, h - y - 2)
    for cno, gname, svid, used in sats[:max_rows]:
        mark = '●' if used else ' '
        put(y, 2, f'{gname:>4} {svid:4d}  {cno:3d} {mark}')
        put(y, bar_x, meter(cno, CN0_BAR_MAX, bar_w), cn0_color(cno, colors))
        y += 1
    if len(sats) > max_rows:
        put(y, 2, f'… {len(sats) - max_rows} more', curses.A_DIM)

    put(h - 1, 0, '  ● = used in fix    CN0: <25 too weak for fix · 25–34 marginal'
                  ' · 35+ healthy', curses.A_DIM)
    scr.refresh()


def main(scr, ser):
    curses.curs_set(0)
    curses.use_default_colors()
    curses.start_color()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_RED, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    colors = {
        'good': curses.color_pair(1),
        'warn': curses.color_pair(2),
        'bad': curses.color_pair(3),
        'title': curses.color_pair(4),
    }
    scr.nodelay(True)

    state = GnssState()
    stop = threading.Event()
    threads = [
        threading.Thread(target=reader_thread, args=(ser, state, stop), daemon=True),
        threading.Thread(target=poller_thread, args=(ser, state, stop), daemon=True),
    ]
    for t in threads:
        t.start()

    t0 = time.monotonic()
    try:
        while True:
            if scr.getch() in (ord('q'), ord('Q')):
                break
            draw(scr, state, colors, t0)
            # redraw as soon as new data lands (module streams at 10 Hz);
            # cap at ~10 fps and force a repaint every 0.5 s regardless
            state.updated.wait(0.5)
            state.updated.clear()
            time.sleep(0.1)
    finally:
        stop.set()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Live GNSS status TUI')
    parser.add_argument('--port', default='/dev/ttyAMA4')
    parser.add_argument('--baud', type=int, default=460800)
    args = parser.parse_args()

    ser = serial.Serial(args.port, args.baud, timeout=1)
    try:
        curses.wrapper(main, ser)
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
