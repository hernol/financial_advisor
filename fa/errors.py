"""Domain errors for the financial analyzer."""
from __future__ import annotations


class FinancialAnalyzerError(Exception):
    """Base error for every failure raised by this application."""


class ConfigError(FinancialAnalyzerError):
    """Raised when required configuration (API keys, paths) is missing."""


class ProviderError(FinancialAnalyzerError):
    """Raised when a single market-data provider fails."""


class DataUnavailableError(FinancialAnalyzerError):
    """Raised when every configured provider failed to supply the data.

    The analyzer never falls back to simulated numbers: no data is a hard error.
    """


class ValidationError(FinancialAnalyzerError):
    """Raised when user supplied input is invalid."""
