/* SPDX-License-Identifier: MIT */
#import "SweetGPUEngine.h"
#import <OpenGLES/ES3/gl.h>
#import <dlfcn.h>
#import <mach/mach_time.h>
#import <stdatomic.h>
#import <math.h>

/* OSMesa's ES-profile header is part of the locally built Mesa bridge. Keep
 * the app independent of that private header and resolve every entry point
 * dynamically, so this file never links against desktop OpenGL. */
typedef void *SweetOSMesaContext;
enum { kSweetOSMesaFormat = 0x22, kSweetOSMesaProfile = 0x33,
       kSweetOSMesaESProfile = 0x38, kSweetOSMesaMajor = 0x36 };
typedef SweetOSMesaContext (*SweetOSMesaCreate)(const int *, SweetOSMesaContext);
typedef GLboolean (*SweetOSMesaMake)(SweetOSMesaContext, void *, GLenum, GLsizei, GLsizei);
typedef void (*SweetOSMesaDestroy)(SweetOSMesaContext);
typedef void *(*SweetOSMesaProc)(const char *);

typedef GLuint (*SweetCreateShader)(GLenum);
typedef void (*SweetShaderSource)(GLuint, GLsizei, const GLchar *const *, const GLint *);
typedef void (*SweetCompileShader)(GLuint);
typedef void (*SweetGetShaderiv)(GLuint, GLenum, GLint *);
typedef void (*SweetGetShaderInfoLog)(GLuint, GLsizei, GLsizei *, GLchar *);
typedef void (*SweetDeleteShader)(GLuint);
typedef GLuint (*SweetCreateProgram)(void);
typedef void (*SweetAttachShader)(GLuint, GLuint);
typedef void (*SweetLinkProgram)(GLuint);
typedef void (*SweetGetProgramiv)(GLuint, GLenum, GLint *);
typedef void (*SweetGetProgramInfoLog)(GLuint, GLsizei, GLsizei *, GLchar *);
typedef void (*SweetDeleteProgram)(GLuint);
typedef void (*SweetUseProgram)(GLuint);
typedef GLint (*SweetGetUniformLocation)(GLuint, const GLchar *);
typedef void (*SweetUniform1i)(GLint, GLint);
typedef GLint (*SweetGetAttribLocation)(GLuint, const GLchar *);
typedef void (*SweetGen)(GLsizei, GLuint *);
typedef void (*SweetBindTexture)(GLenum, GLuint);
typedef void (*SweetTexParameteri)(GLenum, GLenum, GLint);
typedef void (*SweetTexImage2D)(GLenum, GLint, GLint, GLsizei, GLsizei, GLint, GLenum, GLenum, const void *);
typedef void (*SweetBindFramebuffer)(GLenum, GLuint);
typedef GLenum (*SweetCheckFramebufferStatus)(GLenum);
typedef void (*SweetFramebufferTexture2D)(GLenum, GLenum, GLenum, GLuint, GLint);
typedef void (*SweetViewport)(GLint, GLint, GLsizei, GLsizei);
typedef void (*SweetClearColor)(GLfloat, GLfloat, GLfloat, GLfloat);
typedef void (*SweetClear)(GLbitfield);
typedef void (*SweetDrawArrays)(GLenum, GLint, GLsizei);
typedef void (*SweetFinish)(void);
typedef void (*SweetDelete)(GLsizei, const GLuint *);
typedef void (*SweetBindBuffer)(GLenum, GLuint);
typedef void (*SweetBufferData)(GLenum, GLsizeiptr, const void *, GLenum);
typedef void (*SweetEnableVertexAttribArray)(GLuint);
typedef void (*SweetVertexAttribPointer)(GLuint, GLint, GLenum, GLboolean, GLsizei, const void *);
typedef const GLubyte *(*SweetGetString)(GLenum);
typedef GLenum (*SweetGetError)(void);
typedef void (*SweetReadPixels)(GLint, GLint, GLsizei, GLsizei, GLenum, GLenum, void *);

