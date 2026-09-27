#!/usr/bin/env python3
"""
einkpower - measure and compare power draw of the Modos e-paper display setup
on a Framework Laptop 13 (AMD 7040U, mainboard FRANMDCP05).

The e-paper panel is an opaque DP sink to the OS: there is no per-panel power
telemetry anywhere in sysfs. The only honest way to attribute watts to it is
differential measurement of the whole machine, plus physical A/B tests.
This tool produces the numbers for those tests.

Subcommands:
  doctor     show what this machine exposes (and what it does not)
  log        sample power/activity signals to a CSV
  report     summarise one or more CSV logs
  delta      difference between two logs
  experiment run a scripted state matrix (needs hyprctl; you drive the switches)

Everything is read-only from /sys and /proc. No root, no packages needed.
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import statistics
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(REPO, "results")


# ---------------------------------------------------------------- sysfs helpers

def _read(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def _read_int(path):
    v = _read(path)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def hwmon(name):
    """Return the hwmon dir whose `name` file matches, else None."""
    for nm in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        if _read(os.path.join(nm, "name")) == name:
            return nm
    return None


def power_supply(kind, wanted=("BAT",)):
    for ps in sorted(glob.glob("/sys/class/power_supply/*")):
        typ = _read(os.path.join(ps, "type"))
        name = os.path.basename(ps)
        if typ == kind and any(name.startswith(w) for w in wanted):
            return ps
    return None


def drm_connectors():
    out = []
    for c in sorted(glob.glob("/sys/class/drm/card*-*")):
        if os.path.basename(c).endswith("Writeback-1"):
            continue
        out.append(c)
    return out


# ------------------------------------------------------------------- collectors

class Sampler:
    """One instantaneous reading of everything we can see."""

    def __init__(self):
        self.bat = hwmon("BAT1")
        self.bat_psy = power_supply("Battery")
        self.gpu = hwmon("amdgpu")
        self.ac = power_supply("Mains", ("AC",))
        self.ucsi = {}
        for nm in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
            name = _read(os.path.join(nm, "name")) or ""
            if name.startswith("ucsi"):
                self.ucsi[name] = nm
        self.conns = drm_connectors()
        self._prev = {}
        self._prev_ts = None
        self._cpu_prev = None
        self._gfx_prev = {}
        self._prev_rapl = {}
        self._t0 = time.time()

    # -- power ------------------------------------------------------------
    def battery(self):
        """(watts, millivolts, milliamps) from the battery pack sensors."""
        if self.bat:
            v = _read_int(os.path.join(self.bat, "in0_input"))
            i = _read_int(os.path.join(self.bat, "curr1_input"))
            if v and i:
                return round(v * i / 1e6, 4), v, i
        if self.bat_psy:
            v = _read_int(os.path.join(self.bat_psy, "voltage_now"))
            i = _read_int(os.path.join(self.bat_psy, "current_now"))
            if v and i:
                return round(v * i / 1e6, 4), round(v / 1000), round(i / 1000)
        return None, None, None

    def battery_state(self):
        ps = self.bat_psy or self.bat
        if not ps:
            return {}
        out = {}
        for k in ("capacity", "status", "charge_now", "charge_full",
                  "charge_full_design", "cycle_count"):
            v = _read(os.path.join(ps, k))
            if v is not None:
                out[k] = v
        if self.ac:
            out["ac_online"] = _read(os.path.join(self.ac, "online"))
        return out

    def soc(self):
        """AMD SMU package (board) power in watts, when the kernel exposes it."""
        if not self.gpu:
            return None, None
        inst = _read_int(os.path.join(self.gpu, "power1_input"))
        avg = _read_int(os.path.join(self.gpu, "power1_average"))
        f = lambda x: round(x / 1e6, 4) if x is not None else None
        return f(inst), f(avg)

    def rapl(self, dt):
        """RAPL domain powers in watts, from energy_uj deltas.

        energy_uj is root-only on this machine, and AMD's RAPL MSRs are
        indicative rather than calibrated, so this is a second opinion on the
        CPU/package split, nothing more.
        """
        cur = {}
        for d in sorted(glob.glob("/sys/class/powercap/intel-rapl:*")):
            if not os.path.isdir(d):
                continue
            e = _read_int(os.path.join(d, "energy_uj"))
            if e is not None:
                cur[os.path.basename(d)] = e
        if not dt or dt <= 0 or not self._prev_rapl:
            self._prev_rapl = cur
            return {}
        out = {}
        for k, v in cur.items():
            old = self._prev_rapl.get(k)
            if old is None:
                continue
            d_uj = v - old
            if d_uj < 0:            # counter wrapped
                d_uj += _read_int(os.path.join(
                    "/sys/class/powercap", k, "max_energy_range_uj")) or 0
            out["rapl_" + k + "_w"] = round(d_uj / 1e6 / dt, 3)
        self._prev_rapl = cur
        return out

    def usb_c_ports(self):
        """Per USB-C port PD telemetry, watts, when negotiated."""
        out = {}
        for name, d in self.ucsi.items():
            v = _read_int(os.path.join(d, "in0_input"))
            i = _read_int(os.path.join(d, "curr1_input"))
            key = name.replace("ucsi_source_psy_", "")
            out[key + "_w"] = round(v * i / 1e6, 3) if (v and i) else 0.0
            out[key + "_v"] = round(v / 1000, 2) if v else 0.0
        return out

    # -- activity ---------------------------------------------------------
    def cpu_busy_pct(self, dt):
        if dt is None or dt <= 0:
            return None
        v = [int(x) for x in _read("/proc/stat").splitlines()[0].split()[1:]]
        total = sum(v)
        idle = v[3] + (v[4] if len(v) > 4 else 0)
        p = self._cpu_prev
        if p is None:
            return None
        dt_total, dt_idle = total - p[0], idle - p[1]
        if dt_total <= 0:
            return None
        return round(100.0 * (1 - dt_idle / dt_total), 1)

    def _remember_cpu(self):
        v = [int(x) for x in _read("/proc/stat").splitlines()[0].split()[1:]]
        self._cpu_prev = (sum(v), v[3] + (v[4] if len(v) > 4 else 0))

    def gfx_busy_pct(self, dt):
        """Sum of GPU engine busy time over all DRM clients, as % of wall time.

        Multiple fds can share one drm-client-id (same counters); count each
        (pid, client-id) pair once.
        """
        if dt is None or dt <= 0:
            return None
        cur = {}
        for fddir in glob.glob("/proc/[0-9]*/fdinfo/*"):
            try:
                with open(fddir) as fh:
                    txt = fh.read()
            except OSError:
                continue
            if "drm-engine-gfx" not in txt:
                continue
            pid = fddir.split("/")[2]
            cid = re.search(r"drm-client-id:\s*(\d+)", txt)
            eng = re.search(r"drm-engine-gfx:\s*(\d+) ns", txt)
            if not (cid and eng):
                continue
            cur[(pid, cid.group(1))] = int(eng.group(1))
        self._gfx_prev = getattr(self, "_gfx_prev", {})
        used = 0
        for k, val in cur.items():
            old = self._gfx_prev.get(k)
            if old is not None and val >= old:
                used += val - old
        self._gfx_prev = cur
        return round(100.0 * used / 1e9 / dt, 1)

    def net_kbps(self, dt):
        if dt is None or dt <= 0:
            return None
        cur = {}
        for line in _read("/proc/net/dev").splitlines()[2:]:
            name, rest = line.split(":", 1)
            if name.strip() == "lo":
                continue
            f = rest.split()
            cur[name.strip()] = int(f[0]) + int(f[8])
        p = self._prev.get("net", {})
        self._prev["net"] = cur
        if not p:
            return None
        d = sum(max(0, cur.get(k, 0) - v) for k, v in p.items())
        return round(d * 8 / dt / 1000, 1)

    def display(self):
        out = {}
        for c in self.conns:
            base = os.path.basename(c)
            if _read(os.path.join(c, "status")) != "connected":
                continue
            out[base + ".dpms"] = _read(os.path.join(c, "dpms"))
            out[base + ".enabled"] = _read(os.path.join(c, "enabled"))
        return out

    # -- the whole sample -------------------------------------------------
    def sample(self, ts=None):
        ts = ts if ts is not None else time.time()
        dt = None if self._prev_ts is None else ts - self._prev_ts
        w, mv, ma = self.battery()
        row = {
            "ts": round(ts, 3),
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts)),
            "monotonic": round(ts - self._t0, 1),
            "battery_w": w,
            "batt_mv": mv,
            "batt_ma": ma,
        }
        row.update(self.battery_state())
        soc_i, soc_a = self.soc()
        row["soc_w"] = soc_i
        row["soc_avg_w"] = soc_a
        row["cpu_busy_pct"] = self.cpu_busy_pct(dt)
        row["gfx_busy_pct"] = self.gfx_busy_pct(dt)
        row["net_kbps"] = self.net_kbps(dt)
        row.update(self.rapl(dt))
        row.update(self.usb_c_ports())
        row.update(self.display())
        self._remember_cpu()
        self._prev_ts = ts
        return row


def edid_name(path):
    """Descriptor text from the 4 18-byte EDID descriptors (0xFC serial, 0xFF name).

    The Modos board advertises 'Paper Monitor' in a *serial* descriptor, and
    hyprctl happily shows it as the model, so read both.
    """
    try:
        with open(path, "rb") as fh:
            b = fh.read()
    except OSError:
        return ""
    out = []
    for off in (54, 72, 90, 108):
        d = b[off:off + 18]
        if len(d) < 18:
            continue
        # the descriptor tag sits at byte 2; the Modos board puts it at byte 3
        for t in (2, 3):
            if d[t] in (0xFC, 0xFE, 0xFF):
                s = d[t + 1:18].replace(b"\x0a", b"").replace(b"\x00", b"")
                s = s.decode("ascii", "replace").strip()
                if s and all(32 <= ord(c) < 127 for c in s):
                    out.append(s)
                break
    return " / ".join(dict.fromkeys(out))


# -------------------------------------------------------------------- commands

def cmd_doctor(args):
    s = Sampler()
    print("MACHINE")
    for f in ("/sys/devices/virtual/dmi/id/product_name",
              "/sys/devices/virtual/dmi/id/board_name",
              "/sys/devices/virtual/dmi/id/product_version"):
        if _read(f):
            print(f"  {os.path.basename(f):16} {_read(f)}")
    print(f"  {'kernel':16} {os.uname().release}")
    print(f"  {'running_on_batt':16} {'no (on AC)' if _read(os.path.join(s.ac, 'online')) == '1' else 'yes'}")
    print(f"  {'hwmon BAT1':16} {s.bat or 'ABSENT'}")
    print(f"  {'hwmon amdgpu':16} {s.gpu or 'ABSENT'}")

    w, mv, ma = s.battery()
    print("\nPOWER SIGNALS AVAILABLE")
    print(f"  battery pack power      {w} W   ({mv} mV x {ma} mA)"
          f"  <- the only trustworthy system power figure on battery")
    si, sa = s.soc()
    print(f"  SoC package power       {si} W instantaneous / {sa} W averaged"
          "  <- amdgpu SMU, SoC only, excludes display")
    for k in sorted(s.usb_c_ports()):
        print(f"  {k:24} {s.usb_c_ports()[k]}")

    print("\nDISPLAY SIGNALS AVAILABLE")
    print(f"  /sys/class/backlight    {sorted(os.listdir('/sys/class/backlight')) or 'EMPTY -> no backlight, no frontlight'}")
    for c in s.conns:
        if _read(os.path.join(c, "status")) == "connected":
            print(f"  {os.path.basename(c):16} dpms={_read(os.path.join(c,'dpms')):4}"
                  f" enabled={_read(os.path.join(c,'enabled')):5}"
                  f" name={edid_name(os.path.join(c, 'edid'))!r}")
    print("  panel power / refresh telemetry: NOT EXPOSED (black box, see README)")

    print("\nVERDICT")
    print("  Per-panel watts cannot be read. Measure differentially: run the same")
    print("  workload with the panel in state A and state B, compare battery_w.")
    print("  Available packages that add nothing here: powertop (its display")
    print("  backlight estimate is meaningless when /sys/class/backlight is empty).")
    return 0


def _blocks(rows, block):
    """Split a state's rows into contiguous blocks of ~block seconds."""
    out, cur, t0 = [], [], None
    for r in rows:
        t = float(r["monotonic"])
        if t0 is None:
            t0 = t
        if t - t0 >= block:
            out.append(cur)
            cur, t0 = [], t
        cur.append(r)
    if cur:
        out.append(cur)
    return [b for b in out if len(b) >= max(3, block / 4)]


