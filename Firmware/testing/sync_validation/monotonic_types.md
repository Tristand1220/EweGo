# `CLOCK_MONOTONIC` vs `CLOCK_MONOTONIC_RAW` on a Raspberry Pi (CM4 / BCM2711)

| | `CLOCK_MONOTONIC` | `CLOCK_MONOTONIC_RAW` |
|---|---|---|
| Goes backward? | Never | Never |
| Affected by setting the wall clock (NTP step, `settimeofday`, DST)? | No | No |
| Affected by NTP/`adjtime` **rate** correction (slewing)? | **Yes** | **No** |
| Counts time while system is suspended? | No | No |
| What Python's `time.monotonic()` uses on Linux | **This one** | No |
| Best for accurate elapsed-time intervals | **Yes** | No (tracks bare crystal) |
| Best for measuring oscillator drift itself | No | **Yes** |

**On a CM4, both clocks read the same underlying 54 MHz ARM architected timer (`arch_sys_counter`, ~18 ns tick). The only difference is whether the kernel's NTP/`adjtime` frequency correction is folded in.**

---

## The core difference

Both clocks are *monotonic*: neither can jump backward, and neither is affected by someone setting the wall clock. The difference is entirely in **whether the kernel's clock-discipline layer is allowed to adjust the rate**.

- **`CLOCK_MONOTONIC`** is not affected by discontinuous jumps in system time, *but is affected by the incremental adjustments performed by `adjtime(3)` and NTP*. When chrony/ntpd/`adjtimex` decides the oscillator runs fast or slow, it **slews** this clock — speeding it up or slowing it down slightly to stay consistent with disciplined time. It never goes backward, but a "second" of `CLOCK_MONOTONIC` is a second of *corrected* time, not necessarily of physical time.

- **`CLOCK_MONOTONIC_RAW`** (Linux 2.6.28+, Linux-specific) provides access to a raw hardware-based time that is **not** subject to NTP adjustments or `adjtime(3)`. It reflects the raw hardware counter rate, with no frequency correction applied.

### Why `_RAW` exists

It was introduced by John Stultz specifically so clock-synchronization code wouldn't be *"painting a road using the lines we're painting as the guide"* — if you're measuring oscillator drift in order to correct it, you can't measure against a clock that's already being corrected. `_RAW` was exposed by the kernel mainly for synchronization protocols (like NTP) to measure quartz crystal oscillator drift, with little resemblance to the actual passing of time.

### Practical punchline (counterintuitive)

For measuring **real elapsed wall-time intervals**, `CLOCK_MONOTONIC` is usually the **better** choice, because its job is to track corrected (accurate) time. `_RAW` tracks the bare crystal, which on a cheap board can drift by tens of ppm. The intuition "RAW must be more accurate because it's untouched" is backwards for interval timing.

Kernel-list illustration: if NTP is slewing `CLOCK_MONOTONIC` to run faster, a profiled program *appears to use fewer instructions per second but run longer* than it would under `CLOCK_MONOTONIC_RAW`. So `_RAW` is right when you want to attribute work to *uncorrected* hardware ticks; wrong when you want accurate seconds.

### Suspend gotcha

**Neither** clock counts time spent suspended. If you need a monotonic clock that *includes* suspend time, use **`CLOCK_BOOTTIME`** (identical to `CLOCK_MONOTONIC` except it also includes suspend).

---

## What Python's `time.monotonic()` gets

On Linux (including any Raspberry Pi), CPython's `time.monotonic()` calls `clock_gettime(CLOCK_MONOTONIC)` — **not** `_RAW`. This is specified in PEP 418 and implemented in `Modules/timemodule.c`. As of **Python 3.13**, `time.perf_counter()` uses the same clock as `time.monotonic()`, so on Linux both land on `CLOCK_MONOTONIC`.

Verify on the actual board:

```python
import time
print(time.get_clock_info('monotonic'))
# -> namespace(implementation='clock_gettime(CLOCK_MONOTONIC)',
#              monotonic=True, adjustable=False, resolution=1e-09)
```

Get the raw clock explicitly (Python 3.3+):

```python
import time
raw    = time.clock_gettime(time.CLOCK_MONOTONIC_RAW)      # seconds, float
raw_ns = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)   # integer ns, no float loss
```

