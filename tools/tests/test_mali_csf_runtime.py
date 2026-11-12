"""Exercise the persistent transport's real C with a CS instruction model."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "mali_csf_runtime.h"
#define GPU 0x13000000ull
#define SH 0x90000000ull
#define MEM 0x91000000ull
struct model {
 uint32_t gpu[0x110000/4],sh[0x10000/4],mem[SWEET_CSF_QUEUE_BYTES/4],reg[96];
 uint64_t ticks,user[4];unsigned writes,calls,mode,user_doorbells,extract_tear,extract_high_reads;
};
static uint32_t *addr(struct model*m,uint64_t a){
 assert(!(a&3));
 if(a>=GPU&&a<GPU+sizeof(m->gpu))return m->gpu+(a-GPU)/4;
 if(a>=SH&&a<SH+sizeof(m->sh))return m->sh+(a-SH)/4;
 if(a>=MEM&&a<MEM+sizeof(m->mem))return m->mem+(a-MEM)/4;
 assert(0);return NULL;
}
static uint32_t rd(void*p,uint64_t a){
 struct model*m=p;uint32_t value=*addr(m,a);
 if(a==MEM+0x2004&&m->extract_tear){
  m->extract_high_reads++;
  if(m->extract_tear==2)return value+m->extract_high_reads;
  if(m->extract_high_reads==1)return value-1; /* one high/low rollover */
 }
 return value;
}
static uint64_t word(struct model*m,uint64_t a){return rd(m,a)|((uint64_t)rd(m,a+4)<<32);}
static void instruction(struct model*m,uint64_t ins,unsigned depth){
 unsigned op=ins>>56,dst=(ins>>48)&255,ar=(ins>>40)&255,vr=(ins>>32)&255;
 if(!op)return;
 if(op==1){assert(dst+1<96);m->reg[dst]=ins;m->reg[dst+1]=(ins>>32)&0xffff;}
 else if(op==2){assert(dst<96);m->reg[dst]=ins;}
 else if(op==3){assert((ins>>16)&255);}
 else if(op==36){assert(ar<96&&m->reg[ar]==0);assert((ins&0xffffffff)==0x233||(ins&0xffffffff)==0x211);}
 else if(op==32){
  assert(!depth&&ar==92&&vr==94&&m->reg[92]==0x10000000&&m->reg[93]==0);
  assert(m->reg[vr]&&m->reg[vr]<=sizeof(m->user));m->calls++;
  for(unsigned i=0;i<m->reg[vr]/8;i++)instruction(m,m->user[i],depth+1);
 }else if(op==38){
  assert(!depth&&ar==92&&vr==94&&m->reg[92]==SWEET_CSF_QUEUE_VA+0x3000);
  if(m->mode!=2)m->mem[0x3000/4]=m->reg[vr];
 }else assert(0);
}
/* Firmware may acknowledge doorbells long before the CS consumes commands.
 * Execute complete envelopes explicitly to model multiple jobs in flight. */
static void process(struct model*m,unsigned envelopes){
 uint64_t insert=word(m,MEM+0x1000),extract=word(m,MEM+0x2000);
 assert(insert>=extract&&insert-extract<=4096);
 uint64_t end=extract+(uint64_t)envelopes*128;
 if(end>insert)end=insert;
 for(uint64_t n=extract;n<end;n+=8)instruction(m,word(m,MEM+(n&4095)),0);
 m->mem[0x2000/4]=end;m->mem[0x2004/4]=end>>32;
}
static void doorbell(struct model*m,int user){
 uint32_t *gi=m->sh+0x2000/4,*go=m->sh+0x3000/4,*in=m->sh+0x4000/4,*out=m->sh+0x5000/4;
 uint32_t *ci=m->sh+0x6000/4,*co=m->sh+0x7000/4;
 if(!user){assert((gi[2]^go[2])&1);go[2]=gi[2];out[2]=in[2];}
 if(!(in[0]&7)){if(m->mode!=4)out[0]=in[0];return;}
 /* An individual CS STOP may not acknowledge while another group holds
  * resources; CSG TERMINATE above must still finish and leave a stale CS ACK. */
 if(!(ci[0]&7)){if(m->mode!=5)co[0]=ci[0];return;}
 out[0]=in[0];co[0]=ci[0];
 /* Firmware request notifications do not stand in for a queue kick. */
 if(!user)return;
 assert(ci[1]==0x10f);m->user_doorbells++;
 if(m->mode==1||m->mode==6)return;
 if(m->mode==3){co[0x80/4]=0x44;return;}
 process(m,32);
}
static void wr(void*p,uint64_t a,uint32_t n){
 struct model*m=p;m->writes++;*addr(m,a)=n;
 if(a==GPU+0x80000)doorbell(m,0);
 if(a==GPU+0x90000)doorbell(m,1);
 if(a==GPU+0x2458)m->gpu[0x2000/4]|=0x20000;
}
static uint64_t tick(void*p){return ++((struct model*)p)->ticks;}
static void barrier(void*p){(void)p;}
static void init(struct model*m){
 memset(m,0,sizeof(*m));m->gpu[0x18/4]=3;m->gpu[0x100/4]=0x7ff;m->gpu[0x110/4]=1;
 m->sh[4]=8;m->sh[5]=160;
 m->sh[0x1004/4]=0x04004000;m->sh[0x1008/4]=0x04005000;
 m->sh[0x100c/4]=26112;m->sh[0x1044/4]=0x04006000;m->sh[0x1048/4]=0x04007000;
}
int main(void){
 struct model*m=calloc(1,sizeof(*m));assert(m);
 struct sweet_gpu_io io={m,rd,wr,tick,barrier,1000000};
 struct sweet_csf_queue_config c={.gpu=GPU,.root_pa=SH,.shared_pa=SH,.memory_pa=MEM,
  .shared_va=0x04000000,.shared_bytes=0x10000,.input_va=0x04002000,.output_va=0x04003000,
  .memory_bytes=SWEET_CSF_QUEUE_BYTES,.timeout_ms=1};
 struct sweet_csf_runtime_group g={0};struct sweet_csf_queue_report r;
 init(m);assert(sweet_csf_runtime_bind(&c,&io,0,0x80000000)==-60&&!m->writes);
 assert(!sweet_csf_runtime_bind(&c,&io,1,0x80000000));
 assert(word(m,GPU+0x2440)==0x80000000&&word(m,GPU+0x2470)==0x42000006);
 assert(!sweet_csf_runtime_invalidate(&c,&io,1)&&word(m,GPU+0x2450)==47);
 assert(!sweet_csf_runtime_start(&c,&io,&g,0,1));
 assert(m->sh[0x4050/4]==1&&m->sh[0x4034/4]==0xf0010b0b);
 for(unsigned i=0;i<100;i++){
  m->user[0]=i?0:0x025000002438cafeull;
  assert(!sweet_csf_runtime_submit(&c,&io,&g,0x10000000,8,&r));
  assert(r.stage==7&&r.after==i+1&&r.extract==(i+1)*128&&r.error==0);
  assert(m->reg[80]==0x2438cafe&&m->calls==i+1&&m->user_doorbells==i+1);
 }
 unsigned writes=m->writes;
 assert(sweet_csf_runtime_submit(&c,&io,&g,0x10000001,8,&r)==-60&&m->writes==writes);
 assert(sweet_csf_runtime_start(&c,&io,&g,0,1)==-62&&m->writes==writes);
 m->mode=5;
 assert(!sweet_csf_runtime_stop(&c,&io,&g)&&!g.active);
 assert((m->sh[0x7000/4]&7)==1 && !(m->sh[0x6000/4]&7));
 memset(&g,0,sizeof(g));m->mode=0;
 assert(!sweet_csf_runtime_start(&c,&io,&g,0,1));
 m->user[0]=0x025000002438cafeull;
 assert(!sweet_csf_runtime_submit(&c,&io,&g,0x10000000,8,&r));
 assert(r.after==1 && r.extract==128 && m->reg[80]==0x2438cafe);
 assert(!sweet_csf_runtime_stop(&c,&io,&g));

 /* A high-word rollover during extract sampling must be retried before
  * occupancy is evaluated; persistent tearing is retryable backpressure. */
 init(m);memset(&g,0,sizeof(g));assert(!sweet_csf_runtime_start(&c,&io,&g,0,1));m->mode=6;
 g.insert=UINT64_C(0x100000080);
 m->mem[0x1000/4]=(uint32_t)g.insert;m->mem[0x1004/4]=g.insert>>32;
 m->mem[0x2000/4]=0;m->mem[0x2004/4]=1;m->extract_tear=1;
 assert(!sweet_csf_runtime_enqueue(&c,&io,&g,0x10000000,8,&r));
 assert(!g.failed&&g.sequence==1&&g.insert==UINT64_C(0x100000100));
 assert(m->extract_high_reads>=4);m->extract_tear=0;
 assert(!sweet_csf_runtime_stop(&c,&io,&g));
 init(m);memset(&g,0,sizeof(g));assert(!sweet_csf_runtime_start(&c,&io,&g,0,1));m->mode=6;
 m->extract_tear=2;writes=m->writes;
 assert(sweet_csf_runtime_enqueue(&c,&io,&g,0x10000000,8,&r)==-62);
 assert(!g.failed&&!g.sequence&&!g.insert&&m->writes==writes&&r.error==-62);
 assert(m->extract_high_reads>=6);m->extract_tear=0;
 assert(!sweet_csf_runtime_enqueue(&c,&io,&g,0x10000000,8,&r));
 assert(g.sequence==1&&g.insert==128&&!g.failed);
 process(m,1);assert(!sweet_csf_runtime_wait(&c,&io,&g,1,128,1,&r));
 assert(!sweet_csf_runtime_stop(&c,&io,&g));
 assert(!sweet_csf_runtime_bind(&c,&io,1,0)&&word(m,GPU+0x2470)==1);
 /* A stalled queue times out without declaring a hardware fault. Its
  * existing submission stays intact: retry WAIT, never enqueue it twice. */
 init(m);memset(&g,0,sizeof(g));assert(!sweet_csf_runtime_start(&c,&io,&g,0,1));
 m->mode=1;
 assert(sweet_csf_runtime_submit(&c,&io,&g,0x10000000,8,&r)==-63);
 assert(!g.failed&&g.active&&g.sequence==1&&g.insert==128);
 assert(r.after==0&&r.extract==0&&r.error==-63&&m->calls==0);
 /* A timeout is retried by waiting on this ticket. Ring space could admit
  * newer tickets, but resubmitting this same work would execute it twice. */
 m->mode=0;
 /* Enough budget to exercise the early doorbell re-ring at poll 2000. */
 assert(!sweet_csf_runtime_wait(&c,&io,&g,1,128,3,&r));
 assert(r.stage==7&&r.after==1&&r.extract==128&&!g.failed&&m->calls==1);
 assert(m->user_doorbells==2&&g.sequence==1&&g.insert==128);
 assert(!sweet_csf_runtime_submit(&c,&io,&g,0x10000000,8,&r));
 assert(r.after==2&&r.extract==256&&g.sequence==2&&m->calls==2);
 /* Waiting for an old sequence remains valid after newer progress. */
 assert(!sweet_csf_runtime_wait(&c,&io,&g,1,128,1,&r));
 assert(r.after==2&&r.extract==256&&m->calls==2);
 assert(!sweet_csf_runtime_stop(&c,&io,&g));

 /* Keep 31 complete envelopes outstanding. The remaining slot separates
  * full from empty; rejecting entry 32 must not touch memory or a ticket. */
 init(m);memset(&g,0,sizeof(g));assert(!sweet_csf_runtime_start(&c,&io,&g,0,1));
 m->mode=6;
 for(unsigned i=0;i<31;i++){
  assert(!sweet_csf_runtime_enqueue(&c,&io,&g,0x10000000,8,&r));
  assert(g.sequence==i+1&&g.insert==(i+1)*128&&r.stage==4&&r.error==0);
 }
 assert(!word(m,MEM+0x2000)&&!m->mem[0x3000/4]&&!m->calls);
 uint32_t saved[4096/4];memcpy(saved,m->mem,sizeof(saved));writes=m->writes;
 assert(sweet_csf_runtime_enqueue(&c,&io,&g,0x10000000,8,&r)==-62);
 assert(!g.failed&&g.sequence==31&&g.insert==3968&&m->writes==writes);
 assert(!memcmp(saved,m->mem,sizeof(saved)));
 /* Retire only three jobs; the producer may refill exactly those slots,
  * crossing ring offset 4096 without overwriting unconsumed envelopes. */
 process(m,3);assert(m->calls==3&&word(m,MEM+0x2000)==384&&m->mem[0x3000/4]==3);
 for(unsigned i=0;i<3;i++)assert(!sweet_csf_runtime_enqueue(&c,&io,&g,0x10000000,8,&r));
 assert(g.sequence==34&&g.insert==4352&&word(m,MEM+0x1000)==4352);
 writes=m->writes;
 assert(sweet_csf_runtime_enqueue(&c,&io,&g,0x10000000,8,&r)==-62&&m->writes==writes);
 assert(!sweet_csf_runtime_wait(&c,&io,&g,3,384,1,&r));
 assert(r.after==3&&r.extract==384&&m->calls==3);
 process(m,31);assert(m->calls==34&&word(m,MEM+0x2000)==4352);
 assert(!sweet_csf_runtime_wait(&c,&io,&g,34,4352,1,&r)&&r.after==34);
 /* Exercise several complete wraps with deferred consumers. */
 for(unsigned round=0;round<4;round++){
  uint32_t seq=g.sequence;
  for(unsigned i=0;i<31;i++)assert(!sweet_csf_runtime_enqueue(&c,&io,&g,0x10000000,8,&r));
  writes=m->writes;
  assert(sweet_csf_runtime_enqueue(&c,&io,&g,0x10000000,8,&r)==-62&&m->writes==writes);
  process(m,31);assert(g.sequence==seq+31&&m->calls==g.sequence);
  assert(!sweet_csf_runtime_wait(&c,&io,&g,g.sequence,(uint32_t)g.insert,1,&r));
 }
 assert(!g.failed&&g.insert>4096*4);
 assert(!sweet_csf_runtime_stop(&c,&io,&g));

 /* A high 32-bit ring generation still uses absolute producer/consumer
  * positions. This is independent of wrapping the 4096-byte backing. */
 init(m);memset(&g,0,sizeof(g));assert(!sweet_csf_runtime_start(&c,&io,&g,0,1));m->mode=6;
 g.insert=UINT64_C(0x100000000)+3968;
 m->mem[0x1000/4]=(uint32_t)g.insert;m->mem[0x1004/4]=g.insert>>32;
 m->mem[0x2000/4]=(uint32_t)g.insert;m->mem[0x2004/4]=g.insert>>32;
 assert(!sweet_csf_runtime_enqueue(&c,&io,&g,0x10000000,8,&r));
 assert(g.sequence==1&&g.insert==UINT64_C(0x100001000));
 process(m,1);assert(word(m,MEM+0x2000)==g.insert&&m->mem[0x3000/4]==1);
 assert(!sweet_csf_runtime_wait(&c,&io,&g,1,(uint32_t)g.insert,1,&r));
 assert(!sweet_csf_runtime_stop(&c,&io,&g));

 /* Invalid hardware extract values are not retryable fullness: fail closed
  * without writing the ring, producer index, sequence or a doorbell. */
 const uint64_t invalid_extract[]={128,1,UINT64_C(0x100000000)};
 for(unsigned i=0;i<sizeof(invalid_extract)/sizeof(invalid_extract[0]);i++){
  init(m);memset(&g,0,sizeof(g));assert(!sweet_csf_runtime_start(&c,&io,&g,0,1));m->mode=6;
  if(i==1)g.insert=128; /* unaligned extract behind an aligned producer */
  m->mem[0x2000/4]=(uint32_t)invalid_extract[i];m->mem[0x2004/4]=invalid_extract[i]>>32;
  writes=m->writes;
  assert(sweet_csf_runtime_enqueue(&c,&io,&g,0x10000000,8,&r)==-64);
  assert(g.failed&&!g.sequence&&g.insert==(i==1?128u:0u)&&m->writes==writes&&r.error==-64);
 }
 init(m);memset(&g,0,sizeof(g));assert(!sweet_csf_runtime_start(&c,&io,&g,0,1));m->mode=6;
 g.insert=4224;writes=m->writes; /* valid alignment but impossible occupancy */
 assert(sweet_csf_runtime_enqueue(&c,&io,&g,0x10000000,8,&r)==-64);
 assert(g.failed&&g.insert==4224&&!g.sequence&&m->writes==writes);

 /* Extract is an instruction cursor, not an envelope boundary. A valid
  * eight-byte advance must not be mistaken for a corrupt queue. */
 init(m);memset(&g,0,sizeof(g));assert(!sweet_csf_runtime_start(&c,&io,&g,0,1));m->mode=6;
 g.insert=128;m->mem[0x1000/4]=128;m->mem[0x2000/4]=8;
 assert(!sweet_csf_runtime_enqueue(&c,&io,&g,0x10000000,8,&r));
 assert(!g.failed&&g.sequence==1&&g.insert==256);
 assert(!sweet_csf_runtime_stop(&c,&io,&g));

 /* A different CS/AS status register must not poison this healthy group.
  * MMU upper completion bits are retained as diagnostic status only. */
 init(m);memset(&g,0,sizeof(g));assert(!sweet_csf_runtime_start(&c,&io,&g,0,1));m->mode=6;
 m->sh[0x8080/4]=0x44;m->gpu[(0x241c+2*64)/4]=0x44;m->gpu[0x2000/4]=0x20000;
 assert(!sweet_csf_runtime_enqueue(&c,&io,&g,0x10000000,8,&r));process(m,1);
 assert(!sweet_csf_runtime_wait(&c,&io,&g,1,128,1,&r));
 assert(!g.failed&&!r.fault&&!r.fatal&&!r.as_fault&&r.mmu_raw==0x20000);
 assert(!sweet_csf_runtime_stop(&c,&io,&g));

 /* Extract advancing without the GPU-written fence is not completion. A
  * delayed fence may arrive after timeout and satisfy the same wait. */
 init(m);memset(&g,0,sizeof(g));assert(!sweet_csf_runtime_start(&c,&io,&g,0,1));
 m->mode=2;
 assert(sweet_csf_runtime_submit(&c,&io,&g,0x10000000,8,&r)==-63);
 assert(!g.failed&&g.active&&r.after==0&&r.extract==128&&m->calls==1);
 writes=m->writes;
 assert(sweet_csf_runtime_wait(&c,&io,&g,1,128,1,&r)==-63);
 assert(!g.failed&&m->writes==writes&&m->calls==1&&g.sequence==1);
 m->mem[0x3000/4]=1; /* late completion written by the modeled GPU */
 assert(!sweet_csf_runtime_wait(&c,&io,&g,1,128,1,&r));
 assert(r.stage==7&&r.after==1&&r.extract==128&&m->calls==1);
 m->mode=0;
 assert(!sweet_csf_runtime_submit(&c,&io,&g,0x10000000,8,&r));
 assert(r.after==2&&r.extract==256&&m->calls==2&&!g.failed);
 assert(!sweet_csf_runtime_stop(&c,&io,&g));

 /* A reported CS fault does poison the group and refuses further work. */
 init(m);memset(&g,0,sizeof(g));assert(!sweet_csf_runtime_start(&c,&io,&g,0,1));
 m->mode=3;
 assert(sweet_csf_runtime_submit(&c,&io,&g,0x10000000,8,&r)==-64&&g.failed);
 assert(r.fault==0x44&&r.after==0&&m->calls==0);
 writes=m->writes;
 assert(sweet_csf_runtime_submit(&c,&io,&g,0x10000000,8,&r)==-60&&m->writes==writes);
 assert(sweet_csf_runtime_wait(&c,&io,&g,1,128,1,&r)==-60&&m->writes==writes);
 assert(!sweet_csf_runtime_stop(&c,&io,&g));
 init(m);memset(&g,0,sizeof(g));assert(!sweet_csf_runtime_start(&c,&io,&g,0,1));
 m->mode=4;assert(sweet_csf_runtime_stop(&c,&io,&g)<0&&g.active&&g.failed);
 free(m);return 0;
}
'''


# Isolate wait I/O from start/enqueue traffic so the test measures the actual
# polling contract, including report-only reads and fault-before-success.
WAIT_HARNESS = r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "mali_csf_runtime.h"
#define GPU UINT64_C(0x13000000)
#define SH UINT64_C(0x90000000)
#define MEM UINT64_C(0x91000000)
enum { CSG_REQ, CSG_ACK, CS_REQ, CS_ACK, CMD_LO, CMD_HI,
 EXTRACT_LO, EXTRACT_HI, FENCE_LO, FAULT, FATAL, MMU, AS_FAULT, FENCE_HI, REGS };
static const uint64_t addresses[REGS]={
 SH+0x4000,SH+0x5000,SH+0x6000,SH+0x7000,SH+0x7040,SH+0x7044,
 MEM+0x2000,MEM+0x2004,MEM+0x3000,SH+0x7080,SH+0x7084,
 GPU+0x2000,GPU+0x245c,MEM+0x3004
};
struct model {
 uint32_t values[REGS];uint64_t reads[REGS],total,ticks,barriers,writes;
 unsigned complete_poll,fault_poll,fault_index,diagnostics_on_tick;
};
static void diagnostics(struct model*m,unsigned base){
 for(unsigned i=0;i<6;i++)m->values[i]=base+i;
}
static uint32_t rd(void*p,uint64_t a){
 struct model*m=p;
 for(unsigned i=0;i<REGS;i++)if(a==addresses[i]){
  m->reads[i]++;m->total++;
  if(i==FENCE_LO&&m->complete_poll&&m->reads[i]==m->complete_poll){
   m->values[i]=1;diagnostics(m,0x200);
  }
  if(i==m->fault_index&&m->fault_poll&&m->reads[i]==m->fault_poll){
   diagnostics(m,0x300);return 0x44; /* transient, not stored */
  }
  return m->values[i];
 }
 assert(0);return 0;
}
static void wr(void*p,uint64_t a,uint32_t v){
 struct model*m=p;assert(a==GPU+0x90000&&v==1);m->writes++;
}
static uint64_t tick(void*p){
 struct model*m=p;m->ticks++;
 if(m->diagnostics_on_tick&&m->ticks==m->diagnostics_on_tick)diagnostics(m,0x400);
 return m->ticks;
}
static void barrier(void*p){((struct model*)p)->barriers++;}
static void latest(const struct sweet_csf_queue_report*r,unsigned base){
 assert(r->csg_req==base&&r->csg_ack==base+1&&r->cs_req==base+2&&r->cs_ack==base+3);
 assert(r->cmd_lo==base+4&&r->cmd_hi==base+5);
}
static void once(const struct model*m){
 for(unsigned i=0;i<6;i++)assert(m->reads[i]==1);
}
static void healthy_reads(const struct model*m,unsigned polls){
 once(m);for(unsigned i=6;i<REGS;i++)assert(m->reads[i]==polls);
 assert(m->total==8*(uint64_t)polls+6);
}
int main(int argc,char**argv){
 assert(argc==2);unsigned scenario=(unsigned)atoi(argv[1]);
 struct model m={0};diagnostics(&m,0x100);
 struct sweet_gpu_io io={&m,rd,wr,tick,barrier,1000};
 struct sweet_csf_queue_config c={.gpu=GPU,.shared_pa=SH,.shared_va=0x04000000,
  .shared_bytes=0x10000,.memory_bytes=SWEET_CSF_QUEUE_BYTES,.timeout_ms=1};
 struct sweet_csf_runtime_group g={.input=SH+0x4000,.output=SH+0x5000,
  .cs_input=SH+0x6000,.cs_output=SH+0x7000,.memory_pa=MEM,.active=1,.as_nr=1};
 struct sweet_csf_queue_report r={0};
 if(scenario==0){
  /* Eight stalled polls cost 8 reads apiece, then six final diagnostics.
   * Values change during the wait; timeout must capture their final state. */
  m.diagnostics_on_tick=5;
  assert(sweet_csf_runtime_wait(&c,&io,&g,1,128,8,&r)==-63);
  assert(r.polls==8&&r.error==-63&&!g.failed&&g.active&&m.ticks==9);
  assert(m.barriers==8&&!m.writes);healthy_reads(&m,8);latest(&r,0x400);
 }else if(scenario==1){
  /* A later GPU-written completion gets the same final diagnostics; request
   * ACKs alone never complete the first two polls. */
  m.values[EXTRACT_LO]=128;m.complete_poll=3;
  assert(!sweet_csf_runtime_wait(&c,&io,&g,1,128,8,&r));
  assert(r.polls==3&&r.stage==7&&!r.error&&!g.failed&&r.after==1&&r.extract==128);
  assert(m.ticks==3&&m.barriers==3&&!m.writes);healthy_reads(&m,3);latest(&r,0x200);
 }else if(scenario==2){
  /* Every fault source wins even when extract and fence already completed. */
  const unsigned faults[]={FAULT,FATAL,MMU,AS_FAULT,FENCE_HI};
  for(unsigned n=0;n<sizeof(faults)/sizeof(faults[0]);n++){
   memset(&m,0,sizeof(m));diagnostics(&m,0x100);memset(&r,0,sizeof(r));g.failed=0;
   m.values[EXTRACT_LO]=128;m.values[FENCE_LO]=1;m.values[faults[n]]=0x44;
   assert(sweet_csf_runtime_wait(&c,&io,&g,1,128,8,&r)==-64);
   assert(r.polls==1&&r.error==-64&&g.failed&&r.stage!=7&&r.after==1);
   assert(m.ticks==1&&m.barriers==1&&!m.writes);once(&m);latest(&r,0x100);
   if(faults[n]==FAULT)assert(r.fault==0x44);
   if(faults[n]==FATAL)assert(r.fatal==0x44);
   if(faults[n]==MMU)assert(r.mmu_raw==0x44);
   if(faults[n]==AS_FAULT)assert(r.as_fault==0x44);
  }
 }else if(scenario==3){
  /* A fault present for only one read must still be observed on that poll.
   * Moving all status reads after the loop would silently lose it. */
  const unsigned faults[]={FAULT,FATAL,MMU,AS_FAULT,FENCE_HI};
  for(unsigned n=0;n<sizeof(faults)/sizeof(faults[0]);n++){
   memset(&m,0,sizeof(m));diagnostics(&m,0x100);memset(&r,0,sizeof(r));g.failed=0;
   m.fault_poll=3;m.fault_index=faults[n];
   assert(sweet_csf_runtime_wait(&c,&io,&g,1,128,8,&r)==-64);
   assert(r.polls==3&&r.error==-64&&g.failed&&r.stage!=7&&!r.after);
   assert(m.reads[faults[n]]==3&&!m.values[faults[n]]);
   assert(m.ticks==3&&m.barriers==3&&!m.writes);once(&m);latest(&r,0x300);
   if(faults[n]==FAULT)assert(r.fault==0x44);
   if(faults[n]==FATAL)assert(r.fatal==0x44);
   if(faults[n]==MMU)assert(r.mmu_raw==0x44);
   if(faults[n]==AS_FAULT)assert(r.as_fault==0x44);
  }
 }else if(scenario==4){
  /* Diagnostics must not affect the original poll-index re-doorbells or
   * deadline. This reaches all three kicks without inventing completion. */
  assert(sweet_csf_runtime_wait(&c,&io,&g,1,128,200002,&r)==-63);
  assert(r.polls==200002&&r.error==-63&&!g.failed&&m.ticks==200003);
  assert(m.writes==3&&m.barriers==(uint64_t)r.polls+3);
  healthy_reads(&m,r.polls);latest(&r,0x100);
 }else if(scenario==5){
  /* Invalid waits perform no register access, barrier, clock read or kick. */
  g.active=0;
  assert(sweet_csf_runtime_wait(&c,&io,&g,1,128,8,&r)==-60&&r.error==-60);
  g.active=1;
  assert(sweet_csf_runtime_wait(&c,&io,&g,0,128,8,&r)==-60);
  assert(sweet_csf_runtime_wait(&c,&io,&g,1,128,8,NULL)==-60);
  assert(!m.total&&!m.ticks&&!m.barriers&&!m.writes);
 }else assert(0);
 return 0;
}
'''


class RuntimeTests(unittest.TestCase):
    def test_persistent_ring_vm_and_failure_paths(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / 'test.c').write_text(HARNESS)
            subprocess.run(['clang', '-std=c11', '-O1', '-g', '-Wall', '-Wextra',
                            '-Werror', '-fsanitize=address,undefined',
                            '-I', str(ROOT / 'xnu/gpu'), str(p / 'test.c'),
                            str(ROOT / 'xnu/gpu/mali_csf_runtime.c'),
                            '-o', str(p / 'test')], check=True)
            subprocess.run([str(p / 'test')], check=True)


class WaitPollingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temporary.name)
        (cls.directory / 'wait.c').write_text(WAIT_HARNESS)
        subprocess.run(['clang', '-std=c11', '-O1', '-g', '-Wall', '-Wextra',
                        '-Werror', '-fsanitize=address,undefined',
                        '-I', str(ROOT / 'xnu/gpu'), str(cls.directory / 'wait.c'),
                        str(ROOT / 'xnu/gpu/mali_csf_runtime.c'),
                        '-o', str(cls.directory / 'wait')], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_scenario(self, scenario):
        subprocess.run([str(self.directory / 'wait'), str(scenario)], check=True)

    def test_stalled_poll_read_budget_and_final_timeout_snapshot(self):
        self.run_scenario(0)

    def test_delayed_fence_completion_and_final_success_snapshot(self):
        self.run_scenario(1)

    def test_all_fault_sources_take_precedence_over_completion(self):
        self.run_scenario(2)

    def test_transient_faults_are_detected_on_each_poll(self):
        self.run_scenario(3)

    def test_deadline_and_all_redoorbell_indices_are_preserved(self):
        self.run_scenario(4)

    def test_invalid_wait_has_no_io(self):
        self.run_scenario(5)


if __name__ == '__main__':
    unittest.main()
