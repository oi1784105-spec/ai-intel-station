"""配图提取与 URL 校验测试。

断言分两类，两类都必要：

- **合成片段**测边界（引号变体、`data:` 注入、超长 URL、追踪像素、扫描上限），
  这些分支很难恰好都在真实 feed 里出现；
- **真实样本**测主干 —— `tests/fixtures/replay/` 存的是 2026-09-16 抓回来的原样响应。
  其中「配图在 `content:encoded` 而不是 `description` 里」这条是实际踩到的坑：
  首版实现只扫 `description`，爱范儿 / 钛媒体 / The Verge 三个源的配图**全部静默丢失**
  且不报任何错。用真实样本把这条锁死，避免以后重构取数逻辑时再次漏图。
"""

from __future__ import annotations

from pathlib import Path

import feedparser
import pytest

from pipeline import config, store
from pipeline.adapters import _entry_html, _entry_image
from pipeline.models import RawItem
from pipeline.normalize import normalize_batch
from pipeline.parse import clean_image_url, find_first_image

from helpers import NOW, make_article

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "replay"


def _entries(name: str) -> list:
    """读取真实样本并交给 feedparser 解析，与线上取数路径一致。"""
    return feedparser.parse(FIXTURES / f"{name}.xml").entries


# --------------------------------------------------------------------------- #
# clean_image_url：协议白名单与规范化
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://a.example/x.jpg", "https://a.example/x.jpg"),
        ("  https://a.example/x.jpg  ", "https://a.example/x.jpg"),
        # 协议相对地址在 feed 里很常见，不补全就无法加载
        ("//cdn.example.com/x.jpg", "https://cdn.example.com/x.jpg"),
        # XML 实体未还原会带着 `&amp;` 去请求，必然 404
        ("https://a.example/x.jpg?a=1&amp;b=2", "https://a.example/x.jpg?a=1&b=2"),
        ('"https://a.example/x.jpg"', "https://a.example/x.jpg"),
    ],
)
def test_clean_image_url_normalizes(raw: str, expected: str) -> None:
    assert clean_image_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        # `data:` 会把图片塞进 HTML 本体，单文件产物会因此膨胀几百 KB
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==",
        "javascript:alert(1)",  # 注入向量
        "ftp://a.example/x.jpg",
        "/relative/path.jpg",  # 相对路径无法定位，宁可不用
    ],
)
def test_clean_image_url_rejects_unusable(raw: str | None) -> None:
    assert clean_image_url(raw) is None


def test_clean_image_url_rejects_overlong() -> None:
    """超长 CDN 签名地址直接丢弃，避免产物里混进异常数据。"""
    assert clean_image_url("https://a.example/" + "x" * config.IMAGE_URL_MAX_CHARS) is None


# --------------------------------------------------------------------------- #
# find_first_image：从正文 HTML 里挑第一张像样的图
# --------------------------------------------------------------------------- #

def test_find_first_image_skips_decorations_and_takes_first_real() -> None:
    """正文里的头像、占位像素、logo 都不是配图，必须跳过后再取第一张真图。"""
    html = (
        '<p><img src="https://a.example/avatar/u1.png"></p>'
        '<p><img src="https://a.example/spacer.gif"></p>'
        '<p><img src="https://a.example/logo-brand.png"></p>'
        '<p><img src="https://a.example/real.jpg"></p>'
        '<p><img src="https://a.example/real2.jpg"></p>'
    )
    assert find_first_image(html) == "https://a.example/real.jpg"


def test_find_first_image_skips_feed_template_image() -> None:
    """钛媒体给部分条目挂的 RSS 模板图与正文无关，必须跳过它取后面的真图。

    展示一张与文章无关的图比不展示更糟 —— 读者会以为那就是配图。
    """
    html = (
        '<img src="https://images.tmtpost.com/uploads/images/2023/tkrss/2y56jutyh3.png">'
        '<img src="https://images.tmtpost.com/uploads/images/2026/09/16/8aa828f3.jpeg">'
    )
    assert (
        find_first_image(html)
        == "https://images.tmtpost.com/uploads/images/2026/09/16/8aa828f3.jpeg"
    )


