#!/usr/bin/env python3
"""Embed a SWEETXNU payload after shim.ld's _end, outside both ramdisks."""
import argparse
from pathlib import Path
import struct
from typing import Optional

MARKER = b'SWEETAPP'
PAYLOAD_MAGIC = b'SWEETXNU'
IMAGE_HEADER_SIZE_OFFSET = 16
IMAGE_MAGIC_OFFSET = 56
MIN_PAYLOAD_HEADER = 0x2a8


def embed(image: bytes, payload: bytes, max_image_size: Optional[int] = None) -> bytes:
    if len(image) % 4096 or len(image) < 64:
        raise ValueError('shim Image must end at a 4 KiB-aligned _end')
    if image[IMAGE_MAGIC_OFFSET:IMAGE_MAGIC_OFFSET + 4] != b'ARM\x64':
        raise ValueError('not an arm64 Linux Image')
    if struct.unpack_from('<Q', image, IMAGE_HEADER_SIZE_OFFSET)[0] != len(image):
        raise ValueError('shim Image header size does not match _end')
    if len(payload) < MIN_PAYLOAD_HEADER or not payload.startswith(PAYLOAD_MAGIC):
        raise ValueError('invalid SWEETXNU payload')
    result = bytearray(image)
    result += MARKER + struct.pack('<Q', len(payload)) + payload
    if max_image_size is not None and len(result) > max_image_size:
        raise ValueError('embedded image would overlap the measured DTB load window')
    struct.pack_into('<Q', result, IMAGE_HEADER_SIZE_OFFSET, len(result))
    return bytes(result)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('image', type=Path)
    ap.add_argument('payload', type=Path)
    ap.add_argument('--max-image-size', type=lambda x: int(x, 0))
    args = ap.parse_args()
    payload = args.payload.read_bytes()
    output = embed(args.image.read_bytes(), payload, args.max_image_size)
    args.image.write_bytes(output)
    print(f'kernel: embedded SWEETXNU payload {len(payload)} bytes, image {len(output)} bytes')


if __name__ == '__main__':
    main()
