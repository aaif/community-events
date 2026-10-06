from aaif_events.cities import chapter_for


def test_absorbed_cities_fold_into_their_country_chapter():
    assert chapter_for("Tokyo") == "Japan"
    assert chapter_for("  wellington ") == "New Zealand"
    assert chapter_for("Auckland") == "New Zealand"


def test_everything_else_is_untouched():
    assert chapter_for("Boston") == "Boston"
    assert chapter_for("Japan") == "Japan"
    assert chapter_for("") == ""
    assert chapter_for(None) is None