def cmd_paired(path, block=30.0):
    """Paired A/B analysis: compare state-by-state, round by round.

    Whole-machine drift (other load, wifi, thermals) moves the total by more
    than the display does, so sequential 'A for 5 min then B for 5 min' lies.
    Interleaving short blocks makes the drift common-mode.
    """
    rows = _rows(path)
    states = []
    for r in rows:
        if r.get("state") and r["state"] not in states:
            states.append(r["state"])
    if len(states) != 2:
        print(f"paired analysis needs exactly 2 states, found {states}")
        return 1
    a, b = states
    by = {s: [r for r in rows if r.get("state") == s] for s in states}
    ba = _blocks(by[a], block)
    bb = _blocks(by[b], block)
    n = min(len(ba), len(bb))
    print(f"paired: {n} rounds of ~{block:.0f}s   A={a}  B={b}")
    print(f"{'round':>5} {'A W':>7} {'B W':>7} {'B-A':>7}   {'A cpu':>5} {'B cpu':>5}")
    deltas = []
    for i in range(n):
        wa = statistics.fmean(float(x["battery_w"]) for x in ba[i])
        wb = statistics.fmean(float(x["battery_w"]) for x in bb[i])
        ca = statistics.fmean(float(x["cpu_busy_pct"] or 0) for x in ba[i])
        cb = statistics.fmean(float(x["cpu_busy_pct"] or 0) for x in bb[i])
        deltas.append(wb - wa)
        print(f"{i+1:5d} {wa:7.2f} {wb:7.2f} {wb-wa:+7.2f}   {ca:5.1f} {cb:5.1f}")
    if deltas:
        m = statistics.fmean(deltas)
        sd = statistics.pstdev(deltas)
        se = sd / len(deltas) ** 0.5 if len(deltas) > 1 else float("nan")
        print(f"\n  mean difference {m:+.2f} W   sd {sd:.2f}   stderr {se:.2f}   n={len(deltas)}")
        print(f"  95% CI ~ {m - 2 * se:+.2f} .. {m + 2 * se:+.2f} W")
        wh = _batt_wh()
        ma = statistics.fmean(statistics.fmean(float(x["battery_w"]) for x in b)
                              for b in ba[:n])
        mb = statistics.fmean(statistics.fmean(float(x["battery_w"]) for x in b)
                              for b in bb[:n])
        if wh and ma > 0 and mb > 0:
            print(f"  whole-system mean: A {ma:.2f} W -> B {mb:.2f} W")
            print(f"  runtime on a {wh:.0f} Wh pack: {wh/ma:.1f} h -> {wh/mb:.1f} h "
                  f"({(wh/mb)/(wh/ma)*100-100:+.0f}%)")
    return 0


