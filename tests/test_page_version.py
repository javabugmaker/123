from __future__ import annotations

from institution_scanner.page_version import (
    PUBLIC_PAGE_VERSION,
    PUBLIC_PAGE_VERSION_ID,
    apply_public_page_version_html,
)


def test_page_engineering_version_is_updated_without_model_marker_changes() -> None:
    html = (
        '<html><head>'
        '<meta name="model-signature-v102" content="abc123">'
        '<meta name="web-report-version" content="old">'
        '</head><body>'
        '<span>页面版本</span><strong>v105</strong>'
        '</body></html>'
    )

    result = apply_public_page_version_html(html)

    assert f"<strong>{PUBLIC_PAGE_VERSION}</strong>" in result
    assert f'content="{PUBLIC_PAGE_VERSION_ID}"' in result
    assert 'model-signature-v102" content="abc123"' in result
    assert 'name="public-page-version"' in result
