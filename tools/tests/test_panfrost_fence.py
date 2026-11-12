"""Run patched Mesa fences and st_finish against the actual Darwin transport.

Only the GPU IOKit endpoint and Gallium type scaffolding are mocked. These
checks establish completion ownership, not device rendering performance.
"""
from pathlib import Path
import hashlib
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

from tools.tests.test_sweet_panthor_transport import MOCK, MESA

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path('src/gallium/drivers/panfrost/pan_fence.c')
ORIGINAL = '699c815152720fdcbbae76629b8ede845d694bc29401a1bd8c13724bc0adb309'
PATCHED = 'f35e1088b8f91da5628e51468b0978c5292ffabf745000039b3e44365532208c'


def builder():
    spec = importlib.util.spec_from_file_location('fence_builder', ROOT / 'tools/build-sweet-panfrost.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TYPES = r'''
#ifndef TEST_PIPE_STATE_H
#define TEST_PIPE_STATE_H
#include <stdint.h>
#include <stdbool.h>
struct pipe_reference { int count; };
struct panfrost_device {int fd;};
struct pipe_context;
struct pipe_fence_handle;
struct pipe_screen {
 struct panfrost_device dev;
 bool (*fence_finish)(struct pipe_screen *,struct pipe_context *,struct pipe_fence_handle *,uint64_t);
 void (*fence_reference)(struct pipe_screen *,struct pipe_fence_handle **,struct pipe_fence_handle *);
};
struct pipe_context {struct pipe_screen *screen;};
enum pipe_fd_type {PIPE_FD_TYPE_NATIVE_SYNC,PIPE_FD_TYPE_SYNCOBJ};
#endif
'''
INLINES = r'''
static inline void pipe_reference_init(struct pipe_reference *p,int count){p->count=count;}
static inline bool pipe_reference(struct pipe_reference *old,struct pipe_reference *next){
 if(old==next)return false;if(next)next->count++;
 return old&&!--old->count;
}
'''
TIME = r'''
#define OS_TIMEOUT_INFINITE UINT64_MAX
static inline uint64_t os_time_get_absolute_timeout(uint64_t timeout){
 if(timeout==OS_TIMEOUT_INFINITE)return OS_TIMEOUT_INFINITE;
 uint64_t now=(uint64_t)monotonic_ns();return timeout>INT64_MAX-now?INT64_MAX:now+timeout;
}
'''
HARNESS = r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <errno.h>
#include <IOKit/IOKitLib.h>
#include "xf86drm.h"
#include "drm-uapi/panthor_drm.h"
#include "sweet_gpu_user.h"
static unsigned gpu_submitted,gpu_waits,gpu_wait_failure,last_wait;
static unsigned fence_allocs,fence_frees,fence_create_failure,fence_transfer_failure,fence_alloc_failure;
static unsigned fence_objects_created,fence_objects_destroyed,export_calls;
void *IOServiceMatching(const char *n){(void)n;return (void *)1;}
io_service_t IOServiceGetMatchingService(unsigned a,void*b){(void)a;(void)b;return 1;}
kern_return_t IOServiceOpen(io_service_t a,unsigned b,unsigned c,io_connect_t *out){(void)a;(void)b;(void)c;*out=1;return 0;}
kern_return_t IOObjectRelease(unsigned a){(void)a;return 0;}
kern_return_t IOServiceClose(unsigned a){(void)a;return 0;}
unsigned mach_task_self(void){return 1;}
#include "sweet-panthor-drm.c"
kern_return_t IOConnectCallStructMethod(unsigned c,unsigned s,const void *in,size_t n,void *out,size_t *size){
 (void)c;assert(in&&out&&n==*size);
 if(s==SWEET_GPU_PING)return 0;
 if(s==SWEET_GPU_SUBMIT_ASYNC){((struct sweet_gpu_submit *)out)->sequence=++gpu_submitted;return 0;}
 if(s==SWEET_GPU_WAIT){
  gpu_waits++;last_wait=(unsigned)((struct sweet_gpu_wait *)out)->sequence;
  if(gpu_wait_failure==1)return kIOReturnTimeout;
  if(gpu_wait_failure==2)return 7;
  return 0;
 }
 return 0;
}
kern_return_t IOConnectMapMemory64(unsigned a,unsigned b,unsigned c,mach_vm_address_t *p,mach_vm_size_t*s,unsigned f){
 (void)a;(void)b;(void)c;(void)f;*p=(uintptr_t)calloc(1,*s);return *p?0:1;}
kern_return_t IOConnectUnmapMemory64(unsigned a,unsigned b,unsigned c,mach_vm_address_t p){(void)a;(void)b;(void)c;free((void*)(uintptr_t)p);return 0;}
static void *fence_calloc(size_t n,size_t s){
 if(fence_alloc_failure)return NULL;void *p=calloc(n,s);if(p)fence_allocs++;return p;
}
static void fence_free(void *p){if(p)fence_frees++;free(p);}
static int fence_create(int fd,uint32_t flags,uint32_t *h){
 if(fence_create_failure)return error(ENOMEM);
 int ret=drmSyncobjCreate(fd,flags,h);if(!ret)fence_objects_created++;return ret;
}
static int fence_destroy(int fd,uint32_t h){fence_objects_destroyed++;return drmSyncobjDestroy(fd,h);}
static int fence_transfer(int fd,uint32_t dst,uint64_t dp,uint32_t src,uint64_t sp,uint32_t flags){
 if(fence_transfer_failure)return error(EIO);return drmSyncobjTransfer(fd,dst,dp,src,sp,flags);
}
static int fence_export(int fd,uint32_t h,int *out){
 export_calls++;return drmSyncobjExportSyncFile(fd,h,out);
}
#define calloc fence_calloc
#define free fence_free
#define drmSyncobjCreate fence_create
#define drmSyncobjDestroy fence_destroy
#define drmSyncobjTransfer fence_transfer
#define drmSyncobjExportSyncFile fence_export
#define mesa_loge(...) ((void)0)
#include "pan_fence.c"
#undef calloc
#undef free
#undef drmSyncobjCreate
#undef drmSyncobjDestroy
#undef drmSyncobjTransfer
#undef drmSyncobjExportSyncFile
struct st_context {struct pipe_screen *screen;struct panfrost_context *ctx;};
#define MESA_TRACE_FUNC() ((void)0)
#define PIPE_FLUSH_ASYNC 1
#define PIPE_FLUSH_HINT_FINISH 2
static void st_manager_flush_swapbuffers(void){}
static void st_flush(struct st_context *st,struct pipe_fence_handle **f,unsigned flags){
 (void)flags;*f=panfrost_fence_create(st->ctx);
}
#include "st_finish.inc"
static uint32_t object(unsigned flags){uint32_t h;assert(!drmSyncobjCreate(0,flags,&h));return h;}
static void submit(uint32_t h){
 struct drm_panthor_sync_op op={.flags=DRM_PANTHOR_SYNC_OP_SIGNAL,.handle=h};
 struct drm_panthor_queue_submit q={.stream_addr=0x10000000,.stream_size=32,.syncs=DRM_PANTHOR_OBJ_ARRAY(1,&op)};
 struct drm_panthor_group_submit g={.group_handle=1,.queue_submits=DRM_PANTHOR_OBJ_ARRAY(1,&q)};
 assert(!drmIoctl(0,DRM_IOCTL_PANTHOR_GROUP_SUBMIT,&g));
}
static void drop(struct pipe_screen *screen,struct pipe_fence_handle **f){panfrost_fence_reference(screen,f,NULL);}
int main(int argc,char **argv){
 assert(argc==2);async_submit=1;connection=1;
 struct pipe_screen screen={.dev={.fd=0},.fence_finish=panfrost_fence_finish,.fence_reference=panfrost_fence_reference};
 struct panfrost_context ctx={.base={.screen=&screen},.syncobj=object(DRM_SYNCOBJ_CREATE_SIGNALED)};
 const char *mode=argv[1];submit(ctx.syncobj);
 if(!strcmp(mode,"snapshot")){
  struct pipe_fence_handle *f=panfrost_fence_create(&ctx);assert(f&&!f->signaled&&!gpu_waits&&!export_calls);
  assert(f->syncobj!=ctx.syncobj&&f->reference.count==1);
  uint32_t old=ctx.syncobj;assert(!drmSyncobjReset(0,&old,1));assert(!drmSyncobjDestroy(0,old));
  ctx.syncobj=object(0);assert(ctx.syncobj==old);submit(ctx.syncobj);
  assert(panfrost_fence_finish(&screen,NULL,f,OS_TIMEOUT_INFINITE));
  assert(gpu_waits==1&&last_wait==1&&pend_count==1&&!signalled(ctx.syncobj,0));
  assert(panfrost_fence_finish(&screen,NULL,f,0)&&gpu_waits==1);drop(&screen,&f);
 }else if(!strcmp(mode,"st-finish")){
  struct st_context st={.screen=&screen,.ctx=&ctx};st_finish(&st);
  assert(gpu_waits==1&&last_wait==1&&signalled(ctx.syncobj,0)&&!export_calls);
 }else if(!strcmp(mode,"expired")){
  struct pipe_fence_handle *f=panfrost_fence_create(&ctx);
  assert(!panfrost_fence_finish(&screen,NULL,f,0)&&!gpu_waits&&!f->signaled);
  assert(panfrost_fence_finish(&screen,NULL,f,OS_TIMEOUT_INFINITE)&&gpu_waits==1);drop(&screen,&f);
 }else if(!strcmp(mode,"timeout")||!strcmp(mode,"hardware-error")){
  struct pipe_fence_handle *f=panfrost_fence_create(&ctx);gpu_wait_failure=!strcmp(mode,"timeout")?1:2;
  assert(!panfrost_fence_finish(&screen,NULL,f,OS_TIMEOUT_INFINITE)&&!f->signaled);
  assert(errno==(gpu_wait_failure==1?ETIMEDOUT:EIO)&&pend_count==1&&!signalled(f->syncobj,0));
  gpu_wait_failure=0;assert(panfrost_fence_finish(&screen,NULL,f,OS_TIMEOUT_INFINITE));drop(&screen,&f);
 }else if(!strcmp(mode,"allocation-failure")){
  fence_alloc_failure=1;assert(!panfrost_fence_create(&ctx)&&!fence_allocs&&!fence_objects_created&&!gpu_waits);
 }else if(!strcmp(mode,"create-failure")){
  fence_create_failure=1;assert(!panfrost_fence_create(&ctx)&&fence_allocs==1&&fence_frees==1&&!fence_objects_created&&!gpu_waits);
 }else if(!strcmp(mode,"transfer-failure")){
  fence_transfer_failure=1;assert(!panfrost_fence_create(&ctx)&&fence_allocs==1&&fence_frees==1);
  assert(fence_objects_created==1&&fence_objects_destroyed==1&&!gpu_waits);
 }else if(!strcmp(mode,"references")){
  struct pipe_fence_handle *f=panfrost_fence_create(&ctx),*other=NULL;
  panfrost_fence_reference(&screen,&other,f);assert(f->reference.count==2);
  drop(&screen,&f);assert(!f&&!fence_objects_destroyed&&!fence_frees&&other->reference.count==1);
  drop(&screen,&other);assert(!other&&fence_objects_destroyed==1&&fence_frees==1);
 }else return 2;
 assert(!export_calls&&fence_allocs==fence_frees&&fence_objects_created==fence_objects_destroyed);
 gpu_wait_failure=0;assert(!pend_drain_all());
 for(unsigned i=1;i<OBJECTS;i++)if(syncs[i].used)assert(!drmSyncobjDestroy(0,i));
 printf("PASS %s waits=%u\n",mode,gpu_waits);return 0;
}
'''


@unittest.skipUnless(MESA.exists(), 'requires pinned Mesa source fixture')
class PanfrostFenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name)
        cls.tree = path / 'mesa'
        target = cls.tree / SOURCE
        target.parent.mkdir(parents=True)
        shutil.copyfile(MESA / SOURCE, target)
        # Allow the external fixture to have been built by this same patch.
        if hashlib.sha256(target.read_bytes()).hexdigest() == PATCHED:
            subprocess.run(['patch', '--batch', '--fuzz=0', '-R', '-p1', '-i',
                            str(ROOT / 'userland/panfrost-fence.patch')],
                           cwd=cls.tree, check=True, capture_output=True)
        if hashlib.sha256(target.read_bytes()).hexdigest() != ORIGINAL:
            raise AssertionError('incompatible pinned Mesa fence fixture')
        cls.pristine = target.read_bytes()
        builder().apply_panfrost_fence_patch(cls.tree)
        (path / 'pan_fence.c').write_bytes(target.read_bytes())
        shutil.copyfile(MESA / SOURCE.with_name('pan_fence.h'), path / 'pan_fence.h')
        (path / 'pipe').mkdir()
        (path / 'pipe/p_state.h').write_text(TYPES)
        (path / 'pan_context.h').write_text('#include "pipe/p_state.h"\nstruct panfrost_context {struct pipe_context base;uint32_t syncobj;};\n')
        (path / 'pan_screen.h').write_text('#include "pipe/p_state.h"\nstatic struct panfrost_device *pan_device(struct pipe_screen *s){return &s->dev;}\nstatic int panfrost_device_fd(struct panfrost_device *d){return d->fd;}\n')
        (path / 'util').mkdir()
        (path / 'util/u_inlines.h').write_text(INLINES)
        (path / 'util/os_time.h').write_text(TIME)
        (path / 'IOKit').mkdir()
        (path / 'IOKit/IOKitLib.h').write_text(MOCK)
        # Keep the state tracker's real finish control flow unchanged.
        st = (MESA / 'src/mesa/state_tracker/st_cb_flush.c').read_text()
        start = st.index('void\nst_finish(')
        end = st.index('\n\nvoid\nst_glFlush', start)
        (path / 'st_finish.inc').write_text(st[start:end])
        (path / 'test.c').write_text(HARNESS)
        cls.binary = path / 'test'
        result = subprocess.run([
            'clang', '-std=c11', '-O1', '-g', '-fsanitize=address,undefined',
            '-I', str(path), '-I', str(MESA / 'src/redwood-drm'),
            '-I', str(MESA / 'include'), '-I', str(ROOT / 'xnu/gpu'),
            '-I', str(ROOT / 'userland'), str(path / 'test.c'), '-o', str(cls.binary)],
            capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check_case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True, text=True, timeout=10,
                                env={**os.environ, 'UBSAN_OPTIONS': 'halt_on_error=1'})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_snapshot_survives_context_reset_destroy_and_reuse(self): self.check_case('snapshot')
    def test_actual_st_finish_waits_for_gpu_completion(self): self.check_case('st-finish')
    def test_expired_probe_does_not_fake_completion(self): self.check_case('expired')
    def test_timeout_preserves_pending_fence(self): self.check_case('timeout')
    def test_hardware_error_preserves_pending_fence(self): self.check_case('hardware-error')
    def test_allocation_failure_has_no_syncobj(self): self.check_case('allocation-failure')
    def test_create_failure_frees_fence(self): self.check_case('create-failure')
    def test_transfer_failure_destroys_new_syncobj(self): self.check_case('transfer-failure')
    def test_reference_lifetime_destroys_once(self): self.check_case('references')

    def test_builder_patch_is_idempotent_and_rejects_drift(self):
        module = builder()
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            target = tree / SOURCE
            target.parent.mkdir(parents=True)
            target.write_bytes(self.pristine)
            module.apply_panfrost_fence_patch(tree)
            self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), PATCHED)
            module.apply_panfrost_fence_patch(tree)
            target.write_text(target.read_text() + '\n/* local drift */\n')
            before = target.read_bytes()
            with self.assertRaisesRegex(ValueError, 'local drift'):
                module.apply_panfrost_fence_patch(tree)
            self.assertEqual(target.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
