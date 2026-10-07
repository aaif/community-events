from aaif_events.cities import CITY_FOLDS, chapter_for


def test_absorbed_cities_fold_into_their_country_chapter():
    assert chapter_for("Tokyo") == "Japan"
    assert chapter_for("  wellington ") == "New Zealand"
    assert chapter_for("Auckland") == "New Zealand"


def test_case_whitespace_punctuation_and_accents_do_not_matter():
    assert chapter_for("TOKYO") == "Japan"
    assert chapter_for("Tōkyō") == "Japan"
    assert chapter_for("  Auckland   ") == "New Zealand"


def test_a_city_typed_with_its_own_country_folds():
    assert chapter_for("Tokyo, Japan") == "Japan"
    assert chapter_for("Auckland, New Zealand") == "New Zealand"
    assert chapter_for("Wellington New Zealand") == "New Zealand"


def test_a_city_with_the_wrong_country_is_left_alone():
    assert chapter_for("Tokyo, Brazil") == "Tokyo, Brazil"


def test_everything_else_is_untouched():
    assert chapter_for("Boston") == "Boston"
    assert chapter_for("Boston ") == "Boston "
    assert chapter_for("Japan") == "Japan"
    assert chapter_for("") == ""
    assert chapter_for(None) is None


def test_every_fold_lands_on_a_chapter_name_that_is_itself_untouched():
    for chapter in set(CITY_FOLDS.values()):
        assert chapter_for(chapter) == chapter
