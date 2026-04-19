import json
import os
import logging
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)


class _CredentialContext:
    """Context manager for safe credential usage."""

    def __init__(self, loader, provider: str, index: int):
        self.loader = loader
        self.provider = provider
        self.index = index
        self.key = None

    def __enter__(self):
        self.key = self.loader.get_key(self.provider, self.index)
        return self.key

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Clear the key from memory after use
        self.key = None


class CredentialsLoader:
    """Load and manage API credentials from secure location."""

    def __init__(self, credentials_path: str = None):
        if credentials_path is None:
            credentials_path = os.path.expanduser("~/.apple_memory/.credentials.json")
        self.credentials_path = Path(credentials_path)
        self._credentials: Dict[str, Any] = {}

    def load(self) -> Dict[str, Any]:
        """Load credentials from file."""
        if not self.credentials_path.exists():
            raise FileNotFoundError(
                f"Credentials file not found: {self.credentials_path}"
            )

        # Fix overly permissive permissions before reading
        current_stat = self.credentials_path.stat()
        current_mode = oct(current_stat.st_mode)[-3:]
        if current_mode != "600":
            self.credentials_path.chmod(0o600)
            logger.debug(
                f"Fixed credentials file permissions from {current_mode} to 600"
            )

        with open(self.credentials_path, "r") as f:
            self._credentials = json.load(f)

        return self._credentials

    def get_keys(self, provider: str) -> list:
        """Get API keys for a specific provider."""
        if not self._credentials:
            self.load()

        provider_config = self._credentials.get(provider, {})

        # Handle both old format (string api_key) and new format (object with api_key)
        if isinstance(provider_config, str):
            # Old format: provider is a plain string (API key)
            return [provider_config]
        elif isinstance(provider_config, dict):
            # New format: provider is an object
            keys = provider_config.get("api_keys", [])
            api_key = provider_config.get("api_key")
            if api_key:
                keys = [api_key]

            # Fallback to test_key if available
            test_key = provider_config.get("test_key")
            if test_key and test_key not in keys:
                keys.append(test_key)

            return keys

        return []

    def get_key(self, provider: str, index: int = 0) -> str:
        """Get a specific API key for a provider."""
        keys = self.get_keys(provider)
        if not keys:
            raise ValueError(f"No API keys found for provider: {provider}")

        # Validate and rotate index if out of range
        if index < 0 or index >= len(keys):
            index = 0

        return keys[index]

    def use_key(self, provider: str, index: int = 0):
        """Context manager for safe credential usage with automatic cleanup."""
        return _CredentialContext(self, provider, index)

        return keys[index]

    def get_telegram_config(self) -> dict:
        """Get telegram bot configuration section."""
        if not self._credentials:
            self.load()
        return self._credentials.get("telegram", {})

    def has_keys(self, provider: str) -> bool:
        """Check if provider has configured keys."""
        if not self._credentials:
            self.load()

        keys = self.get_keys(provider)
        return len(keys) > 0


# Global instance
_credentials_loader = None


def get_credentials_loader() -> "CredentialsLoader":
    """Get the global credentials loader instance."""
    global _credentials_loader
    if _credentials_loader is None:
        _credentials_loader = CredentialsLoader()
    return _credentials_loader
