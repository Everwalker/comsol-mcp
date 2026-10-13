"""Build a compact offline Java harness for W23 perimeter/PML contracts.

The Java harness exercises the exact public pure helpers called by
NativeW23PortTransitionV1; it never constructs a COMSOL Model or starts an
engine. Supply an external scratch directory so generated classes/logs stay
outside the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


HARNESS = r'''import java.util.*;

public final class W23PortTransitionContractHarness {
  private static final double[] C = {0, 0, 0};
  private static final double[] N = {1, 0, 0};
  private static final double[] U = {0, 1, 0};
  private static final double[] V = {0, 0, 1};
  private static final double HU = 2, HV = 3;

  private static NativeW23PortTransitionV1.PerimeterEdgeSample e(int adjacency, double[][] p) {
    return new NativeW23PortTransitionV1.PerimeterEdgeSample(adjacency, p);
  }
  private static double[][] p(double... xyz) {
    if (xyz.length % 3 != 0) throw new IllegalArgumentException("coordinate tuple size");
    double[][] out = new double[xyz.length / 3][3];
    for (int i = 0; i < out.length; i++)
      System.arraycopy(xyz, i * 3, out[i], 0, 3);
    return out;
  }
  private static List<NativeW23PortTransitionV1.PerimeterEdgeSample> rect() {
    return new ArrayList<>(Arrays.asList(
      e(1, p(0,-2,-3, 0,-2,3)),
      e(1, p(0,2,3, 0,2,-3)),
      e(1, p(0,2,-3, 0,-2,-3)),
      e(1, p(0,-2,3, 0,2,3)),
      e(2, new double[0][])));
  }
  private static void ok(List<NativeW23PortTransitionV1.PerimeterEdgeSample> rows, String name) {
    NativeW23PortTransitionV1.verifyPerimeterSamples(C,N,U,V,HU,HV,rows);
  }
  private static void rejects(List<NativeW23PortTransitionV1.PerimeterEdgeSample> rows, String name) {
    try { ok(rows,name); throw new AssertionError(name + " unexpectedly accepted"); }
    catch (IllegalArgumentException | IllegalStateException expected) { }
  }
  private static Map<String,Object> dir(String owner, String d, String dmax) {
    Map<String,Object> m=new LinkedHashMap<>();
    m.put("owner",owner); m.put("distance_expression",d); m.put("dmax_expression",dmax);
    return m;
  }
  public static void main(String[] args) {
    int perimeterPass=0, perimeterReject=0, pmlPass=0, pmlReject=0;
    ok(rect(),"signed rectangle sides"); perimeterPass++;

    List<NativeW23PortTransitionV1.PerimeterEdgeSample> segmented=rect();
    segmented.remove(0);
    segmented.add(e(1,p(0,-2,-3,0,-2,0)));
    segmented.add(e(1,p(0,-2,0,0,-2,3)));
    ok(segmented,"segmented signed side"); perimeterPass++;

    List<NativeW23PortTransitionV1.PerimeterEdgeSample> gap=rect();
    gap.remove(0); gap.add(e(1,p(0,-2,-3,0,-2,0)));
    rejects(gap,"missing perimeter interval"); perimeterReject++;

    List<NativeW23PortTransitionV1.PerimeterEdgeSample> missing=rect();
    missing.remove(1);
    rejects(missing,"missing side"); perimeterReject++;

    List<NativeW23PortTransitionV1.PerimeterEdgeSample> hole=rect();
    hole.add(e(1,p(0,0,-1,0,0,1)));
    rejects(hole,"interior hole edge"); perimeterReject++;

    List<NativeW23PortTransitionV1.PerimeterEdgeSample> duplicate=rect();
    duplicate.add(e(1,p(0,-2,-3,0,-2,3)));
    rejects(duplicate,"duplicate perimeter interval"); perimeterReject++;

    List<NativeW23PortTransitionV1.PerimeterEdgeSample> wrongSide=rect();
    wrongSide.set(0,e(1,p(0,0,-3,0,0,3)));
    rejects(wrongSide,"wrong signed side"); perimeterReject++;

    List<NativeW23PortTransitionV1.PerimeterEdgeSample> nonfinite=rect();
    nonfinite.set(0,e(1,p(0,Double.NaN,-3,0,-2,3)));
    rejects(nonfinite,"nonfinite coordinate"); perimeterReject++;

    List<Map<String,Object>> expected=Arrays.asList(dir("upper","y-H","2[um]"),dir("outer","z-W","3[um]"));
    NativeW23PortTransitionV1.verifyPmlDirectionReadback(expected,expected); pmlPass++;
    for (String key : new String[]{"distance_expression","dmax_expression","owner"}) {
      List<Map<String,Object>> actual=new ArrayList<>(expected);
      Map<String,Object> changed=new LinkedHashMap<>(actual.get(0));
      changed.put(key,"changed"); actual.set(0,changed);
      try { NativeW23PortTransitionV1.verifyPmlDirectionReadback(expected,actual);
        throw new AssertionError("PML " + key + " mismatch unexpectedly accepted"); }
      catch (IllegalArgumentException expectedFailure) { pmlReject++; }
    }
    try { NativeW23PortTransitionV1.verifyPmlDirectionReadback(expected,expected.subList(0,1));
      throw new AssertionError("PML direction count mismatch unexpectedly accepted"); }
    catch (IllegalArgumentException expectedFailure) { pmlReject++; }

    System.out.println("W23_PORT_TRANSITION_CONTRACTS_PASS perimeter_positive="+perimeterPass+
      " perimeter_negative="+perimeterReject+" pml_positive="+pmlPass+
      " pml_negative="+pmlReject+" engine=NOT_STARTED");
  }
}
'''


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--java", required=True, type=Path, help="Java 11 java executable")
    parser.add_argument("--javac", required=True, type=Path, help="Java 11 javac executable")
    parser.add_argument("--comsol-root", required=True, type=Path, help="COMSOL 6.4 installation root")
    parser.add_argument("--scratch", required=True, type=Path, help="new task-owned external scratch directory")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    adapter = repo / "tools/java/NativeW23PortTransitionV1.java"
    if not args.scratch.is_dir():
        parser.error("--scratch must be an existing task-owned directory on the approved external volume")
    for executable in (args.java, args.javac):
        if not executable.is_file():
            parser.error(f"Java executable does not exist: {executable}")
    if not adapter.is_file() or not (args.comsol_root / "plugins").is_dir():
        parser.error("adapter source or COMSOL plugin directory is missing")

    run_dir = args.scratch / "w23-java-contracts"
    if run_dir.exists():
        parser.error(f"refusing to reuse existing output directory: {run_dir}")
    classes = run_dir / "classes"
    run_dir.mkdir()
    classes.mkdir()
    harness_path = run_dir / "W23PortTransitionContractHarness.java"
    harness_path.write_text(HARNESS, encoding="utf-8")
    classpath = f"{args.comsol_root}/plugins/*:{args.comsol_root}/apiplugins/*"
    compile_cmd = [str(args.javac), "-Xlint:none", "-cp", classpath, "-d", str(classes),
                   str(adapter), str(harness_path)]
    compile_result = subprocess.run(compile_cmd, text=True, capture_output=True, check=False, timeout=120)
    (run_dir / "javac.stdout.log").write_text(compile_result.stdout, encoding="utf-8")
    (run_dir / "javac.stderr.log").write_text(compile_result.stderr, encoding="utf-8")
    if compile_result.returncode != 0:
        print(json.dumps({"status":"COMPILE_FAILED","return_code":compile_result.returncode,
                          "stderr_path":str(run_dir / "javac.stderr.log")},sort_keys=True))
        return compile_result.returncode

    run_cmd = [str(args.java), "-cp", f"{classes}:{classpath}", "W23PortTransitionContractHarness"]
    run_result = subprocess.run(run_cmd, text=True, capture_output=True, check=False, timeout=60)
    stdout_path, stderr_path = run_dir / "java.stdout.log", run_dir / "java.stderr.log"
    stdout_path.write_text(run_result.stdout, encoding="utf-8")
    stderr_path.write_text(run_result.stderr, encoding="utf-8")
    report = {
        "status":"PASS" if run_result.returncode == 0 else "FAIL",
        "native_engine":"NOT_STARTED",
        "compile_return_code":compile_result.returncode,
        "run_return_code":run_result.returncode,
        "adapter_sha256":_hash(adapter),
        "harness_sha256":_hash(harness_path),
        "harness_stdout_path":str(stdout_path),
        "harness_stdout_sha256":_hash(stdout_path),
        "harness_stderr_path":str(stderr_path),
        "compile_stderr_path":str(run_dir / "javac.stderr.log"),
        "compile_command":compile_cmd,
        "run_command":run_cmd,
        "result_line":run_result.stdout.strip(),
    }
    report_path = run_dir / "receipt.json"
    report_path.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps({"status":report["status"],"receipt_path":str(report_path),
                      "receipt_sha256":_hash(report_path),"result_line":report["result_line"]},sort_keys=True))
    return run_result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