typedef struct {
    SweetCreateShader CreateShader; SweetShaderSource ShaderSource; SweetCompileShader CompileShader;
    SweetGetShaderiv GetShaderiv; SweetGetShaderInfoLog GetShaderInfoLog; SweetDeleteShader DeleteShader;
    SweetCreateProgram CreateProgram; SweetAttachShader AttachShader; SweetLinkProgram LinkProgram;
    SweetGetProgramiv GetProgramiv; SweetGetProgramInfoLog GetProgramInfoLog; SweetDeleteProgram DeleteProgram;
    SweetUseProgram UseProgram; SweetGetUniformLocation GetUniformLocation; SweetUniform1i Uniform1i;
    SweetGetAttribLocation GetAttribLocation; SweetGen GenTextures, GenFramebuffers, GenBuffers; SweetBindTexture BindTexture;
    SweetTexParameteri TexParameteri; SweetTexImage2D TexImage2D; SweetBindFramebuffer BindFramebuffer;
    SweetCheckFramebufferStatus CheckFramebufferStatus; SweetFramebufferTexture2D FramebufferTexture2D;
    SweetViewport Viewport; SweetClearColor ClearColor; SweetClear Clear; SweetDrawArrays DrawArrays;
    SweetFinish Finish; SweetDelete DeleteTextures, DeleteFramebuffers, DeleteBuffers;
    SweetBindBuffer BindBuffer; SweetBufferData BufferData;
    SweetEnableVertexAttribArray EnableVertexAttribArray; SweetVertexAttribPointer VertexAttribPointer;
    SweetGetString GetString; SweetGetError GetError;
    SweetReadPixels ReadPixels;
} SweetGL;

static char SweetGPUQueueKey;

static uint64_t sweet_now(void) { return mach_absolute_time(); }
static uint64_t sweet_ns(uint64_t ticks)
{
    static mach_timebase_info_data_t tb; static dispatch_once_t once;
    dispatch_once(&once, ^{ mach_timebase_info(&tb); });
    return ticks * tb.numer / tb.denom;
}
static uint64_t sweet_ticks_from_ns(uint64_t ns)
{
    static mach_timebase_info_data_t tb; static dispatch_once_t once;
    dispatch_once(&once, ^{ mach_timebase_info(&tb); });
    return ns * tb.denom / tb.numer;
}

@interface SweetGPUEngine () {
    dispatch_queue_t _queue;
    atomic_uint_fast64_t _generation;
    atomic_bool _running;
    uint64_t _activeGeneration, _nextTick, _startTick, _lastReportTick;
    NSUInteger _targetFPS, _load, _frames, _lastReportFrames, _missed, _samples;
    double _totalFrameMs;
    NSMutableArray<NSNumber *> *_times, *_allTimes;
    NSString *_renderer;
    NSDictionary *_snapshot;
    SweetGPUStatsBlock _update;
    SweetGPUFailureBlock _failure;
    void *_library;
    SweetOSMesaContext _context;
    void *_pixels;
    SweetGL _gl;
    SweetOSMesaDestroy _mesaDestroy;
    SweetOSMesaMake _mesaMake;
    GLuint _program, _texture, _fbo, _vbo;
    GLint _loadLocation, _positionLocation;
}
@end

@implementation SweetGPUEngine

- (instancetype)init
{
    if ((self = [super init])) {
        _queue = dispatch_queue_create("com.sweet.gpu-benchmark", DISPATCH_QUEUE_SERIAL);
        dispatch_queue_set_specific(_queue, &SweetGPUQueueKey, (__bridge void *)self, NULL);
        atomic_init(&_generation, 0);
        atomic_init(&_running, false);
        _times = [NSMutableArray arrayWithCapacity:256];
        _allTimes = [NSMutableArray arrayWithCapacity:2400];
        _snapshot = @{};
    }
    return self;
}

- (BOOL)isRunning { return atomic_load(&_running); }
- (NSDictionary *)snapshot
{
    if (dispatch_get_specific(&SweetGPUQueueKey) == (__bridge void *)self) return _snapshot;
    __block NSDictionary *value;
    dispatch_sync(_queue, ^{ value = self->_snapshot; });
    return value;
}

- (void)dealloc
{
    /* Pending timer blocks hold only a weak reference. A dropped engine must
     * release its GL owner even if the caller forgot to stop it. */
    atomic_fetch_add(&_generation, 1);
    if (dispatch_get_specific(&SweetGPUQueueKey) == (__bridge void *)self) {
        [self releaseGLOnQueue];
    } else {
        __unsafe_unretained SweetGPUEngine *owner = self;
        dispatch_sync(_queue, ^{ [owner releaseGLOnQueue]; });
    }
}

static void *sweet_symbol(void *handle, const char *name) { return handle ? dlsym(handle, name) : NULL; }

