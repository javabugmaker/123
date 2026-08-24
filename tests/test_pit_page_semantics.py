from __future__ import annotations

from institution_scanner.pit_page_semantics import (
    apply_pit_page_semantics_html,
)


def _legacy_html() -> str:
    return (
        "<html><head></head><body>"
        '<section id="before">BEFORE</section>'
        '<section class="section card console-v93">'
        '<div class="section-head"><h2>'
        "HELD-OUT SCORE CALIBRATION / 测试集评分分桶"
        "</h2><p>生产回测 held-out test set；不重拟合、不回灌当前排名</p></div>"
        '<div class="meta93"><span>生产校准治理 '
        '<strong class="bad93">OFF · 仅诊断</strong></span>'
        "<span>· walk-forward=UNSTABLE, 20D RankIC&lt;=0</span></div>"
        "<p>legacy metrics</p></section>"
        '<section id="next">NEXT</section>'
        "</body></html>"
    )


def test_pit_warmup_replaces_misleading_legacy_calibration_panel() -> None:
    summary = {
        "heldout_point_in_time_status": "PIT_WARMUP",
        "heldout_metric_available": False,
        "heldout_raw_test_samples": 50937,
        "heldout_verified_test_samples": 0,
        "heldout_unverified_test_samples": 50937,
    }

    result = apply_pit_page_semantics_html(_legacy_html(), summary)

    assert "PIT WARM-UP · 历史校准冷启动" in result
    assert "Raw held-out <strong>50937</strong>" in result
    assert "PIT verified <strong>0</strong>" in result
    assert "生产评分 <strong>正常运行</strong>" in result
    assert "历史校准 <strong>OFF</strong>" in result
    assert "walk-forward=UNSTABLE" not in result
    assert "HELD-OUT SCORE CALIBRATION / 测试集评分分桶" not in result
    assert '<section id="before">BEFORE</section>' in result
    assert '<section id="next">NEXT</section>' in result
    assert "pit-page-semantics-version" in result


def test_verified_subset_keeps_real_metrics_and_adds_pit_scope() -> None:
    summary = {
        "heldout_point_in_time_status": "VERIFIED_SUBSET",
        "heldout_metric_available": True,
        "heldout_raw_test_samples": 20,
        "heldout_verified_test_samples": 12,
        "heldout_unverified_test_samples": 8,
    }

    result = apply_pit_page_semantics_html(_legacy_html(), summary)

    assert "HELD-OUT SCORE CALIBRATION / 测试集评分分桶" in result
    assert "walk-forward=UNSTABLE" in result
    assert 'data-pit-heldout-scope="v106.3"' in result
    assert "PIT scope <strong>VERIFIED_SUBSET</strong>" in result
    assert "Verified <strong>12</strong>" in result
    assert "Unverified <strong>8</strong>" in result


def test_missing_summary_is_noop() -> None:
    source = _legacy_html()
    assert apply_pit_page_semantics_html(source, {}) == source
