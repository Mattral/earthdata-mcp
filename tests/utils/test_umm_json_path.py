"""Tests for UMM JSON path utilities."""

from lambdas.enrichment.umm.json_path import (
    get_value_at_path,
    remove_value_at_path,
    set_value_at_path,
)


class TestGetValueAtPath:
    """Tests for get_value_at_path helper."""

    def test_gets_simple_value(self):
        """Should get value at simple path."""
        metadata = {"EntryTitle": "Test Collection"}
        value = get_value_at_path(metadata, "$.EntryTitle")
        assert value == "Test Collection"

    def test_gets_nested_value(self):
        """Should get nested value."""
        metadata = {"DOI": {"DOI": "10.5067/ABC123"}}
        value = get_value_at_path(metadata, "$.DOI.DOI")
        assert value == "10.5067/ABC123"

    def test_gets_array_element(self):
        """Should get array element."""
        metadata = {"Platforms": [{"ShortName": "TERRA"}]}
        value = get_value_at_path(metadata, "$.Platforms[0].ShortName")
        assert value == "TERRA"

    def test_returns_none_for_missing_path(self):
        """Should return None for missing path."""
        metadata = {"EntryTitle": "Test"}
        value = get_value_at_path(metadata, "$.MissingField")
        assert value is None


class TestSetValueAtPath:
    """Tests for set_value_at_path helper."""

    def test_sets_simple_value(self):
        """Should set value at simple path."""
        metadata = {"EntryTitle": "Old"}
        result = set_value_at_path(metadata, "$.EntryTitle", "New")
        assert result is True
        assert metadata["EntryTitle"] == "New"

    def test_sets_nested_value(self):
        """Should set nested value."""
        metadata = {"DOI": {"DOI": "old"}}
        result = set_value_at_path(metadata, "$.DOI.DOI", "10.5067/NEW")
        assert result is True
        assert metadata["DOI"]["DOI"] == "10.5067/NEW"

    def test_sets_array_element(self):
        """Should set array element."""
        metadata = {"Platforms": [{"ShortName": "OLD"}]}
        result = set_value_at_path(metadata, "$.Platforms[0].ShortName", "NEW")
        assert result is True
        assert metadata["Platforms"][0]["ShortName"] == "NEW"

    def test_creates_missing_key_on_existing_parent(self):
        """Should create a new key when the parent object exists."""
        metadata = {"RelatedUrls": [{"URL": "http://example.com"}]}
        result = set_value_at_path(metadata, "$.RelatedUrls[0].Description", "A description")
        assert result is True
        assert metadata["RelatedUrls"][0]["Description"] == "A description"


class TestRemoveValueAtPath:
    """Tests for remove_value_at_path helper."""

    def test_removes_simple_field(self):
        """Should remove field at simple path."""
        metadata = {"EntryTitle": "Test", "Abstract": "To remove"}
        success, removed = remove_value_at_path(metadata, "$.Abstract")
        assert success is True
        assert removed == "To remove"
        assert "Abstract" not in metadata

    def test_removes_array_element(self):
        """Should remove array element."""
        metadata = {"Platforms": [{"ShortName": "A"}, {"ShortName": "B"}]}
        success, removed = remove_value_at_path(metadata, "$.Platforms[0]")
        assert success is True
        assert removed["ShortName"] == "A"
        assert len(metadata["Platforms"]) == 1

    def test_returns_false_for_missing_path(self):
        """Should return False for missing path."""
        metadata = {"EntryTitle": "Test"}
        success, removed = remove_value_at_path(metadata, "$.Missing")
        assert success is False
        assert removed is None