- (BOOL)loadGLOnQueue:(NSString **)reason
{
    if (!_library) {
        NSMutableArray<NSString *> *paths = [NSMutableArray array];
        NSString *bundle = [[NSBundle mainBundle] pathForResource:@"libOSMesa.8" ofType:@"dylib" inDirectory:@"Frameworks"];
        if (bundle) [paths addObject:bundle];
        [paths addObject:@"/usr/local/sweet-gpu/lib/libOSMesa.8.dylib"];
        for (NSString *path in paths) { _library = dlopen(path.UTF8String, RTLD_NOW | RTLD_LOCAL); if (_library) break; }
    }
    if (!_library) { if (reason) *reason = [NSString stringWithFormat:@"Mesa bridge unavailable: %s", dlerror() ?: "unknown error"]; return NO; }
#define MESA(name, type) type name = (type)sweet_symbol(_library, #name)
    MESA(OSMesaCreateContextAttribs, SweetOSMesaCreate);
    MESA(OSMesaMakeCurrent, SweetOSMesaMake);
    MESA(OSMesaDestroyContext, SweetOSMesaDestroy);
    MESA(OSMesaGetProcAddress, SweetOSMesaProc);
#undef MESA
    if (!OSMesaCreateContextAttribs || !OSMesaMakeCurrent || !OSMesaDestroyContext || !OSMesaGetProcAddress) {
        if (reason) *reason = @"Mesa bridge is missing OSMesa entry points"; return NO;
    }
    _mesaDestroy = OSMesaDestroyContext; _mesaMake = OSMesaMakeCurrent;
    int attrs[] = { kSweetOSMesaFormat, GL_RGBA, kSweetOSMesaProfile, kSweetOSMesaESProfile,
                    kSweetOSMesaMajor, 3, 0 };
    _context = OSMesaCreateContextAttribs(attrs, NULL);
    if (!_context) { if (reason) *reason = @"Could not create OpenGL ES 3 Mesa context"; return NO; }
    _pixels = calloc(512u * 512u, 4);
    if (!_pixels || !_mesaMake(_context, _pixels, GL_UNSIGNED_BYTE, 512, 512)) { if (reason) *reason = @"Could not bind Mesa framebuffer"; return NO; }
#define GLF(name, type) do { \
    _gl.name = (type)OSMesaGetProcAddress("gl" #name); \
    if (!_gl.name) { if(reason)*reason=[NSString stringWithFormat:@"Mesa is missing gl%s", #name]; return NO; } \
} while (0)
    GLF(CreateShader, SweetCreateShader); GLF(ShaderSource, SweetShaderSource); GLF(CompileShader, SweetCompileShader);
    GLF(GetShaderiv, SweetGetShaderiv); GLF(GetShaderInfoLog, SweetGetShaderInfoLog); GLF(DeleteShader, SweetDeleteShader);
    GLF(CreateProgram, SweetCreateProgram); GLF(AttachShader, SweetAttachShader); GLF(LinkProgram, SweetLinkProgram);
    GLF(GetProgramiv, SweetGetProgramiv); GLF(GetProgramInfoLog, SweetGetProgramInfoLog); GLF(DeleteProgram, SweetDeleteProgram);
    GLF(UseProgram, SweetUseProgram); GLF(GetUniformLocation, SweetGetUniformLocation); GLF(Uniform1i, SweetUniform1i);
    GLF(GetAttribLocation, SweetGetAttribLocation); GLF(GenTextures, SweetGen); GLF(BindTexture, SweetBindTexture);
    GLF(TexParameteri, SweetTexParameteri); GLF(TexImage2D, SweetTexImage2D); GLF(BindFramebuffer, SweetBindFramebuffer);
    GLF(CheckFramebufferStatus, SweetCheckFramebufferStatus); GLF(FramebufferTexture2D, SweetFramebufferTexture2D);
    GLF(Viewport, SweetViewport); GLF(ClearColor, SweetClearColor); GLF(Clear, SweetClear); GLF(DrawArrays, SweetDrawArrays);
    GLF(Finish, SweetFinish); GLF(DeleteTextures, SweetDelete);
    GLF(GenFramebuffers, SweetGen); GLF(GenBuffers, SweetGen);
    GLF(DeleteFramebuffers, SweetDelete); GLF(DeleteBuffers, SweetDelete); GLF(BindBuffer, SweetBindBuffer);
    GLF(BufferData, SweetBufferData); GLF(EnableVertexAttribArray, SweetEnableVertexAttribArray);
    GLF(VertexAttribPointer, SweetVertexAttribPointer); GLF(GetString, SweetGetString); GLF(GetError, SweetGetError);
    GLF(ReadPixels, SweetReadPixels);
#undef GLF
    const char *renderer = (const char *)_gl.GetString(GL_RENDERER);
    _renderer = renderer ? [NSString stringWithUTF8String:renderer] : @"unknown";
    if (![_renderer containsString:@"Panfrost"] || ![_renderer containsString:@"G710"]) {
        if (reason) *reason = [NSString stringWithFormat:@"Refusing non-hardware renderer: %@", _renderer]; return NO;
    }
    const char *vs = "#version 300 es\nlayout(location=0) in vec2 p; void main(){gl_Position=vec4(p,0.,1.);}";
    const char *fs = "#version 300 es\nprecision highp float; uniform int iterations; out vec4 c; void main(){ vec3 v=fract(vec3(gl_FragCoord.xy/512.,.79)); for(int i=0;i<2048;i++){ if(i>=iterations) break; v=fract(v*vec3(1.37,1.61,1.91)+vec3(.11,.07,.13)); v=inversesqrt(dot(v,v)+.001)*v; } c=vec4(v,1.); }";
    GLuint v = _gl.CreateShader(GL_VERTEX_SHADER), f = _gl.CreateShader(GL_FRAGMENT_SHADER); _gl.ShaderSource(v,1,&vs,NULL); _gl.ShaderSource(f,1,&fs,NULL); _gl.CompileShader(v); _gl.CompileShader(f);
    GLint ok=0; _gl.GetShaderiv(v,GL_COMPILE_STATUS,&ok); if (!ok) { if(reason)*reason=@"Vertex shader compilation failed"; _gl.DeleteShader(v); _gl.DeleteShader(f); return NO; }
    _gl.GetShaderiv(f,GL_COMPILE_STATUS,&ok); if (!ok) { if(reason)*reason=@"Fragment shader compilation failed"; _gl.DeleteShader(v); _gl.DeleteShader(f); return NO; }
    _program=_gl.CreateProgram(); _gl.AttachShader(_program,v); _gl.AttachShader(_program,f); _gl.LinkProgram(_program); _gl.DeleteShader(v); _gl.DeleteShader(f); _gl.GetProgramiv(_program,GL_LINK_STATUS,&ok);
    if (!ok) { if(reason)*reason=@"Benchmark shader link failed"; return NO; }
    _loadLocation=_gl.GetUniformLocation(_program,"iterations"); _positionLocation=0;
    _gl.GenTextures(1,&_texture); _gl.BindTexture(GL_TEXTURE_2D,_texture); _gl.TexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR); _gl.TexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR); _gl.TexImage2D(GL_TEXTURE_2D,0,GL_RGBA,512,512,0,GL_RGBA,GL_UNSIGNED_BYTE,NULL);
    _gl.GenFramebuffers(1,&_fbo); _gl.BindFramebuffer(GL_FRAMEBUFFER,_fbo); _gl.FramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,_texture,0);
    if (_gl.CheckFramebufferStatus(GL_FRAMEBUFFER)!=GL_FRAMEBUFFER_COMPLETE) { if(reason)*reason=@"Benchmark framebuffer incomplete"; return NO; }
    _gl.GenBuffers(1,&_vbo); _gl.BindBuffer(GL_ARRAY_BUFFER,_vbo); const GLfloat vertices[] = {-1,-1,3,-1,-1,3}; _gl.BufferData(GL_ARRAY_BUFFER,sizeof(vertices),vertices,GL_STATIC_DRAW); _gl.UseProgram(_program); _gl.EnableVertexAttribArray(_positionLocation); _gl.VertexAttribPointer(_positionLocation,2,GL_FLOAT,GL_FALSE,0,NULL); _gl.Viewport(0,0,512,512);
    /* Validate one GPU-written pixel, then warm the actual shader variant.
     * Neither readback nor first-use compilation enters measured frames. */
    _gl.ClearColor(.25f,.5f,.75f,1); _gl.Clear(GL_COLOR_BUFFER_BIT); _gl.Finish();
    GLubyte pixel[4]={0};
    _gl.ReadPixels(0,0,1,1,GL_RGBA,GL_UNSIGNED_BYTE,pixel);
    if (abs((int)pixel[0]-64)>2 || abs((int)pixel[1]-128)>2 ||
        abs((int)pixel[2]-191)>2 || pixel[3]!=255) {
        if(reason)*reason=@"GPU 初始化像素校验失败"; return NO;
    }
    _gl.Uniform1i(_loadLocation,(GLint)(_load*256));
    _gl.DrawArrays(GL_TRIANGLES,0,3); _gl.Finish();
    if (_gl.GetError() != GL_NO_ERROR) { if(reason)*reason=@"GPU returned an initialization error"; return NO; }
    _mesaMake(NULL, NULL, GL_UNSIGNED_BYTE, 0, 0);
    return YES;
}

