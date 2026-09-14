"""分歧点清单的判据守卫（见 pipeline/conflicts.py 文件头的实测与三条教训）。

判据都是「只在规则正确时才成立」的特征：把「1 路歧也点名」「孤证不单列」「给倾向措辞」
任一条改回去，都会有用例变红。素材取自 EBG-02ZL 实测（2026-08-24 金标裁决过的真实分歧）。
"""
from pathlib import Path

from pipeline import conflicts

# 五段真实形态：3路歧互异 / 3路歧一致 / 1路歧 / 孤证 / 全轨一致
P2 = """[00:51.6 - 00:56.0] [ELV] R: 主要是逆变器，还有储能箱。主要是这些吧。
                    [DB] /
                    [FA] 主要是逆变器还有储藏箱主要是这些吧
                    [XF] 主要是逆变器还有收藏箱主要是这些吧

[02:15.7 - 02:29.0] [ELV] M: 还有这种响应的即时性，您觉得怎么样？
                    [DB] 还有这种响应的及时性您觉得怎么样
                    [FA] 还有这种响应的及时性您觉得怎么样
                    [XF] 还有这种响应的及时性您觉得怎么样

[05:39.1 - 05:44.0] [ELV] R: 即使他们能分析到一些原因，可能也不是主因。
                    [DB] 其实他们能分析到一些原因可能也不是主因
                    [FA] 即使他们能分析到一些原因可能也不是主因
                    [XF] 即使他们能分析到一些原因可能也不是主因

[25:07.2 - 25:10.0] [ELV] R: 预审的这些备一部分，着急嘛。
                    [DB] /
                    [FA] /
                    [XF] /

[10:38.3 - 10:43.0] [ELV] R: 我们这边是牧光互补嘛，是吧？
                    [DB] 我们这边是牧光互补嘛是吧
                    [FA] 我们这边是牧光互补嘛是吧
                    [XF] 我们这边是牧光互补嘛是吧

[03:08.0 - 03:09.0] [ELV] M: 嗯。
                    [DB] 呃
                    [FA] 啊
                    [XF] 哦
"""


def test_两路以上分歧才点名_一路歧不点():
    conf, _ = conflicts.analyze(P2)
    times = [t for t, _, _ in conf]
    assert "00:51.6" in times      # 3 路歧·参考互异（储能/储藏/收藏）
    assert "02:15.7" in times      # 3 路歧·参考一致（及时性 3:1）——做减法会漏掉它，实测更差
    assert "05:39.1" not in times  # 1 路歧：只有 DB 反对，多半是它自己错
    assert "10:38.3" not in times  # 全轨一致，无分歧


def test_孤证单独成节_不混进分歧表():
    conf, lone = conflicts.analyze(P2)
    assert [t for t, _ in lone] == ["25:07.2"]          # 参考轨全空
    assert "25:07.2" not in [t for t, _, _ in conf]     # 不重复出现在分歧表


def test_短句分歧不点名():
    """「嗯/呃/啊/哦」这种分歧没有判读价值，点名只会稀释注意力。"""
    conf, lone = conflicts.analyze(P2)
    assert "03:08.0" not in [t for t, _, _ in conf] + [t for t, _ in lone]


def test_清单里不出现任何倾向措辞():
    """上游 shape 版实测教训：给「通常该信主轨」这类先验，模型会照着偷懒（静默漏 3→13）。
    清单只陈述事实，判据顺序写在提示里，倾向一个字都不给。"""
    sec = conflicts.build_section(P2)
    for banned in ("通常保留", "倾向", "多半是", "建议采用", "各半", "更可信"):
        assert banned not in sec, f"清单里混入了倾向措辞：{banned}"
    assert "主轨只算平等一票" in sec
    assert "禁止进入终稿正文" in sec      # 防止模型把这张表抄进稿子


