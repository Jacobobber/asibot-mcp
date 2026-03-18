"""Tests for input validation module."""

import pytest

from asibot.validation import (
    validate_base_url,
    validate_content,
    validate_date,
    validate_email_address,
    validate_folder_name,
    validate_id,
    validate_issue_key,
    validate_limit,
    validate_project_key,
    validate_query,
    validate_repo,
    validate_sf_object_type,
)


class TestValidateId:
    def test_valid_simple(self):
        assert validate_id("abc-123", "id") is None

    def test_valid_with_dots(self):
        assert validate_id("user.name-123", "id") is None

    def test_valid_with_colons(self):
        assert validate_id("a:b:c", "id") is None

    def test_rejects_empty(self):
        assert validate_id("", "id") is not None

    def test_rejects_whitespace_only(self):
        assert validate_id("   ", "id") is not None

    def test_rejects_path_traversal(self):
        assert validate_id("../../etc/passwd", "id") is not None

    def test_rejects_backslash(self):
        assert validate_id("foo\\bar", "id") is not None

    def test_rejects_null_byte(self):
        assert validate_id("foo\x00bar", "id") is not None

    def test_rejects_control_chars(self):
        assert validate_id("foo\x01bar", "id") is not None

    def test_rejects_newline(self):
        assert validate_id("foo\nbar", "id") is not None

    def test_rejects_slash(self):
        assert validate_id("foo/bar", "id") is not None

    def test_rejects_too_long(self):
        assert validate_id("a" * 257, "id") is not None

    def test_max_length_ok(self):
        assert validate_id("a" * 256, "id") is None

    def test_rejects_spaces(self):
        assert validate_id("foo bar", "id") is not None


class TestValidateRepo:
    def test_valid_simple(self):
        assert validate_repo("my-repo") is None

    def test_valid_with_org(self):
        assert validate_repo("org/my-repo") is None

    def test_valid_with_dots(self):
        assert validate_repo("org/my.repo") is None

    def test_rejects_empty(self):
        assert validate_repo("") is not None

    def test_rejects_traversal(self):
        assert validate_repo("../../secret") is not None

    def test_rejects_multiple_slashes(self):
        assert validate_repo("a/b/c") is not None

    def test_rejects_control_chars(self):
        assert validate_repo("repo\x00name") is not None

    def test_rejects_spaces(self):
        assert validate_repo("my repo") is not None


class TestValidateIssueKey:
    def test_valid(self):
        assert validate_issue_key("PROJ-123") is None

    def test_valid_long(self):
        assert validate_issue_key("MYPROJECT-99999") is None

    def test_rejects_empty(self):
        assert validate_issue_key("") is not None

    def test_rejects_lowercase(self):
        assert validate_issue_key("proj-123") is not None

    def test_rejects_no_dash(self):
        assert validate_issue_key("PROJ123") is not None

    def test_rejects_traversal(self):
        assert validate_issue_key("../../etc") is not None

    def test_rejects_spaces(self):
        assert validate_issue_key("PROJ 123") is not None


class TestValidateProjectKey:
    def test_valid(self):
        assert validate_project_key("PROJ") is None

    def test_valid_with_digits(self):
        assert validate_project_key("PROJ2") is None

    def test_rejects_empty(self):
        assert validate_project_key("") is not None

    def test_rejects_lowercase(self):
        assert validate_project_key("proj") is not None

    def test_rejects_too_long(self):
        assert validate_project_key("A" * 31) is not None


class TestValidateSfObjectType:
    def test_standard_account(self):
        assert validate_sf_object_type("Account") is None

    def test_standard_contact(self):
        assert validate_sf_object_type("Contact") is None

    def test_standard_opportunity(self):
        assert validate_sf_object_type("Opportunity") is None

    def test_custom_object(self):
        assert validate_sf_object_type("MyCustomObj__c") is None

    def test_rejects_empty(self):
        assert validate_sf_object_type("") is not None

    def test_rejects_unknown(self):
        assert validate_sf_object_type("FakeObject") is not None

    def test_rejects_traversal(self):
        assert validate_sf_object_type("../../etc") is not None


