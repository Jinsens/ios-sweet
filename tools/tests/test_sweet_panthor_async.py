"""Exercise the actual Darwin transport's asynchronous fence ownership.

The IOKit endpoint completes only requested hardware tickets. This verifies
host synchronization/lifetimes, not Mali execution or measured device speed.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

from tools.tests.test_sweet_panthor_transport import MOCK, MESA

ROOT = Path(__file__).resolve().parents[2]

HARNESS = r'''
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <errno.h>
#include <stdio.h>
#include <time.h>
#include <IOKit/IOKitLib.h>
#include "xf86drm.h"
#include "drm-uapi/panthor_drm.h"
#include "sweet_gpu_user.h"
static unsigned submissions,waits,failed_wait,busy_once,max_pending;
static unsigned sequence[8],last_wait_sequence,last_wait_group;
static unsigned allocation_blocked,blocked_allocations;
static unsigned timeout_waits,wait_budget[16];
static int64_t mock_ns=1000000000;
static int test_clock_gettime(clockid_t clock,struct timespec *now) {
 assert(clock==CLOCK_MONOTONIC);
 now->tv_sec=mock_ns/1000000000;now->tv_nsec=mock_ns%1000000000;return 0;
}
static void *test_calloc(size_t n,size_t size);
void *IOServiceMatching(const char *n){(void)n;return (void *)1;}
io_service_t IOServiceGetMatchingService(unsigned a,void*b){(void)a;(void)b;return 1;}
kern_return_t IOServiceOpen(io_service_t a,unsigned b,unsigned c,io_connect_t *out){(void)a;(void)b;(void)c;*out=1;return 0;}
kern_return_t IOObjectRelease(unsigned a){(void)a;return 0;}
kern_return_t IOServiceClose(unsigned a){(void)a;return 0;}
unsigned mach_task_self(void){return 1;}
/* Include the production C unchanged, so static state can also be checked for
 * outstanding owners after API calls. Only the IOKit endpoint is mocked. */
#define calloc test_calloc
#define clock_gettime test_clock_gettime
#include "sweet-panthor-drm.c"
#undef clock_gettime
#undef calloc
static void *test_calloc(size_t n,size_t size) {
 if(allocation_blocked){blocked_allocations++;errno=ENOMEM;return NULL;}
 return calloc(n,size);
}
kern_return_t IOConnectCallStructMethod(unsigned c,unsigned s,const void *in,size_t n,void *out,size_t *size){
 (void)c;assert(in&&out&&n==*size);
 if(s==SWEET_GPU_PING)return 0;
 if(s==SWEET_GPU_SUBMIT_ASYNC) {
  struct sweet_gpu_submit *r=out;assert(r->group<8);
  if(busy_once){busy_once--;return kIOReturnBusy;}
  submissions++;r->sequence=++sequence[r->group];
  if(pend_count+1>max_pending)max_pending=pend_count+1;
  assert(pend_count<24);return 0;
 }
 if(s==SWEET_GPU_SUBMIT) {
  struct sweet_gpu_submit *r=out;submissions++;r->sequence=++sequence[r->group];
  allocation_blocked=1;return 0;
 }
 if(s==SWEET_GPU_WAIT) {
  struct sweet_gpu_wait *r=out;assert(r->group<8&&r->sequence<=sequence[r->group]);
  if(waits<16)wait_budget[waits]=r->timeout_ms;
  waits++;last_wait_sequence=(unsigned)r->sequence;last_wait_group=r->group;
  if(failed_wait)return 7;
  if(timeout_waits) {
   timeout_waits--;mock_ns+=(int64_t)r->timeout_ms*1000000;return kIOReturnTimeout;
  }
  return 0;
 }
 return 0;
}
kern_return_t IOConnectMapMemory64(unsigned a,unsigned b,unsigned c,mach_vm_address_t *p,mach_vm_size_t*s,unsigned f){
 (void)a;(void)b;(void)c;(void)f;*p=(uintptr_t)calloc(1,*s);return *p?0:1;}
kern_return_t IOConnectUnmapMemory64(unsigned a,unsigned b,unsigned c,mach_vm_address_t p){(void)a;(void)b;(void)c;free((void*)(uintptr_t)p);return 0;}
static uint32_t object(unsigned flags) {uint32_t h;assert(!drmSyncobjCreate(0,flags,&h));return h;}
static void submit_ops(unsigned group,struct drm_panthor_sync_op *ops,unsigned count) {
 struct drm_panthor_queue_submit q={.stream_addr=0x10000000,.stream_size=32,
   .syncs=DRM_PANTHOR_OBJ_ARRAY(count,ops)};
 struct drm_panthor_group_submit g={.group_handle=group,.queue_submits=DRM_PANTHOR_OBJ_ARRAY(1,&q)};
 assert(!drmIoctl(0,DRM_IOCTL_PANTHOR_GROUP_SUBMIT,&g));
}
static void submit(unsigned group,uint32_t h,uint64_t point,int timeline) {
 struct drm_panthor_sync_op op={.flags=DRM_PANTHOR_SYNC_OP_SIGNAL|
   (timeline?DRM_PANTHOR_SYNC_OP_HANDLE_TYPE_TIMELINE_SYNCOBJ:0),.handle=h,.timeline_value=point};
 submit_ops(group,&op,1);
}
static int wait_for(uint32_t h,uint64_t point) {return drmSyncobjTimelineWait(0,&h,&point,1,INT64_MAX,0,NULL);}
static void clean(void) {
 failed_wait=timeout_waits=0;assert(!pend_drain_all());
 for(unsigned i=1;i<OBJECTS;i++)if(syncs[i].used)assert(!drmSyncobjDestroy(0,i));
 assert(!pend_count);
 for(unsigned i=1;i<OBJECTS;i++)assert(!sync_points[i]);
}
int main(int argc,char **argv) {
 assert(argc==2);async_submit=1;connection=1;const char *mode=argv[1];
 uint32_t src=object(DRM_SYNCOBJ_CREATE_SIGNALED),dst=object(0);
 if(!strcmp(mode,"defer-chain")) {
  uint32_t chain=object(0);submit(1,src,1,1);
  assert(!waits&&pend_count==1&&!signalled(src,1));
  assert(!drmSyncobjTransfer(0,dst,3,src,1,0)&&!waits&&!signalled(dst,3));
  assert(!drmSyncobjTransfer(0,chain,9,dst,3,0)&&!waits&&!signalled(chain,9));
  assert(!wait_for(chain,9)&&waits==1&&signalled(src,1)&&signalled(dst,3));
 } else if(!strcmp(mode,"source-reset-reuse")) {
  submit(1,src,1,1);assert(!drmSyncobjTransfer(0,dst,1,src,1,0));
  assert(!drmSyncobjReset(0,&src,1));assert(!drmSyncobjDestroy(0,src));
  uint32_t reused=object(0);assert(reused==src);submit(1,reused,1,1);
  assert(!wait_for(dst,1)&&waits==1&&last_wait_sequence==1&&pend_count==1);
  assert(!signalled(reused,1));assert(!drmSyncobjReset(0,&reused,1));
  assert(!pend_drain_all()&&!signalled(reused,1));
 } else if(!strcmp(mode,"timeline-earliest")) {
  uint32_t later=object(0);submit(1,src,1,1);submit(1,src,2,1);
  assert(!drmSyncobjTransfer(0,dst,10,src,1,0));
  assert(!drmSyncobjTransfer(0,later,20,src,2,0)&&!waits);
  assert(!wait_for(dst,10)&&waits==1&&last_wait_sequence==1&&pend_count==1);
  assert(signalled(src,1)&&!signalled(src,2)&&!signalled(later,20));
  assert(!wait_for(later,20)&&waits==2&&last_wait_sequence==2);
 } else if(!strcmp(mode,"cross-group")) {
  uint32_t other=object(0);submit(1,src,1,1);submit(2,other,1,1);
  assert(!drmSyncobjTransfer(0,dst,1,src,1,0)&&!waits);
  assert(!drmSyncobjTransfer(0,dst,2,other,1,0)&&waits==1&&last_wait_group==1);
  assert(signalled(dst,1)&&!signalled(dst,2));
  assert(!wait_for(dst,2)&&waits==2&&last_wait_group==2);
 } else if(!strcmp(mode,"many-signals")) {
  struct drm_panthor_sync_op ops[7];uint32_t handles[7];
  for(unsigned i=0;i<7;i++) {handles[i]=object(0);ops[i]=(struct drm_panthor_sync_op){
   .flags=DRM_PANTHOR_SYNC_OP_SIGNAL|DRM_PANTHOR_SYNC_OP_HANDLE_TYPE_TIMELINE_SYNCOBJ,
   .handle=handles[i],.timeline_value=i+1};}
  submit_ops(1,ops,7);assert(!waits);assert(!wait_for(handles[6],7)&&waits==1);
  for(unsigned i=0;i<7;i++)assert(signalled(handles[i],i+1));
 } else if(!strcmp(mode,"batch-capacity")) {
  struct drm_panthor_sync_op ops[50];struct drm_panthor_queue_submit qs[50];
  for(unsigned i=0;i<50;i++) {
   ops[i]=(struct drm_panthor_sync_op){.flags=DRM_PANTHOR_SYNC_OP_SIGNAL|
    DRM_PANTHOR_SYNC_OP_HANDLE_TYPE_TIMELINE_SYNCOBJ,.handle=src,.timeline_value=i+1};
   qs[i]=(struct drm_panthor_queue_submit){.stream_addr=0x10000000,.stream_size=32,
    .syncs=DRM_PANTHOR_OBJ_ARRAY(1,&ops[i])};
  }
  struct drm_panthor_group_submit g={.group_handle=1,.queue_submits=DRM_PANTHOR_OBJ_ARRAY(50,qs)};
  assert(!drmIoctl(0,DRM_IOCTL_PANTHOR_GROUP_SUBMIT,&g));
  assert(submissions==50&&max_pending==24&&pend_count==24&&waits==26);
  assert(!wait_for(src,50)&&waits==50);
 } else if(!strcmp(mode,"failed-completion")) {
  submit(1,src,1,1);assert(!drmSyncobjTransfer(0,dst,1,src,1,0));failed_wait=1;
  assert(wait_for(dst,1)==-EIO&&errno==EIO&&waits==1&&!signalled(src,1)&&!signalled(dst,1)&&pend_count==1);
  failed_wait=0;assert(!wait_for(dst,1)&&waits==2&&signalled(dst,1));
 } else if(!strcmp(mode,"failed-output-order")) {
  submit(1,src,1,1);failed_wait=1;
  struct drm_panthor_sync_op ops[2]={
   {.flags=DRM_PANTHOR_SYNC_OP_SIGNAL|DRM_PANTHOR_SYNC_OP_HANDLE_TYPE_TIMELINE_SYNCOBJ,.handle=dst,.timeline_value=1},
   {.flags=DRM_PANTHOR_SYNC_OP_SIGNAL|DRM_PANTHOR_SYNC_OP_HANDLE_TYPE_TIMELINE_SYNCOBJ,.handle=src,.timeline_value=2}};
  struct drm_panthor_queue_submit q={.stream_addr=0x10000000,.stream_size=32,.syncs=DRM_PANTHOR_OBJ_ARRAY(2,ops)};
  struct drm_panthor_group_submit g={.group_handle=2,.queue_submits=DRM_PANTHOR_OBJ_ARRAY(1,&q)};
  assert(drmIoctl(0,DRM_IOCTL_PANTHOR_GROUP_SUBMIT,&g)<0);
  assert(submissions==1&&sequence[2]==0&&pend_count==1&&!point_find(dst,1)&&!signalled(dst,1));
  assert(!point_find(src,2)&&!signalled(src,2));failed_wait=0;
 } else if(!strcmp(mode,"busy-retry")) {
  submit(1,src,1,1);busy_once=1;submit(1,src,2,1);
  assert(submissions==2&&waits==1&&!failed&&pend_count==1&&!signalled(src,2));
  assert(!wait_for(src,2)&&waits==2);
 } else if(!strcmp(mode,"persistent-busy")) {
  submit(1,src,1,1);busy_once=2;
  struct drm_panthor_sync_op op={.flags=DRM_PANTHOR_SYNC_OP_SIGNAL|
   DRM_PANTHOR_SYNC_OP_HANDLE_TYPE_TIMELINE_SYNCOBJ,.handle=src,.timeline_value=2};
  struct drm_panthor_queue_submit q={.stream_addr=0x10000000,.stream_size=32,.syncs=DRM_PANTHOR_OBJ_ARRAY(1,&op)};
  struct drm_panthor_group_submit g={.group_handle=1,.queue_submits=DRM_PANTHOR_OBJ_ARRAY(1,&q)};
  assert(drmIoctl(0,DRM_IOCTL_PANTHOR_GROUP_SUBMIT,&g)<0&&errno==EBUSY&&!failed&&submissions==1);
  assert(signalled(src,1)&&!point_find(src,2)&&!signalled(src,2));
  assert(!drmIoctl(0,DRM_IOCTL_PANTHOR_GROUP_SUBMIT,&g)&&submissions==2&&!failed);
  assert(!wait_for(src,2)&&waits==2);
 } else if(!strcmp(mode,"binary-replace")) {
  submit(1,src,0,0);assert(!signalled(src,0));
  assert(!drmSyncobjTransfer(0,dst,0,src,0,0)&&!waits);
  assert(!drmSyncobjReset(0,&src,1));submit(1,src,0,0);
  assert(!wait_for(dst,0)&&waits==1&&!signalled(src,0));
  assert(!wait_for(src,0)&&waits==2&&signalled(src,0));
 } else if(!strcmp(mode,"expired-probe")) {
  submit(1,src,1,1);uint64_t point=1;
  assert(drmSyncobjTimelineWait(0,&src,&point,1,0,0,NULL)==-ETIME&&errno==ETIME&&!waits);
  assert(!wait_for(src,1)&&waits==1);
  assert(!drmSyncobjTimelineWait(0,&src,&point,1,0,0,NULL));
 } else if(!strcmp(mode,"chunk-timeout-retry")) {
  submit(1,src,1,1);uint64_t point=1;timeout_waits=2;
  assert(!drmSyncobjTimelineWait(0,&src,&point,1,mock_ns+12000000000LL,0,NULL));
  assert(waits==3&&wait_budget[0]==5000&&wait_budget[1]==5000&&wait_budget[2]==2000);
  assert(signalled(src,1)&&!pend_count);
 } else if(!strcmp(mode,"infinite-chunk-retry")) {
  submit(1,src,1,1);timeout_waits=2;
  assert(!wait_for(src,1)&&waits==3);
  for(unsigned i=0;i<3;i++)assert(wait_budget[i]==5000);
  assert(signalled(src,1)&&!pend_count);
 } else if(!strcmp(mode,"absolute-deadline-expires")) {
  submit(1,src,1,1);uint64_t point=1;timeout_waits=10;
  assert(drmSyncobjTimelineWait(0,&src,&point,1,mock_ns+6500000000LL,0,NULL)==-ETIME&&errno==ETIME);
  assert(waits==2&&wait_budget[0]==5000&&wait_budget[1]==1500);
  assert(!signalled(src,1)&&pend_count==1&&point_find(src,1));
  timeout_waits=0;assert(!wait_for(src,1)&&waits==3&&signalled(src,1));
 } else if(!strcmp(mode,"wait-error-abi")) {
  uint64_t point=1;uint32_t invalid=0;
  assert(drmSyncobjTimelineWait(0,&src,&point,0,0,0,NULL)==-EINVAL&&errno==EINVAL);
  assert(drmSyncobjTimelineWait(0,&invalid,&point,1,0,0,NULL)==-EINVAL&&errno==EINVAL);
  assert(drmSyncobjWait(0,&dst,1,0,0,NULL)==-ETIME&&errno==ETIME);
 } else if(!strcmp(mode,"wait-any-available")) {
  submit(1,src,1,1);submit(1,dst,1,1);uint64_t points[2]={1,1};
  uint32_t handles[2]={dst,src};uint32_t first=99;
  assert(!drmSyncobjTimelineWait(0,handles,points,2,0,DRM_SYNCOBJ_WAIT_FLAGS_WAIT_AVAILABLE,&first)&&!waits);
  uint64_t future=2;
  assert(drmSyncobjTimelineWait(0,&src,&future,1,0,DRM_SYNCOBJ_WAIT_FLAGS_WAIT_AVAILABLE,NULL)==-ETIME&&errno==ETIME&&!waits);
  assert(!drmSyncobjTimelineWait(0,handles,points,2,INT64_MAX,0,&first)&&waits==1&&first==1&&pend_count==1);
 } else if(!strcmp(mode,"sync-publication-no-alloc")) {
  async_submit=0;submit(1,src,1,1);
  assert(submissions==1&&signalled(src,1)&&!blocked_allocations&&!failed);
 } else if(!strcmp(mode,"invalid-transfer")) {
  assert(drmSyncobjTransfer(0,dst,1,src,1,0)<0&&errno==EINVAL&&!waits);
  assert(!drmSyncobjDestroy(0,src));
  assert(drmSyncobjTransfer(0,dst,1,src,0,0)<0&&errno==EINVAL&&!waits);
 } else return 2;
 clean();printf("PASS %s submits=%u waits=%u\n",mode,submissions,waits);return 0;
}
'''


@unittest.skipUnless(MESA.exists(), "requires pinned Mesa header fixture")
class AsyncTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name)
        (path / "IOKit").mkdir()
        (path / "IOKit/IOKitLib.h").write_text(MOCK)
        (path / "test.c").write_text(HARNESS)
        cls.binary = path / "test"
        result = subprocess.run([
            "clang", "-std=c11", "-O1", "-g", "-fsanitize=address,undefined",
            "-I", str(path), "-I", str(MESA / "src/redwood-drm"),
            "-I", str(MESA / "include"), "-I", str(ROOT / "xnu/gpu"),
            "-I", str(ROOT / "userland"), str(path / "test.c"),
            "-o", str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def check_case(self, mode):
        result = subprocess.run([str(self.binary), mode], capture_output=True,
                                text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_transfer_and_chain_are_deferred(self): self.check_case("defer-chain")
    def test_source_reset_destroy_reuse_does_not_retarget_ticket(self): self.check_case("source-reset-reuse")
    def test_older_timeline_wait_selects_earliest_point(self): self.check_case("timeline-earliest")
    def test_cross_group_replacement_waits_for_previous_dependency(self): self.check_case("cross-group")
    def test_more_than_four_signals_are_not_dropped(self): self.check_case("many-signals")
    def test_multi_item_batch_keeps_capacity_limit(self): self.check_case("batch-capacity")
    def test_failed_completion_never_signals_ticket(self): self.check_case("failed-completion")
    def test_failing_output_order_wait_occurs_before_submission(self): self.check_case("failed-output-order")
    def test_busy_retries_after_drain(self): self.check_case("busy-retry")
    def test_persistent_busy_leaves_vm_retryable(self): self.check_case("persistent-busy")
    def test_binary_replacement_does_not_revive_old_fence(self): self.check_case("binary-replace")
    def test_expired_probe_does_not_wait(self): self.check_case("expired-probe")
    def test_chunk_timeout_retries_until_external_deadline(self): self.check_case("chunk-timeout-retry")
    def test_infinite_deadline_keeps_each_kernel_poll_bounded(self): self.check_case("infinite-chunk-retry")
    def test_absolute_deadline_expires_without_signalling(self): self.check_case("absolute-deadline-expires")
    def test_wait_public_return_values_follow_libdrm_abi(self): self.check_case("wait-error-abi")
    def test_wait_any_and_availability_preserve_pending_fences(self): self.check_case("wait-any-available")
    def test_completed_sync_publication_does_not_allocate(self): self.check_case("sync-publication-no-alloc")
    def test_unattached_or_destroyed_source_is_rejected(self): self.check_case("invalid-transfer")


if __name__ == "__main__":
    unittest.main()
