# -*- coding: utf-8 -*-
"""MzS 封装解密器（E-mote / M2 engine 游戏资源通用）。

容器布局（docs/mzs-container-format.md）：
  0x00  "mzs\\0"
  0x04  uint32 LE  解压后载荷大小
  0x08  MDF 加密的 Zstd 帧
MDF = MT19937（init_by_array 播种，种子为 MD5(key) 的 4 个 LE uint32）
      输出字节流前 131 字节循环，与密文逐字节异或；加密区从偏移 8 开始。
完整密钥 = 基础密钥串 + 文件全名（含扩展名）。

**本工具不内置任何厂商密钥。** 基础密钥需用户从自己合法持有的游戏中提取，
请阅读 docs/finding-your-base-key.md。提供方式（任选其一，优先级从高到低）：

  1. 命令行参数   --key <BASE_KEY>
  2. 环境变量     MZS_BASE_KEY
  3. 本地配置文件 mzs_key.txt（与脚本同目录，内容仅一行基础密钥串；
     该文件已被 .gitignore 排除，切勿提交到版本库）

用法:
  python tools/mzs_decrypt.py <in.psb.m> [out.psb]
"""
import hashlib, struct, sys, os

MDF_PERIOD = 131
N = 624
M = 397
MATRIX_A = 0x9908b0df
UPPER = 0x80000000
LOWER = 0x7fffffff


def load_base_key(argv):
    """从 --key 参数 / MZS_BASE_KEY 环境变量 / mzs_key.txt 读取基础密钥。"""
    if '--key' in argv:
        i = argv.index('--key')
        if i + 1 >= len(argv):
            raise SystemExit('--key 需要一个参数')
        argv.pop(i)
        argv.pop(i)
        return argv[i]
    env = os.environ.get('MZS_BASE_KEY')
    if env:
        return env.strip()
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mzs_key.txt')
    if os.path.isfile(cfg):
        with open(cfg, 'r', encoding='utf-8-sig') as f:
            key = f.read().strip()
        if key:
            return key
    raise SystemExit(
        '未提供基础密钥。本工具不内置厂商密钥；请用 --key 参数、MZS_BASE_KEY '
        '环境变量或本地 mzs_key.txt 提供（提取方法见 docs/finding-your-base-key.md）。'
    )


class MT:
    """mt19937ar，init_by_array 播种。"""

    def __init__(self, key_uint32s):
        self.mt = [0] * N
        self.mt[0] = 19650218
        for j in range(1, N):
            self.mt[j] = (1812433253 * (self.mt[j - 1] ^ (self.mt[j - 1] >> 30)) + j) & 0xFFFFFFFF
        i, j, k = 1, 0, max(N, len(key_uint32s))
        while k:
            self.mt[i] = ((self.mt[i] ^ ((self.mt[i - 1] ^ (self.mt[i - 1] >> 30)) * 1664525))
                          + key_uint32s[j] + j) & 0xFFFFFFFF
            i += 1
            j += 1
            if i >= N:
                self.mt[0] = self.mt[N - 1]
                i = 1
            if j >= len(key_uint32s):
                j = 0
            k -= 1
        for k in range(N - 1, 0, -1):
            self.mt[i] = ((self.mt[i] ^ ((self.mt[i - 1] ^ (self.mt[i - 1] >> 30)) * 1566083941)) - i) & 0xFFFFFFFF
            i += 1
            if i >= N:
                self.mt[0] = self.mt[N - 1]
                i = 1
        self.mt[0] = UPPER
        self.idx = N

    def next_int(self):
        if self.idx >= N:
            mt = self.mt
            for kk in range(N - M):
                y = (mt[kk] & UPPER) | (mt[kk + 1] & LOWER)
                mt[kk] = mt[kk + M] ^ (y >> 1) ^ (MATRIX_A if y & 1 else 0)
            for kk in range(N - M, N - 1):
                y = (mt[kk] & UPPER) | (mt[kk + 1] & LOWER)
                mt[kk] = mt[kk + (M - N)] ^ (y >> 1) ^ (MATRIX_A if y & 1 else 0)
            y = (mt[N - 1] & UPPER) | (mt[0] & LOWER)
            mt[N - 1] = mt[M - 1] ^ (y >> 1) ^ (MATRIX_A if y & 1 else 0)
            self.idx = 0
        y = self.mt[self.idx]
        self.idx += 1
        y ^= y >> 11
        y ^= (y << 7) & 0x9d2c5680
        y ^= (y << 15) & 0xefc60000
        y ^= y >> 18
        return y & 0xFFFFFFFF


def mdf_keystream(key_string, nbytes):
    seed = hashlib.md5(key_string.encode('utf-8')).digest()
    words = list(struct.unpack('<4I', seed))
    mt = MT(words)
    raw = bytearray()
    while len(raw) < nbytes:
        raw += struct.pack('<I', mt.next_int())
    return bytes(raw[:nbytes])


def mdf_crypt(data, key_string):
    """131 字节周期 MDF 异或（加密=解密）。"""
    ks = mdf_keystream(key_string, MDF_PERIOD)
    return bytes(b ^ ks[i % MDF_PERIOD] for i, b in enumerate(data))


def mzs_decrypt(data, key_string):
    if data[:4] != b'mzs\x00':
        raise ValueError('not an mzs file: %r' % data[:4])
    payload_size = struct.unpack('<I', data[4:8])[0]
    payload = mdf_crypt(data[8:], key_string)
    try:
        import zstandard
        out = zstandard.ZstdDecompressor().decompress(payload, max_output_size=payload_size + 65536)
    except ImportError:
        import zstd
        out = zstd.decompress(payload)
    if len(out) != payload_size:
        print(f'[warn] payload size {len(out)} != header {payload_size}')
    return out


def mzs_encrypt(psb_data, key_string):
    """回封（供 PsBuild 回包流程参考）：Zstd 压缩 + MDF 异或 + 头。"""
    try:
        import zstandard
        comp = zstandard.ZstdCompressor().compress(psb_data)
    except ImportError:
        import zstd
        comp = zstd.compress(psb_data)
    return b'mzs\x00' + struct.pack('<I', len(psb_data)) + mdf_crypt(comp, key_string)


def main(argv):
    base_key = load_base_key(argv)
    if len(argv) < 2:
        print(__doc__)
        return 1
    src = argv[1]
    name = os.path.basename(src)
    key = base_key + name
    data = open(src, 'rb').read()
    out = mzs_decrypt(data, key)
    dst = argv[2] if len(argv) > 2 else os.path.splitext(src)[0] + '.dec.psb'
    open(dst, 'wb').write(out)
    # 不打印完整密钥，避免密钥泄漏到 shell 历史 / 日志
    print(f'{src} -> {dst} ({len(data)} -> {len(out)} bytes)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
