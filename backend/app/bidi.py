"""从右往左语种（阿拉伯语等）的导出处理。

**为什么单独一个模块**：转录导出（txt/docx）与后处理产物下载三处都要用，写三份必漂移；
而漂移的症状是「有的下载对、有的下载不对」，不报错。

⚠️ 前端有一份同口径的实现（`src/lib/bidi.ts`）。

说话人标签**不在这里**——它在 `app/speaker_labels.py`（2026-08-30 搬走）。两件事凑在一个
模块里只是因为最初一起做，实际毫无关系：方向由**录音语种**决定，标签由**界面语言**决定。
放一起会让人以为标签也该跟录音走。

---
## 方向

纯文本没有「属性」可设，只能靠 Unicode 的方向控制字符。**四种写法实测过（2026-08-29，
真 .txt + 真实文本查看器），只有 RLI 有效**：

| 写法 | 性质 | 查看器把方向锁成 LTR 时 |
|---|---|---|
| RLM `U+200F` | **提示**：装成隐形的阿拉伯字母，供查看器自己判方向时参考 | ❌ 查看器不判断 → 白放 |
| RLE…PDF `U+202B…202C` | **嵌入**：老一代强制写法 | ❌ 已被 Unicode 取代，实测被忽略 |
| **RLI…PDI `U+2067…2069`** | **隔离**：把这一段圈起来单独声明方向 | ✅ **照样生效** |

⚠️ **别改回 RLM**——它看起来更"标准"（很多文章推荐它），但它只是个提示。
判据是「查看器锁死方向时还灵不灵」，不是「哪个字符名字听起来更对」。

⚠️ 字符顺序修好了，但**整块文字的对齐管不了**——纯文本没有对齐这个概念，那归查看器。
要含右对齐的百分之百可控，只有 .docx（见 export.py 的 `_set_rtl`）。
"""

# 27 门里只有 ar 是 RTL；he/fa/ur 先列上，将来加语种不用回头改这里。
RTL_LANGS = frozenset({"ar", "he", "fa", "ur"})

RLI = "⁧"   # RIGHT-TO-LEFT ISOLATE
PDI = "⁩"   # POP DIRECTIONAL ISOLATE


def is_rtl(lang: str | None) -> bool:
    """录音语种是不是从右往左。`zh-CN` 这种带地区码的取前缀。"""
    return (lang or "").split("-")[0].strip().lower() in RTL_LANGS


def isolate(line: str) -> str:
    """把一行圈进 RTL 隔离区。空行原样返回——给空行加控制字符只会让文件里多两个隐形字符。"""
    return f"{RLI}{line}{PDI}" if line.strip() else line


def isolate_text(text: str, lang: str | None) -> str:
    """整份纯文本逐行加隔离；非 RTL 语种原样返回（零影响）。"""
    if not is_rtl(lang):
        return text
    return "\n".join(isolate(ln) for ln in text.split("\n"))
