"""访谈大纲文件 → 纯文本（2026-08-19）。

只管 **.docx**：`.txt` / `.md` 由前端直接读，不上传——大纲里常有还没公开的公司名与
受访者姓名，能不过网就不过网。.docx 必须解析，才走这一趟。

用的是导出 docx 时就已经装好的 python-docx（`requirements.txt` 里早有），**零新依赖**。
不支持老的 `.doc`：那是另一种二进制格式，python-docx 读不了，要拖进 LibreOffice 那一套。

⚠️ 段落与表格必须**按文档里的原始顺序**取。先取全部段落再取全部表格是常见写法，
但大纲里的表格往往夹在小节中间（「第二部分：渠道 / 表格：待确认的名词」），
顺序一乱，模型读到的上下文就跟原件对不上了。
"""
from __future__ import annotations

import io

MAX_BYTES = 2 * 1024 * 1024          # 大纲不会更大；超了多半是传错了文件
SUPPORTED_EXTS = (".docx",)


class OutlineReadError(Exception):
    """文档打不开或读不出文字（上层转 422）。

    ⚠️ **带的是错误码不是中文句子**（2026-08-30）：这句话要用界面语言显示给用户，
    而文案在前端。这里只说「是哪一种失败」。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _iter_blocks(doc):
    """按 body 里的原始顺序产出段落与表格。"""
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, doc)
        elif child.tag == qn("w:tbl"):
            yield Table(child, doc)


def _table_lines(table) -> list[str]:
    """表格拉平成一行一行。单元格之间用制表符——比空格更能让「两列」看出是两列，
    而模型对制表符的容忍度比对重建 Markdown 表格好。整行空白的丢掉。"""
    out = []
    for row in table.rows:
        cells = [(c.text or "").strip() for c in row.cells]
        line = "\t".join(cells).strip()
        if line:
            out.append(line)
    return out


def read_docx(data: bytes) -> str:
    """.docx 字节 → 纯文本。图片、批注、页眉页脚不在返回内容里
    （python-docx 的 body 遍历本来就取不到它们，这正合我们的意）。"""
    try:
        from docx import Document
        doc = Document(io.BytesIO(data))
    except Exception as e:                      # 空文件、假扩展名、加密文档都落这里
        raise OutlineReadError("outline_bad_docx") from e

    lines: list[str] = []
    try:
        for block in _iter_blocks(doc):
            if hasattr(block, "rows"):          # Table
                lines.extend(_table_lines(block))
            else:                               # Paragraph
                t = (block.text or "").strip()
                if t:
                    lines.append(t)
    except Exception as e:
        raise OutlineReadError("outline_unreadable") from e

    return "\n".join(lines)


def read(filename: str, data: bytes) -> str:
    """按扩展名分派。目前只有 .docx 一条路，留着这层是为了以后加格式时
    调用方（api.py）不用跟着改。"""
    name = (filename or "").lower()
    if not name.endswith(SUPPORTED_EXTS):
        raise OutlineReadError("outline_not_docx")
    return read_docx(data)