def _bell(times=1):
    """Terminal bell. While the monitor is unplugged the panel holds its last
    frame, so the only way to signal 'window over, plug back in' is audible."""
    for _ in range(times):
        sys.stdout.write("\a")
    sys.stdout.flush()


def _wait(prompt):
    """input() that aborts cleanly when stdin is not a terminal."""
    try:
        return input(prompt)
    except EOFError:
        print("\nno terminal on stdin, aborting")
        raise SystemExit(1)


def cmd_ab_test(args):
    """Interleaved A/B. Two flavours: scripted (DPMS) or manual (unplug cable)."""
    os.makedirs(RESULTS, exist_ok=True)
    out = os.path.join(RESULTS,
                       time.strftime("%Y%m%d-%H%M%S") + f"_ab-{args.tag or 'test'}.csv")
    s = Sampler()
    n = int(args.block / args.interval)
    if n < 3:
        n = 3
    if args.rounds < 1:
        args.rounds = 1
    pairs = [i % 2 for i in range(args.rounds * 2)]
    manual = args.manual
    with open(out, "w", newline="") as fh:
        w = None
        for idx, state in enumerate(pairs):
            if manual:
                if state == 1:
                    print("\n=== round %d: UNPLUG the monitor's USB-C cable now ==="
                          % (idx // 2 + 1))
                    _wait("    press Enter when done (Ctrl-C to abort): ")
                elif idx == 0:
                    print("\n=== round 1: leave the monitor plugged in ===")
                else:
                    print("\n=== round %d: plug the monitor's USB-C cable back in ==="
                          % (idx // 2 + 1))
                    _wait("    press Enter when done (Ctrl-C to abort): ")
                # the panel holds its last frame while unplugged, so the prompt
                # above stays readable; discard the PD renegotiation transient
                time.sleep(max(args.settle, 5.0))
            else:
                cmd = args.cmd_on if state == 0 else args.cmd_off
                os.system(cmd)
                print(f"\n=== round {idx//2+1}, state {state}: {cmd!r} ===")
                time.sleep(args.settle)
            name = args.name_on if state == 0 else args.name_off
            for i in range(n):
                row = s.sample()
                row["state"] = name
                if w is None:
                    w = csv.DictWriter(fh, fieldnames=list(row),
                                       extrasaction="ignore")
                    w.writeheader()
                w.writerow(row)
                fh.flush()
                print(f"  {row['battery_w']:>7} W  cpu={row['cpu_busy_pct']}%  "
                      f"[{i+1}/{n}]", end="\r", flush=True)
                time.sleep(args.interval)
            _bell(2)
    if not manual and "hyprctl" in args.cmd_on:
        os.system("hyprctl keyword dpms on")
    print(f"\nwrote {out}")
    return cmd_paired(out, args.block)


def cmd_verify(args):
    """Do the current sensor and the coulomb counter agree?

    If they do, every wattage number from this tool can be integrated over time
    and trusted. If they do not, one of them is lying and nothing here means
    anything. Cheap insurance: 60 s.
    """
    s = Sampler()
    print(f"verifying for {args.seconds:.0f} s - do not change anything\n")
    q0, q1 = None, None
    t0 = t1 = None
    i_acc, t_last = 0.0, None
    w_acc = 0.0
    start = time.time()
    while time.time() - start < args.seconds:
        row = s.sample()
        q = row.get("charge_now")
        t = row["monotonic"]
        if q is None:
            print("charge_now unavailable, cannot verify")
            return 1
        q = int(q)
        if q0 is None:
            q0, t0 = q, t
        q1, t1 = q, t
        if t_last is not None:
            dt = t - t_last
            if row["batt_ma"] is not None:
                i_acc += row["batt_ma"] / 1000.0 * dt / 3600.0
            if row["battery_w"] is not None:
                w_acc += row["battery_w"] * dt / 3600.0
        t_last = t
        print(f"  {row['battery_w']:>7} W   charge_now={q/1e6:.4f} Ah   "
              f"[{t - t0:5.0f}/{args.seconds:.0f}s]", end="\r", flush=True)
        time.sleep(1)
    _bell()
    dur = (t1 - t0) / 3600.0
    dq = (q1 - q0) / 1e6
    coulomb_a = -dq / dur if dur else 0
    sensor_a = i_acc / dur if dur else 0
    ratio = (coulomb_a / sensor_a) if sensor_a else 0
    print(f"\n\n  window                     {dur*3600:.0f} s")
    print(f"  coulomb counter (charge_now) {-dq*1000:+.1f} mAh -> {coulomb_a:.0f} mA avg")
    print(f"  current sensor  integral    {i_acc*1000:+.1f} mAh -> {sensor_a:.0f} mA avg")
    print(f"  energy integrated           {w_acc:.3f} Wh  ({w_acc/(dur or 1):.2f} W avg)")
    print(f"  agreement                   {ratio*100:.1f}%  "
          f"({'OK, the wattage numbers are sound' if 0.8 < ratio < 1.25 else 'MISMATCH - do not trust these readings'})")
    if abs(-dq * 1000) < 20:
        print(f"\n  WARNING: only {abs(-dq*1000):.0f} mAh moved in this window and "
              f"charge_now has 1 mAh resolution,\n  so this is quantisation, not "
              f"disagreement. Use --seconds 300 or longer.")
    return 0


def cmd_log(args):
    s = Sampler()
    os.makedirs(RESULTS, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    label = args.label or "log"
    out = args.out or os.path.join(RESULTS, f"{stamp}_{label}.csv")
    fields = None
    n = int(args.duration / args.interval) if args.duration else 0
    state = getattr(args, "state", "x")
    print(f"logging {label}: {args.duration or 'until Ctrl-C'}s @ {args.interval}s -> {out}")
    with open(out, "w", newline="") as fh:
        w = None
        i = 0
        try:
            while True:
                row = s.sample()
                row["state"] = state
                if w is None:
                    fields = list(row)
                    w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
                    w.writeheader()
                w.writerow(row)
                i += 1
                print(f"  {row['battery_w']} W  soc={row['soc_w']} cpu={row['cpu_busy_pct']}%"
                      f" gfx={row['gfx_busy_pct']}%", end="\r", flush=True)
                if n and i >= n:
                    break
                time.sleep(args.interval)
        except KeyboardInterrupt:
            pass
    print(f"\nwrote {out}")
    return 0


def _rows(path):
    with open(path) as fh:
        return list(csv.DictReader(fh))


def _stats(path, key="battery_w", only_state=None, settle=0.0):
    r = _rows(path)
    if only_state:
        r = [x for x in r if x.get("state") == only_state]
    rows = [x for x in r if x.get(key) not in (None, "", "None")]
    if not rows:
        return None
    vals = [float(x[key]) for x in rows]
    dur = (float(r[-1]["monotonic"]) - float(r[0]["monotonic"])) or len(vals)
    t0 = float(r[0]["monotonic"])
    f = lambda k: statistics.fmean(
        [float(x[k]) for x in r if x.get(k) not in (None, "", "None")] or [0])
    # A phase does not reach its true level instantly. Right after a cable is
    # pulled the machine still holds the old power profile and relaxes into the
    # new one over tens of seconds, so a window mean blurs the two states
    # together. Keep everything after `settle` seconds for the honest number.
    kept = [x for x in rows if float(x["monotonic"]) - t0 >= settle]
    kvals = [float(x[key]) for x in kept] or vals
    kvals.sort()
    kn = len(kvals)
    vs = sorted(vals)
    n = len(vals)
    return {
        "file": os.path.basename(path),
        "state": r[0].get("state", ""),
        "n": n,
        "minutes": dur / 60.0,
        "mean": statistics.fmean(vals),
        "settled_mean": statistics.fmean(kvals),
        "settled_n": kn,
        "settled": bool(kept),
        "median": statistics.median(vals),
        "min": vs[0],
        "p05": vs[max(0, int(0.05 * n) - 1)],
        "p95": vs[min(n - 1, int(0.95 * n))],
        "max": vs[-1],
        "stdev": statistics.pstdev(vals),
        "settled_stdev": statistics.pstdev(kvals),
        "last": kvals[-1],
        "wh": statistics.fmean(kvals) * dur / 3600.0,
        "cpu": f("cpu_busy_pct"),
        "gfx": f("gfx_busy_pct"),
    }


def _batt_wh():
    """Nominal pack energy in Wh: design charge (Ah) x design voltage (V)."""
    s = Sampler()
    if not s.bat_psy:
        return None
    full = _read_int(os.path.join(s.bat_psy, "charge_full_design")) or \
        _read_int(os.path.join(s.bat_psy, "charge_full"))
    v = _read_int(os.path.join(s.bat_psy, "voltage_min_design"))
    if not full or not v:
        return None
    return full / 1e6 * v / 1e6


def cmd_report(args):
    files = args.files or sorted(glob.glob(os.path.join(RESULTS, "*.csv")))
    settle = getattr(args, "settle", 0.0)
    # A CSV holding several phases (one file, several states) is the
    # interesting case, so break every such file up into one row per phase and
    # keep single-state logs as they are.
    stats = []
    for path in files:
        states = []
        for x in _rows(path):
            if x.get("state") and x["state"] not in states:
                states.append(x["state"])
        if len(states) > 1:
            stats += [s for s in (_stats(path, only_state=st, settle=settle)
                                  for st in states) if s]
        else:
            s = _stats(path, settle=settle)
            if s:
                stats.append(s)
    if not stats:
        print("no logs", file=sys.stderr)
        return 1
    wh = _batt_wh()
    hdr = (f"{'run':30} {'state':22} {'mins':>5} {'mean W':>7} {'med':>6} {'p05':>6} "
           f"{'p95':>6} {'max':>6} {'sd':>5} {'cpu%':>5} {'gfx%':>5}")
    print(hdr)
    print("-" * len(hdr))
    for st in stats:
        name = os.path.basename(st["file"]).replace(".csv", "")[:30]
        print(f"{name:30} {st['state'][:22]:22} {st['minutes']:5.1f} {st['mean']:7.2f} "
              f"{st['median']:6.2f} {st['p05']:6.2f} {st['p95']:6.2f} {st['max']:6.2f} "
              f"{st['stdev']:5.2f} {st['cpu']:5.1f} {st['gfx']:5.1f}")
    if len(stats) > 1:
        # Deltas only mean something between phases of the *same* run, and only
        # for phases that actually lasted long enough to settle.
        bkey = "settled_mean" if settle else "mean"
        runs = {}
        for st in stats:
            runs.setdefault(st["file"], []).append(st)
        for fname, group in runs.items():
            if len(group) < 2:
                continue
            base = group[0]
            lines = []
            for st in group[1:]:
                if st["settled_n"] < max(5, 0.2 * st["n"]):
                    lines.append(f"  {st['state'][:34]:34} skipped "
                                 f"(phase too short to settle: {st['settled_n']}/{st['n']} samples)")
                    continue
                d = st[bkey] - base[bkey]
                extra = ""
                if wh:
                    h_old = wh / base[bkey] if base[bkey] else 0
                    h_new = wh / st[bkey] if st[bkey] else 0
                    extra = f"  runtime {h_old:.1f}h -> {h_new:.1f}h ({(h_new/h_old-1)*100:+.0f}%)"
                lines.append(f"  {st['state'][:34]:34} {d:+6.2f} W{extra}")
            if not lines:
                continue
            print(f"\ndeltas in {fname[:38]}, vs {base['state']}"
                  f"{f', after {settle:.0f}s settle' if settle else ''}:")
            print("\n".join(lines))
        print("\n  note: only trust a delta that is larger than the run-to-run noise")
        print("  (compare stdev and p05..p95 across repeats of the same state)")
    if settle:
        print(f"\n  settled columns: mean over samples at least {settle:.0f}s into each phase")
    if wh:
        print(f"\nbattery design capacity ~{wh:.1f} Wh")
    return 0


def cmd_delta(args):
    a, b = _stats(args.a), _stats(args.b)
    if not (a and b):
        print("need two readable logs", file=sys.stderr)
        return 1
    print(f"{os.path.basename(args.a):38} {a['mean']:7.2f} W mean over {a['minutes']:.1f} min "
          f"(cpu {a['cpu']:.1f}% gfx {a['gfx']:.1f}%)")
    print(f"{os.path.basename(args.b):38} {b['mean']:7.2f} W mean over {b['minutes']:.1f} min "
          f"(cpu {b['cpu']:.1f}% gfx {b['gfx']:.1f}%)")
    print(f"{'difference':38} {b['mean']-a['mean']:+7.2f} W")
    d = b["mean"] - a["mean"]
    wh = _batt_wh()
    if wh and d:
        print(f"  as battery life: {(wh/a['mean'])*60:.0f} min -> {(wh/b['mean'])*60:.0f} min")
    return 0


PHASES = [
    ("monitor-connected", 60,
     "monitor connected, screen on. do not touch anything."),
    ("monitor-unplugged", 90,
     "UNPLUG THE MONITOR'S USB-C CABLE NOW (video and power share it).\n"
     "    the panel will keep its last image; the laptop has no display at all."),
    ("monitor-replugged", 60,
     "plug the USB-C cable back in. wait for the image to come back."),
]


def cmd_unplug_test(args):
    """The decisive test: the kit gets video AND power from one USB-C cable,
    so removing that cable removes the whole e-paper subsystem from the
    machine. Whatever the battery draw drops by is the panel's real cost."""
    os.makedirs(RESULTS, exist_ok=True)
    out = os.path.join(RESULTS, time.strftime("%Y%m%d-%H%M%S") + "_unplug-test.csv")
    s = Sampler()
    w = None
    fh = open(out, "w", newline="")
    try:
        for phase, secs, how in PHASES:
            if args.seconds:
                secs = args.seconds
            print(f"\n=== {phase} ({secs}s) ===\n    {how}")
            if phase != PHASES[0][0]:
                _wait("    press Enter once you have done it: ")
                time.sleep(2)
            n = int(secs / args.interval)
            for i in range(n):
                row = s.sample()
                row["state"] = phase
                if w is None:
                    w = csv.DictWriter(fh, fieldnames=list(row), extrasaction="ignore")
                    w.writeheader()
                w.writerow(row)
                fh.flush()
                print(f"  {row['battery_w']:>7} W  soc={row['soc_w']}  "
                      f"cpu={row['cpu_busy_pct']}%  [{i+1}/{n}]", end="\r", flush=True)
                time.sleep(args.interval)
            _bell(2)
    finally:
        fh.close()

    print(f"\nwrote {out}")
    settle = min(45.0, 0.45 * secs)
    return cmd_report(argparse.Namespace(files=[out], settle=settle))


STATES = [
    ("idle", "do nothing at all for the whole window, do not type", None),
    ("typing", "type slowly in a terminal / write text", None),
    ("scroll", "scroll continuously (a long page, or `yes` in a fullscreen pager)", None),
    ("dpms-off", "run: hyprctl keyword dpms off   (we restore it after)", "off"),
    ("dpms-off-idle", "dpms off AND no input, the panel in its deepest sleep", None),
]


def cmd_experiment(args):
    if not shutil_which("hyprctl"):
        print("hyprctl not found; drive the states manually", file=sys.stderr)
        return 1
    for name, how, dpms in STATES:
        if args.only and name not in args.only:
            continue
        print(f"\n=== {name} ({args.seconds}s) ===\n    {how}")
        os.system("hyprctl keyword dpms " + (dpms or "on"))
        time.sleep(3)
        if input("    press Enter when you are ready, 's' to skip: ").strip().lower() == "s":
            continue
        cmd_log(argparse.Namespace(interval=1.0, duration=args.seconds, label=name,
                                   state=name, out=None))
    os.system("hyprctl keyword dpms on")
    print("\ndone. compare with:  bin/einkpower report")
    return 0


def shutil_which(x):
    for d in os.environ.get("PATH", "").split(":"):
        p = os.path.join(d, x)
        if os.access(p, os.X_OK):
            return p
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="what this machine exposes")
    d.set_defaults(fn=cmd_doctor)

    l = sub.add_parser("log", help="sample to CSV")
    l.add_argument("--interval", type=float, default=1.0)
    l.add_argument("--duration", type=float, default=60.0, help="0 = until Ctrl-C")
    l.add_argument("--label", default=None)
    l.add_argument("--state", default="x", help="phase label, used by report")
    l.add_argument("--out", default=None)
    l.set_defaults(fn=cmd_log)

    v = sub.add_parser("verify", help="check the current sensor against the coulomb counter")
    v.add_argument("--seconds", type=float, default=300.0)
    v.set_defaults(fn=cmd_verify)

    u = sub.add_parser("unplug-test",
                       help="THE decisive test: unplug the monitor's USB-C cable")
    u.add_argument("--interval", type=float, default=1.0)
    u.add_argument("--seconds", type=float, default=0.0,
                   help="override each phase length (0 = built-in 60/90/60)")
    u.set_defaults(fn=cmd_unplug_test)

    r = sub.add_parser("report", help="summarise logs")
    r.add_argument("files", nargs="*")
    r.add_argument("--settle", type=float, default=0.0,
                   help="ignore this many seconds at the start of every phase")
    r.set_defaults(fn=cmd_report)

    dd = sub.add_parser("delta", help="difference between two logs")
    dd.add_argument("a")
    dd.add_argument("b")
    dd.set_defaults(fn=cmd_delta)

    e = sub.add_parser("experiment", help="scripted state matrix")
    e.add_argument("--seconds", type=float, default=180.0)
    e.add_argument("--only", nargs="*", default=None)
    e.set_defaults(fn=cmd_experiment)

    ab = sub.add_parser("ab-test", help="interleaved paired A/B (the valid design)")
    ab.add_argument("--block", type=float, default=30.0, help="seconds per block")
    ab.add_argument("--rounds", type=int, default=4, help="A/B pairs to run")
    ab.add_argument("--interval", type=float, default=1.0)
    ab.add_argument("--manual", action="store_true",
                    help="prompt me to unplug/replug the monitor's USB-C cable")
    ab.add_argument("--tag", default=None)
    ab.add_argument("--cmd-on", default="hyprctl keyword dpms on",
                    help="shell command that puts the machine in state A")
    ab.add_argument("--cmd-off", default="hyprctl keyword dpms off",
                    help="shell command that puts the machine in state B")
    ab.add_argument("--settle", type=float, default=3.0,
                    help="seconds to wait after switching, before sampling")
    ab.add_argument("--name-on", default="A-display-on")
    ab.add_argument("--name-off", default="B-display-off")
    ab.set_defaults(fn=cmd_ab_test)

    pr = sub.add_parser("paired", help="paired analysis of an existing A/B log")
    pr.add_argument("file")
    pr.add_argument("--block", type=float, default=30.0)
    pr.set_defaults(fn=lambda a: cmd_paired(a.file, a.block))

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
