import lint


def test_banned_phrases_are_caught_case_insensitively():
    for phrase in lint.BANNED:
        hits = lint.lint_text("Some text. %s apply." % phrase.upper())
        assert any(h[0] == phrase for h in hits), phrase


def test_word_boundaries():
    assert lint.lint_text("The balanced approach") == []
    assert lint.lint_text("optionally") == []
    assert lint.lint_text("this is not a consideration") == []
    assert lint.lint_text("Treat it as correct.") == []


def test_phrase_across_line_break_is_caught():
    hits = lint.lint_text("You must try\nto do this.")
    assert any(h[0] == "try to" for h in hits)


def test_every_rendered_prompt_is_clean():
    bad = []
    for name, text in lint.all_prompts():
        bad.extend((name, h) for h in lint.lint_text(text))
    assert bad == []
