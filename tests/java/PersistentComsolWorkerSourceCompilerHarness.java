package comsol_mcp.worker_java;

import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import javax.annotation.processing.AbstractProcessor;
import javax.annotation.processing.RoundEnvironment;
import javax.annotation.processing.SupportedAnnotationTypes;
import javax.annotation.processing.SupportedSourceVersion;
import javax.lang.model.SourceVersion;
import javax.lang.model.element.TypeElement;

/** Actual production snapshot and javac helper; no Worker constructor, socket, ModelUtil or model. */
public final class PersistentComsolWorkerSourceCompilerHarness {
  @SupportedAnnotationTypes("*")
  @SupportedSourceVersion(SourceVersion.RELEASE_11)
  public static final class MarkerProcessor extends AbstractProcessor {
    @Override public boolean process(Set<? extends TypeElement> annotations, RoundEnvironment round) {
      String marker=System.getProperty("m2.processor.marker");
      if(marker!=null) try { Files.write(Paths.get(marker),new byte[]{1}); }
      catch(Exception e) { throw new RuntimeException(e); }
      return false;
    }
  }
  static Method source, compile;
  static Path root;
  static int checks;
  static void check(boolean value, String message) { checks++; if (!value) throw new AssertionError(message); }
  static String sha(byte[] bytes) throws Exception {
    StringBuilder value=new StringBuilder(); for(byte b:MessageDigest.getInstance("SHA-256").digest(bytes)) value.append(String.format("%02x",b&255)); return value.toString();
  }
  static Object snapshot(Path path,String entry) throws Exception {
    Map<String,Object> body=new HashMap<>();body.put("source_artifact",path.toString());body.put("entrypoint",entry);
    return source.invoke(null,body);
  }
  @SuppressWarnings("unchecked")
  static Map<String,Object> compile(Object snap) throws Exception {
    Path classes=Files.createTempDirectory(root,"classes-");return (Map<String,Object>)compile.invoke(null,snap,classes);
  }
  static Path registered(String text) throws Exception {
    byte[] bytes=text.getBytes(StandardCharsets.UTF_8);Path path=root.resolve(sha(bytes)+".java");Files.write(path,bytes);return path;
  }
  static void rejected(Path p,String entry,String message) throws Exception {
    try { snapshot(p,entry);throw new AssertionError("accepted "+message); }
    catch(InvocationTargetException e) { check(e.getCause().getClass().getSimpleName().equals("WorkerFailure"),message); }
  }
  public static void main(String[] args) throws Exception {
    root=Paths.get(args[0]);Files.createDirectories(root);
    Class<?> spec=Class.forName("comsol_mcp.worker_java.PersistentComsolWorker$SourceSpec");
    source=PersistentComsolWorker.class.getDeclaredMethod("sourceSpec",Map.class);source.setAccessible(true);
    compile=PersistentComsolWorker.class.getDeclaredMethod("compileSnapshot",spec,Path.class);compile.setAccessible(true);
    String[] texts={
      "public class Getter { public static int run(){return 1;} }",
      "package some.pkg; public class Packaged { public static int run(){return 1;} }",
      "public class Outer { public static class Nested { public static int run(){return 1;} } }",
      "public class Primary {} class Helper { public static int run(){return 1;} }",
      "/* public class Fake {} */ public class Actual { String s=\"public class Other {}\"; }",
      "class Legacy { public static int run(){return 1;} }",
      "public interface Face {}",
      "public enum Choice { A }"
    };
    String[] entries={"Getter","some.pkg.Packaged","Outer$Nested","Helper","Actual","Legacy","Face","Choice"};
    for(int i=0;i<texts.length;i++) {
      Path path=registered(texts[i]);byte[] before=Files.readAllBytes(path);Map<String,Object> result=compile(snapshot(path,entries[i]));
      check(Boolean.TRUE.equals(result.get("compiled")),entries[i]+": "+result);
      check(Arrays.equals(before,Files.readAllBytes(path)),"original registered bytes preserved");
    }
    for(String text:new String[]{"public class First {} public class Second {}","public class Broken { !!! }","public class {}", "public class 123Bad {}"}) {
      Path p=registered(text);Map<String,Object> result=compile(snapshot(p,"First"));
      check(Boolean.FALSE.equals(result.get("compiled")),"real compiler failure preserved");
      List<?> rows=(List<?>)result.get("diagnostics");check(!rows.isEmpty(),"diagnostics retained");
      check(((Map<?,?>)rows.get(0)).get("source").equals(p.toString()),"registered source diagnostic association");
    }
    Path stable=registered("public class Stable {}");Object snap=snapshot(stable,"Stable");
    Files.write(stable,"public class Replaced { !!! }".getBytes(StandardCharsets.UTF_8));
    check(Boolean.TRUE.equals(compile(snap).get("compiled")),"compile immutable snapshot after path changed");
    rejected(stable,"Stable","content address mutation rejected before compile");
    Path good=registered("public class Good {}");Path link=root.resolve("Linked.java");Files.createSymbolicLink(link,good);
    rejected(link,"Good","symlink source rejected");
    Path parentLink=root.resolve("parentlink");Files.createSymbolicLink(parentLink,root);
    rejected(parentLink.resolve(good.getFileName()),"Good","symlink parent rejected");
    Path oversized=root.resolve("Large.java");Files.write(oversized,new byte[2*1024*1024+1]);rejected(oversized,"Large","bounded snapshot limit");
    Path old=root.resolve("Legacy.java");Files.write(old,"class Legacy {}".getBytes(StandardCharsets.UTF_8));
    check(Boolean.TRUE.equals(compile(snapshot(old,"Legacy")).get("compiled")),"ordinary legacy basename supported");
    for (byte[] invalid : new byte[][] {
        new byte[]{'/', '/', (byte)0xff, '\n', 'c', 'l', 'a', 's', 's', ' ', 'X', '{', '}'},
        new byte[]{'c','l','a','s','s',' ','X','{','S','t','r','i','n','g',' ','s','=','"',(byte)0xff,'"',';','}'}}) {
      Path p=root.resolve(sha(invalid)+".java");Files.write(p,invalid);
      try { snapshot(p,"X");throw new AssertionError("malformed UTF8 accepted"); }
      catch(InvocationTargetException ex) {
        check(ex.getCause().getMessage().contains("invalid UTF-8"),"comment/string invalid UTF8 remains refused");
        Field code=ex.getCause().getClass().getDeclaredField("code");code.setAccessible(true);
        check(code.get(ex.getCause()).equals("COMPILE_ERROR"),"invalid UTF8 typed compiler error");
        Field detailsField=ex.getCause().getClass().getDeclaredField("details");detailsField.setAccessible(true);
        Map<?,?> details=(Map<?,?>)detailsField.get(ex.getCause());
        check(details.get("source_sha256").equals(sha(invalid)) && details.get("entrypoint").equals("X"),"invalid UTF8 preserves original SHA and entrypoint");
        check(((Map<?,?>)((List<?>)details.get("diagnostics")).get(0)).get("source").equals(p.toString()),"invalid UTF8 preserves original source diagnostic");
      }
    }
    java.nio.file.attribute.BasicFileAttributes beforeDirectory=Files.readAttributes(root,java.nio.file.attribute.BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS);
    Files.write(root.resolve("unrelated-sibling"),new byte[]{1});
    java.nio.file.attribute.BasicFileAttributes afterDirectory=Files.readAttributes(root,java.nio.file.attribute.BasicFileAttributes.class,LinkOption.NOFOLLOW_LINKS);
    Method sameDirectory=PersistentComsolWorker.class.getDeclaredMethod("sameDirectoryIdentity",java.nio.file.attribute.BasicFileAttributes.class,java.nio.file.attribute.BasicFileAttributes.class);sameDirectory.setAccessible(true);
    check(Boolean.TRUE.equals(sameDirectory.invoke(null,beforeDirectory,afterDirectory)),"actual unrelated sibling change preserves stable directory identity");
    check(snapshot(good,"Good")!=null,"actual source snapshot remains valid after unrelated sibling changes");
    // Exercise the actual instance compile/cache method without its socket-binding constructor.
    Field unsafeField=sun.misc.Unsafe.class.getDeclaredField("theUnsafe");unsafeField.setAccessible(true);
    sun.misc.Unsafe unsafe=(sun.misc.Unsafe)unsafeField.get(null);
    Object worker=unsafe.allocateInstance(PersistentComsolWorker.class);
    Field codeRoot=PersistentComsolWorker.class.getDeclaredField("codeRoot");codeRoot.setAccessible(true);codeRoot.set(worker,root);
    Field cache=PersistentComsolWorker.class.getDeclaredField("compiledArtifacts");cache.setAccessible(true);
    cache.set(worker,new java.util.concurrent.ConcurrentHashMap<>());
    Method actualCompile=PersistentComsolWorker.class.getDeclaredMethod("compileJava",spec);actualCompile.setAccessible(true);
    Map<?,?> actualResult=(Map<?,?>)actualCompile.invoke(worker,snap);
    check(Boolean.TRUE.equals(actualResult.get("compiled")),"actual compiler method reuses snapshot after source path mutation");
    Map<?,?> again=(Map<?,?>)actualCompile.invoke(worker,snap);
    check(actualResult.get("source_sha256").equals(again.get("source_sha256")),"snapshot SHA stable across cache reuse");
    for(String invalidName:new String[]{"public class {}", "public class 123Bad {}"}) {
      Path p=registered(invalidName);
      try { actualCompile.invoke(worker,snapshot(p,"Invalid"));throw new AssertionError("invalid AST name accepted"); }
      catch(InvocationTargetException ex) {
        Field code=ex.getCause().getClass().getDeclaredField("code");code.setAccessible(true);
        check(code.get(ex.getCause()).equals("COMPILE_ERROR"),"invalid AST names return typed COMPILE_ERROR, not invalid pathname");
      }
    }
    Path forbidden=registered("public class Forbidden { static { } }");
    try { actualCompile.invoke(worker,snapshot(forbidden,"Forbidden"));throw new AssertionError("static initializer accepted"); }
    catch(InvocationTargetException ex) { check(ex.getCause().getMessage().contains("static initializer"),"actual javac AST static initializer rejection"); }
    for (String blocked : new String[] {
        "public class UsesCreate { Object run(){ return com.comsol.model.util.ModelUtil.create(\"x\"); } }",
        "public class UsesCreateUnique { Object run(){ return com.comsol.model.util.ModelUtil.createUnique(\"x\"); } }",
        "public class UsesClear { void run(){ com.comsol.model.util.ModelUtil.clear(); } }",
        "public class UsesRemove { void run(){ com.comsol.model.util.ModelUtil.remove(\"x\"); } }",
        "public class UsesModel { Object run(){ return com.comsol.model.util.ModelUtil.model(\"x\"); } }",
        "public class UsesLoad { Object run() throws Exception { return com.comsol.model.util.ModelUtil.load(\"x\", \"x.mph\"); } }",
        "public class UsesConnect { void run(){ com.comsol.model.util.ModelUtil.connect(); } }",
        "public class UsesDisconnect { void run(){ com.comsol.model.util.ModelUtil.disconnect(); } }",
        "public class UsesTags { String[] run(){ return com.comsol.model.util.ModelUtil.tags(); } }",
        "import static com.comsol.model.util.ModelUtil.create; public class UsesStaticImport { Object run(){ return create(\"x\"); } }",
        "import static com.comsol.model.util.ModelUtil.tags; public class UsesGlobalTags { String[] run(){ return tags(); } }",
        "public class UsesMethodRef { Runnable run(){ return com.comsol.model.util.ModelUtil::clear; } }",
        "public class UsesUnicode { Object run(){ return com.comsol.model.util.ModelUtil.cr\\u0065ate(\"x\"); } }"
    }) {
      Path p=registered(blocked);
      try { actualCompile.invoke(worker,snapshot(p,"Blocked"));throw new AssertionError("global ModelUtil operation accepted: "+blocked); }
      catch(InvocationTargetException ex) { check(ex.getCause().getMessage().contains("ModelUtil"),"resolved ModelUtil boundary: "+blocked); }
    }
    Path harmless=registered("package example; class ModelUtil { static Object model(String value){return null;} } public class Harmless { String text=\"ModelUtil.create\"; Object run(){ /* ModelUtil.clear(); */ return ModelUtil.model(text); } }");
    check(Boolean.TRUE.equals(((Map<?,?>)actualCompile.invoke(worker,snapshot(harmless,"Harmless"))).get("compiled")),
        "comments, string contents, and non-COMSOL ModelUtil lookalike remain ordinary Java");
    Path metadata=registered("public class MetadataOnly { String run(){ return com.comsol.model.util.ModelUtil.getComsolVersion(); } }");
    check(Boolean.TRUE.equals(((Map<?,?>)actualCompile.invoke(worker,snapshot(metadata,"MetadataOnly"))).get("compiled")),
        "explicitly reviewed read-only ModelUtil version metadata remains usable");
    Path fakeStatic=registered("public class FakeStatic { String text=\"static { ModelUtil.clear(); }\"; /* static { ignored(); } */ }");
    check(Boolean.TRUE.equals(((Map<?,?>)actualCompile.invoke(worker,snapshot(fakeStatic,"FakeStatic"))).get("compiled")),
        "static initializer text in comments and literals is not treated as an AST initializer");
    Path serviceRoot=root.resolve("processor-service");
    Path service=serviceRoot.resolve("META-INF/services/javax.annotation.processing.Processor");
    Files.createDirectories(service.getParent());
    Files.write(service,"comsol_mcp.worker_java.PersistentComsolWorkerSourceCompilerHarness$MarkerProcessor\n".getBytes(StandardCharsets.UTF_8));
    Path marker=root.resolve("annotation-processor-ran");
    String oldClasspath=System.getProperty("java.class.path","");
    System.setProperty("java.class.path",oldClasspath+java.io.File.pathSeparator+serviceRoot);
    System.setProperty("m2.processor.marker",marker.toString());
    try {
      Path processorProbe=registered("@Deprecated public class ProcessorProbe {} ");
      check(Boolean.TRUE.equals(((Map<?,?>)actualCompile.invoke(worker,snapshot(processorProbe,"ProcessorProbe"))).get("compiled")),
          "annotated project source compiles with processors disabled");
      check(!Files.exists(marker),"javac -proc:none prevents annotation-processor side effects");
    } finally {
      System.setProperty("java.class.path",oldClasspath);
      System.clearProperty("m2.processor.marker");
    }
    for (String name : new String[]{"P0ParameterReadback.java", "P0ExportJava.java"}) {
      Path fixture=Paths.get(args[1]).resolve(name);byte[] bytes=Files.readAllBytes(fixture);
      Path registered=root.resolve(sha(bytes)+".java");Files.write(registered,bytes);
      check(Boolean.TRUE.equals(compile(snapshot(registered,name.substring(0,name.length()-5))).get("compiled")),"actual fixed fixture: "+name);
      check(Arrays.equals(bytes,Files.readAllBytes(registered)),"actual fixture artifact bytes preserved");
    }
    Path spaced=root.resolve("ordinary source.java");Files.write(spaced,"class OldName {}".getBytes(StandardCharsets.UTF_8));
    check(Boolean.TRUE.equals(compile(snapshot(spaced,"OldName")).get("compiled")),"legacy basename spaces remain legal");
    System.out.println("PASS_SOURCE_COMPILER checks="+checks+" engine=NOT_RUN Worker_constructor=NOT_RUN");
  }
}
