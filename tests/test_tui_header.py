from harness.tui.header import header_text_markup


def test_header_text_markup_supports_brand_e_mark():
    markup = header_text_markup('DON≡', '0.5.0', 'Get Shit Done')
    assert '[b]DON≡[/b]' in markup
    assert 'v0.5.0' in markup
    assert 'Get Shit Done' in markup


def test_header_text_markup_renders_model_line_under_the_rule():
    """The model · provider line lives directly under the header rule when a
    model line is supplied — the rule precedes it, the model follows it."""
    markup = header_text_markup('≡', '0.5.0', 'Get Shit Done',
                                model_line='gpt-5.4 Vibeproxy')
    rule = '─' * len('Get Shit Done')
    assert 'gpt-5.4 Vibeproxy' in markup
    # the rule comes BEFORE the model line (model sits under the rule)
    assert markup.index(rule) < markup.index('gpt-5.4 Vibeproxy')


def test_header_text_markup_omits_model_line_when_absent():
    """No model line → header is unchanged (name / tagline / rule only); the
    rule is the last content line."""
    markup = header_text_markup('≡', '0.5.0', 'Get Shit Done')
    rule = '─' * len('Get Shit Done')
    assert rule in markup
    # nothing after the rule's row but its closing markup tag
    assert markup.split(rule, 1)[1].strip() == '[/]'
