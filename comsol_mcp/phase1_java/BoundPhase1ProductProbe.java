package comsol_mcp.phase1_java;
import com.comsol.model.util.ModelUtil;
/** Non-checkout product query; ACDC is a documented COMSOL product identifier. */
public final class BoundPhase1ProductProbe {
 public static void main(String[] a) { ModelUtil.connect(a[0],Integer.parseInt(a[1])); try { System.out.println("W02_PRODUCT ACDC="+ModelUtil.hasProduct("ACDC")); } finally { ModelUtil.disconnect(); }
  System.exit(0); }
}