- (void)releaseGLOnQueue
{
    BOOL current = _context && _pixels && _mesaMake &&
        _mesaMake(_context, _pixels, GL_UNSIGNED_BYTE, 512, 512);
    if (current) {
        if (_gl.Finish) _gl.Finish();
        if (_gl.DeleteFramebuffers && _fbo) _gl.DeleteFramebuffers(1,&_fbo);
        if (_gl.DeleteTextures && _texture) _gl.DeleteTextures(1,&_texture);
        if (_gl.DeleteBuffers && _vbo) _gl.DeleteBuffers(1,&_vbo);
        if (_gl.DeleteProgram && _program) _gl.DeleteProgram(_program);
    }
    _fbo=_texture=_program=_vbo=0; if (_mesaMake && _context) (void)_mesaMake(NULL, NULL, GL_UNSIGNED_BYTE, 0, 0); if (_mesaDestroy && _context) _mesaDestroy(_context); _context=NULL; free(_pixels); _pixels=NULL; _gl=(SweetGL){}; _mesaMake=NULL; _mesaDestroy=NULL;
    /* Mesa owns process-wide TLS dispatch tables. Keep its single dlopen
     * reference for the app lifetime, including after a run is stopped. */
}

- (void)startWithFPS:(NSUInteger)fps load:(NSUInteger)load update:(SweetGPUStatsBlock)update failure:(SweetGPUFailureBlock)failure
{
    if (fps!=60 && fps!=120) { if(failure) dispatch_async(dispatch_get_main_queue(), ^{ failure(@"Only 60 FPS and 120 FPS are supported"); }); return; }
    uint64_t generation = atomic_fetch_add(&_generation, 1) + 1;
    atomic_store(&_running, false);
    dispatch_async(_queue, ^{
        if (generation != atomic_load(&self->_generation)) return;
        [self releaseGLOnQueue];
        self->_activeGeneration=generation; self->_targetFPS=fps; self->_load=MIN(MAX(load,1u),8u); self->_update=[update copy]; self->_failure=[failure copy]; self->_frames=self->_lastReportFrames=self->_missed=self->_samples=0; self->_startTick=self->_lastReportTick=0; self->_totalFrameMs=0; self->_snapshot=@{}; self->_renderer=nil; [self->_times removeAllObjects]; [self->_allTimes removeAllObjects]; NSString *reason=nil;
        if (![self loadGLOnQueue:&reason]) { [self failOnQueue:reason ?: @"GPU initialization failed" generation:generation]; return; }
        if (generation != atomic_load(&self->_generation)) { [self releaseGLOnQueue]; self->_update=nil; self->_failure=nil; return; }
        atomic_store(&self->_running, true); self->_startTick=self->_lastReportTick=sweet_now(); self->_nextTick=self->_startTick;
        [self scheduleFrame:generation];
    });
}

