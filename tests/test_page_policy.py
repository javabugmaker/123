from __future__ import annotations

from institution_scanner.page_policy import polish_public_page_html


def test_public_page_hides_freshness_exception_section() -> None:
    html = """
    <html><head></head><body>
    <section class="section" data-section="market_state">MARKET</section>
    <section id="freshness-exceptions-v102" class="section card">
      <h2>DATA FRESHNESS EXCEPTIONS / 时效例外</h2>
      <div>600000.SH</div>
    </section>
    <section id="model-health-v105">MODEL HEALTH</section>
    </body></html>
    """
    polished = polish_public_page_html(html, marker="交易快报 2026-08-24")

    assert 'id="freshness-exceptions-v102"' not in polished
    assert "DATA FRESHNESS EXCEPTIONS / 时效例外" not in polished
    assert "600000.SH" not in polished
    assert "MODEL HEALTH" in polished
    assert "交易快报 2026-08-24" in polished


def test_public_page_keeps_snapshot_status_copy() -> None:
    html = "<body><div>LIVE · 数据就绪</div><div>● 数据已就绪</div></body>"
    polished = polish_public_page_html(html)

    assert "PUBLISHED SNAPSHOT · 数据对齐" in polished
    assert "● 已发布快照" in polished
