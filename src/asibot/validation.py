"""Input validation for connector tool parameters.

Validates user-supplied strings before they reach external APIs to prevent
path traversal, injection, and DoS attacks.
"""

import re

# Max lengths by parameter category
_MAX_ID_LENGTH = 256
_MAX_QUERY_LENGTH = 2000
_MAX_CONTENT_LENGTH = 100_000

# Characters forbidden in all user inputs
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# --- ID validators (used in URL path segments) ---

_SAFE_ID = re.compile(r"^[a-zA-Z0-9_.:\-]+$")

_GITHUB_REPO = re.compile(r"^[a-zA-Z0-9._-]+(/[a-zA-Z0-9._-]+)?$")
_JIRA_ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]+-\d+$")
_JIRA_PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9_]+$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Salesforce standard + common custom objects
_SF_OBJECT_ALLOWLIST = frozenset({
    "Account", "Contact", "Opportunity", "Lead", "Case", "Task", "Event",
    "Campaign", "CampaignMember", "Contract", "Order", "OrderItem",
    "Product2", "Pricebook2", "PricebookEntry", "Quote", "QuoteLineItem",
    "Asset", "Solution", "ContentDocument", "ContentVersion", "Note",
    "Attachment", "User", "Group", "UserRole", "Profile",
    "Report", "Dashboard", "EmailMessage", "FeedItem",
})
# Custom objects end with __c
_SF_CUSTOM_OBJECT = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*__c$")


def _check_control_chars(value: str, param_name: str) -> str | None:
    """Return error message if value contains control characters."""
    if _CONTROL_CHARS.search(value):
        return f"Invalid {param_name}: contains control characters."
    return None


def validate_id(value: str, param_name: str) -> str | None:
    """Validate a generic resource ID used in URL paths.

    Returns error message string if invalid, None if OK.
    """
    if not value or not value.strip():
        return f"{param_name} is required."
    if len(value) > _MAX_ID_LENGTH:
        return f"{param_name} is too long (max {_MAX_ID_LENGTH} characters)."
    err = _check_control_chars(value, param_name)
    if err:
        return err
    if ".." in value or "\\" in value:
        return f"Invalid {param_name}: contains forbidden characters."
    if not _SAFE_ID.match(value):
        return f"Invalid {param_name}: only alphanumeric characters, dots, hyphens, underscores, and colons are allowed."
    return None


def validate_repo(repo: str) -> str | None:
    """Validate a GitHub repository name (e.g., 'my-repo' or 'org/my-repo')."""
    if not repo or not repo.strip():
        return "Repository name is required."
    if len(repo) > _MAX_ID_LENGTH:
        return "Repository name is too long."
    err = _check_control_chars(repo, "repo")
    if err:
        return err
    if not _GITHUB_REPO.match(repo):
        return "Invalid repository name. Expected format: 'repo-name' or 'org/repo-name'."
    return None


def validate_issue_key(key: str) -> str | None:
    """Validate a Jira issue key (e.g., 'PROJ-123')."""
    if not key or not key.strip():
        return "Issue key is required."
    if len(key) > 50:
        return "Issue key is too long."
    if not _JIRA_ISSUE_KEY.match(key):
        return "Invalid issue key. Expected format: 'PROJ-123'."
    return None


def validate_project_key(key: str) -> str | None:
    """Validate a Jira project key (e.g., 'PROJ')."""
    if not key or not key.strip():
        return "Project key is required."
    if len(key) > 30:
        return "Project key is too long."
    if not _JIRA_PROJECT_KEY.match(key):
        return "Invalid project key. Expected format: 'PROJ' (uppercase letters/digits)."
    return None


def validate_sf_object_type(object_type: str) -> str | None:
    """Validate a Salesforce object type against an allowlist."""
    if not object_type or not object_type.strip():
        return "Object type is required."
    if object_type in _SF_OBJECT_ALLOWLIST:
        return None
    if _SF_CUSTOM_OBJECT.match(object_type):
        return None
    return f"Unknown Salesforce object type: {object_type}. Use standard objects (Account, Contact, etc.) or custom objects (MyObj__c)."


def validate_query(value: str, param_name: str = "query") -> str | None:
    """Validate a search query string."""
    if not value or not value.strip():
        return f"{param_name} is required."
    if len(value) > _MAX_QUERY_LENGTH:
        return f"{param_name} is too long (max {_MAX_QUERY_LENGTH} characters)."
    err = _check_control_chars(value, param_name)
    if err:
        return err
    return None


