"""Read-only SmartSEM API check for pyFIB. MOVES NOTHING, SETS NOTHING (Get calls only).

Run from the pyFIB folder (the one containing smartfib_executor\\):

    python test_scripts\\api_check.py          # pyFIB's normal connection
    python test_scripts\\api_check.py --late   # forcing late-bound COM (ignores any cached wrapper)

It prints: other python.exe processes, the pywin32 version, the win32com gen_py cache
(wrappers another program such as serialFIB may have generated), whether pyFIB's API
object is late-bound (CDispatch) or early-bound (a gen_py class), and the REAL error for a
few reads that pyFIB normally swallows.
"""
import os
import subprocess
import sys
import traceback

LATE = "--late" in sys.argv
print("=" * 70)
print("python  :", sys.version.split()[0], "|", sys.executable)

# ---- 1) other python processes (a still-running serialFIB would show here) ----
try:
    out = subprocess.run(["tasklist", "/V", "/FI", "IMAGENAME eq python.exe"],
                         capture_output=True, text=True, timeout=15).stdout
    rows = [l for l in out.splitlines() if l.lower().startswith("python")]
    print(f"python.exe processes: {len(rows)} (this check is one of them)")
    for l in rows:
        print("   ", " ".join(l.split())[:150])
except Exception as e:
    print("tasklist failed:", e)

# ---- 2) pywin32 + gen_py cache ----
try:
    import pythoncom
    import win32com
    import win32com.client
    import win32com.client.dynamic
except Exception as e:
    print("pywin32 import FAILED:", e)
    sys.exit(1)
try:
    from importlib.metadata import version
    print("pywin32 :", version("pywin32"))
except Exception:
    pass
gen = getattr(win32com, "__gen_path__", None)
print("gen_py cache folder:", gen)
if gen and os.path.isdir(gen):
    entries = sorted(e for e in os.listdir(gen) if not e.startswith("__"))
    print("  cached wrappers:", entries if entries else "(none)")

# ---- 3) pyFIB's connection + reads ----
sys.path.insert(0, os.getcwd())
try:
    from smartfib_executor.smartsem_com import SmartSEM, CZEMAPI_CLSID
except Exception:
    print("Could not import pyFIB -- run this from the pyFIB folder.")
    traceback.print_exc()
    sys.exit(1)

sem = SmartSEM()
if LATE:
    sem.api = win32com.client.dynamic.Dispatch(CZEMAPI_CLSID)
cls = type(sem.api)
bound = ("LATE-bound (CDispatch)" if cls.__name__ == "CDispatch"
         else f"EARLY-bound via gen_py wrapper ({cls.__module__}.{cls.__name__})")
print("API object:", bound, "| forced late" if LATE else "")

mode = None
for name, fn in (("remoting", sem.connect_remoting), ("local", sem.connect_local)):
    try:
        fn()
        mode = name
        break
    except Exception as e:
        print(f"connect_{name} FAILED: {e}")
print("connected:", mode or "NO")

if mode:
    try:
        raw = sem.api.Get("AP_STAGE_AT_X", 0.0)
        print("raw  api.Get('AP_STAGE_AT_X', 0.0) ->", repr(raw))
    except Exception as e:
        print("raw  api.Get('AP_STAGE_AT_X', 0.0) RAISED:", repr(e))
    for mn, fn in (("AP_STAGE_AT_X", sem.get_float), ("AP_STAGE_AT_T", sem.get_float),
                   ("AP_FIB_MAGNIFICATION", sem.get_float), ("DP_FIB_IMAGING", sem.get_str),
                   ("DP_STAGE_IS", sem.get_str)):
        try:
            print(f"pyFIB {fn.__name__}({mn}) -> {fn(mn)!r}")
        except Exception as e:
            print(f"pyFIB {fn.__name__}({mn}) FAILED: {e}")
    try:
        p = sem.get_stage_position()
        print(f"stage: X {p.x_m * 1e3:.4f} Y {p.y_m * 1e3:.4f} Z {p.z_m * 1e3:.4f} mm, "
              f"T {p.tilt_deg:.2f}, R {p.rot_deg:.2f}")
    except Exception as e:
        print("get_stage_position FAILED:", e)
    sem.close()

print("=" * 70)
print("How to read this:")
print(" * Another python.exe running serialFIB -> close it fully, then re-run.")
print(" * EARLY-bound here but reads work with --late -> a cached COM wrapper (gen_py)")
print("   changed how pyFIB talks to the API: clear the gen_py cache folder above.")
print(" * Reads fail in BOTH modes, or connect fails -> the SmartSEM API server itself is")
print("   unhappy: restart the SmartSEM Remote API / SmartSEM (sample and column safe first).")