@pytest.mark.parametrize(
    "tag",
    [
        '<img src="https://a.example/q.jpg">',
        "<img src='https://a.example/q.jpg'>",
        "<img src=https://a.example/q.jpg>",
        '<img data-src="https://a.example/q.jpg">',
        '<img src="https://a.example/placeholder.png" data-original="https://a.example/q.jpg">',
    ],
)
def test_find_first_image_handles_attribute_variants(tag: str) -> None:
    """各源输出风格不一：单引号、无引号、懒加载 `data-src` 都要能取到。"""
    assert find_first_image(tag) == "https://a.example/q.jpg"


def test_find_first_image_returns_none_on_empty() -> None:
    assert find_first_image(None) is None
    assert find_first_image("") is None
    assert find_first_image("<p>纯文本，没有图片。</p>") is None


def test_find_first_image_honours_scan_cap() -> None:
    """扫描上限刻意存在：正文常含十几张装饰图，无上限会为找配图付出无谓成本。"""
    filler = "".join(
        f'<img src="https://a.example/logo-{index}.png">'
        for index in range(config.IMAGE_SCAN_MAX_TAGS)
    )
    assert find_first_image(filler + '<img src="https://a.example/real.jpg">') is None


# --------------------------------------------------------------------------- #
# _entry_image：按载体优先级取图（全部只读 feed 载荷，零额外请求）
# --------------------------------------------------------------------------- #

def test_entry_image_prefers_media_rss_over_body_html() -> None:
    """Media RSS 是显式声明，可信度高于正文 HTML，应优先采用。"""
    entry = {
        "media_content": [{"url": "https://a.example/media.jpg", "type": "image/jpeg"}],
        "summary": '<img src="https://a.example/body.jpg">',
    }
    assert _entry_image(entry) == "https://a.example/media.jpg"


def test_entry_image_ignores_audio_enclosure() -> None:
    """`enclosure` 同样用于播客音视频，`type` 非图片时必须丢弃。"""
    entry = {"enclosures": [{"href": "https://a.example/ep-42.mp3", "type": "audio/mpeg"}]}
    assert _entry_image(entry) is None


def test_entry_image_reads_content_encoded_when_description_has_none() -> None:
    """**回归测试（真实踩到的坑）**：配图写在 `content:encoded`，`description` 只有导语。

    feedparser 把 `<content:encoded>` 放进 `entry.content[0].value`；只扫
    `description` 会让爱范儿 / 钛媒体 / The Verge 的配图全部丢失且不报错。
    """
    entry = {
        "description": "只有几十字导语，正文与配图都不在这里。",
        "content": [{"value": '<figure><img src="https://a.example/hero.jpg"></figure>'}],
    }
    assert _entry_image(entry) == "https://a.example/hero.jpg"


def test_entry_html_merges_summary_and_content() -> None:
    """合并取数是上面那条回归的实现基础，两个字段都要在里面。"""
    entry = {"description": "导语", "content": [{"value": "正文"}]}
    merged = _entry_html(entry)
    assert "导语" in merged
    assert "正文" in merged


@pytest.mark.parametrize(
    ("fixture", "minimum"),
    [
        # Media RSS 载体：实测 15 条全部带图
        ("google-ai", 10),
        # 以下三个的配图**只在 `content:encoded` 里**，正是首版实现漏掉的那批
        ("ifanr", 1),
        ("tmtpost", 1),
        ("verge-ai", 1),
        ("leiphone", 1),
    ],
)
def test_real_fixtures_yield_images(fixture: str, minimum: int) -> None:
    """对真实响应断言确实能取到图 —— 宁可少取，也不能静默地一张都取不到。"""
    found = sum(1 for entry in _entries(fixture) if _entry_image(entry))
    assert found >= minimum, f"{fixture} 只取到 {found} 张配图"


def test_real_fixture_image_is_absolute_https() -> None:
    """取到的地址必须是可直接给浏览器用的绝对地址（相对协议已补全为 https）。"""
    urls = [url for entry in _entries("ifanr") if (url := _entry_image(entry))]
    assert urls
    for url in urls:
        assert url.startswith("https://")


def test_every_source_has_english_display_name() -> None:
    """源展示名全站统一为英文形态 —— 界面只读 `name_en`，缺一个就会漏出中文名。"""
    for source in config.ALL_SOURCES:
        assert source.display_name, f"{source.id} 没有英文展示名"
        assert source.display_name.isascii(), f"{source.id} 的展示名不是英文：{source.display_name}"


