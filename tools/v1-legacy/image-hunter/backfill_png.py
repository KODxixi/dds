#!/usr/bin/env python3
"""把 ArchLib 案例目录(10_~80_)下的 jpg/jpeg/webp/bmp 批量转 PNG 并删除原文件。
gif 动图保留不转。在本机原生运行（远比沙箱挂载快）。
用法: python backfill_png.py            # 转 D:\\ArchLib 下 [1-8]0_* 案例目录
      python backfill_png.py D:\\其它路径
"""
import os, sys, glob, time
try:
    from PIL import Image
except ImportError:
    print("需先 pip install pillow"); sys.exit(1)

ROOT = sys.argv[1] if len(sys.argv) > 1 else r"D:\ArchLib"
EXTS = (".jpg", ".jpeg", ".webp", ".bmp")   # 不含 .gif（动图保留）

def collect(root):
    fs = []
    dirs = glob.glob(os.path.join(root, "[1-8]0_*")) or [root]
    for d in dirs:
        for dp, _, fns in os.walk(d):
            for f in fns:
                if f.lower().endswith(EXTS):
                    fs.append(os.path.join(dp, f))
    return fs

def main():
    files = collect(ROOT)
    print(f"待转换: {len(files)} 张  根目录: {ROOT}")
    ok = err = 0; t0 = time.time()
    for p in files:
        base, _ = os.path.splitext(p)
        out = base + ".png"
        if os.path.exists(out):
            out = base + "_conv.png"
        try:
            with Image.open(p) as im:
                if im.mode in ("P", "LA"): im = im.convert("RGBA")
                elif im.mode == "CMYK":    im = im.convert("RGB")
                im.save(out, "PNG")
            os.remove(p); ok += 1
            if ok % 200 == 0:
                print(f"  ...{ok} 张  ({ok/(time.time()-t0):.0f}/s)")
        except Exception as e:
            err += 1; print(f"  ⚠️ {p}: {e}")
    print(f"完成: 成功 {ok}  失败 {err}  用时 {time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()