class TestValidateQuery:
    def test_valid(self):
        assert validate_query("search term") is None

    def test_rejects_empty(self):
        assert validate_query("") is not None

    def test_rejects_null_byte(self):
        assert validate_query("foo\x00bar") is not None

    def test_rejects_too_long(self):
        assert validate_query("a" * 2001) is not None

    def test_max_length_ok(self):
        assert validate_query("a" * 2000) is None


class TestValidateContent:
    def test_valid(self):
        assert validate_content("Hello world") is None

    def test_rejects_empty(self):
        assert validate_content("") is not None

    def test_rejects_null_byte(self):
        assert validate_content("foo\x00bar") is not None

    def test_rejects_too_long(self):
        assert validate_content("a" * 100_001) is not None

    def test_allows_newlines(self):
        assert validate_content("line1\nline2\n") is None


class TestValidateDate:
    def test_valid(self):
        assert validate_date("2024-01-15") is None

    def test_rejects_empty(self):
        assert validate_date("") is not None

    def test_rejects_wrong_format(self):
        assert validate_date("01/15/2024") is not None

    def test_rejects_partial(self):
        assert validate_date("2024-01") is not None

    def test_rejects_text(self):
        assert validate_date("yesterday") is not None


class TestValidateEmail:
    def test_valid(self):
        assert validate_email_address("user@example.com") is None

    def test_rejects_empty(self):
        assert validate_email_address("") is not None

    def test_rejects_no_at(self):
        assert validate_email_address("userexample.com") is not None

    def test_rejects_no_domain(self):
        assert validate_email_address("user@") is not None

    def test_rejects_null_byte(self):
        assert validate_email_address("user\x00@example.com") is not None


class TestValidateLimit:
    def test_clamps_to_max(self):
        assert validate_limit(200) == 100

    def test_clamps_to_min(self):
        assert validate_limit(0) == 1

    def test_negative(self):
        assert validate_limit(-5) == 1

    def test_within_range(self):
        assert validate_limit(50) == 50

    def test_custom_max(self):
        assert validate_limit(200, max_val=500) == 200


class TestValidateBaseUrl:
    def test_valid_https(self):
        assert validate_base_url("https://api.example.com") is None

    def test_valid_https_with_path(self):
        assert validate_base_url("https://api.example.com/v1") is None

    def test_rejects_empty(self):
        assert validate_base_url("") is not None

    def test_rejects_http(self):
        result = validate_base_url("http://api.example.com")
        assert result is not None
        assert "HTTPS" in result

    def test_rejects_no_scheme(self):
        assert validate_base_url("api.example.com") is not None

    def test_rejects_path_traversal(self):
        assert validate_base_url("https://api.example.com/../etc") is not None

    def test_rejects_backslash(self):
        assert validate_base_url("https://api.example.com\\evil") is not None

    def test_rejects_control_chars(self):
        assert validate_base_url("https://api.example.com\x00") is not None

    def test_rejects_ftp(self):
        assert validate_base_url("ftp://files.example.com") is not None


class TestValidateFolderName:
    def test_valid_folder(self):
        allowed = frozenset({"inbox", "sentitems", "drafts"})
        assert validate_folder_name("inbox", allowed) is None

    def test_rejects_invalid_folder(self):
        allowed = frozenset({"inbox", "sentitems", "drafts"})
        result = validate_folder_name("../../etc", allowed)
        assert result is not None
        assert "Invalid folder" in result

    def test_rejects_empty(self):
        allowed = frozenset({"inbox"})
        assert validate_folder_name("", allowed) is not None

    def test_lists_allowed_in_error(self):
        allowed = frozenset({"inbox", "drafts"})
        result = validate_folder_name("junk", allowed)
        assert "drafts" in result
        assert "inbox" in result
