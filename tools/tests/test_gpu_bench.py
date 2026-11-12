"""Run the real Objective-C benchmark engine against a deterministic GL owner.

This validates host lifecycle, callback generations and pacing only. The fake
library does not emulate Mali execution or prove device GPU performance.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]

HEADER = r'''
#include <stddef.h>
typedef unsigned int GLenum, GLuint, GLbitfield;
typedef int GLint, GLsizei;
typedef char GLchar;
typedef float GLfloat;
typedef ptrdiff_t GLsizeiptr;
typedef unsigned char GLboolean, GLubyte;
#define GL_FALSE 0
#define GL_TRUE 1
#define GL_NO_ERROR 0
'''

FAKE = r'''
#include <OpenGLES/ES3/gl.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
static _Thread_local void *current;
static atomic_int live, draws, bad_current, created, destroyed, reads, finishes;
static GLuint next_id=1;
static void check(void) { if (!current) atomic_fetch_add(&bad_current,1); }
int fake_live(void) { return atomic_load(&live); }
int fake_draws(void) { return atomic_load(&draws); }
int fake_bad_current(void) { return atomic_load(&bad_current); }
int fake_created(void) { return atomic_load(&created); }
int fake_destroyed(void) { return atomic_load(&destroyed); }
int fake_reads(void) { return atomic_load(&reads); }
void *OSMesaCreateContextAttribs(const int *attrs,void *share) {
    (void)attrs; (void)share;
    if (getenv("FAKE_INIT_DELAY")) usleep(150000);
    void *ctx=malloc(1); if(ctx) { atomic_fetch_add(&live,1); atomic_fetch_add(&created,1); }
    return ctx;
}
GLboolean OSMesaMakeCurrent(void *ctx,void *pixels,GLenum type,GLsizei w,GLsizei h) {
    (void)type;
    if(!ctx && !pixels) { current=NULL; return GL_TRUE; }
    if(!ctx || !pixels || w<1 || h<1) return GL_FALSE;
    current=ctx; return GL_TRUE;
}
void OSMesaDestroyContext(void *ctx) {
    if(ctx) { if(current==ctx) atomic_fetch_add(&bad_current,1);
        free(ctx); atomic_fetch_sub(&live,1); atomic_fetch_add(&destroyed,1); }
}
static GLuint glCreateShader(GLenum type) { (void)type; check(); return next_id++; }
static void glShaderSource(GLuint s,GLsizei n,const GLchar *const *p,const GLint *l) { (void)s;(void)n;(void)p;(void)l;check(); }
static void glCompileShader(GLuint s) {(void)s;check();}
static void glGetShaderiv(GLuint s,GLenum e,GLint *v) {(void)s;(void)e;check();*v=1;}
static void glGetShaderInfoLog(GLuint s,GLsizei n,GLsizei *len,GLchar *str) {(void)s;(void)n;check();if(len)*len=0;if(str)*str=0;}
static void glDeleteShader(GLuint s) {(void)s;check();}
static GLuint glCreateProgram(void) {check();return next_id++;}
static void glAttachShader(GLuint p,GLuint s) {(void)p;(void)s;check();}
static void glLinkProgram(GLuint p) {(void)p;check();}
static void glGetProgramiv(GLuint p,GLenum e,GLint *v) {(void)p;(void)e;check();*v=1;}
static void glGetProgramInfoLog(GLuint s,GLsizei n,GLsizei *len,GLchar *str) {glGetShaderInfoLog(s,n,len,str);}
static void glDeleteProgram(GLuint p) {(void)p;check();}
static void glUseProgram(GLuint p) {(void)p;check();}
static GLint glGetUniformLocation(GLuint p,const GLchar *n) {(void)p;(void)n;check();return 0;}
static void glUniform1i(GLint p,GLint v) {(void)p;(void)v;check();}
static GLint glGetAttribLocation(GLuint p,const GLchar *n) {(void)p;(void)n;check();return 0;}
static void gen(GLsizei n,GLuint *v) {check();while(n--)*v++=next_id++;}
static void del(GLsizei n,const GLuint *v) {(void)n;(void)v;check();}
static void bind(GLenum e,GLuint n) {(void)e;(void)n;check();}
static void glTexParameteri(GLenum e,GLenum p,GLint v) {(void)e;(void)p;(void)v;check();}
static void glTexImage2D(GLenum e,GLint l,GLint in,GLsizei w,GLsizei h,GLint b,GLenum f,GLenum t,const void *p) {(void)e;(void)l;(void)in;(void)w;(void)h;(void)b;(void)f;(void)t;(void)p;check();}
static GLenum glCheckFramebufferStatus(GLenum e) {(void)e;check();return GL_FRAMEBUFFER_COMPLETE;}
static void glFramebufferTexture2D(GLenum e,GLenum a,GLenum t,GLuint n,GLint l) {(void)e;(void)a;(void)t;(void)n;(void)l;check();}
static void glViewport(GLint x,GLint y,GLsizei w,GLsizei h) {(void)x;(void)y;(void)w;(void)h;check();}
static void glClearColor(GLfloat r,GLfloat g,GLfloat b,GLfloat a) {(void)r;(void)g;(void)b;(void)a;check();}
static void glClear(GLbitfield b) {(void)b;check();}
static void glDrawArrays(GLenum e,GLint f,GLsizei n) {(void)e;(void)f;(void)n;check();atomic_fetch_add(&draws,1);usleep(100);}
static void glFinish(void) {
    check();atomic_fetch_add(&finishes,1);
    if(getenv("FAKE_FINISH_SLOW"))usleep(30000);
}
static void glBufferData(GLenum e,GLsizeiptr n,const void *p,GLenum u) {(void)e;(void)n;(void)p;(void)u;check();}
static void glEnableVertexAttribArray(GLuint n) {(void)n;check();}
static void glVertexAttribPointer(GLuint n,GLint size,GLenum t,GLboolean z,GLsizei stride,const void *p) {(void)n;(void)size;(void)t;(void)z;(void)stride;(void)p;check();}
static const GLubyte *glGetString(GLenum e) {(void)e;check();return (const GLubyte *)(getenv("FAKE_SOFTWARE")?"llvmpipe":"Mali-G710 (Panfrost)");}
static GLenum glGetError(void) {
    check();
    const char *threshold=getenv("FAKE_RUNTIME_ERROR_AFTER_DRAWS");
    return getenv("FAKE_GL_ERROR") || (threshold && atomic_load(&draws)>=atoi(threshold))
        ? 0x502 : GL_NO_ERROR;
}
static void glReadPixels(GLint x,GLint y,GLsizei w,GLsizei h,GLenum f,GLenum t,void *p) {
    (void)x;(void)y;(void)f;(void)t;check();atomic_fetch_add(&reads,1);
    unsigned char *b=p;for(int i=0;i<w*h;i++){*b++=64;*b++=128;*b++=191;*b++=255;}
    if(getenv("FAKE_BAD_PIXEL"))memset(p,0,(size_t)w*h*4);
}
#ifndef OMIT_MESA_PROC
void *OSMesaGetProcAddress(const char *name) {
    if(getenv("FAKE_MISSING_GL")&&!strcmp(name,"glFinish"))return NULL;
    #define P(n) if(!strcmp(name,#n))return (void *)n
    P(glCreateShader);P(glShaderSource);P(glCompileShader);P(glGetShaderiv);P(glGetShaderInfoLog);P(glDeleteShader);
    P(glCreateProgram);P(glAttachShader);P(glLinkProgram);P(glGetProgramiv);P(glGetProgramInfoLog);P(glDeleteProgram);
    P(glUseProgram);P(glGetUniformLocation);P(glUniform1i);P(glGetAttribLocation);
    P(glTexParameteri);P(glTexImage2D);P(glCheckFramebufferStatus);P(glFramebufferTexture2D);
    P(glViewport);P(glClearColor);P(glClear);P(glDrawArrays);P(glFinish);P(glBufferData);
    P(glEnableVertexAttribArray);P(glVertexAttribPointer);P(glGetString);P(glGetError);P(glReadPixels);
    #undef P
    if(!strcmp(name,"glGenTextures")||!strcmp(name,"glGenFramebuffers")||!strcmp(name,"glGenBuffers"))return (void *)gen;
    if(!strcmp(name,"glDeleteTextures")||!strcmp(name,"glDeleteFramebuffers")||!strcmp(name,"glDeleteBuffers"))return (void *)del;
    if(!strcmp(name,"glBindTexture")||!strcmp(name,"glBindFramebuffer")||!strcmp(name,"glBindBuffer"))return (void *)bind;
    return NULL;
}
#endif
'''

DRIVER = r'''
#import <Foundation/Foundation.h>
#import "SweetGPUEngine.h"
#import <dlfcn.h>
#include <assert.h>
#include <math.h>
#include <stdlib.h>
static void pump(double seconds) {
    NSDate *until=[NSDate dateWithTimeIntervalSinceNow:seconds];
    while(until.timeIntervalSinceNow>0)
        [[NSRunLoop currentRunLoop] runMode:NSDefaultRunLoopMode beforeDate:[NSDate dateWithTimeIntervalSinceNow:0.002]];
}
int main(int argc,char **argv) {
 @autoreleasepool {
    assert(argc==3); const char *mode=argv[1];
    void *lib=dlopen(argv[2],RTLD_NOW|RTLD_LOCAL);assert(lib);
    int(*live)(void)=dlsym(lib,"fake_live");
    int(*bad)(void)=dlsym(lib,"fake_bad_current");
    int(*draws)(void)=dlsym(lib,"fake_draws");
    int(*created)(void)=dlsym(lib,"fake_created");
    int(*destroyed)(void)=dlsym(lib,"fake_destroyed");
    __block NSUInteger updates=0,finals=0,failures=0;
    __block NSDictionary *last=nil;
    __block NSString *reason=nil;
    SweetGPUStatsBlock update=^(NSDictionary *stats){updates++;last=stats;if(![stats[@"running"] boolValue])finals++;};
    SweetGPUFailureBlock fail=^(NSString *why){failures++;reason=why;};
    SweetGPUEngine *engine=[SweetGPUEngine new];
    if(!strcmp(mode,"60")||!strcmp(mode,"120")) {
        NSUInteger fps=(NSUInteger)strtoul(mode,NULL,10);
        [engine startWithFPS:fps load:1 update:update failure:fail];
        pump(1.2);[engine stop];pump(.1);
        assert(!failures && updates>=2 && finals==1 && last);
        double actual=[last[@"overallFPS"] doubleValue];
        assert(actual>fps*.65 && actual<fps*1.15);
        assert([last[@"frames"] unsignedIntegerValue]>fps*.6);
        assert(![last[@"running"] boolValue] && !engine.running);
    } else if(!strcmp(mode,"early")) {
        [engine startWithFPS:60 load:1 update:update failure:fail];
        pump(.12);[engine stop];pump(.1);
        assert(!failures && finals==1 && last);
        assert([last[@"frames"] unsignedIntegerValue]>0);
        assert([last[@"frames"] unsignedIntegerValue]<30);
    } else if(!strcmp(mode,"slow")) {
        setenv("FAKE_FINISH_SLOW","1",1);
        [engine startWithFPS:120 load:1 update:update failure:fail];
        pump(.85);[engine stop];pump(.12);
        assert(!failures && finals==1 && last);
        NSUInteger frames=[last[@"frames"] unsignedIntegerValue];
        assert(frames>=10 && frames<=30 && [last[@"missed"] unsignedIntegerValue]>frames);
        assert([last[@"overallAvgMs"] doubleValue]>=29);
        assert([last[@"overallFPS"] doubleValue]<40);
        /* One warm-up draw; over-budget completion must not cause bursts. */
        assert(draws()<35);
    } else if(!strcmp(mode,"runtime-error")) {
        /* First draw warms up; twenty later successful frames are counted.
         * The following draw returns a GL error and must never count. */
        setenv("FAKE_RUNTIME_ERROR_AFTER_DRAWS","22",1);
        [engine startWithFPS:120 load:1 update:update failure:fail];
        pump(.45);
        assert(failures==1 && !engine.running && live()==0);
        assert(draws()==22 && [reason containsString:@"GPU error"]);
        NSDictionary *stats=engine.snapshot;
        /* Query final internal state by stopping after failure if needed. */
        [engine stop];pump(.05);stats=engine.snapshot;
        assert([stats[@"frames"] unsignedIntegerValue]==20);
    } else if(!strcmp(mode,"report-stop")) {
        [engine startWithFPS:60 load:1 update:^(NSDictionary *s){
            update(s);
            if([s[@"running"] boolValue])[engine stop];
        } failure:fail];
        pump(.75);
        assert(!failures && finals==1 && last);
        assert([last[@"frames"] unsignedIntegerValue]>0);
        assert([last[@"fps"] doubleValue]>0 && [last[@"avgMs"] doubleValue]>0 && [last[@"p95Ms"] doubleValue]>0);
        assert([last[@"fps"] doubleValue]>40 && [last[@"fps"] doubleValue]<70);
        assert(fabs([last[@"fps"] doubleValue]-[last[@"overallFPS"] doubleValue])<.001);
        assert(fabs([last[@"avgMs"] doubleValue]-[last[@"overallAvgMs"] doubleValue])<.001);
    } else if(!strcmp(mode,"init-stop")) {
        setenv("FAKE_INIT_DELAY","1",1);
        [engine startWithFPS:60 load:1 update:update failure:fail];
        pump(.025);[engine stop];pump(.25);
        assert(!engine.running && !failures && !updates && live()==0);
    } else if(!strcmp(mode,"restart")) {
        __block NSUInteger oldCallbacks=0,newCallbacks=0;
        [engine startWithFPS:60 load:1 update:^(NSDictionary *s){(void)s;oldCallbacks++;} failure:fail];
        pump(.1);[engine stop];
        [engine startWithFPS:120 load:1 update:^(NSDictionary *s){newCallbacks++;last=s;} failure:fail];
        pump(.7);[engine stop];pump(.1);
        assert(!failures && oldCallbacks==0 && newCallbacks>=2);
        assert([last[@"targetFPS"] unsignedIntegerValue]==120 && ![last[@"running"] boolValue]);
    } else if(!strcmp(mode,"software")||!strcmp(mode,"missing-gl")||!strcmp(mode,"init-error")||!strcmp(mode,"missing-entry")||!strcmp(mode,"bad-pixel")) {
        if(!strcmp(mode,"software"))setenv("FAKE_SOFTWARE","1",1);
        if(!strcmp(mode,"missing-gl"))setenv("FAKE_MISSING_GL","1",1);
        if(!strcmp(mode,"init-error"))setenv("FAKE_GL_ERROR","1",1);
        if(!strcmp(mode,"bad-pixel"))setenv("FAKE_BAD_PIXEL","1",1);
        [engine startWithFPS:60 load:1 update:update failure:fail];pump(.25);
        assert(failures==1 && !updates && !engine.running && live()==0 && reason.length);
        if(!strcmp(mode,"software"))assert([reason containsString:@"non-hardware"]);
        if(!strcmp(mode,"bad-pixel"))assert([reason containsString:@"像素"]);
    } else if(!strcmp(mode,"invalid")) {
        [engine startWithFPS:90 load:1 update:update failure:fail];pump(.1);
        assert(failures==1 && !updates && !created() && !engine.running);
    } else if(!strcmp(mode,"dealloc")) {
        [engine startWithFPS:60 load:1 update:update failure:fail];pump(.1);
        engine=nil;pump(.1);assert(live()==0);
    } else return 2;
    if(engine)assert(!engine.running);
    assert(live()==0 && bad()==0 && created()==destroyed());
    printf("PASS %s draws=%d contexts=%d\n",mode,draws(),created());
    engine=nil;dlclose(lib);
 }
 return 0;
}
'''


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("clang"),
                     "requires macOS Foundation and clang")
class EngineRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = path = Path(cls.tmp.name)
        source=(ROOT/"userland/gpu-bench/SweetGPUEngine.m").read_text()
        gl = path/"OpenGLES/ES3/gl.h"
        gl.parent.mkdir(parents=True)
        constants=sorted(set(re.findall(r"\bGL_[A-Z0-9_]+",source))-
                         {"GL_FALSE","GL_TRUE","GL_NO_ERROR"})
        gl.write_text(HEADER+"\n".join(f"#define {name} {0x100+i}" for i,name in enumerate(constants))+"\n")
        (path/"fake.c").write_text(FAKE)
        (path/"driver.m").write_text(DRIVER)
        shutil.copy2(ROOT/"userland/gpu-bench/SweetGPUEngine.h",path)
        cls.lib=path/"libFakeOSMesa.dylib"
        cls.missing=path/"libMissingOSMesa.dylib"
        for lib,defs in [(cls.lib,[]),(cls.missing,["-DOMIT_MESA_PROC"])]:
            subprocess.run(["clang","-std=c11","-dynamiclib","-O1",*defs,"-I",str(path),
                            str(path/"fake.c"),"-o",str(lib)],check=True,capture_output=True)
        cls.binaries={}
        for kind,lib in [("normal",cls.lib),("missing-entry",cls.missing)]:
            transformed=source.replace('@"/usr/local/sweet-gpu/lib/libOSMesa.8.dylib"',
                                       '@"'+str(lib)+'"')
            if transformed==source:
                raise AssertionError("Expected one test-only Mesa lookup-path replacement")
            engine=path/(kind+".m");engine.write_text(transformed)
            binary=path/(kind+"-test")
            subprocess.run(["clang","-fobjc-arc","-fblocks","-O1","-g","-I",str(path),
                            "-framework","Foundation",str(engine),str(path/"driver.m"),
                            "-o",str(binary)],check=True,capture_output=True)
            cls.binaries[kind]=binary

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check_case(self,case):
        kind="missing-entry" if case=="missing-entry" else "normal"
        lib=self.missing if case=="missing-entry" else self.lib
        env={k:v for k,v in os.environ.items() if not k.startswith("FAKE_")}
        result=subprocess.run([str(self.binaries[kind]),case,str(lib)],
                              capture_output=True,text=True,timeout=10,env=env)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_60_fps_completion_pacing(self):self.check_case("60")
    def test_120_fps_completion_pacing(self):self.check_case("120")
    def test_early_stop_delivers_final_completed_frames(self):self.check_case("early")
    def test_over_budget_finish_skips_target_slots_without_bursting(self):self.check_case("slow")
    def test_runtime_error_does_not_count_failed_frame(self):self.check_case("runtime-error")
    def test_stopping_on_report_keeps_nonempty_metrics(self):self.check_case("report-stop")
    def test_bad_initial_pixel_is_rejected_and_freed(self):self.check_case("bad-pixel")
    def test_stop_during_initialization_cancels_callbacks(self):self.check_case("init-stop")
    def test_restart_suppresses_previous_session_callbacks(self):self.check_case("restart")
    def test_software_renderer_is_rejected_and_freed(self):self.check_case("software")
    def test_missing_gl_entry_is_rejected_and_freed(self):self.check_case("missing-gl")
    def test_missing_mesa_entry_is_rejected(self):self.check_case("missing-entry")
    def test_initialization_gl_error_is_cleaned_up(self):self.check_case("init-error")
    def test_unsupported_target_is_rejected_before_context_creation(self):self.check_case("invalid")
    def test_dropping_running_engine_releases_context(self):self.check_case("dealloc")


if __name__=="__main__":
    unittest.main()
