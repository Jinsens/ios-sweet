"""Synthetic FDT compatible-walker regressions; no phone or private DT dump."""
import ctypes
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest


def fdt(children):
    strings = b'compatible\0reg\0'
    pad = lambda b: b + bytes((-len(b)) % 4)
    u32 = lambda v: struct.pack('>I', v)
    def prop(name, value):
        return u32(3) + u32(len(value)) + u32(strings.index(name.encode()+b'\0')) + pad(value)
    def node(name, props=(), nested=()):
        return u32(1) + pad(name.encode()+b'\0') + b''.join(
            u32(4) if p is None else prop(*p) for p in props) + b''.join(nested) + u32(2)
    body = node('', nested=[node(*c) for c in children]) + u32(9)
    offset = 56
    total = offset + len(body) + len(strings)
    return struct.pack('>10I', 0xd00dfeed, total, offset, offset+len(body), 40, 17, 16, 0, len(strings), len(body)) + bytes(16) + body + strings


class FDTCompatibleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cc = shutil.which('clang') or shutil.which('cc')
        if not cc:
            raise unittest.SkipTest('host C compiler not installed')
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name)/'fdt.dylib'
        source = Path(__file__).resolve().parents[2]/'shim/fdt.c'
        subprocess.run([cc, '-shared', '-fPIC', '-O1', str(source), '-o', str(path)], check=True)
        cls.lib = ctypes.CDLL(str(path))
        cls.lib.fdt_find_compatible.argtypes = [ctypes.c_size_t,ctypes.c_char_p,ctypes.c_char_p,ctypes.c_int]
        cls.lib.fdt_prop.argtypes = [ctypes.c_size_t,ctypes.c_char_p,ctypes.c_char_p,ctypes.c_void_p,ctypes.c_void_p]
        cls.lib.fdt_reg_n.argtypes = [ctypes.c_size_t,ctypes.c_char_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def lookup(self, blob, compatible, capacity=160):
        buf=ctypes.create_string_buffer(blob); out=ctypes.create_string_buffer(max(1,capacity))
        rc=self.lib.fdt_find_compatible(ctypes.addressof(buf),compatible.encode(),out,capacity)
        return rc,out.value

    def test_first_property_consumes_token(self):
        blob=fdt([('dsi@14017000',[('compatible',b'mediatek,dsi0\0mediatek,mt6983-dsi\0')])])
        self.assertEqual(self.lookup(blob,'mediatek,dsi0'),(1,b'/dsi@14017000'))
        self.assertEqual(self.lookup(blob,'mediatek,mt6983-dsi'),(1,b'/dsi@14017000'))

    def test_property_after_reg_and_nop(self):
        blob=fdt([('dsi',[('reg',struct.pack('>4I',0,0x14017000,0,0x1000)),None,('compatible',b'test,dsi\0')])])
        self.assertEqual(self.lookup(blob,'test,dsi'),(1,b'/dsi'))

    def test_unterminated_compatible_rejected(self):
        self.assertEqual(self.lookup(fdt([('bad',[('compatible',b'test,dsi')])]),'test,dsi')[0],0)

    def test_short_output_refuses_truncation(self):
        blob=fdt([('dsi',[('compatible',b'test,dsi\0')])])
        self.assertEqual(self.lookup(blob,'test,dsi',4)[0],0)
        self.assertEqual(self.lookup(blob,'test,dsi',0)[0],0)

    def test_absent_compatible(self):
        self.assertEqual(self.lookup(fdt([('a',[('compatible',b'test,a\0')])]),'test,b')[0],0)

    def test_shallow_target_does_not_read_uninitialized_components(self):
        blob=fdt([('a',[('compatible',b'test,a\0')])]); buf=ctypes.create_string_buffer(blob)
        self.assertEqual(self.lib.fdt_prop(ctypes.addressof(buf),b'/',b'missing',None,None),0)

    def test_multi_reg_mutex_uses_index(self):
        blob=fdt([('mutex',[('reg',struct.pack('>8I',0,0x14001000,0,0x1000,0,0x14401000,0,0x1000))])]);buf=ctypes.create_string_buffer(blob)
        for i,expected in enumerate((0x14001000,0x14401000)):
            a,b=ctypes.c_uint64(),ctypes.c_uint64()
            self.assertEqual(self.lib.fdt_reg_n(ctypes.addressof(buf),b'/mutex',i,ctypes.byref(a),ctypes.byref(b)),1)
            self.assertEqual((a.value,b.value),(expected,0x1000))


if __name__=='__main__':unittest.main()
