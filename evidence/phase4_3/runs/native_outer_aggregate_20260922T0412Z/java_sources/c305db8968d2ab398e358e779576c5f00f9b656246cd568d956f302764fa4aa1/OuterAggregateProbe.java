
import com.comsol.model.*; import java.util.*;
public final class OuterAggregateProbe {
 public static Object run(Model model, Map<String,Object> args) {
  Map<String,Object> out=new LinkedHashMap<>();
  for(String kind:new String[]{"AvSurface","IntSurface"}) {
   String tag="outeragg"+kind; NumericalFeature n=model.result().numerical().create(tag,kind);
   Map<String,Object> row=new LinkedHashMap<>();
   try {
    n.set("data","dset2"); n.set("expr",new String[]{"100*p1+10*p2+t/1[s]+x/1[m]","(100*p1+10*p2+t/1[s]+x/1[m])*i"});
    for(int outer=1;outer<=4;outer++) {
     Map<String,Object> vals=new LinkedHashMap<>();
     n.set("outersolnum",outer); n.run();
     vals.put("explicit_is_complex",n.isComplex(outer)); vals.put("default_real",n.getReal()); vals.put("default_imag",n.getImag());
     vals.put("explicit_real",n.getReal(false,outer)); vals.put("explicit_imag",n.getImag(outer));
     row.put(Integer.toString(outer),vals);
    }
   } catch(Exception e) { row.put("error",e.toString()); }
   finally {model.result().numerical().remove(tag);}
   out.put(kind,row);
  }
  return out;
 }
}
