"""Run the actual allocation-free BT protocol code under ASan and UBSan."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

HARNESS = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "mtk_bt_h4.h"
#include "mtk_bt_firmware.h"

static const uint8_t stream[] = {
    4, 0xe4, 5, 2, 6, 1, 0, 0,
    2, 1, 0, 6, 0, 4, 0xe4, 3, 2, 5, 0,
    3, 1, 0, 3, 4, 2, 5,
    5, 1, 0, 2, 0xc0, 0xab, 0xcd,
    4, 0x0e, 0,
    2, 0, 0, 0, 0,
    3, 0, 0, 0,
    5, 0, 0, 0, 0xc0
};
static const size_t lengths[] = {8, 11, 7, 7, 3, 5, 4, 5};
struct capture { size_t offset, frames; };
static void received(void *opaque, const uint8_t *frame, size_t length) {
    struct capture *capture = opaque;
    assert(capture->frames < sizeof(lengths) / sizeof(lengths[0]));
    assert(length == lengths[capture->frames]);
    assert(memcmp(frame, stream + capture->offset, length) == 0);
    capture->offset += length;
    capture->frames++;
}
static void ignore_frame(void *opaque, const uint8_t *frame, size_t length) {
    unsigned *frames = opaque;
    (void)frame; (void)length;
    ++*frames;
}
static void split_test(void) {
    /* Every two-chunk boundary, including within all length fields/payloads. */
    for(size_t split=0; split<=sizeof(stream); split++) {
        uint8_t storage[16]; struct mtk_bt_h4 decoder;
        struct capture capture={0}; size_t consumed=999;
        assert(mtk_bt_h4_init(&decoder,storage,sizeof(storage),received,&capture)==0);
        assert(mtk_bt_h4_feed(&decoder,stream,split,&consumed)==0);
        assert(consumed==split);
        assert(mtk_bt_h4_feed(&decoder,stream+split,sizeof(stream)-split,&consumed)==0);
        assert(consumed==sizeof(stream)-split);
        assert(capture.frames==8 && capture.offset==sizeof(stream));
        assert(mtk_bt_h4_finish(&decoder)==0);
    }
    /* Fixed-size fragments cross packet boundaries at a different alignment. */
    for(size_t step=1; step<sizeof(stream); step++) {
        uint8_t storage[16]; struct mtk_bt_h4 decoder;
        struct capture capture={0};
        assert(mtk_bt_h4_init(&decoder,storage,sizeof(storage),received,&capture)==0);
        for(size_t pos=0; pos<sizeof(stream);) {
            size_t chunk=sizeof(stream)-pos; if(chunk>step) chunk=step;
            assert(mtk_bt_h4_feed(&decoder,stream+pos,chunk,NULL)==0); pos+=chunk;
        }
        assert(capture.frames==8 && mtk_bt_h4_finish(&decoder)==0);
    }
}
static void oversize_test(void) {
    const uint8_t bad[]={2,0,0,0xff,0xff,4,0x0e,0};
    uint8_t storage[11]; struct mtk_bt_h4 decoder;
    unsigned frames=0; size_t consumed=999;
    assert(mtk_bt_h4_init(&decoder,storage,sizeof(storage),ignore_frame,&frames)==0);
    assert(mtk_bt_h4_feed(&decoder,bad,sizeof(bad),&consumed)==MTK_BT_H4_OVERSIZE);
    assert(consumed==5 && frames==0);
    assert(mtk_bt_h4_feed(&decoder,stream,sizeof(stream),&consumed)==MTK_BT_H4_OVERSIZE);
    assert(consumed==0 && frames==0);
    mtk_bt_h4_reset(&decoder);
    assert(mtk_bt_h4_feed(&decoder,stream+8,11,NULL)==0 && frames==1);
    mtk_bt_h4_reset(&decoder);
    /* Event length one above a tight caller capacity; no payload consumed. */
    const uint8_t event[]={4,0x0e,9,4,0x0e,0};
    assert(mtk_bt_h4_feed(&decoder,event,sizeof(event),&consumed)==MTK_BT_H4_OVERSIZE);
    assert(consumed==3 && frames==1);
}
static void malformed_test(void) {
    uint8_t storage[16]; struct mtk_bt_h4 decoder;
    unsigned frames=0; size_t consumed;
    const uint8_t bad[]={0xff,4,0x0e,0};
    assert(mtk_bt_h4_init(&decoder,storage,sizeof(storage),ignore_frame,&frames)==0);
    assert(mtk_bt_h4_feed(&decoder,bad,sizeof(bad),&consumed)==MTK_BT_H4_INDICATOR);
    assert(consumed==1 && frames==0);
    assert(mtk_bt_h4_feed(&decoder,stream,8,&consumed)==MTK_BT_H4_INDICATOR);
    assert(consumed==0 && frames==0);
    mtk_bt_h4_reset(&decoder);
    assert(mtk_bt_h4_feed(&decoder,stream,8,NULL)==0 && frames==1);
    assert(mtk_bt_h4_finish(&decoder)==0);
    assert(mtk_bt_h4_feed(&decoder,NULL,0,NULL)==0);
    assert(mtk_bt_h4_feed(&decoder,NULL,1,NULL)==MTK_BT_H4_ARGUMENT);
    assert(mtk_bt_h4_init(NULL,storage,sizeof(storage),ignore_frame,&frames)==MTK_BT_H4_ARGUMENT);
    assert(mtk_bt_h4_init(&decoder,storage,4,ignore_frame,&frames)==MTK_BT_H4_ARGUMENT);
    mtk_bt_h4_reset(&decoder);
    assert(mtk_bt_h4_feed(&decoder,stream,8,NULL)==MTK_BT_H4_ARGUMENT);
}
static void truncated_test(void) {
    for(size_t length=1; length<8; length++) {
        uint8_t storage[16]; struct mtk_bt_h4 decoder; unsigned frames=0;
        assert(mtk_bt_h4_init(&decoder,storage,sizeof(storage),ignore_frame,&frames)==0);
        assert(mtk_bt_h4_feed(&decoder,stream,length,NULL)==0 && frames==0);
        assert(mtk_bt_h4_finish(&decoder)==MTK_BT_H4_TRUNCATED);
        assert(mtk_bt_h4_feed(&decoder,stream+length,8-length,NULL)==MTK_BT_H4_TRUNCATED);
        mtk_bt_h4_reset(&decoder);
        assert(mtk_bt_h4_feed(&decoder,stream,8,NULL)==0 && frames==1);
    }
}
static void wmt_test(void) {
    uint8_t output[12], status=0xff;
    const uint8_t command[]={1,0x6f,0xfc,6,1,6,2,0,0,1};
    memset(output,0xa5,sizeof(output));
    assert(mtk_bt_wmt_power_command(output+1,10,1)==0);
    assert(output[0]==0xa5 && output[11]==0xa5);
    assert(memcmp(output+1,command,10)==0);
    assert(mtk_bt_wmt_power_command(output+1,10,0)==0 && output[10]==0);
    assert(mtk_bt_wmt_power_command(output+1,9,0)==-1);
    assert(mtk_bt_wmt_power_command(output+1,10,2)==-1);
    assert(mtk_bt_wmt_power_command(NULL,10,1)==-1);
    assert(mtk_bt_wmt_power_event(stream,8,&status)==0 && status==0);
    for(size_t index=0; index<7; index++) {
        uint8_t event[9]; memcpy(event,stream,8); event[index]^=0x01;
        status=0xa5;
        assert(mtk_bt_wmt_power_event(event,8,&status)==-1 && status==0xa5);
    }
    for(size_t length=0; length<8; length++)
        assert(mtk_bt_wmt_power_event(stream,length,&status)==-1);
    assert(mtk_bt_wmt_power_event(stream,9,&status)==-1);
    uint8_t failure[8]; memcpy(failure,stream,8); failure[7]=0x03;
    assert(mtk_bt_wmt_power_event(failure,8,&status)==0 && status==3);
}

static void make_patch(uint8_t *image) {
    memset(image,0,128);
    memcpy(image,"2026111300000000ALPS",20);
    image[25]=0xbc; image[26]=5; image[27]=0xf0;
    image[28]=4;
    for(size_t i=48;i<128;i++) image[i]=(uint8_t)(i-47);
    image[46]=0xb9; image[47]=0x91; /* independent fixed checksum vector */
}
static void firmware_test(void) {
    uint8_t image[129]; struct mtk_bt_patch patch;
    make_patch(image);
    assert(mtk_bt_patch_checksum(image+48,80)==0x91b9);
    assert(mtk_bt_patch_checksum(image+48,79)==0xe1b9);
    assert(mtk_bt_patch_checksum(image,0)==0xffff);
    /* Vendor signed-high fold differs from a standard unsigned checksum once
     * the cumulative word sum crosses INT32_MAX. This is in real BT blobs. */
    uint8_t *large=malloc(100000); assert(large);
    memset(large,0xff,100000);
    assert(mtk_bt_patch_checksum(large,100000)==1);
    free(large);
    assert(mtk_bt_patch_parse(image,128,0x5bc00,80,0x7e0000,&patch)==0);
    assert(patch.emi_offset==0x5bc00 && patch.payload==image+48);
    assert(patch.payload_bytes==80 && patch.subsystem==4);
    assert(patch.firmware_address==0xf005bc00);
    assert(memcmp(patch.datetime,image,16)==0 && memcmp(patch.platform,"ALPS",4)==0);
    /* Output remains untouched after any failed validation. */
    struct mtk_bt_patch before=patch;
    for(size_t length=0;length<96;length++) {
        assert(mtk_bt_patch_parse(image,length,0,0x7e0000,0x7e0000,&patch)==MTK_BT_PATCH_TRUNCATED);
        assert(memcmp(&before,&patch,sizeof(patch))==0);
    }
    assert(mtk_bt_patch_parse(image,127,0,0x7e0000,0x7e0000,&patch)==MTK_BT_PATCH_CHECKSUM);
    image[48]^=1;
    assert(mtk_bt_patch_parse(image,128,0,0x7e0000,0x7e0000,&patch)==MTK_BT_PATCH_CHECKSUM);
    image[48]^=1;
    image[128]=0xa5;
    assert(mtk_bt_patch_parse(image,129,0,0x7e0000,0x7e0000,&patch)==MTK_BT_PATCH_CHECKSUM);
    assert(memcmp(&before,&patch,sizeof(patch))==0);
    image[46]=0xb9; image[47]=0xe1;
    assert(mtk_bt_patch_parse(image,127,0,0x7e0000,0x7e0000,&patch)==0);
}
static void range_test(void) {
    uint8_t image[128]; struct mtk_bt_patch patch;
    make_patch(image);
    assert(mtk_bt_patch_parse(image,128,0x5bc00,79,0x7e0000,&patch)==MTK_BT_PATCH_RANGE);
    assert(mtk_bt_patch_parse(image,128,0x5bd00,80,0x7e0000,&patch)==MTK_BT_PATCH_RANGE);
    assert(mtk_bt_patch_parse(image,128,0,0x5bc4f,0x5bc4f,&patch)==MTK_BT_PATCH_RANGE);
    assert(mtk_bt_patch_parse(image,128,SIZE_MAX,80,SIZE_MAX,&patch)==MTK_BT_PATCH_ARGUMENT);
    assert(mtk_bt_patch_parse(image,128,1,SIZE_MAX,SIZE_MAX,&patch)==MTK_BT_PATCH_ARGUMENT);
    assert(mtk_bt_patch_parse(image,128,0,0x7e0001,0x7e0000,&patch)==MTK_BT_PATCH_ARGUMENT);
    assert(mtk_bt_patch_parse(NULL,128,0,0x7e0000,0x7e0000,&patch)==MTK_BT_PATCH_ARGUMENT);
    assert(mtk_bt_patch_parse(image,128,0,0x7e0000,0x7e0000,NULL)==MTK_BT_PATCH_ARGUMENT);
    image[24]=0x11; /* vendor silently discards it; this parser rejects aliasing */
    assert(mtk_bt_patch_parse(image,128,0,0x7e0000,0x7e0000,&patch)==MTK_BT_PATCH_ADDRESS);
    image[24]=0; image[27]=0;
    assert(mtk_bt_patch_parse(image,128,0,0x7e0000,0x7e0000,&patch)==MTK_BT_PATCH_ADDRESS);
    image[27]=0xf0; image[25]=0xff; image[26]=0xff;
    assert(mtk_bt_patch_parse(image,128,0,0x7e0000,0x7e0000,&patch)==MTK_BT_PATCH_RANGE);
}
int main(int argc,char **argv) {
    assert(argc==2);
    if(!strcmp(argv[1],"splits")) split_test();
    else if(!strcmp(argv[1],"oversize")) oversize_test();
    else if(!strcmp(argv[1],"malformed")) malformed_test();
    else if(!strcmp(argv[1],"truncated")) truncated_test();
    else if(!strcmp(argv[1],"wmt")) wmt_test();
    else if(!strcmp(argv[1],"firmware")) firmware_test();
    else if(!strcmp(argv[1],"range")) range_test();
    else assert(0);
    return 0;
}
'''


class BluetoothTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("clang")
        if not compiler:
            raise unittest.SkipTest("clang is required")
        cls.temp = tempfile.TemporaryDirectory(prefix="mtk-bt-test-")
        directory = Path(cls.temp.name)
        harness = directory / "harness.c"
        harness.write_text(HARNESS)
        cls.executable = directory / "test"
        subprocess.run([
            compiler, "-std=c11", "-Wall", "-Wextra", "-Werror", "-g", "-O1",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
            "-I", str(ROOT / "xnu/wireless"), str(harness),
            str(ROOT / "xnu/wireless/mtk_bt_h4.c"),
            str(ROOT / "xnu/wireless/mtk_bt_firmware.c"),
            "-o", str(cls.executable),
        ], check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_case(self, case):
        result = subprocess.run([str(self.executable), case], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_all_fragment_boundaries_and_coalesced_frames(self):
        self.run_case("splits")

    def test_oversize_is_sticky_and_never_resynchronizes_into_payload(self):
        self.run_case("oversize")

    def test_unknown_indicator_invalid_arguments_and_reset(self):
        self.run_case("malformed")

    def test_end_of_stream_rejects_each_truncated_prefix(self):
        self.run_case("truncated")

    def test_strict_wmt_power_command_and_event(self):
        self.run_case("wmt")

    def test_firmware_checksum_truncation_and_odd_lengths(self):
        self.run_case("firmware")

    def test_firmware_rejects_aliasing_and_window_overflow(self):
        self.run_case("range")


if __name__ == "__main__":
    unittest.main()