- (void)stop
{
    uint64_t generation=atomic_fetch_add(&_generation,1)+1;
    atomic_store(&_running,false);
    dispatch_async(_queue, ^{
        if (self->_activeGeneration > generation) return;
        NSDictionary *stats=self->_startTick?[self statsOnQueue]:nil;
        SweetGPUStatsBlock update=self->_update;
        [self releaseGLOnQueue]; self->_update=nil; self->_failure=nil;
        if (stats && update) {
            __weak SweetGPUEngine *weakSelf=self;
            dispatch_async(dispatch_get_main_queue(), ^{
                SweetGPUEngine *owner=weakSelf;
                if(owner && generation==atomic_load(&owner->_generation)) update(stats);
            });
        }
    });
}

- (void)failOnQueue:(NSString *)reason generation:(uint64_t)generation
{
    atomic_store(&_running,false);
    SweetGPUFailureBlock block=_failure;
    [self releaseGLOnQueue]; _update=nil; _failure=nil;
    __weak SweetGPUEngine *weakSelf=self;
    dispatch_async(dispatch_get_main_queue(), ^{
        SweetGPUEngine *owner=weakSelf;
        if (owner && generation==atomic_load(&owner->_generation) && block) block(reason);
    });
}

- (NSDictionary *)statsOnQueue
{
    BOOL running=atomic_load(&_running);
    NSArray<NSNumber *> *times=running?_times:_allTimes;
    uint64_t now=sweet_now(); uint64_t elapsed=_startTick?sweet_ns(now-_startTick):0; double seconds=elapsed/1e9; double sum=0; for(NSNumber*n in times)sum+=n.doubleValue; double avg=times.count?sum/times.count:0; NSArray<NSNumber *> *sorted=[times sortedArrayUsingSelector:@selector(compare:)]; double p95=sorted.count?sorted[(NSUInteger)MIN(sorted.count-1,(NSUInteger)ceil(sorted.count*.95)-1)].doubleValue:0;
    double actual=seconds>0?_frames/seconds:0;
    double intervalSeconds=_lastReportTick?sweet_ns(now-_lastReportTick)/1e9:0;
    double intervalFPS=intervalSeconds>0?(_frames-_lastReportFrames)/intervalSeconds:0;
    NSDictionary *result = @{ @"targetFPS":@(_targetFPS), @"load":@(_load), @"frames":@(_frames), @"fps":@(running?intervalFPS:actual), @"overallFPS":@(actual), @"overallAvgMs":@(_frames?_totalFrameMs/_frames:0), @"avgMs":@(avg), @"p95Ms":@(p95), @"elapsed":@(seconds), @"missed":@(_missed), @"samples":@(_samples), @"intervalFrames":@(_frames-_lastReportFrames), @"renderer":_renderer ?: @"unknown", @"hardware":@([_renderer containsString:@"Panfrost"] && [_renderer containsString:@"G710"]), @"running":@(running), @"width":@512, @"height":@512 };
    _snapshot = result;
    return result;
}

