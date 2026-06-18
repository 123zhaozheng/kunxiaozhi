from src.infra.share.seo import (
    build_public_route_seo,
    build_shared_page_error_seo,
    build_shared_page_seo,
    inject_public_route_seo_into_html,
    inject_share_seo_into_html,
)


def test_build_shared_page_seo_uses_share_specific_metadata() -> None:
    seo = build_shared_page_seo(
        base_url="https://kunxiaozhi.com",
        share_id="ssehSOzUgKnX",
        session={
            "name": "🤖 创意Agent专利",
            "agent_name": "Search Agent",
            "created_at": "2026-04-29T18:51:10.499000",
        },
        owner={"username": "clivia.yang"},
        events=[
            {
                "event_type": "thinking",
                "data": {"content": "内部推理内容不应该出现在 SEO 摘要里"},
            },
            {
                "event_type": "tool:result",
                "data": {"result": "/home/user/outputs/secret.docx"},
            },
            {
                "event_type": "user:message",
                "data": {
                    "content": "帮我写一个创意agent的训练方法的专利，并根据工具调用能力来组织方案。"
                },
            },
            {
                "event_type": "message:chunk",
                "data": {"content": "技术交底书已完整生成，包含技术方案、保护点和实施例摘要。"},
            },
        ],
    )

    assert seo.title == "🤖 创意Agent专利 - 昆小智 Shared Session"
    assert seo.canonical_url == "https://kunxiaozhi.com/shared/ssehSOzUgKnX"
    assert seo.robots == "noindex, follow, max-image-preview:large"
    assert "帮我写一个创意agent的训练方法的专利" in seo.description
    assert "技术交底书已完整生成" in seo.description
    assert "内部推理内容" not in seo.description
    assert "/home/user/outputs/secret.docx" not in seo.description


def test_build_shared_page_error_seo_returns_noindex_metadata() -> None:
    seo = build_shared_page_error_seo(
        base_url="https://kunxiaozhi.com",
        share_id="missing-share",
        reason="not_found",
    )

    assert seo.title == "Shared session not found - 昆小智"
    assert seo.canonical_url == "https://kunxiaozhi.com/shared/missing-share"
    assert seo.robots == "noindex, follow, max-image-preview:large"


def test_inject_share_seo_into_html_replaces_default_meta_and_adds_preview() -> None:
    html = """
<!doctype html>
<html lang="en">
  <head>
    <link rel="canonical" href="https://kunxiaozhi.com/" />
    <title>昆小智 - AI Agent Platform</title>
    <meta name="description" content="Default description" />
    <meta name="robots" content="index, follow, max-image-preview:large" />
    <meta property="og:type" content="website" />
    <meta property="og:title" content="Default og title" />
    <meta property="og:description" content="Default og description" />
    <meta property="og:url" content="https://kunxiaozhi.com/" />
    <meta name="twitter:title" content="Default twitter title" />
    <meta name="twitter:description" content="Default twitter description" />
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>
""".strip()

    seo = build_shared_page_seo(
        base_url="https://kunxiaozhi.com",
        share_id="ssehSOzUgKnX",
        session={
            "name": "🤖 创意Agent专利",
            "agent_name": "Search Agent",
            "created_at": "2026-04-29T18:51:10.499000",
        },
        owner={"username": "clivia.yang"},
        events=[
            {
                "event_type": "user:message",
                "data": {"content": "帮我写一个创意agent的训练方法的专利。"},
            },
            {
                "event_type": "message:chunk",
                "data": {"content": "技术交底书已完整生成。"},
            },
        ],
    )

    rendered = inject_share_seo_into_html(html, seo)

    assert "<title>🤖 创意Agent专利 - 昆小智 Shared Session</title>" in rendered
    assert 'rel="canonical" href="https://kunxiaozhi.com/shared/ssehSOzUgKnX"' in rendered
    assert 'meta name="robots" content="noindex, follow, max-image-preview:large"' in rendered
    assert 'property="og:type" content="article"' in rendered
    assert 'property="og:url" content="https://kunxiaozhi.com/shared/ssehSOzUgKnX"' in rendered
    assert "Shared session preview" in rendered
    assert "clivia.yang" in rendered
    assert "技术交底书已完整生成" in rendered


def test_inject_public_home_seo_adds_crawlable_landing_content() -> None:
    html = """
<!doctype html>
<html lang="en">
  <head>
    <link rel="canonical" href="https://kunxiaozhi.com/" />
    <title>昆小智 - AI Agent Platform</title>
    <meta name="description" content="Default description" />
    <meta name="robots" content="index, follow, max-image-preview:large" />
    <meta property="og:type" content="website" />
    <meta property="og:title" content="Default og title" />
    <meta property="og:description" content="Default og description" />
    <meta property="og:url" content="https://kunxiaozhi.com/" />
    <meta name="twitter:title" content="Default twitter title" />
    <meta name="twitter:description" content="Default twitter description" />
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>
""".strip()

    seo = build_public_route_seo(base_url="https://kunxiaozhi.com", path="/")
    rendered = inject_public_route_seo_into_html(html, seo)

    assert '<link rel="canonical" href="https://kunxiaozhi.com/"' in rendered
    assert 'meta name="robots" content="index, follow, max-image-preview:large"' in rendered
    assert "<h1>昆小智 AI Agent Platform</h1>" in rendered
    assert "Multi-model AI chat" in rendered
    assert "Model Context Protocol" in rendered


def test_inject_public_home_seo_adds_domestic_and_bing_crawler_meta() -> None:
    html = """
<!doctype html>
<html lang="en">
  <head>
    <title>昆小智 - AI Agent Platform</title>
    <meta name="robots" content="index, follow, max-image-preview:large" />
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>
""".strip()

    seo = build_public_route_seo(base_url="https://kunxiaozhi.com", path="/")
    rendered = inject_public_route_seo_into_html(html, seo)

    for crawler in [
        "bingbot",
        "Baiduspider",
        "360Spider",
        "Sogou web spider",
        "YisouSpider",
        "Bytespider",
    ]:
        assert f'meta name="{crawler}" content="index, follow, max-image-preview:large"' in rendered


def test_build_public_route_seo_marks_app_routes_noindex() -> None:
    seo = build_public_route_seo(base_url="https://kunxiaozhi.com", path="/chat")

    assert seo.canonical_url == "https://kunxiaozhi.com/chat"
    assert seo.robots == "noindex, follow, max-image-preview:large"


def test_inject_public_app_route_seo_noindexes_domestic_and_bing_crawlers() -> None:
    html = """
<!doctype html>
<html lang="en">
  <head>
    <title>昆小智 - AI Agent Platform</title>
    <meta name="robots" content="index, follow, max-image-preview:large" />
    <meta name="bingbot" content="index, follow, max-image-preview:large" />
    <meta name="Baiduspider" content="index, follow, max-image-preview:large" />
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>
""".strip()

    seo = build_public_route_seo(base_url="https://kunxiaozhi.com", path="/chat")
    rendered = inject_public_route_seo_into_html(html, seo)

    assert 'meta name="bingbot" content="noindex, follow, max-image-preview:large"' in rendered
    assert 'meta name="Baiduspider" content="noindex, follow, max-image-preview:large"' in rendered
    assert 'meta name="360Spider" content="noindex, follow, max-image-preview:large"' in rendered