def validate_content(value: str, param_name: str = "content") -> str | None:
    """Validate user-supplied content (titles, bodies, comments)."""
    if not value or not value.strip():
        return f"{param_name} is required."
    if len(value) > _MAX_CONTENT_LENGTH:
        return f"{param_name} is too long (max {_MAX_CONTENT_LENGTH} characters)."
    if "\x00" in value:
        return f"Invalid {param_name}: contains null bytes."
    return None


def validate_date(value: str, param_name: str = "date") -> str | None:
    """Validate an ISO date string (YYYY-MM-DD)."""
    if not value or not value.strip():
        return f"{param_name} is required."
    if not _ISO_DATE.match(value):
        return f"Invalid {param_name}. Expected format: YYYY-MM-DD."
    return None


def validate_email_address(value: str) -> str | None:
    """Basic email format validation."""
    if not value or not value.strip():
        return "Email address is required."
    if len(value) > 320:
        return "Email address is too long."
    if "\x00" in value or _CONTROL_CHARS.search(value):
        return "Invalid email address: contains control characters."
    if "@" not in value or "." not in value.split("@")[-1]:
        return "Invalid email address format."
    return None


def validate_limit(value: int, max_val: int = 100) -> int:
    """Clamp a pagination limit to a safe range."""
    return max(1, min(value, max_val))


_SAFE_SUBDOMAIN = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?$")

# Known GitHub token prefixes
_GITHUB_TOKEN_PREFIXES = ("ghp_", "ghs_", "github_pat_", "gho_")


def validate_base_url(value: str, param_name: str = "base_url") -> str | None:
    """Validate a base URL is HTTPS and doesn't contain path traversal."""
    if not value or not value.strip():
        return f"{param_name} is required."
    if not value.startswith("https://"):
        return f"Invalid {param_name}: must use HTTPS."
    if ".." in value or "\\" in value:
        return f"Invalid {param_name}: contains forbidden characters."
    err = _check_control_chars(value, param_name)
    if err:
        return err
    return None


def validate_folder_name(value: str, allowed: frozenset[str]) -> str | None:
    """Validate a folder name against an allowlist."""
    if not value or not value.strip():
        return "Folder name is required."
    if value not in allowed:
        return f"Invalid folder: '{value}'. Allowed: {', '.join(sorted(allowed))}"
    return None


# --- Credential Validation ---

_MIN_API_KEY_LENGTH = 10


def strip_credential_values(creds: dict) -> dict:
    """Strip leading/trailing whitespace from all string credential values."""
    return {k: v.strip() if isinstance(v, str) else v for k, v in creds.items()}


def validate_credentials(service: str, creds: dict) -> str | None:
    """Validate credential format for a service before storage.

    Returns an error message string if invalid, None if OK.
    Credentials should already be whitespace-stripped before calling this.
    """
    errors: list[str] = []

    # Check all values are non-empty after stripping
    for key, value in creds.items():
        if isinstance(value, str) and not value:
            errors.append(f"'{key}' is empty.")

    if errors:
        return "Invalid credentials: " + " ".join(errors)

    # Service-specific format validation
    if service == "github":
        token = creds.get("token", "")
        if token and not any(token.startswith(p) for p in _GITHUB_TOKEN_PREFIXES):
            prefix = token[:4] + "..." if len(token) > 4 else token
            errors.append(
                f"GitHub token should start with 'ghp_', 'ghs_', 'github_pat_', or 'gho_'. "
                f"Got: '{prefix}'"
            )

    elif service == "salesforce":
        instance_url = creds.get("instance_url", "")
        if instance_url and not instance_url.startswith("https://"):
            errors.append("Salesforce instance_url must start with 'https://'.")

    elif service in ("atlassian", "confluence"):
        base_url = creds.get("base_url", "")
        domain = creds.get("domain", "")
        if base_url and not base_url.startswith("https://"):
            errors.append(f"{service} base_url must start with 'https://'.")
        if domain and not domain.strip("."):
            errors.append(f"{service} domain appears invalid.")

    elif service == "sap":
        base_url = creds.get("base_url", "")
        if base_url and not base_url.startswith("https://"):
            errors.append("SAP base_url must start with 'https://'.")

    # API key minimum length check (applies to fields named 'token', 'api_key', 'api_token')
    for key in ("token", "api_key", "api_token"):
        value = creds.get(key, "")
        if value and len(value) < _MIN_API_KEY_LENGTH:
            errors.append(
                f"'{key}' seems too short (min {_MIN_API_KEY_LENGTH} chars). "
                f"Check for typos."
            )

    if errors:
        return "Credential validation failed: " + " ".join(errors)
    return None
