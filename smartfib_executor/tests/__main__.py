from __future__ import annotations
import sys
from . import test_workflow, test_positions_schema, test_recall_arrival, test_capture_fresh_frame
from . import test_after_image, test_tilt_guard, test_mill_stages
if __name__ == "__main__":
    print("="*60); print("test_workflow"); print("="*60)
    rc1 = test_workflow._run_all()
    print("="*60); print("test_positions_schema"); print("="*60)
    rc2 = test_positions_schema._run_all()
    print("="*60); print("test_recall_arrival"); print("="*60)
    rc3 = test_recall_arrival._run_all()
    print("="*60); print("test_capture_fresh_frame"); print("="*60)
    rc4 = test_capture_fresh_frame._run_all()
    print("="*60); print("test_after_image"); print("="*60)
    rc5 = test_after_image._run_all()
    print("="*60); print("test_tilt_guard"); print("="*60)
    rc6 = test_tilt_guard._run_all()
    print("="*60); print("test_mill_stages"); print("="*60)
    rc7 = test_mill_stages._run_all()
    sys.exit(rc1 or rc2 or rc3 or rc4 or rc5 or rc6 or rc7)