def test_全是回落串的段落算孤证():
    """【2026-08-26 订正了 08-25 的结论，两次都用同一份真实素材】

    素材是 32:37.6 那一处。08-25 的判断是：三路参考轨「都听清了」，所以不是孤证 → 于是
    回落串被算作「在场」。**那个判断是错的**，理由有二：

    ① 它们听到的是「跳槽 / 辞职不干」，主轨写的是「他撤出了」——**它们不是在佐证主轨，
       是在反对主轨**。孤证这一类要防的正是「主轨这句没有任何佐证，界面上却看不出来」。
    ② CLAUDE.md 记着「跳槽 ↔ 他撤出了」是 ELV 整档直传那轮**稳定改对的三处之一**，
       即这里主轨确实错了。当年它既没进分歧表（回落串不计票）、也没进孤证表（被算在场），
       **两张表都进不去，被静默丢掉了**。

    ⇒ 规则统一为：**不可靠到不能参与计票的证据，也不该被算作佐证。**
    """
    p2 = """[32:37.6 - 32:38.8] [ELV] R: 他撤，他撤出了。
                    [DB] ⟨未精确对齐⟩ 同事嗯他确他确实
                    [FA] ⟨未精确对齐⟩ 同事嗯他跳他跳槽了嗯就辞职不干
                    [XF] ⟨未精确对齐⟩ 同事嗯他跳他跳槽了就是辞职不干了
"""
    conf, lone = conflicts.analyze(p2)
    assert [t for t, _ in lone] == ["32:37.6"], "全是回落串＝没有可比对的证据，必须算孤证"
    assert conf == [], "回落串仍然不参与分歧计票（边界不可信，逐字比必然不同）"


def test_只要有一路给得出可比对的证据就不算孤证():
    """反向：别把判据收得太狠。有一路真内容在，就不是「无人佐证」。"""
    p2 = """[32:37.6 - 32:38.8] [ELV] R: 他撤，他撤出了。
                    [DB] ⟨未精确对齐⟩ 同事嗯他确他确实
                    [FA] 他跳槽了就辞职不干
                    [XF] ⟨未精确对齐⟩ 同事嗯他跳他跳槽了就是辞职不干了
"""
    conf, lone = conflicts.analyze(p2)
    assert lone == [], "有一路可比对的证据在，不该判孤证"


def test_未精确对齐的参考轨不参与分歧计票():
    """串里混着邻居的话，逐字比必然不同——计进去等于凭空造一批假分歧，把真分歧稀释掉。"""
    p2 = """[10:00.0 - 10:05.0] [ELV] R: 我们这边是牧光互补嘛，是吧？
                    [DB] ⟨未精确对齐⟩ 端子那个头就给我碰折了我们这边是牧光互补嘛是吧碰断了以后
                    [FA] ⟨未精确对齐⟩ 端子那个头就给我碰折了我们这边是牧光互补嘛是吧碰断了
                    [XF] 我们这边是牧光互补嘛是吧
"""
    conf, lone = conflicts.analyze(p2)
    assert conf == [], "未精确对齐的两路被算成了反对票，虚增分歧"


def test_未精确对齐与真空要能区分开():
    """两者以前长得一模一样（都是 `/`），这正是当初查不出问题的原因。"""
    p2 = """[01:00.0 - 01:05.0] [ELV] R: 主要是逆变器还有数采箱。
                    [DB] /
                    [FA] /
                    [XF] /
"""
    _, lone = conflicts.analyze(p2)
    assert [t for t, _ in lone] == ["01:00.0"], "真的全空反而不报孤证了"


def test_无分歧时返回空串():
    clean = """[00:01.0 - 00:05.0] [ELV] M: 我们这边是牧光互补嘛，是吧？
                    [DB] 我们这边是牧光互补嘛是吧
                    [FA] 我们这边是牧光互补嘛是吧
"""
    assert conflicts.build_section(clean) == ""


def test_副本同名不同目录_原文件不动(tmp_path):
    """产物名由输入 base 名决定，改名会连带改掉产物名；原 P2 要留给 DeepSeek 兜底路。"""
    src = tmp_path / "zh_x_P2_Match_0824_1222.md"
    src.write_text(P2, encoding="utf-8")
    out = Path(conflicts.augmented_input(src, tmp_path / "_p3in"))
    assert out.name == src.name and out.parent.name == "_p3in"
    assert src.read_text(encoding="utf-8") == P2                       # 原文件一字未动
    assert out.read_text(encoding="utf-8").endswith(P2)                # 副本 = 清单 + 原文
    assert "分歧点清单" in out.read_text(encoding="utf-8")


def test_无分歧或异常时回落原文件(tmp_path):
    """增强坏了不许连累转录。"""
    src = tmp_path / "clean_P2_Match.md"
    src.write_text("[00:01.0 - 00:05.0] [ELV] M: 只有主轨没有参考轨的稿子。\n", encoding="utf-8")
    # 这份只有主轨、无参考轨行 → 走孤证分支，仍应产出副本
    assert Path(conflicts.augmented_input(src, tmp_path / "_p3in")).exists()
    # 文件不存在 → 回落原路径，不抛异常
    missing = tmp_path / "nope.md"
    assert conflicts.augmented_input(missing, tmp_path / "_p3in") == str(missing)
