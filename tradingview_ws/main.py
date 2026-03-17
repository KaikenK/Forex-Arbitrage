"""
TradingView WebSocket Client - Main Entry Point

Simple script to start the client and subscribe to FX symbols.

Usage:
    python -m tradingview_ws.main
    
Or:
    from tradingview_ws import TradingViewWS
    
    client = TradingViewWS(symbol="FX_IDC:EURUSD")
    client.start()
    client.wait()
"""

import logging
import sys
from .client import TradingViewWS
from .parser import TickData

# ============================================================================
# CONFIGURATION
# ============================================================================

# Symbol to subscribe (TradingView format: "FX_IDC:EURUSD")
SYMBOL = "FX_IDC:EURUSD"

# Multiple symbols (optional)
# SYMBOLS = ["FX_IDC:EURUSD", "FX_IDC:GBPUSD", "FX_IDC:USDJPY"]

# Test mode (print only 1 tick per second)
TEST_MODE = False

# Debug mode (show raw WebSocket messages)
DEBUG_MODE = True  # Set to True to see what TradingView sends

# Logging level
LOG_LEVEL = logging.INFO

# ============================================================================

def on_tick(tick: TickData):
    """
    Callback function called for each tick received.
    
    Args:
        tick: TickData object with symbol, timestamp, bid, ask, last
    """
    # Print tick data
    print(f"\n[TICK] {tick.symbol}")
    print(f"  Timestamp: {tick.timestamp} ({tick.timestamp/1000:.3f}s)")
    print(f"  Bid:  {tick.bid}")
    print(f"  Ask:  {tick.ask}")
    print(f"  Last: {tick.last}")
    print(f"  Spread: {(tick.ask - tick.bid) if (tick.ask and tick.bid) else 'N/A'}")
    
    # Or use dictionary format
    # print(tick.to_dict())


def main():
    """Main entry point."""
    # Setup logging
    logging.basicConfig(
        level=LOG_LEVEL,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    
    logger = logging.getLogger(__name__)
    
    logger.info("=" * 60)
    logger.info("TradingView WebSocket Client - FX Tick Streamer")
    logger.info("=" * 60)
    logger.info(f"Symbol: {SYMBOL}")
    logger.info(f"Test Mode: {TEST_MODE}")
    logger.info("Press Ctrl+C to stop")
    logger.info("-" * 60)
    
    # Create client
    client = TradingViewWS(
        symbol=SYMBOL,
        on_tick=on_tick,
        test_mode=TEST_MODE,
        debug=DEBUG_MODE
    )
    
    # Start client
    try:
        client.start()
        client.wait()  # Block until stopped
    except KeyboardInterrupt:
        logger.info("\nStopping client...")
        client.stop()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        client.stop()
        sys.exit(1)


if __name__ == "__main__":
    main()