So: `time.monotonic()` → corrected (NTP-disciplined) monotonic time; `time.clock_gettime(time.CLOCK_MONOTONIC_RAW)` → uncorrected hardware time.

---

## On a CM4 specifically (BCM2711)

The CM4 uses the BCM2711, the same SoC as the Pi 4. On a 64-bit kernel the active clocksource is the ARM architected generic timer, which **both** clocks read from. The boot log shows it directly:

```
arch_timer: cp15 timer(s) running at 54.00MHz (phys).
clocksource: arch_sys_counter: mask: 0xffffffffffffff max_cycles: 0xc743ce346,
             max_idle_ns: 440795203123 ns
sched_clock: 56 bits at 54MHz, resolution 18ns
```

Confirm the active source on your board:

```bash
cat /sys/devices/system/clocksource/clocksource0/current_clocksource
# -> arch_sys_counter   (on a 64-bit CM4 kernel)
dmesg | grep -i 'arch_timer\|clocksource'
```

What this means on a CM4:

- The underlying counter is **CNTPCT** at **54 MHz** (configurable, but 54 MHz by default on BCM2711), giving a hardware tick of ~18.5 ns (the "resolution 18ns" above). This is the raw substrate for *both* clocks.
- `CLOCK_MONOTONIC_RAW` = that 54 MHz counter scaled to nanoseconds with a fixed mult/shift, no correction.
- `CLOCK_MONOTONIC` = the same counter with the kernel's NTP/`adjtime` frequency correction folded in. With chrony running, the two **diverge slowly over time** (typically by the board's crystal error, often single-to-tens of ppm). With no time daemon and no `adjtimex` calls, the correction factor is unity and the two read essentially identically.
- **Older/32-bit Pis** often defaulted to the 1 MHz BCM2835 system timer (`stc`) instead of the architected timer — relevant when comparing a CM4 against an older Pi.

**Performance:** On the 64-bit Pi OS kernel (5.x/6.x), both clocks are served through the vDSO, so neither needs a real syscall and both are fast. On older/32-bit ARM kernels, `CLOCK_MONOTONIC_RAW` historically fell back to a real syscall (slower than `CLOCK_MONOTONIC`) — benchmark before assuming equal cost on a 32-bit build.

---

## Practical recommendation

- **Timeouts, scheduling, measuring elapsed real time →** `time.monotonic()` (`CLOCK_MONOTONIC`). It's what you almost always want, and on the CM4 it's both accurate and vDSO-fast.
- **Excluding NTP/`adjtime` corrections →** `CLOCK_MONOTONIC_RAW`, only when you explicitly need it: characterizing the CM4's own oscillator drift, or correlating against an external hardware clock where you don't want the kernel warping your timebase underneath you.

---

## References

- `clock_gettime(2)` — Linux man page (clock semantics): <https://linux.die.net/man/2/clock_gettime>
- `clock_gettime(3)` — Linux man page (man7.org): <https://www.man7.org/linux/man-pages/man3/clock_gettime.3.html>
- John Stultz, original `CLOCK_MONOTONIC_RAW` RFC patch (rationale): <https://lkml.iu.edu/hypermail/linux/kernel/0802.1/4389.html>
- LKML thread — `_RAW` advances more constantly than `CLOCK_MONOTONIC` under NTP: <https://lkml.iu.edu/hypermail/linux/kernel/1505.1/00394.html>
- TigerBeetle, "Three Clocks are Better than One" (suspend behavior, `_RAW` purpose): <https://tigerbeetle.com/blog/2021-08-30-three-clocks-are-better-than-one/>
- Python `time` module docs (`monotonic`, `perf_counter`, `get_clock_info`): <https://docs.python.org/3/library/time.html>
- PEP 418 — Add monotonic time, performance counter, and process time functions: <https://peps.python.org/pep-0418/>
- Raspberry Pi forum — CNTFRQ_EL0 / BCM2711 timer is 54 MHz by default: <https://forums.raspberrypi.com/viewtopic.php?t=309297>
- Raspberry Pi boot log showing `arch_timer ... 54.00MHz` / `arch_sys_counter` / `resolution 18ns`: <https://www.raspberrypi.org/forums/viewtopic.php?t=296423>
- Raspberry Pi forum — clocksource differences (`stc` vs `arch_sys_counter`) on older Pis: <https://forums.raspberrypi.com/viewtopic.php?t=133981>