# --------------------------------------------------------------------------- #
# 归一化：字段落到 Article 上，并统计覆盖率
# --------------------------------------------------------------------------- #

def test_normalize_sets_image_and_english_source_name() -> None:
    source = config.SOURCES_BY_ID["qbitai"]
    items = [
        RawItem(
            source_id="qbitai",
            title="无图条目",
            url="https://www.qbitai.com/no-image",
        ),
        RawItem(
            source_id="qbitai",
            title="有图条目",
            url="https://www.qbitai.com/with-image",
            image_raw="https://static.qbitai.com/hero.jpg",
        ),
    ]
    batch = normalize_batch(items, source, NOW)
    by_title = {article.title: article for article in batch.articles}

    assert by_title["有图条目"].image_url == "https://static.qbitai.com/hero.jpg"
    assert by_title["无图条目"].image_url is None
    assert batch.with_image == 1
    assert all(article.source_name_en == source.display_name for article in batch.articles)
    assert source.display_name == "QbitAI"


def test_normalize_drops_unusable_image_url() -> None:
    """`image_raw` 里是不可用的地址时，落库应为空而不是原样存进去。"""
    source = config.SOURCES_BY_ID["qbitai"]
    items = [
        RawItem(
            source_id="qbitai",
            title="坏图条目",
            url="https://www.qbitai.com/bad-image",
            image_raw="data:image/gif;base64,R0lGODlhAQABAAAAACw=",
        )
    ]
    batch = normalize_batch(items, source, NOW)
    assert batch.articles[0].image_url is None
    assert batch.with_image == 0


# --------------------------------------------------------------------------- #
# 合并：配图只补空、展示名按配置回写
# --------------------------------------------------------------------------- #

def test_merge_fills_missing_image() -> None:
    existing = make_article("配图补空测试", image_url=None)
    incoming = make_article("配图补空测试", image_url="https://a.example/filled.jpg")
    outcome = store.merge([existing], [incoming], NOW)
    assert outcome.articles[0].image_url == "https://a.example/filled.jpg"


def test_merge_never_erases_existing_image() -> None:
    """源站某次响应整批不带图时，不能把已抓到的配图抹掉。"""
    existing = make_article("配图保护测试", image_url="https://a.example/kept.jpg")
    incoming = make_article("配图保护测试", image_url=None)
    outcome = store.merge([existing], [incoming], NOW)
    assert outcome.articles[0].image_url == "https://a.example/kept.jpg"


def test_merge_rewrites_english_source_name_from_config() -> None:
    """展示名由配置决定，回写它能让源改名自动传播，不必跑 `--rebuild`。"""
    existing = make_article("源名回写测试", source_name_en="OldName")
    incoming = make_article("源名回写测试", source_name_en="NewName")
    outcome = store.merge([existing], [incoming], NOW)
    assert outcome.articles[0].source_name_en == "NewName"


def test_sync_source_display_names_covers_articles_without_candidates() -> None:
    """**回归测试（真实踩到的坑）**：展示名要覆盖**全部在库条目**，不只本轮抓到的那批。

    实测场景：某源整体抓取失败时，它原有的在库条目本轮没有任何候选可合并，
    展示名于是停留在旧值或空串 —— 230 条里有 19 条如此，页面上直接露出中文源名，
    与「源名全站统一英文」冲突。这里断言没有候选的条目也会被重派生。
    """
    stale = make_article("没进本轮 feed 的旧条目", source_id="qbitai", source_name_en="")
    already_ok = make_article("已正确的条目", source_id="qbitai", source_name_en="QbitAI")
    other_source = make_article("另一源的条目", source_id="hn", source_name_en="Hacker News")

    fixed = store.sync_source_display_names([stale, already_ok, other_source])

    assert fixed == 1, "只应更正那一条空展示名"
    assert stale.source_name_en == "QbitAI"
    assert already_ok.source_name_en == "QbitAI"
    assert other_source.source_name_en == "Hacker News"


def test_sync_source_display_names_is_idempotent() -> None:
    """再跑一次不应产生任何更正 —— 它必须是纯派生，不能累积改动。"""
    articles = [
        make_article("甲", source_id="qbitai"),
        make_article("乙", source_id="solidot", source_name_en="Solidot"),
    ]
    first = store.sync_source_display_names(articles)
    assert first == 1
    assert store.sync_source_display_names(articles) == 0