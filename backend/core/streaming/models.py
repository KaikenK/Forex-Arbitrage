from pydantic import BaseModel
from typing import List, Tuple, Optional
from datetime import datetime

class OrderbookLevel(BaseModel):
    price: float
    volume: float

class OrderbookUpdate(BaseModel):
    symbol: str
    source: str  # e.g., "OANDA", "MT5", "Synthetic"
    timestamp: float
    bids: List[OrderbookLevel]
    asks: List[OrderbookLevel]
    
class TickUpdate(BaseModel):
    symbol: str
    source: str
    timestamp: float
    bid: float
    ask: float
