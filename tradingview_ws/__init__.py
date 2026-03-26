"""
TradingView WebSocket Client for Real-time FX Tick Data

A production-ready Python module for streaming live FX tick data from TradingView's WebSocket.

Installation:
    pip install websocket-client

Usage:
    from tradingview_ws import TradingViewWS
    
    def on_tick(tick):
        print(tick)
    
    client = TradingViewWS(symbol="FX_IDC:EURUSD", on_tick=on_tick)
    client.start()
"""

__version__ = "1.0.0"
__author__ = "Quant Engineer"

from .client import TradingViewWS
from .parser import TickData

__all__ = ['TradingViewWS', 'TickData']




