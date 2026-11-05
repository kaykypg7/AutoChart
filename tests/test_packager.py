"""
core.packager.sanitize_folder_name — Windows reserved chars, empty strings,
length truncation, whitespace collapsing.
"""
import pytest

from core.packager import sanitize_folder_name


class TestReservedChars:
    @pytest.mark.parametrize("ch", list('<>:"/\\|?*'))
    def test_single_reserved_char_replaced(self, ch):
        out = sanitize_folder_name(f"song{ch}title")
        assert ch not in out
        assert "_" in out

    def test_all_reserved_chars_together(self):
        out = sanitize_folder_name('a<b>c:d"e/f\\g|h?i*j')
        for ch in '<>:"/\\|?*':
            assert ch not in out
        assert out.startswith("a_") and out.endswith("_j")

    def test_control_chars_stripped(self):
        out = sanitize_folder_name("abc\x00def\x1fghi")
        assert "\x00" not in out
        assert "\x1f" not in out
        assert "abc" in out and "ghi" in out


class TestEmptyAndWhitespace:
    @pytest.mark.parametrize("s", ["", "   ", ".", " . . ", "..."])
    def test_empty_or_whitespace_returns_untitled(self, s):
        assert sanitize_folder_name(s) == "untitled"

    def test_tabs_and_newlines_become_underscores(self):
        # Tabs and newlines are in the control-char range (\x00-\x1f), so the
        # reserved-char pass replaces each with "_" BEFORE whitespace collapse
        # — they never collapse to a single space.
        assert sanitize_folder_name("\t\n") == "__"

    def test_whitespace_collapsed(self):
        # Spaces collapse to one; tabs do not — they become underscores first.
        assert sanitize_folder_name("a    b   c") == "a b c"
        assert sanitize_folder_name("a    b\t\tc") == "a b__c"

    def test_trailing_dots_and_spaces_stripped(self):
        # Windows disallows trailing dots/spaces on folder names.
        assert sanitize_folder_name("hello.  . ") == "hello"
        assert sanitize_folder_name("  hello  ") == "hello"


class TestLengthTruncation:
    def test_200_chars_truncated_to_default_max(self):
        s = "x" * 250
        out = sanitize_folder_name(s)
        assert len(out) <= 120

    def test_custom_max_len_respected(self):
        out = sanitize_folder_name("x" * 500, max_len=50)
        assert len(out) <= 50

    def test_truncation_does_not_leave_trailing_space(self):
        s = ("word " * 100).strip()
        out = sanitize_folder_name(s, max_len=50)
        assert len(out) <= 50
        assert not out.endswith(" ")

    def test_short_name_untouched_by_truncation(self):
        assert sanitize_folder_name("short") == "short"


class TestUnicode:
    def test_unicode_preserved(self):
        out = sanitize_folder_name("Música — São Paulo")
        assert "Música" in out
        assert "São" in out

    def test_emoji_preserved(self):
        # Emoji are not in the reserved set — pass through untouched.
        out = sanitize_folder_name("Track 🎸 Mix")
        assert "🎸" in out


class TestRealWorldSongNames:
    @pytest.mark.parametrize("raw,expected_substr", [
        ("AC/DC - Back In Black", "AC_DC - Back In Black"),
        ('Guns N" Roses - Welcome', "Guns N_ Roses - Welcome"),
        ("foo: bar | baz?", "foo_ bar _ baz_"),
    ])
    def test_common_song_titles(self, raw, expected_substr):
        assert sanitize_folder_name(raw) == expected_substr