- (void)reportOnQueue
{
    if (!_lastReportTick) return; _samples++; NSDictionary *stats=[self statsOnQueue]; SweetGPUStatsBlock block=_update; uint64_t generation=_activeGeneration; if(block){ __weak SweetGPUEngine *weakSelf=self; dispatch_async(dispatch_get_main_queue(), ^{ SweetGPUEngine *strongSelf=weakSelf; if(strongSelf && generation==atomic_load(&strongSelf->_generation) && atomic_load(&strongSelf->_running)) block(stats); }); } _lastReportTick=sweet_now(); _lastReportFrames=_frames; [_times removeAllObjects];
}

- (void)scheduleFrame:(uint64_t)generation
{
    uint64_t period=sweet_ticks_from_ns(1000000000ull/_targetFPS); uint64_t now=sweet_now(); if(_nextTick<=now){ uint64_t behind=(now-_nextTick)/period; if(behind){_missed+=(NSUInteger)MIN(behind,(uint64_t)NSUIntegerMax); _nextTick+=behind*period;} }
    uint64_t due=_nextTick; _nextTick+=period; uint64_t delay=due>now?due-now:0;
    __weak SweetGPUEngine *weakSelf=self;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,(int64_t)sweet_ns(delay)),_queue,^{
        SweetGPUEngine *owner=weakSelf;
        if(!owner || generation!=atomic_load(&owner->_generation)||!atomic_load(&owner->_running))return;
        uint64_t began=sweet_now();
        if(!owner->_mesaMake(owner->_context,owner->_pixels,GL_UNSIGNED_BYTE,512,512)) {
            [owner failOnQueue:@"Could not bind the GPU context" generation:generation]; return;
        }
        owner->_gl.UseProgram(owner->_program);
        owner->_gl.Uniform1i(owner->_loadLocation,(GLint)(owner->_load*256));
        owner->_gl.DrawArrays(GL_TRIANGLES,0,3);
        owner->_gl.Finish();
        GLenum error=owner->_gl.GetError();
        (void)owner->_mesaMake(NULL,NULL,GL_UNSIGNED_BYTE,0,0);
        uint64_t elapsed=sweet_ns(sweet_now()-began);
        if(error!=GL_NO_ERROR){ [owner failOnQueue:[NSString stringWithFormat:@"GPU error 0x%x",error] generation:generation]; return; }
        if(generation!=atomic_load(&owner->_generation)||!atomic_load(&owner->_running))return;
        NSNumber *duration=@(elapsed/1e6);
        [owner->_times addObject:duration]; [owner->_allTimes addObject:duration]; owner->_totalFrameMs+=elapsed/1e6; owner->_frames++;
        if(sweet_ns(sweet_now()-owner->_lastReportTick)>=500000000ull)[owner reportOnQueue];
        [owner scheduleFrame:generation];
    });
}
@end
