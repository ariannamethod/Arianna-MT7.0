"""
Arianna Interfaces - adapters for different platforms

This package contains interface adapters that connect the core engine
to various platforms and protocols.
"""

from interfaces.telegram_bot import TelegramInterface

__all__ = ['TelegramInterface']
