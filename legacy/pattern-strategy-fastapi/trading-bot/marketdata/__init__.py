"""Market data adapters. The system uses MT5Source exclusively (live bars
pushed by the MT5DataBridge EA). There is no yfinance/Yahoo data source."""
from .base import DataSource, Bar
from .mt5_source import MT5Source

__all__ = ["DataSource", "Bar", "MT5Source"]
