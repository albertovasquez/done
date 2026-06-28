from harness.tui.header import header_text_markup


def test_header_text_markup_supports_brand_e_mark():
    markup = header_text_markup('DON≡', '0.5.0', 'Get Shit Done')
    assert '[b]DON≡[/b]' in markup
    assert 'v0.5.0' in markup
    assert 'Get Shit Done' in markup
